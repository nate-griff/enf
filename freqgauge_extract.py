"""
Extract per-column frequency traces from FNET freqgauge PNGs (OpenCV + NumPy + Pandas).

Time axis (v1): linear map across the inner plot width with the right edge at the
UTC capture time parsed from the filename and the left edge at (capture - window_seconds).

See README.md for CLI usage and tuning notes.
"""

from __future__ import annotations

import argparse
import re
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

import cv2
import numpy as np
import pandas as pd

# --- Calibration from Planning.md (GIMP) ------------------------------------

PLOT_REGIONS: dict[str, dict[str, int]] = {
    "EI": {"x1": 100, "x2": 1180, "y1": 43, "y2": 220},
    "WECC": {"x1": 100, "x2": 1180, "y1": 342, "y2": 520},
    "ERCOT": {"x1": 100, "x2": 1180, "y1": 642, "y2": 820},
    "Quebec": {"x1": 100, "x2": 1180, "y1": 942, "y2": 1120},
}

# Reference RGB from Planning.md (0–100 scale); converted to HSV in _region_hsv_masks().
LINE_RGB_100: dict[str, tuple[float, float, float]] = {
    "EI": (5.1, 55.7, 87.1),
    "WECC": (2.0, 58.5, 16.9),
    "ERCOT": (88.6, 26.3, 11.8),
    "Quebec": (82.0, 1.6, 79.2),
}

# Margins inside each PLOT_REGIONS crop: exclude title, y-axis, x-axis strip.
# Tune with --debug-dir if traces sit high/low or pick up grid/labels.
# Exclude y-axis strip and title; use a small bottom margin only so the 59.95–60.05
# trace (often low in the ROI) is not cropped out. Tune with --debug-dir if needed.
INNER_INSET = {"left": 78, "top": 36, "right": 0, "bottom": 8}

FREQ_MIN_HZ = 59.95
FREQ_MAX_HZ = 60.05

# Observed size for images from fnetpublic.utk.edu (fail fast if layout changes).
EXPECTED_IMAGE_SHAPE = (1200, 1600, 3)

DEFAULT_WINDOW_SECONDS = 55.0

FILENAME_RE = re.compile(
    r"^freqgauge_(?P<y>\d{4})-(?P<mo>\d{2})-(?P<d>\d{2})"
    r"T(?P<h>\d{2})-(?P<mi>\d{2})-(?P<s>\d{2})\.(?P<us>\d+)Z\.(?P<ext>png|jpe?g)$",
    re.IGNORECASE,
)


def _rgb100_to_bgr_u8(r: float, g: float, b: float) -> tuple[int, int, int]:
    return (
        int(round(b * 2.55)),
        int(round(g * 2.55)),
        int(round(r * 2.55)),
    )


def _hsv_bounds_from_bgr(
    bgr: tuple[int, int, int],
    dh: int = 12,
    ds_low: int = 80,
    dv_low: int = 80,
) -> tuple[np.ndarray, np.ndarray]:
    """Single HSV interval in OpenCV 8-bit HSV (H 0–179, S/V 0–255)."""
    px = np.uint8([[bgr]])
    hsv = cv2.cvtColor(px, cv2.COLOR_BGR2HSV)[0, 0]
    h, s, v = int(hsv[0]), int(hsv[1]), int(hsv[2])
    h_lo = max(0, h - dh)
    h_hi = min(179, h + dh)
    lower = np.array([h_lo, ds_low, dv_low], dtype=np.uint8)
    upper = np.array([h_hi, 255, 255], dtype=np.uint8)
    return lower, upper


def _region_hsv_masks() -> dict[str, list[tuple[np.ndarray, np.ndarray]]]:
    """Per-region HSV intervals (ERCOT uses two bands for hue wrap near 0)."""
    out: dict[str, list[tuple[np.ndarray, np.ndarray]]] = {}
    for name, rgb in LINE_RGB_100.items():
        r, g, b = rgb
        bgr = _rgb100_to_bgr_u8(r, g, b)
        lo, hi = _hsv_bounds_from_bgr(bgr)
        h_mid = (int(lo[0]) + int(hi[0])) // 2
        if name == "ERCOT" and h_mid <= 20:
            lo2 = np.array([0, lo[1], lo[2]], dtype=np.uint8)
            hi2 = np.array([min(179, h_mid + 12), hi[1], hi[2]], dtype=np.uint8)
            lo3 = np.array([max(0, 179 - (12 - h_mid)), lo[1], lo[2]], dtype=np.uint8)
            hi3 = np.array([179, hi[1], hi[2]], dtype=np.uint8)
            out[name] = [(lo2, hi2), (lo3, hi3)]
        else:
            out[name] = [(lo, hi)]
    return out


