"""
GUI viewer for frequency CSVs produced by freqgauge_extract.py.

tkinter + matplotlib: one region at a time, scroll (time window position) and zoom
(window duration) without fighting the plot's internal navigation.
"""

from __future__ import annotations

import argparse
import math
from pathlib import Path
from tkinter import filedialog, messagebox, ttk
import tkinter as tk

import matplotlib.dates as mdates
import pandas as pd
from matplotlib.backends.backend_tkagg import FigureCanvasTkAgg
from matplotlib.figure import Figure

REQUIRED_COLUMNS = frozenset({"timestamp_utc", "region", "frequency_hz"})
REGION_ORDER = ("EI", "WECC", "ERCOT", "Quebec")

MIN_WINDOW = pd.Timedelta(seconds=1)
PRESET_DELTAS: dict[str, pd.Timedelta | None] = {
    "15 seconds": pd.Timedelta(seconds=15),
    "30 seconds": pd.Timedelta(seconds=30),
    "1 minute": pd.Timedelta(minutes=1),
    "5 minutes": pd.Timedelta(minutes=5),
    "Full range": None,
}
# Shown after fixed presets; selecting it does not overwrite the current window (slider-driven).
PRESET_CUSTOM = "Custom"
PRESET_COMBO_VALUES = list(PRESET_DELTAS.keys()) + [PRESET_CUSTOM]


