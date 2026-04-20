"""ENF Compare — match an extracted ENF trace against grid reference data.

Slides the query trace across all available grid reference windows and ranks
matches by a composite score combining Pearson correlation and threshold
coverage.  Outputs results to JSON and optionally generates overlay PNGs.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

import numpy as np
import pandas as pd


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Compare an ENF trace against grid reference data."
    )
    parser.add_argument(
        "--trace", required=True, type=Path,
        help="Path to ENF trace CSV (columns: offset_seconds, frequency_hz).",
    )
    parser.add_argument(
        "--grid-dir", required=True, type=Path,
        help="Directory containing daily grid CSVs.",
    )
    parser.add_argument(
        "--region", required=True, choices=["EI", "WECC", "ERCOT", "Quebec"],
        help="Grid region to compare against.",
    )
    parser.add_argument(
        "--date", default=None,
        help=(
            "Only load CSVs for this date (YYYY-MM-DD). "
            "Can be specified multiple times or as comma-separated list. "
            "If omitted, load all CSVs in grid-dir."
        ),
    )
    parser.add_argument(
        "--top-n", type=int, default=3,
        help="Number of top matches to return (default: 3).",
    )
    parser.add_argument(
        "--threshold", type=float, default=0.01,
        help="Hz threshold for 'close enough' scoring (default: 0.01).",
    )
    parser.add_argument(
        "--output", type=Path, default=None,
        help="JSON output path. Defaults to {trace_stem}_results.json.",
    )
    parser.add_argument(
        "--plot", action="store_true",
        help="Generate overlay PNGs for each top match.",
    )
    parser.add_argument(
        "--recording-time", default=None,
        help=(
            "Known UTC start time of the recording (ISO format, e.g. "
            "2026-04-20T16:36:00). Used only for display/validation."
        ),
    )
    return parser.parse_args(argv)


# ---------------------------------------------------------------------------
# Data loading
# ---------------------------------------------------------------------------

def load_trace(path: Path) -> np.ndarray:
    """Load the ENF trace CSV and return the frequency array."""
    df = pd.read_csv(path)
    if "frequency_hz" not in df.columns or "offset_seconds" not in df.columns:
        raise SystemExit(
            f"ERROR: Trace CSV must have columns 'offset_seconds' and 'frequency_hz'. "
            f"Found: {list(df.columns)}"
        )
    df = df.dropna(subset=["frequency_hz"])
    return df["frequency_hz"].to_numpy(dtype=np.float64)


def resolve_dates(date_arg: str | None) -> list[str] | None:
    """Parse the --date argument into a list of date strings or None."""
    if date_arg is None:
        return None
    dates: list[str] = []
    for part in date_arg.split(","):
        dates.append(part.strip())
    return dates if dates else None


def load_grid_data(
    grid_dir: Path, region: str, dates: list[str] | None
) -> pd.DataFrame:
    """Load and concatenate grid CSVs, filter by region and optional dates."""
    csv_files = sorted(grid_dir.glob("*.csv"))
    if not csv_files:
        raise SystemExit(f"ERROR: No CSV files found in {grid_dir}")

    if dates:
        filtered: list[Path] = []
        for f in csv_files:
            for d in dates:
                if d in f.stem or d in f.name:
                    filtered.append(f)
                    break
        if not filtered:
            # If date filtering finds nothing, fall back to loading all
            print(f"WARNING: No files matched dates {dates}; loading all CSVs.")
            filtered = csv_files
        csv_files = filtered

    print(f"Loading {len(csv_files)} grid CSV file(s)...")
    frames: list[pd.DataFrame] = []
    for f in csv_files:
        df = pd.read_csv(f)
        frames.append(df)

    grid = pd.concat(frames, ignore_index=True)

    # Filter to region
    if "region" in grid.columns:
        grid = grid[grid["region"] == region].copy()
    if grid.empty:
        raise SystemExit(f"ERROR: No data for region '{region}' in grid files.")

    # Parse timestamps
    grid["timestamp_utc"] = pd.to_datetime(grid["timestamp_utc"], utc=True)

    # Clean up
    grid = grid.dropna(subset=["frequency_hz"])
    grid = grid.drop_duplicates(subset=["timestamp_utc"])
    grid = grid.sort_values("timestamp_utc").reset_index(drop=True)

    print(
        f"Grid data: {len(grid)} samples from "
        f"{grid['timestamp_utc'].iloc[0]} to {grid['timestamp_utc'].iloc[-1]}"
    )
    return grid


# ---------------------------------------------------------------------------
# Resampling
# ---------------------------------------------------------------------------

def resample_grid(grid: pd.DataFrame) -> tuple[np.ndarray, pd.DatetimeIndex]:
    """Resample grid data to regular 1-second intervals.

    Returns the frequency array and the corresponding DatetimeIndex.
    """
    ts = grid.set_index("timestamp_utc")["frequency_hz"]
    ts = ts[~ts.index.duplicated(keep="first")]
    resampled = ts.resample("1s").mean().interpolate(method="time")
    resampled = resampled.dropna()
    return resampled.values.astype(np.float64), resampled.index


# ---------------------------------------------------------------------------
# Sliding-window comparison
# ---------------------------------------------------------------------------

def fft_cross_correlation(query: np.ndarray, reference: np.ndarray) -> np.ndarray:
    """Compute normalized cross-correlation using FFT.

    Returns an array of correlation values for each valid offset.
    """
    L = len(query)
    R = len(reference)
    n_offsets = R - L + 1

    # Normalize query
    q_mean = query.mean()
    q_std = query.std()
    if q_std == 0:
        raise SystemExit("ERROR: Query trace has zero variance — cannot correlate.")
    q_norm = (query - q_mean) / q_std

    # Sliding mean and std of reference windows
    cumsum = np.cumsum(np.concatenate(([0.0], reference)))
    cumsum2 = np.cumsum(np.concatenate(([0.0], reference ** 2)))

    window_sum = cumsum[L:] - cumsum[:n_offsets]
    window_sum2 = cumsum2[L:] - cumsum2[:n_offsets]

    window_mean = window_sum / L
    window_var = window_sum2 / L - window_mean ** 2
    # Clamp negative variance from floating point
    window_var = np.maximum(window_var, 0.0)
    window_std = np.sqrt(window_var)

    # FFT-based cross-correlation
    fft_size = 1
    while fft_size < R + L - 1:
        fft_size <<= 1

    q_padded = np.zeros(fft_size)
    q_padded[:L] = q_norm[::-1]  # reverse for correlation

    r_padded = np.zeros(fft_size)
    r_padded[:R] = reference

    Q = np.fft.rfft(q_padded)
    Ref = np.fft.rfft(r_padded)
    cross = np.fft.irfft(Q * Ref, n=fft_size)

    # Extract valid positions: the correlation at offset i uses ref[i:i+L]
    # With q reversed and padded at start, the valid cross-corr values are
    # at indices [L-1, L-1 + n_offsets)
    raw_cross = cross[L - 1: L - 1 + n_offsets]

    # Normalize: corr = (sum(q_norm * r_window) / L) but we need to account
    # for reference normalization per-window.
    # raw_cross = sum(q_norm * ref[i:i+L]) for each i
    # Pearson = (raw_cross - L * q_norm_mean * window_mean) / (L * q_norm_std * window_std)
    # Since q_norm already has mean 0 and std 1:
    # Pearson = (raw_cross - 0) / (L * 1 * window_std) ... but raw_cross uses raw ref
    # Actually: raw_cross[i] = sum(q_norm[j] * ref[i+j]) for j in [0,L)
    # Pearson[i] = (1/L) * sum(q_norm[j] * (ref[i+j] - window_mean[i]) / window_std[i])
    #            = (raw_cross[i] - sum(q_norm) * window_mean[i]) / (L * window_std[i])
    # sum(q_norm) = 0, so:
    # Pearson[i] = raw_cross[i] / (L * window_std[i])

    # Avoid division by zero
    valid_std = window_std > 1e-10
    correlations = np.zeros(n_offsets)
    correlations[valid_std] = raw_cross[valid_std] / (L * window_std[valid_std])

    # Clamp to [-1, 1] for numerical safety
    correlations = np.clip(correlations, -1.0, 1.0)

    return correlations


def compute_threshold_coverage(
    query: np.ndarray, reference: np.ndarray, offsets: np.ndarray, threshold: float
) -> np.ndarray:
    """Compute threshold coverage for specific offsets."""
    L = len(query)
    coverages = np.empty(len(offsets), dtype=np.float64)
    for idx, offset in enumerate(offsets):
        window = reference[offset: offset + L]
        coverages[idx] = np.mean(np.abs(query - window) < threshold)
    return coverages


def sliding_window_compare(
    query: np.ndarray,
    reference: np.ndarray,
    threshold: float,
    top_n: int,
    n_candidates: int = 50,
) -> list[dict]:
    """Run the sliding-window comparison and return top matches."""
    L = len(query)
    R = len(reference)
    n_offsets = R - L + 1

    if n_offsets <= 0:
        raise SystemExit(
            f"ERROR: Reference ({R} samples) is shorter than query ({L} samples). "
            "Cannot compare."
        )

    print(
        f"Sliding window: query={L}s, reference={R}s, offsets={n_offsets} ... ",
        end="",
        flush=True,
    )

    # Step 1: FFT-based correlation for all offsets
    correlations = fft_cross_correlation(query, reference)
    print("correlation done ... ", end="", flush=True)

    # Step 2: Pick top candidates by correlation
    n_cand = min(n_candidates, n_offsets)
    candidate_indices = np.argsort(correlations)[::-1][:n_cand]

    # Step 3: Compute threshold coverage only for candidates
    coverages = compute_threshold_coverage(
        query, reference, candidate_indices, threshold
    )
    print("coverage done.")

    # Step 4: Composite scores
    composite = 0.6 * correlations[candidate_indices] + 0.4 * coverages

    # Step 5: Rank by composite score
    ranked = np.argsort(composite)[::-1]

    results: list[dict] = []
    for rank_idx in range(min(top_n, len(ranked))):
        cand_pos = ranked[rank_idx]
        offset = int(candidate_indices[cand_pos])
        results.append({
            "rank": rank_idx + 1,
            "ref_offset_index": offset,
            "correlation": float(correlations[offset]),
            "threshold_coverage": float(coverages[cand_pos]),
            "composite_score": float(composite[cand_pos]),
        })

    return results


# ---------------------------------------------------------------------------
# Output
# ---------------------------------------------------------------------------

def print_results(
    results: list[dict],
    ref_index: pd.DatetimeIndex,
    recording_time: pd.Timestamp | None,
) -> None:
    """Print a summary table to the console."""
    print()
    header = f"{'Rank':<5} {'Reference Start (UTC)':<32} {'Corr':>7} {'Cover':>7} {'Score':>7}"
    if recording_time is not None:
        header += f"  {'Offset':>10}"
    print(header)
    print("-" * len(header))

    for m in results:
        ts = ref_index[m["ref_offset_index"]]
        row = (
            f"{m['rank']:<5} {str(ts):<32} "
            f"{m['correlation']:>7.4f} {m['threshold_coverage']:>7.4f} "
            f"{m['composite_score']:>7.4f}"
        )
        if recording_time is not None:
            delta = ts - recording_time
            row += f"  {delta.total_seconds():>+10.0f}s"
        print(row)
    print()


def write_json(
    results: list[dict],
    ref_index: pd.DatetimeIndex,
    query_length: int,
    trace_path: Path,
    region: str,
    threshold: float,
    output_path: Path,
) -> None:
    """Write results to a JSON file."""
    matches = []
    for m in results:
        start_ts = ref_index[m["ref_offset_index"]]
        end_ts = ref_index[m["ref_offset_index"] + query_length - 1]
        matches.append({
            "rank": m["rank"],
            "ref_start_utc": start_ts.isoformat(),
            "ref_end_utc": end_ts.isoformat(),
            "correlation": round(m["correlation"], 6),
            "threshold_coverage": round(m["threshold_coverage"], 6),
            "composite_score": round(m["composite_score"], 6),
            "ref_offset_index": m["ref_offset_index"],
        })

    payload = {
        "trace_file": str(trace_path),
        "region": region,
        "threshold_hz": threshold,
        "query_length_seconds": query_length,
        "matches": matches,
    }

    output_path.parent.mkdir(parents=True, exist_ok=True)
    with open(output_path, "w", encoding="utf-8") as f:
        json.dump(payload, f, indent=2)
    print(f"Results written to {output_path}")


def generate_plots(
    results: list[dict],
    query: np.ndarray,
    reference: np.ndarray,
    ref_index: pd.DatetimeIndex,
    output_stem: str,
    output_dir: Path,
) -> None:
    """Generate overlay PNGs for each top match."""
    try:
        import matplotlib.pyplot as plt
    except ImportError:
        print("WARNING: matplotlib not installed — skipping plots.")
        return

    output_dir.mkdir(parents=True, exist_ok=True)
    L = len(query)
    seconds = np.arange(L)

    for m in results:
        offset = m["ref_offset_index"]
        ref_window = reference[offset: offset + L]
        start_ts = ref_index[offset]
        end_ts = ref_index[offset + L - 1]

        fig, ax = plt.subplots(figsize=(12, 5))
        ax.plot(seconds, query, color="blue", linewidth=0.8, label="Query (trace)")
        ax.plot(
            seconds, ref_window, color="orangered", linewidth=0.8,
            alpha=0.8, label="Reference (grid)",
        )
        ax.set_xlabel("Seconds from start")
        ax.set_ylabel("Frequency (Hz)")
        ax.set_title(
            f"Match #{m['rank']} — Score: {m['composite_score']:.4f} | "
            f"Corr: {m['correlation']:.4f}\n"
            f"Ref: {start_ts} → {end_ts}"
        )
        ax.legend(loc="upper right")
        ax.grid(True, alpha=0.3)
        fig.tight_layout()

        png_path = output_dir / f"{output_stem}_match_{m['rank']}.png"
        fig.savefig(png_path, dpi=150)
        plt.close(fig)
        print(f"Plot saved: {png_path}")


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)

    # Validate inputs
    if not args.trace.exists():
        print(f"ERROR: Trace file not found: {args.trace}", file=sys.stderr)
        return 1
    if not args.grid_dir.is_dir():
        print(f"ERROR: Grid directory not found: {args.grid_dir}", file=sys.stderr)
        return 1

    # Load data
    query = load_trace(args.trace)
    print(f"Trace loaded: {len(query)} samples from {args.trace.name}")

    dates = resolve_dates(args.date)
    grid = load_grid_data(args.grid_dir, args.region, dates)

    # Resample grid to 1-second intervals
    print("Resampling grid data to 1-second intervals...")
    ref_values, ref_index = resample_grid(grid)
    print(f"Resampled reference: {len(ref_values)} samples")

    # Sliding-window comparison
    results = sliding_window_compare(
        query, ref_values, args.threshold, args.top_n
    )

    if not results:
        print("No matches found.")
        return 1

    # Parse recording time if provided
    recording_time = None
    if args.recording_time:
        recording_time = pd.Timestamp(args.recording_time, tz="UTC")

    # Console output
    print_results(results, ref_index, recording_time)

    # JSON output
    output_path = args.output or args.trace.with_name(
        f"{args.trace.stem}_results.json"
    )
    write_json(
        results, ref_index, len(query),
        args.trace, args.region, args.threshold, output_path,
    )

    # Plot output
    if args.plot:
        output_stem = output_path.stem.replace("_results", "")
        generate_plots(
            results, query, ref_values, ref_index,
            output_stem, output_path.parent,
        )

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