REGION_HSV = _region_hsv_masks()


def parse_capture_utc(path: Path) -> datetime:
    m = FILENAME_RE.match(path.name)
    if not m:
        raise ValueError(f"Filename does not match freqgauge timestamp pattern: {path.name}")
    frac = m.group("us")
    microsecond = int((frac + "000000")[:6])
    return datetime(
        int(m.group("y")),
        int(m.group("mo")),
        int(m.group("d")),
        int(m.group("h")),
        int(m.group("mi")),
        int(m.group("s")),
        microsecond,
        tzinfo=timezone.utc,
    )


def _apply_morphology(mask: np.ndarray, open_ksize: int = 2) -> np.ndarray:
    if open_ksize >= 2:
        k = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (open_ksize, open_ksize))
        mask = cv2.morphologyEx(mask, cv2.MORPH_OPEN, k)
    return mask


def color_mask_hsv(hsv: np.ndarray, bounds_list: list[tuple[np.ndarray, np.ndarray]]) -> np.ndarray:
    combined = np.zeros(hsv.shape[:2], dtype=np.uint8)
    for lo, hi in bounds_list:
        combined = cv2.bitwise_or(combined, cv2.inRange(hsv, lo, hi))
    return combined


def inner_slice(roi_shape: tuple[int, int, int], inset: dict[str, int]) -> tuple[slice, slice]:
    h, w = roi_shape[0], roi_shape[1]
    t, b, l, r = inset["top"], inset["bottom"], inset["left"], inset["right"]
    y0, y1 = t, max(t + 1, h - b)
    x0, x1 = l, max(l + 1, w - r)
    return slice(y0, y1), slice(x0, x1)


def column_median_y(mask: np.ndarray) -> np.ndarray:
    """Per-column median row index of mask pixels; NaN if none."""
    h, w = mask.shape
    out = np.full(w, np.nan, dtype=np.float64)
    for x in range(w):
        ys = np.flatnonzero(mask[:, x])
        if ys.size:
            out[x] = float(np.median(ys))
    return out


def y_to_frequency(y: np.ndarray, plot_height: int) -> np.ndarray:
    """Map inner y (0 = top = FREQ_MAX) to Hz."""
    if plot_height <= 1:
        return np.full_like(y, np.nan, dtype=np.float64)
    span = FREQ_MAX_HZ - FREQ_MIN_HZ
    # Top of inner plot = 60.05, bottom = 59.95
    return FREQ_MAX_HZ - (y / (plot_height - 1)) * span


def x_to_timestamps(
    n_cols: int,
    capture_end: datetime,
    window_seconds: float,
) -> pd.DatetimeIndex:
    """Right column = capture_end; left = capture_end - window_seconds (UTC)."""
    if n_cols <= 0:
        return pd.DatetimeIndex([], tz="UTC")
    end = pd.Timestamp(capture_end)
    start = end - pd.Timedelta(seconds=window_seconds)
    if n_cols == 1:
        return pd.DatetimeIndex([end], tz="UTC")
    seconds = np.linspace(0.0, (end - start).total_seconds(), n_cols)
    return pd.DatetimeIndex(start + pd.to_timedelta(seconds, unit="s"), tz="UTC")


@dataclass
class ExtractConfig:
    window_seconds: float = DEFAULT_WINDOW_SECONDS
    skip_shape_check: bool = False
    morphology_ksize: int = 2