class FreqGaugeCsvViewer(tk.Tk):
    def __init__(self, initial_csv: Path | None = None) -> None:
        super().__init__()
        self.title("FNET frequency CSV viewer")
        self.geometry("1000x640")
        self.minsize(700, 480)

        self._df: pd.DataFrame | None = None
        self._path: Path | None = None
        self._region: str | None = None
        self._t_min: pd.Timestamp | None = None
        self._t_max: pd.Timestamp | None = None
        self._window: pd.Timedelta = pd.Timedelta(minutes=1)
        self._scroll_frac: float = 0.0
        self._preset_var = tk.StringVar(value="1 minute")
        self._updating_scroll = False
        self._updating_zoom_slider = False

        self._build_ui()

        if initial_csv is not None and initial_csv.is_file():
            self.load_csv(initial_csv)

    def _build_ui(self) -> None:
        top = ttk.Frame(self, padding=4)
        top.pack(fill=tk.X)

        ttk.Button(top, text="Open CSV…", command=self._on_open).pack(side=tk.LEFT, padx=(0, 8))
        ttk.Label(top, text="Region:").pack(side=tk.LEFT)
        self._region_combo = ttk.Combobox(
            top,
            width=10,
            state="readonly",
            values=[],
        )
        self._region_combo.pack(side=tk.LEFT, padx=4)
        self._region_combo.bind("<<ComboboxSelected>>", lambda _e: self._on_region_change())

        ttk.Button(top, text="Reset view", command=self._on_reset).pack(side=tk.LEFT, padx=12)

        self._fig = Figure(figsize=(9, 4.5), dpi=100)
        self._ax = self._fig.add_subplot(111)
        self._canvas = FigureCanvasTkAgg(self._fig, master=self)
        self._canvas.get_tk_widget().pack(fill=tk.BOTH, expand=True, padx=4, pady=4)

        zoom_row = ttk.Frame(self, padding=(4, 0, 4, 2))
        zoom_row.pack(fill=tk.X)
        ttk.Label(zoom_row, text="Time zoom:").pack(side=tk.LEFT)
        preset = ttk.Combobox(
            zoom_row,
            width=14,
            state="readonly",
            values=PRESET_COMBO_VALUES,
            textvariable=self._preset_var,
        )
        preset.pack(side=tk.LEFT, padx=4)
        preset.bind("<<ComboboxSelected>>", lambda _e: self._on_preset_change())

        ttk.Button(zoom_row, text="−", width=3, command=self._zoom_out).pack(side=tk.LEFT, padx=2)
        ttk.Button(zoom_row, text="+", width=3, command=self._zoom_in).pack(side=tk.LEFT, padx=2)

        self._window_label = ttk.Label(zoom_row, text="", width=22)
        self._window_label.pack(side=tk.LEFT, padx=8)

        zoom_slider_row = ttk.Frame(self, padding=(4, 0, 4, 4))
        zoom_slider_row.pack(fill=tk.X)
        ttk.Label(zoom_slider_row, text="Window width:").pack(side=tk.LEFT)
        self._zoom_slider = ttk.Scale(
            zoom_slider_row,
            from_=0.0,
            to=1.0,
            orient=tk.HORIZONTAL,
            command=self._on_zoom_slider,
        )
        self._zoom_slider.pack(side=tk.LEFT, fill=tk.X, expand=True, padx=8)
        ttk.Label(zoom_slider_row, text="(log scale: narrow ← → wide)").pack(side=tk.LEFT)

        visible_row = ttk.Frame(self, padding=(4, 0, 4, 0))
        visible_row.pack(fill=tk.X)
        self._visible_label = ttk.Label(visible_row, text="")
        self._visible_label.pack(side=tk.LEFT, padx=4)

        scroll_row = ttk.Frame(self, padding=(8, 0, 8, 8))
        scroll_row.pack(fill=tk.X)
        ttk.Label(scroll_row, text="Scroll time:").pack(side=tk.LEFT)
        self._scroll = ttk.Scale(
            scroll_row,
            from_=0.0,
            to=1.0,
            orient=tk.HORIZONTAL,
            command=self._on_scroll,
        )
        self._scroll.pack(side=tk.LEFT, fill=tk.X, expand=True, padx=8)

        self._status = ttk.Label(self, text="Open a CSV file.", relief=tk.SUNKEN, anchor=tk.W)
        self._status.pack(fill=tk.X, side=tk.BOTTOM)

    def _on_open(self) -> None:
        path = filedialog.askopenfilename(
            title="Open frequency CSV",
            filetypes=[("CSV", "*.csv"), ("All", "*.*")],
        )
        if path:
            self.load_csv(Path(path))

    def load_csv(self, path: Path) -> None:
        try:
            df = pd.read_csv(path, parse_dates=["timestamp_utc"])
        except Exception as exc:
            messagebox.showerror("Load error", str(exc))
            return

        missing = REQUIRED_COLUMNS - set(df.columns)
        if missing:
            messagebox.showerror("CSV error", f"Missing columns: {', '.join(sorted(missing))}")
            return

        self._df = df
        self._path = path.resolve()

        present = [r for r in REGION_ORDER if r in set(df["region"].unique())]
        if not present:
            messagebox.showerror("CSV error", "No known regions (EI, WECC, ERCOT, Quebec) in file.")
            self._df = None
            return

        self._region_combo["values"] = present
        self._region_combo.set(present[0])
        self._region = present[0]

        self._apply_preset_from_ui()
        self._sync_preset_var_from_window()
        self._sync_zoom_slider_from_window()
        self._scroll_frac = 0.0
        self._sync_scroll_widget()
        self._redraw()
        n = len(df)
        self._status.config(text=f"{path.name}  |  {n} rows  |  {self._path}")

    def _series_for_region(self) -> pd.DataFrame:
        assert self._df is not None and self._region is not None
        s = self._df[self._df["region"] == self._region][["timestamp_utc", "frequency_hz"]].copy()
        s = s.dropna(subset=["frequency_hz"])
        s = s.sort_values("timestamp_utc")
        return s

    def _update_bounds(self) -> bool:
        s = self._series_for_region()
        if s.empty:
            self._t_min = self._t_max = None
            return False
        self._t_min = pd.Timestamp(s["timestamp_utc"].iloc[0])
        self._t_max = pd.Timestamp(s["timestamp_utc"].iloc[-1])
        return True

    def _span(self) -> pd.Timedelta:
        assert self._t_min is not None and self._t_max is not None
        d = self._t_max - self._t_min
        if d <= pd.Timedelta(0):
            return pd.Timedelta(seconds=1)
        return d

    def _effective_window(self) -> pd.Timedelta:
        span = self._span()
        if self._window >= span:
            return span
        return self._window

    @staticmethod
    def _format_duration(td: pd.Timedelta) -> str:
        s = abs(td.total_seconds())
        if s >= 3600:
            h, r = divmod(int(s), 3600)
            m, _ = divmod(r, 60)
            return f"{h}h {m}m"
        if s >= 60:
            m, r = divmod(s, 60.0)
            return f"{int(m)}m {r:.1f}s"
        return f"{s:.2f}s"

    def _zoom_seconds_bounds(self) -> tuple[float, float]:
        if self._t_min is None or self._t_max is None:
            return 1.0, 1.0
        span_sec = max(self._span().total_seconds(), MIN_WINDOW.total_seconds())
        lo = max(MIN_WINDOW.total_seconds(), 1.0)
        hi = max(span_sec, lo)
        return lo, hi

    def _zoom_frac_to_window(self, frac: float) -> pd.Timedelta:
        lo, hi = self._zoom_seconds_bounds()
        if hi <= lo:
            return pd.Timedelta(seconds=hi)
        log_lo = math.log10(lo)
        log_hi = math.log10(hi)
        f = max(0.0, min(1.0, float(frac)))
        log_w = log_lo + f * (log_hi - log_lo)
        sec = 10.0**log_w
        sec = min(max(sec, lo), hi)
        return pd.Timedelta(seconds=sec)

    def _window_to_zoom_frac(self) -> float:
        lo, hi = self._zoom_seconds_bounds()
        w = min(max(self._window.total_seconds(), lo), hi)
        if hi <= lo:
            return 0.0
        log_lo = math.log10(lo)
        log_hi = math.log10(hi)
        return (math.log10(w) - log_lo) / (log_hi - log_lo)

    def _sync_zoom_slider_from_window(self) -> None:
        if self._t_min is None or self._t_max is None:
            return
        self._updating_zoom_slider = True
        try:
            self._zoom_slider.set(self._window_to_zoom_frac())
        finally:
            self._updating_zoom_slider = False

    def _sync_preset_var_from_window(self) -> None:
        if self._t_min is None or self._t_max is None:
            return
        span = self._span()
        if self._window >= span:
            self._preset_var.set("Full range")
            return
        ws = self._window.total_seconds()
        for name in ("15 seconds", "30 seconds", "1 minute", "5 minutes"):
            d = PRESET_DELTAS[name]
            assert d is not None
            ps = d.total_seconds()
            if abs(ws - ps) / max(ps, 1.0) <= 0.08:
                self._preset_var.set(name)
                return
        self._preset_var.set(PRESET_CUSTOM)

    def _on_zoom_slider(self, value: str) -> None:
        if self._updating_zoom_slider or self._t_min is None:
            return
        self._window = self._zoom_frac_to_window(float(value))
        self._clamp_window_to_span()
        self._sync_preset_var_from_window()
        self._clamp_scroll_for_window()
        self._sync_scroll_widget()
        self._redraw()

    def _view_start_end(self) -> tuple[pd.Timestamp, pd.Timestamp]:
        assert self._t_min is not None and self._t_max is not None
        span = self._span()
        w = self._effective_window()
        if w >= span:
            return self._t_min, self._t_max
        latest_start = self._t_max - w
        start = self._t_min + pd.Timedelta(
            seconds=self._scroll_frac * (latest_start - self._t_min).total_seconds()
        )
        return start, start + w

    def _clamp_scroll_for_window(self) -> None:
        if self._t_min is None or self._t_max is None:
            return
        span = self._span()
        w = self._effective_window()
        if w >= span:
            self._scroll_frac = 0.0
        else:
            self._scroll_frac = max(0.0, min(1.0, float(self._scroll_frac)))

    def _sync_scroll_widget(self) -> None:
        self._updating_scroll = True
        try:
            self._scroll.set(self._scroll_frac)
        finally:
            self._updating_scroll = False

    def _on_scroll(self, value: str) -> None:
        if self._updating_scroll:
            return
        self._scroll_frac = float(value)
        self._redraw()

    def _on_region_change(self) -> None:
        self._region = self._region_combo.get()
        self._scroll_frac = 0.0
        if not self._update_bounds():
            self._ax.clear()
            self._ax.text(0.5, 0.5, "No data for this region", ha="center", va="center", transform=self._ax.transAxes)
            self._canvas.draw_idle()
            self._visible_label.config(text="")
            self._window_label.config(text="")
            return
        self._clamp_window_to_span()
        self._sync_zoom_slider_from_window()
        self._clamp_scroll_for_window()
        self._sync_scroll_widget()
        self._redraw()

    def _clamp_window_to_span(self) -> None:
        if self._t_min is None:
            return
        span = self._span()
        if self._window > span:
            self._window = span
        if self._window < MIN_WINDOW:
            self._window = MIN_WINDOW

    def _apply_preset_from_ui(self) -> None:
        key = self._preset_var.get()
        if key == PRESET_CUSTOM:
            return
        delta = PRESET_DELTAS.get(key)
        if self._t_min is None or self._t_max is None:
            if not self._update_bounds():
                self._window = pd.Timedelta(minutes=1)
                return
        span = self._span()
        if delta is None:
            self._window = span
        else:
            self._window = min(delta, span) if span > pd.Timedelta(0) else delta
        self._clamp_window_to_span()

    def _on_preset_change(self) -> None:
        if self._preset_var.get() == PRESET_CUSTOM:
            return
        self._apply_preset_from_ui()
        self._sync_zoom_slider_from_window()
        self._clamp_scroll_for_window()
        self._sync_scroll_widget()
        self._redraw()

    def _zoom_in(self) -> None:
        if self._t_min is None:
            return
        span = self._span()
        if span <= MIN_WINDOW:
            return
        half = self._window / 2
        self._window = max(MIN_WINDOW, half)
        if self._window > span:
            self._window = span
        self._sync_preset_var_from_window()
        self._sync_zoom_slider_from_window()
        self._clamp_scroll_for_window()
        self._sync_scroll_widget()
        self._redraw()

    def _zoom_out(self) -> None:
        if self._t_min is None:
            return
        span = self._span()
        double = self._window * 2
        self._window = min(double, span)
        self._sync_preset_var_from_window()
        self._sync_zoom_slider_from_window()
        self._clamp_scroll_for_window()
        self._sync_scroll_widget()
        self._redraw()

    def _on_reset(self) -> None:
        if self._df is None:
            return
        self._preset_var.set("Full range")
        self._apply_preset_from_ui()
        self._sync_zoom_slider_from_window()
        self._scroll_frac = 0.0
        self._sync_scroll_widget()
        self._redraw()

    def _redraw(self) -> None:
        self._ax.clear()
        if self._df is None or self._region is None:
            self._ax.text(0.5, 0.5, "Open a CSV file", ha="center", va="center", transform=self._ax.transAxes)
            self._canvas.draw_idle()
            self._visible_label.config(text="")
            self._window_label.config(text="")
            return

        if not self._update_bounds():
            self._ax.text(
                0.5,
                0.5,
                "No numeric frequency data for this region",
                ha="center",
                va="center",
                transform=self._ax.transAxes,
            )
            self._canvas.draw_idle()
            self._visible_label.config(text="")
            self._window_label.config(text="")
            return

        self._clamp_window_to_span()
        t0, t1 = self._view_start_end()
        s = self._series_for_region()
        mask = (s["timestamp_utc"] >= t0) & (s["timestamp_utc"] <= t1)
        sub = s.loc[mask]

        if sub.empty:
            self._ax.text(0.5, 0.5, "No points in this time window", ha="center", va="center", transform=self._ax.transAxes)
        else:
            self._ax.plot(
                sub["timestamp_utc"],
                sub["frequency_hz"],
                color="#1f77b4",
                linewidth=0.8,
            )
            y = sub["frequency_hz"]
            pad = max(0.002, (y.max() - y.min()) * 0.05 + 1e-6)
            ymin = max(59.94, float(y.min() - pad))
            ymax = min(60.06, float(y.max() + pad))
            if ymax <= ymin:
                ymin, ymax = 59.95, 60.05
            self._ax.set_ylim(ymin, ymax)

        self._ax.set_xlim(t0, t1)
        self._ax.set_xlabel("UTC")
        self._ax.set_ylabel("Frequency (Hz)")
        self._ax.set_title(f"{self._region}")
        self._ax.grid(True, alpha=0.35)
        self._ax.xaxis.set_major_formatter(mdates.DateFormatter("%H:%M:%S"))
        self._fig.autofmt_xdate()
        self._fig.tight_layout()
        self._canvas.draw_idle()

        self._visible_label.config(
            text=f"Visible: {t0.isoformat()}  →  {t1.isoformat()}  (Δ {(t1 - t0).total_seconds():.2f}s)"
        )
        self._window_label.config(text=f"Width: {self._format_duration(self._effective_window())}")


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="View freqgauge_extract CSV (one region, scroll/zoom time).")
    p.add_argument(
        "csv",
        nargs="?",
        type=Path,
        default=None,
        help="Optional CSV path to open on startup.",
    )
    return p.parse_args()


def main() -> int:
    args = parse_args()
    app = FreqGaugeCsvViewer(initial_csv=args.csv)
    app.mainloop()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
