"""ENF Compare — match an extracted ENF trace against grid reference data.

Slides the query trace across all available grid reference windows and ranks
matches by a composite score combining Pearson correlation and threshold
coverage.  Outputs results to JSON and optionally generates overlay PNGs.
"""
from __future__ import annotations

import argparse
import json
import sys
from dataclasses import dataclass
from pathlib import Path

import numpy as np
import pandas as pd

MAX_REFERENCE_GAP_SECONDS = 5.0
CORRELATION_WEIGHT = 0.4
COVERAGE_WEIGHT = 0.6
DEFAULT_MIN_SEPARATION_SECONDS = 5.0


@dataclass(frozen=True)
class ReferenceSegment:
    index: pd.DatetimeIndex
    values: np.ndarray


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
    parser.add_argument(
        "--min-separation-sec",
        type=float,
        default=DEFAULT_MIN_SEPARATION_SECONDS,
        help=(
            "Minimum separation in seconds between returned match start times "
            f"(default: {DEFAULT_MIN_SEPARATION_SECONDS}). Set to 0 to allow "
            "near-duplicate offsets."
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

def resample_grid_segments(
    grid: pd.DataFrame,
    max_gap_seconds: float = MAX_REFERENCE_GAP_SECONDS,
    min_segment_length: int = 1,
) -> list[ReferenceSegment]:
    """Resample contiguous reference segments to 1-second intervals.

    Missing data should not become synthetic match windows. Split the observed
    grid data at large timestamp gaps, then resample each contiguous segment
    independently.
    """
    if grid.empty:
        return []

    ordered = grid.sort_values("timestamp_utc").reset_index(drop=True)
    gap_seconds = ordered["timestamp_utc"].diff().dt.total_seconds()
    segment_ids = gap_seconds.gt(max_gap_seconds).cumsum()

    segments: list[ReferenceSegment] = []
    for _, segment in ordered.groupby(segment_ids):
        ts = segment.set_index("timestamp_utc")["frequency_hz"]
        ts = ts[~ts.index.duplicated(keep="first")]
        resampled = ts.resample("1s").mean().interpolate(method="time", limit_area="inside")
        resampled = resampled.dropna()
        if len(resampled) < min_segment_length:
            continue
        segments.append(
            ReferenceSegment(
                index=resampled.index,
                values=resampled.to_numpy(dtype=np.float64),
            )
        )

    return segments


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
    reference_centered = reference - reference.mean()

    # Center query and compute its sum of squares once.
    q_mean = query.mean()
    q_centered = query - q_mean
    q_ss = np.sum(q_centered ** 2)
    if q_ss <= 1e-12:
        raise SystemExit("ERROR: Query trace has zero variance — cannot correlate.")

    # Sliding mean and sum of squares of reference windows
    cumsum = np.cumsum(np.concatenate(([0.0], reference_centered)))
    cumsum2 = np.cumsum(np.concatenate(([0.0], reference_centered ** 2)))

    window_sum = cumsum[L:] - cumsum[:n_offsets]
    window_sum2 = cumsum2[L:] - cumsum2[:n_offsets]

    window_mean = window_sum / L
    window_ss = window_sum2 - (window_sum ** 2) / L
    window_ss = np.maximum(window_ss, 0.0)

    # FFT-based cross-correlation
    fft_size = 1
    while fft_size < R + L - 1:
        fft_size <<= 1

    q_padded = np.zeros(fft_size)
    q_padded[:L] = q_centered[::-1]  # reverse for correlation

    r_padded = np.zeros(fft_size)
    r_padded[:R] = reference_centered

    Q = np.fft.rfft(q_padded)
    Ref = np.fft.rfft(r_padded)
    cross = np.fft.irfft(Q * Ref, n=fft_size)

    # Extract valid positions: the correlation at offset i uses ref[i:i+L]
    # With q reversed and padded at start, the valid cross-corr values are
    # at indices [L-1, L-1 + n_offsets)
    raw_cross = cross[L - 1: L - 1 + n_offsets]

    # With the centered query, raw_cross is the Pearson numerator:
    # sum((query - q_mean) * reference_window).
    # Divide by sqrt(sum((query-q_mean)^2) * sum((ref-ref_mean)^2)).
    denom = np.sqrt(q_ss * window_ss)
    valid_std = denom > 1e-12
    correlations = np.zeros(n_offsets)
    correlations[valid_std] = raw_cross[valid_std] / denom[valid_std]

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
    correlation_weight: float = CORRELATION_WEIGHT,
    coverage_weight: float = COVERAGE_WEIGHT,
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

    # Step 1: FFT-based correlation for all offsets
    correlations = fft_cross_correlation(query, reference)

    # Step 2: Pick top candidates by correlation
    n_cand = min(n_candidates, n_offsets)
    candidate_indices = np.argsort(correlations)[::-1][:n_cand]

    # Step 3: Compute threshold coverage only for candidates
    coverages = compute_threshold_coverage(
        query, reference, candidate_indices, threshold
    )

    # Step 4: Composite scores
    composite = (
        correlation_weight * correlations[candidate_indices]
        + coverage_weight * coverages
    )

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


def compare_against_reference_segments(
    query: np.ndarray,
    reference_segments: list[ReferenceSegment],
    threshold: float,
    top_n: int,
    n_candidates: int = 50,
    correlation_weight: float = CORRELATION_WEIGHT,
    coverage_weight: float = COVERAGE_WEIGHT,
    min_separation_sec: float = DEFAULT_MIN_SEPARATION_SECONDS,
) -> list[dict]:
    """Compare a query trace against many contiguous reference segments."""
    combined: list[dict] = []
    candidate_pool_size = max(n_candidates, top_n * 10)

    for segment in reference_segments:
        if len(segment.values) < len(query):
            continue

        segment_results = sliding_window_compare(
            query,
            segment.values,
            threshold,
            top_n=candidate_pool_size,
            n_candidates=candidate_pool_size,
            correlation_weight=correlation_weight,
            coverage_weight=coverage_weight,
        )
        for match in segment_results:
            offset = match["ref_offset_index"]
            combined.append(
                {
                    **match,
                    "ref_start_utc": segment.index[offset],
                    "ref_end_utc": segment.index[offset + len(query) - 1],
                    "_reference": segment.values,
                    "_ref_index": segment.index,
                }
            )

    combined.sort(
        key=lambda m: (
            m["composite_score"],
            m["threshold_coverage"],
            m["correlation"],
            m["ref_start_utc"],
        ),
        reverse=True,
    )
    distinct_matches = select_distinct_matches(
        combined,
        top_n=top_n,
        min_separation_sec=min_separation_sec,
    )

    ranked: list[dict] = []
    for rank_idx, match in enumerate(distinct_matches, start=1):
        ranked.append({**match, "rank": rank_idx})
    return ranked


def select_distinct_matches(
    matches: list[dict],
    top_n: int,
    min_separation_sec: float,
) -> list[dict]:
    """Greedily keep the highest-ranked matches that are time-separated."""
    if top_n <= 0:
        return []
    if min_separation_sec <= 0:
        return matches[:top_n]

    selected: list[dict] = []
    for match in matches:
        start = pd.Timestamp(match["ref_start_utc"])
        too_close = any(
            abs((start - pd.Timestamp(kept["ref_start_utc"])).total_seconds())
            <= min_separation_sec
            for kept in selected
        )
        if too_close:
            continue
        selected.append(match)
        if len(selected) >= top_n:
            break

    return selected


# ---------------------------------------------------------------------------
# Output
# ---------------------------------------------------------------------------

def print_results(
    results: list[dict],
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
        ts = pd.Timestamp(m["ref_start_utc"])
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
    query_length: int,
    trace_path: Path,
    region: str,
    threshold: float,
    output_path: Path,
) -> None:
    """Write results to a JSON file."""
    matches = []
    for m in results:
        start_ts = pd.Timestamp(m["ref_start_utc"])
        end_ts = pd.Timestamp(m["ref_end_utc"])
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
        reference = m["_reference"]
        ref_index = m["_ref_index"]
        offset = m["ref_offset_index"]
        ref_window = reference[offset: offset + L]
        start_ts = pd.Timestamp(m["ref_start_utc"])
        end_ts = pd.Timestamp(m["ref_end_utc"])

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

    # Resample observed reference data into contiguous segments only.
    print("Resampling grid data to 1-second contiguous segments...")
    reference_segments = resample_grid_segments(
        grid,
        min_segment_length=len(query),
    )
    total_samples = sum(len(segment.values) for segment in reference_segments)
    print(
        f"Prepared {len(reference_segments)} segment(s) "
        f"with {total_samples} resampled samples"
    )
    print("Scoring candidate windows across contiguous reference segments...")

    # Sliding-window comparison
    results = compare_against_reference_segments(
        query,
        reference_segments,
        args.threshold,
        args.top_n,
        min_separation_sec=args.min_separation_sec,
    )

    if not results:
        print("No matches found.")
        return 1

    # Parse recording time if provided
    recording_time = None
    if args.recording_time:
        recording_time = pd.Timestamp(args.recording_time, tz="UTC")

    # Console output
    print_results(results, recording_time)

    # JSON output
    output_path = args.output or args.trace.with_name(
        f"{args.trace.stem}_results.json"
    )
    write_json(
        results, len(query),
        args.trace, args.region, args.threshold, output_path,
    )

    # Plot output
    if args.plot:
        output_stem = output_path.stem.replace("_results", "")
        generate_plots(
            results, query,
            output_stem, output_path.parent,
        )

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