def extract_image(
    path: Path,
    cfg: ExtractConfig | None = None,
) -> pd.DataFrame:
    cfg = cfg or ExtractConfig()
    img = cv2.imread(str(path), cv2.IMREAD_COLOR)
    if img is None:
        raise FileNotFoundError(f"Could not read image: {path}")

    if not cfg.skip_shape_check and img.shape != EXPECTED_IMAGE_SHAPE:
        raise ValueError(
            f"Unexpected image shape {img.shape}; expected {EXPECTED_IMAGE_SHAPE}. "
            "Update EXPECTED_IMAGE_SHAPE or pass --skip-shape-check."
        )

    capture = parse_capture_utc(path)
    rows: list[dict] = []

    for region, box in PLOT_REGIONS.items():
        roi = img[box["y1"] : box["y2"], box["x1"] : box["x2"]]
        ys_slice, xs_slice = inner_slice(roi.shape, INNER_INSET)
        inner = roi[ys_slice, xs_slice]
        hsv = cv2.cvtColor(inner, cv2.COLOR_BGR2HSV)
        mask = color_mask_hsv(hsv, REGION_HSV[region])
        mask = _apply_morphology(mask, cfg.morphology_ksize)

        y_med = column_median_y(mask)
        plot_h = inner.shape[0]
        freqs = y_to_frequency(y_med, plot_h)
        times = x_to_timestamps(inner.shape[1], capture, cfg.window_seconds)

        for i in range(inner.shape[1]):
            rows.append(
                {
                    "timestamp_utc": times[i],
                    "region": region,
                    "frequency_hz": freqs[i],
                    "pixel_x": int(box["x1"] + xs_slice.start + i),
                    "source_path": str(path.resolve()),
                }
            )

    return pd.DataFrame(rows)


def write_debug_overlays(
    path: Path,
    out_dir: Path,
    cfg: ExtractConfig,
) -> None:
    out_dir.mkdir(parents=True, exist_ok=True)
    img = cv2.imread(str(path), cv2.IMREAD_COLOR)
    if img is None:
        raise FileNotFoundError(path)
    stem = path.stem

    for region, box in PLOT_REGIONS.items():
        roi = img[box["y1"] : box["y2"], box["x1"] : box["x2"]].copy()
        ys_slice, xs_slice = inner_slice(roi.shape, INNER_INSET)
        inner = roi[ys_slice, xs_slice]
        hsv = cv2.cvtColor(inner, cv2.COLOR_BGR2HSV)
        mask = color_mask_hsv(hsv, REGION_HSV[region])
        mask = _apply_morphology(mask, cfg.morphology_ksize)
        y_med = column_median_y(mask)
        plot_h = inner.shape[0]

        vis = inner.copy()
        for i in range(inner.shape[1]):
            y = y_med[i]
            if np.isfinite(y):
                cv2.circle(vis, (i, int(round(y))), 1, (0, 255, 0), -1)

        mask_bgr = cv2.cvtColor(mask, cv2.COLOR_GRAY2BGR)
        hstack = np.hstack([vis, mask_bgr])
        max_w = 1400
        if hstack.shape[1] > max_w:
            scale = max_w / hstack.shape[1]
            hstack = cv2.resize(
                hstack,
                (int(hstack.shape[1] * scale), int(hstack.shape[0] * scale)),
                interpolation=cv2.INTER_AREA,
            )

        out_path = out_dir / f"{stem}__{region}_debug.jpg"
        cv2.imwrite(str(out_path), hstack)


def is_source_gauge_image(path: Path) -> bool:
    """True only for collector-style names (…T…Z.ext), not debug outputs like …Z__EI_debug.jpg."""
    return bool(FILENAME_RE.match(path.name))


def discover_images(root: Path) -> list[Path]:
    if root.is_file():
        return [root] if is_source_gauge_image(root) else []
    paths: list[Path] = []
    for pattern in ("freqgauge_*.png", "freqgauge_*.jpg", "freqgauge_*.jpeg"):
        paths.extend(root.rglob(pattern))
    unique = {p.resolve() for p in paths if is_source_gauge_image(p)}
    return sorted(unique, key=lambda p: p.as_posix().lower())


BASE_CSV_COLUMNS = ("timestamp_utc", "region", "frequency_hz")
EXTRA_CSV_COLUMNS = ("pixel_x", "source_path")


def dataframe_for_csv_export(df: pd.DataFrame, verbose: bool) -> pd.DataFrame:
    """Default CSV: time, region, Hz. With --verbose-csv, also pixel_x and source_path."""
    cols = list(BASE_CSV_COLUMNS)
    if verbose:
        cols.extend(c for c in EXTRA_CSV_COLUMNS if c in df.columns)
    present = [c for c in cols if c in df.columns]
    return df[present] if present else df


