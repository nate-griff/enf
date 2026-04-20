"""
GUI viewer for ENF comparison results.

Overlays query ENF traces against matched reference grid windows for visual
inspection.  Reads results JSON produced by enf_compare.py and the underlying
grid CSVs to render aligned frequency traces.

tkinter + matplotlib: select matches, scroll and zoom the overlaid view.
"""

from __future__ import annotations

import argparse
import json
import math
from datetime import datetime, timezone
from pathlib import Path
from tkinter import filedialog, messagebox, ttk
import tkinter as tk

import pandas as pd
from matplotlib.backends.backend_tkagg import FigureCanvasTkAgg
from matplotlib.figure import Figure

REGIONS = ("EI", "WECC", "ERCOT", "Quebec")
MIN_WINDOW_SEC = 1.0


# ---------------------------------------------------------------------------
# Data helpers
# ---------------------------------------------------------------------------

def load_trace(path: Path) -> pd.DataFrame:
    """Load a query ENF trace CSV (offset_seconds, frequency_hz)."""
    df = pd.read_csv(path)
    required = {"offset_seconds", "frequency_hz"}
    missing = required - set(df.columns)
    if missing:
        raise ValueError(f"Trace CSV missing columns: {', '.join(sorted(missing))}")
    df = df.dropna(subset=["frequency_hz"]).sort_values("offset_seconds").reset_index(drop=True)
    return df


def find_grid_dir(results_path: Path | None, explicit: Path | None) -> Path | None:
    """Resolve grid data directory from explicit path or by searching parents."""
    if explicit is not None:
        p = Path(explicit)
        if p.is_dir():
            return p
        return None
    if results_path is None:
        return None
    # Walk up parent directories looking for source_data/grid_data
    for parent in results_path.resolve().parents:
        candidate = parent / "source_data" / "grid_data"
        if candidate.is_dir():
            return candidate
    return None


def load_grid_window(
    grid_dir: Path,
    region: str,
    start_utc: datetime,
    end_utc: datetime,
) -> pd.DataFrame:
    """Load grid data for a time window, returning (offset_seconds, frequency_hz)."""
    # Determine which daily CSVs might cover the range
    start_date = start_utc.date()
    end_date = end_utc.date()
    csvs: list[Path] = []
    current = start_date
    while current <= end_date:
        pattern = f"*{current.isoformat()}*"
        csvs.extend(grid_dir.glob(pattern))
        current = current.fromordinal(current.toordinal() + 1)

    # Fallback: load all CSVs in directory if date-specific ones not found
    if not csvs:
        csvs = sorted(grid_dir.glob("*.csv"))

    if not csvs:
        return pd.DataFrame(columns=["offset_seconds", "frequency_hz"])

    frames: list[pd.DataFrame] = []
    for csv_path in csvs:
        try:
            df = pd.read_csv(csv_path, parse_dates=["timestamp_utc"])
        except Exception:
            continue
        if "region" in df.columns:
            df = df[df["region"] == region]
        if "frequency_hz" not in df.columns or "timestamp_utc" not in df.columns:
            continue
        frames.append(df[["timestamp_utc", "frequency_hz"]])

    if not frames:
        return pd.DataFrame(columns=["offset_seconds", "frequency_hz"])

    combined = pd.concat(frames, ignore_index=True)
    combined = combined.dropna(subset=["frequency_hz"]).sort_values("timestamp_utc")

    # Filter to the requested window
    start_ts = pd.Timestamp(start_utc)
    end_ts = pd.Timestamp(end_utc)
    mask = (combined["timestamp_utc"] >= start_ts) & (combined["timestamp_utc"] <= end_ts)
    window = combined.loc[mask].copy()

    if window.empty:
        return pd.DataFrame(columns=["offset_seconds", "frequency_hz"])

    t0 = window["timestamp_utc"].iloc[0]
    window["offset_seconds"] = (window["timestamp_utc"] - t0).dt.total_seconds()
    return window[["offset_seconds", "frequency_hz"]].reset_index(drop=True)