def stitch_dataframes(dfs: list[pd.DataFrame], dedupe_ms: int) -> pd.DataFrame:
    if not dfs:
        return pd.DataFrame(
            columns=["timestamp_utc", "region", "frequency_hz", "pixel_x", "source_path"]
        )
    out = pd.concat(dfs, ignore_index=True)
    out = out.sort_values(["timestamp_utc", "region", "source_path"]).reset_index(drop=True)
    # Bin-averaging is for overlapping *sequences* of images; a single PNG already has one row per column.
    if dedupe_ms > 0 and len(dfs) > 1:
        bin_ns = dedupe_ms * 1_000_000
        out["_bin"] = (out["timestamp_utc"].astype("int64") // bin_ns) * bin_ns
        out = (
            out.sort_values(["_bin", "region", "source_path"])
            .groupby(["_bin", "region"], as_index=False)
            .agg(
                {
                    "timestamp_utc": "first",
                    "frequency_hz": "mean",
                    "pixel_x": "first",
                    "source_path": "first",
                }
            )
            .drop(columns=["_bin"])
        )
    return out


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Extract FNET freqgauge traces from PNGs to CSV.")
    p.add_argument(
        "--input",
        type=Path,
        required=True,
        help="Single image or directory (recursive; only freqgauge_YYYY-MM-DDTHH-MM-SS.µsZ.ext).",
    )
    p.add_argument("--output", type=Path, help="Output CSV path.")
    p.add_argument(
        "--window-seconds",
        type=float,
        default=DEFAULT_WINDOW_SECONDS,
        help=f"Visible time span on x-axis (default {DEFAULT_WINDOW_SECONDS}).",
    )
    p.add_argument(
        "--dedupe-ms",
        type=int,
        default=1000,
        help="When merging 2+ images, bin timestamps to this many ms and average Hz (ignored for a single file). 0=off.",
    )
    p.add_argument(
        "--debug-dir",
        type=Path,
        default=None,
        help="If set, write mask+overlay JPGs per region for each input image.",
    )
    p.add_argument(
        "--skip-shape-check",
        action="store_true",
        help=f"Allow image sizes other than {EXPECTED_IMAGE_SHAPE}.",
    )
    p.add_argument(
        "--morphology",
        type=int,
        default=2,
        help="OpenCV morphology open kernel size (0=disable, default 2).",
    )
    p.add_argument(
        "--verbose-csv",
        action="store_true",
        help="Include pixel_x and source_path columns in CSV / printed preview.",
    )
    return p.parse_args()


def main() -> int:
    args = parse_args()
    cfg = ExtractConfig(
        window_seconds=args.window_seconds,
        skip_shape_check=args.skip_shape_check,
        morphology_ksize=max(0, args.morphology),
    )
    paths = discover_images(args.input)
    if not paths:
        if args.input.is_file():
            print(
                f"Not a collector gauge image (expected name like "
                f"freqgauge_2026-03-20T22-39-56.541331Z.png): {args.input}"
            )
        else:
            print("No matching freqgauge_…Z.png/jpg files found under this path.")
        return 1

    print(
        f"Found {len(paths)} image(s); input {args.input.resolve()}",
        flush=True,
    )
    run_t0 = time.perf_counter()
    dfs: list[pd.DataFrame] = []
    skipped = 0
    for i, img_path in enumerate(paths, start=1):
        print(f"[{i}/{len(paths)}] {img_path.name}", flush=True)
        t0 = time.perf_counter()
        try:
            df = extract_image(img_path, cfg)
            dfs.append(df)
        except ValueError as e:
            skipped += 1
            print(f"  skipped: {e}", flush=True)
            continue
        dt = time.perf_counter() - t0
        print(f"  rows={len(df)} in {dt:.2f}s", flush=True)
        if args.debug_dir is not None:
            t1 = time.perf_counter()
            write_debug_overlays(img_path, args.debug_dir, cfg)
            print(f"  debug overlays in {time.perf_counter() - t1:.2f}s", flush=True)

    print(
        f"Processed {len(paths)} file(s) in {time.perf_counter() - run_t0:.1f}s "
        f"({len(dfs)} ok, {skipped} skipped); merging…",
        flush=True,
    )
    merged = stitch_dataframes(dfs, args.dedupe_ms)
    out_df = dataframe_for_csv_export(merged, args.verbose_csv)
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        out_df.to_csv(args.output, index=False)
        print(f"Wrote {len(out_df)} rows to {args.output}")
    else:
        print(f"Extracted {len(out_df)} rows (use --output PATH.csv to save).")
        if not out_df.empty:
            print(out_df.head(5).to_string(index=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