# ---------------------------------------------------------------------------
# Main viewer class
# ---------------------------------------------------------------------------

class ENFViewer(tk.Tk):
    def __init__(
        self,
        results_path: Path | None = None,
        trace_path: Path | None = None,
        grid_dir: Path | None = None,
        region: str | None = None,
    ) -> None:
        super().__init__()
        self.title("ENF Comparison Viewer")
        self.geometry("1100x700")
        self.minsize(800, 520)

        self._results_path: Path | None = results_path
        self._trace_path: Path | None = trace_path
        self._grid_dir: Path | None = grid_dir
        self._region: str | None = region

        self._trace_df: pd.DataFrame | None = None
        self._ref_df: pd.DataFrame | None = None
        self._matches: list[dict] = []
        self._current_match_idx: int = 0

        # View state
        self._total_seconds: float = 1.0
        self._scroll_frac: float = 0.0
        self._zoom_frac: float = 1.0  # 1.0 = full range visible
        self._updating_scroll: bool = False
        self._updating_zoom: bool = False

        self._build_ui()

        if results_path is not None:
            self._load_results_file(results_path)
        elif trace_path is not None:
            self._load_trace_standalone(trace_path)

    # ------------------------------------------------------------------
    # UI construction
    # ------------------------------------------------------------------

    def _build_ui(self) -> None:
        # Top bar
        top = ttk.Frame(self, padding=4)
        top.pack(fill=tk.X)

        ttk.Button(top, text="Open Results…", command=self._on_open).pack(side=tk.LEFT, padx=(0, 8))

        ttk.Label(top, text="Match:").pack(side=tk.LEFT)
        self._match_combo = ttk.Combobox(top, width=30, state="readonly", values=[])
        self._match_combo.pack(side=tk.LEFT, padx=4)
        self._match_combo.bind("<<ComboboxSelected>>", lambda _e: self._on_match_selected())

        self._score_label = ttk.Label(top, text="", font=("TkDefaultFont", 9, "bold"))
        self._score_label.pack(side=tk.LEFT, padx=12)

        # Matplotlib figure
        self._fig = Figure(figsize=(10, 4.5), dpi=100)
        self._ax = self._fig.add_subplot(111)
        self._canvas = FigureCanvasTkAgg(self._fig, master=self)
        self._canvas.get_tk_widget().pack(fill=tk.BOTH, expand=True, padx=4, pady=4)

        # Bottom controls
        ctrl = ttk.Frame(self, padding=(4, 0, 4, 4))
        ctrl.pack(fill=tk.X)

        ttk.Label(ctrl, text="Scroll:").pack(side=tk.LEFT)
        self._scroll_slider = ttk.Scale(
            ctrl, from_=0.0, to=1.0, orient=tk.HORIZONTAL, command=self._on_scroll,
        )
        self._scroll_slider.pack(side=tk.LEFT, fill=tk.X, expand=True, padx=4)

        zoom_row = ttk.Frame(self, padding=(4, 0, 4, 4))
        zoom_row.pack(fill=tk.X)

        ttk.Label(zoom_row, text="Zoom:").pack(side=tk.LEFT)
        self._zoom_slider = ttk.Scale(
            zoom_row, from_=0.0, to=1.0, orient=tk.HORIZONTAL, command=self._on_zoom,
        )
        self._zoom_slider.set(1.0)
        self._zoom_slider.pack(side=tk.LEFT, fill=tk.X, expand=True, padx=4)
        ttk.Label(zoom_row, text="(narrow ← → full)").pack(side=tk.LEFT)

        nav_row = ttk.Frame(self, padding=(4, 0, 4, 6))
        nav_row.pack(fill=tk.X)

        ttk.Button(nav_row, text="← Prev Match", command=self._prev_match).pack(side=tk.LEFT, padx=4)
        ttk.Button(nav_row, text="Next Match →", command=self._next_match).pack(side=tk.LEFT, padx=4)

        self._window_label = ttk.Label(nav_row, text="")
        self._window_label.pack(side=tk.LEFT, padx=12)

        # Status bar
        self._status = ttk.Label(self, text="Open a results JSON file.", relief=tk.SUNKEN, anchor=tk.W)
        self._status.pack(fill=tk.X, side=tk.BOTTOM)

    # ------------------------------------------------------------------
    # File loading
    # ------------------------------------------------------------------

    def _on_open(self) -> None:
        path = filedialog.askopenfilename(
            title="Open results JSON",
            filetypes=[("JSON", "*.json"), ("All", "*.*")],
        )
        if path:
            self._load_results_file(Path(path))

    def _load_results_file(self, path: Path) -> None:
        try:
            with open(path, "r", encoding="utf-8") as f:
                data = json.load(f)
        except Exception as exc:
            messagebox.showerror("Load error", str(exc))
            return

        self._results_path = path.resolve()
        self._region = data.get("region", self._region)
        self._matches = data.get("matches", [])

        # Resolve trace file
        trace_file = data.get("trace_file")
        if trace_file:
            tp = Path(trace_file)
            if not tp.is_absolute():
                tp = path.parent / tp
            if tp.is_file():
                self._trace_path = tp

        # Resolve grid dir
        if self._grid_dir is None:
            self._grid_dir = find_grid_dir(self._results_path, None)

        # Load trace
        if self._trace_path and self._trace_path.is_file():
            try:
                self._trace_df = load_trace(self._trace_path)
            except Exception as exc:
                messagebox.showwarning("Trace warning", f"Could not load trace: {exc}")
                self._trace_df = None
        else:
            self._trace_df = None

        # Populate match combobox
        self._populate_match_combo()

        if self._matches:
            self._current_match_idx = 0
            self._match_combo.current(0)
            self._select_match(0)

        self._status.config(text=f"Results: {path.name}  |  {len(self._matches)} matches  |  Region: {self._region or '?'}")

    def _load_trace_standalone(self, path: Path) -> None:
        try:
            self._trace_df = load_trace(path)
            self._trace_path = path.resolve()
        except Exception as exc:
            messagebox.showerror("Trace error", str(exc))
            return
        self._total_seconds = float(self._trace_df["offset_seconds"].max() - self._trace_df["offset_seconds"].min())
        self._total_seconds = max(self._total_seconds, MIN_WINDOW_SEC)
        self._reset_view()
        self._redraw()
        self._status.config(text=f"Trace: {path.name}  |  {len(self._trace_df)} points")

    def _populate_match_combo(self) -> None:
        labels = []
        for m in self._matches:
            rank = m.get("rank", "?")
            score = m.get("composite_score", 0.0)
            labels.append(f"Match {rank} — score: {score:.3f}")
        self._match_combo["values"] = labels

    # ------------------------------------------------------------------
    # Match selection
    # ------------------------------------------------------------------

    def _on_match_selected(self) -> None:
        idx = self._match_combo.current()
        if idx >= 0:
            self._select_match(idx)

    def _prev_match(self) -> None:
        if not self._matches:
            return
        idx = max(0, self._current_match_idx - 1)
        self._match_combo.current(idx)
        self._select_match(idx)

    def _next_match(self) -> None:
        if not self._matches:
            return
        idx = min(len(self._matches) - 1, self._current_match_idx + 1)
        self._match_combo.current(idx)
        self._select_match(idx)

    def _select_match(self, idx: int) -> None:
        self._current_match_idx = idx
        match = self._matches[idx]

        # Update score label
        corr = match.get("correlation", 0.0)
        cov = match.get("threshold_coverage", 0.0)
        score = match.get("composite_score", 0.0)
        self._score_label.config(text=f"Score: {score:.3f} (r={corr:.3f}, cov={cov * 100:.0f}%)")

        # Load reference window
        self._ref_df = None
        if self._grid_dir and self._region:
            start_str = match.get("ref_start_utc")
            end_str = match.get("ref_end_utc")
            if start_str and end_str:
                try:
                    start_utc = datetime.fromisoformat(start_str)
                    end_utc = datetime.fromisoformat(end_str)
                    self._ref_df = load_grid_window(self._grid_dir, self._region, start_utc, end_utc)
                except Exception:
                    self._ref_df = None

        # Compute total visible seconds from the longest series
        lengths = []
        if self._trace_df is not None and not self._trace_df.empty:
            lengths.append(self._trace_df["offset_seconds"].max() - self._trace_df["offset_seconds"].min())
        if self._ref_df is not None and not self._ref_df.empty:
            lengths.append(self._ref_df["offset_seconds"].max() - self._ref_df["offset_seconds"].min())
        self._total_seconds = max(lengths) if lengths else MIN_WINDOW_SEC
        self._total_seconds = max(self._total_seconds, MIN_WINDOW_SEC)

        self._reset_view()
        self._redraw()

    # ------------------------------------------------------------------
    # Scroll / Zoom
    # ------------------------------------------------------------------

    def _reset_view(self) -> None:
        self._scroll_frac = 0.0
        self._zoom_frac = 1.0
        self._updating_scroll = True
        self._scroll_slider.set(0.0)
        self._updating_scroll = False
        self._updating_zoom = True
        self._zoom_slider.set(1.0)
        self._updating_zoom = False

    def _on_scroll(self, value: str) -> None:
        if self._updating_scroll:
            return
        self._scroll_frac = float(value)
        self._redraw()

    def _on_zoom(self, value: str) -> None:
        if self._updating_zoom:
            return
        self._zoom_frac = float(value)
        self._clamp_scroll()
        self._redraw()

    def _clamp_scroll(self) -> None:
        window_sec = self._window_seconds()
        if window_sec >= self._total_seconds:
            self._scroll_frac = 0.0
            self._updating_scroll = True
            self._scroll_slider.set(0.0)
            self._updating_scroll = False

    def _window_seconds(self) -> float:
        """Compute visible window width from zoom_frac (log scale)."""
        lo = max(MIN_WINDOW_SEC, 1.0)
        hi = max(self._total_seconds, lo)
        if hi <= lo:
            return hi
        log_lo = math.log10(lo)
        log_hi = math.log10(hi)
        f = max(0.0, min(1.0, self._zoom_frac))
        log_w = log_lo + f * (log_hi - log_lo)
        return 10.0 ** log_w

    def _view_range_seconds(self) -> tuple[float, float]:
        """Return (start_sec, end_sec) of the visible window."""
        w = self._window_seconds()
        if w >= self._total_seconds:
            return 0.0, self._total_seconds
        max_start = self._total_seconds - w
        start = self._scroll_frac * max_start
        return start, start + w

    # ------------------------------------------------------------------
    # Drawing
    # ------------------------------------------------------------------

    def _redraw(self) -> None:
        self._ax.clear()

        has_trace = self._trace_df is not None and not self._trace_df.empty
        has_ref = self._ref_df is not None and not self._ref_df.empty

        if not has_trace and not has_ref:
            self._ax.text(
                0.5, 0.5, "No data to display",
                ha="center", va="center", transform=self._ax.transAxes,
            )
            self._canvas.draw_idle()
            self._window_label.config(text="")
            return

        start_sec, end_sec = self._view_range_seconds()

        # Plot query trace
        if has_trace:
            assert self._trace_df is not None
            mask = (
                (self._trace_df["offset_seconds"] >= start_sec)
                & (self._trace_df["offset_seconds"] <= end_sec)
            )
            sub = self._trace_df.loc[mask]
            if not sub.empty:
                self._ax.plot(
                    sub["offset_seconds"], sub["frequency_hz"],
                    color="#1f77b4", linewidth=1.0, label="Query ENF",
                )

        # Plot reference
        if has_ref:
            assert self._ref_df is not None
            mask = (
                (self._ref_df["offset_seconds"] >= start_sec)
                & (self._ref_df["offset_seconds"] <= end_sec)
            )
            sub = self._ref_df.loc[mask]
            if not sub.empty:
                self._ax.plot(
                    sub["offset_seconds"], sub["frequency_hz"],
                    color="#e55100", linewidth=1.0, alpha=0.85, label="Reference Grid",
                )

        # Axis formatting
        self._ax.set_xlim(start_sec, end_sec)
        self._ax.set_xlabel("Seconds from start")
        self._ax.set_ylabel("Frequency (Hz)")

        # Auto-scale Y with padding
        y_vals: list[float] = []
        if has_trace:
            assert self._trace_df is not None
            mask_t = (
                (self._trace_df["offset_seconds"] >= start_sec)
                & (self._trace_df["offset_seconds"] <= end_sec)
            )
            y_vals.extend(self._trace_df.loc[mask_t, "frequency_hz"].tolist())
        if has_ref:
            assert self._ref_df is not None
            mask_r = (
                (self._ref_df["offset_seconds"] >= start_sec)
                & (self._ref_df["offset_seconds"] <= end_sec)
            )
            y_vals.extend(self._ref_df.loc[mask_r, "frequency_hz"].tolist())

        if y_vals:
            ymin, ymax = min(y_vals), max(y_vals)
            pad = max(0.002, (ymax - ymin) * 0.08 + 1e-6)
            self._ax.set_ylim(ymin - pad, ymax + pad)

        # Title with UTC info
        title_parts = []
        if self._region:
            title_parts.append(self._region)
        if self._matches and self._current_match_idx < len(self._matches):
            m = self._matches[self._current_match_idx]
            ref_start = m.get("ref_start_utc", "")
            ref_end = m.get("ref_end_utc", "")
            if ref_start and ref_end:
                title_parts.append(f"Ref: {ref_start} → {ref_end}")
        self._ax.set_title("  |  ".join(title_parts) if title_parts else "ENF Overlay")

        self._ax.legend(loc="upper right", fontsize=8)
        self._ax.grid(True, alpha=0.35)
        self._fig.tight_layout()
        self._canvas.draw_idle()

        # Window label
        w = self._window_seconds()
        self._window_label.config(text=f"Window: {w:.1f}s  |  View: {start_sec:.1f}s – {end_sec:.1f}s")


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(
        description="Visual inspector for ENF comparison results.",
    )
    p.add_argument(
        "--results",
        type=Path,
        default=None,
        help="Path to results JSON from enf_compare.py.",
    )
    p.add_argument(
        "--trace",
        type=Path,
        default=None,
        help="Path to ENF trace CSV (offset_seconds, frequency_hz).",
    )
    p.add_argument(
        "--grid-dir",
        type=Path,
        default=None,
        help="Path to grid data directory.",
    )
    p.add_argument(
        "--region",
        type=str,
        default=None,
        choices=REGIONS,
        help="Grid region (EI, WECC, ERCOT, Quebec).",
    )
    args = p.parse_args()

    if args.results is None and args.trace is None:
        p.error("Either --results or --trace (with --grid-dir and --region) must be provided.")
    if args.results is None:
        if args.grid_dir is None or args.region is None:
            p.error("When not using --results, all of --trace, --grid-dir, and --region are required.")

    return args


def main() -> int:
    args = parse_args()

    grid_dir = args.grid_dir
    if grid_dir is None and args.results:
        grid_dir = find_grid_dir(args.results, None)

    app = ENFViewer(
        results_path=args.results,
        trace_path=args.trace,
        grid_dir=grid_dir,
        region=args.region,
    )
    app.mainloop()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
