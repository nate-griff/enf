"""Extract Electric Network Frequency (ENF) from audio or video files.

Uses Quadratically Interpolated FFT (QIFFT) to achieve sub-bin frequency
resolution on short overlapping frames, then aggregates estimates to a
~1 Hz cadence and optionally applies median filtering.
"""
from __future__ import annotations

import argparse
import os
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Mapping

import numpy as np
import scipy.io.wavfile as wavfile
import scipy.signal


Trace = tuple[np.ndarray, np.ndarray]
NEUTRAL_SNR_WEIGHT = 1.0
MAX_LOCAL_SNR_WEIGHT = 1_000.0
DEFAULT_DP_JUMP_PENALTY = 0.5


class MarkExplicitAction(argparse.Action):
    """Argparse action that records when an option was explicitly provided."""

    def __init__(self, option_strings, dest, **kwargs):
        self.explicit_dest = kwargs.pop("explicit_dest")
        super().__init__(option_strings, dest, **kwargs)

    def __call__(self, parser, namespace, values, option_string=None):
        setattr(namespace, self.dest, values)
        setattr(namespace, self.explicit_dest, True)


def parse_harmonics_spec(value: str) -> list[int]:
    """Parse a comma-separated harmonic list for experimental multi-harmonic mode."""
    allowed_harmonics = {1, 2, 3}

    if not value.strip():
        raise argparse.ArgumentTypeError("harmonics list must not be empty")

    try:
        harmonics = [int(part.strip()) for part in value.split(",")]
    except ValueError as exc:
        raise argparse.ArgumentTypeError("harmonics must be a comma-separated list of integers") from exc

    if any(harmonic not in allowed_harmonics for harmonic in harmonics):
        raise argparse.ArgumentTypeError("harmonics must only include 1, 2, or 3")
    if len(set(harmonics)) != len(harmonics):
        raise argparse.ArgumentTypeError("harmonics must not contain duplicates")

    return harmonics


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Extract ENF from audio/video using QIFFT."
    )
    parser.set_defaults(harmonics_explicit=False, harmonic_fusion_explicit=False)
    parser.add_argument("--input", required=True, help="Path to audio or video file.")
    parser.add_argument("--output", default=None, help="Output CSV path (default: {input_stem}_enf.csv).")
    parser.add_argument("--nominal", type=float, default=60.0, help="Nominal grid frequency in Hz.")
    parser.add_argument("--bandwidth", type=float, default=0.5, help="Half-bandwidth in Hz around nominal for bandpass filter and peak search (default: 0.5).")
    parser.add_argument("--harmonic", type=int, default=2, help="Which harmonic to extract (1=fundamental 60Hz, 2=second harmonic 120Hz, recommended). Result is divided back to fundamental.")
    parser.add_argument("--frame-sec", type=float, default=1.0, help="Frame duration in seconds.")
    parser.add_argument("--overlap", type=float, default=0.5, help="Frame overlap fraction 0-1.")
    parser.add_argument("--pad-factor", type=int, default=16, help="Zero-padding multiplier for FFT.")
    parser.add_argument("--median-window", type=int, default=3, help="Median filter window size (0=disable).")
    parser.add_argument(
        "--multi-harmonic",
        action="store_true",
        help="Experimental: extract and fuse multiple harmonics.",
    )
    parser.add_argument(
        "--harmonics",
        action=MarkExplicitAction,
        explicit_dest="harmonics_explicit",
        type=parse_harmonics_spec,
        default=parse_harmonics_spec("1,2,3"),
        help="Experimental: comma-separated harmonics to fuse (default: 1,2,3).",
    )
    parser.add_argument(
        "--harmonic-fusion",
        action=MarkExplicitAction,
        explicit_dest="harmonic_fusion_explicit",
        choices=("weighted", "vote", "mean", "snr-weighted"),
        default="weighted",
        help="Experimental: fusion method for multi-harmonic extraction.",
    )
    parser.add_argument(
        "--tracking-mode",
        choices=("peak", "dp"),
        default="peak",
        help="Per-frame ridge tracking mode (default: peak).",
    )
    parser.add_argument(
        "--spectrum-estimator",
        choices=("fft", "multitaper"),
        default="fft",
        help="Spectrum estimator used within each frame (default: fft).",
    )
    parser.add_argument(
        "--confidence-output",
        action="store_true",
        help="Experimental: append confidence_score to the fused CSV output.",
    )
    parser.add_argument(
        "--detail-output",
        action="store_true",
        help="Experimental: write per-harmonic CSVs next to the main output.",
    )
    parser.add_argument(
        "--export-figure",
        action="store_true",
        help="Export a two-panel figure with a spectrogram and the final extracted trace.",
    )
    parser.add_argument(
        "--figure-output",
        default=None,
        help="Figure output path. Implies figure export when provided.",
    )
    args = parser.parse_args(argv)
    if args.detail_output and not args.multi_harmonic:
        parser.error("--detail-output requires --multi-harmonic")
    if args.harmonics_explicit and not args.multi_harmonic:
        parser.error("--harmonics requires --multi-harmonic")
    if args.harmonic_fusion_explicit and not args.multi_harmonic:
        parser.error("--harmonic-fusion requires --multi-harmonic")
    return args


def extract_audio_from_video(input_path: str) -> str:
    """Shell out to ffmpeg to extract mono 48 kHz WAV from a video file."""
    tmp_wav = tempfile.mktemp(suffix=".wav", prefix="enf_tmp_")
    cmd = [
        "ffmpeg", "-y", "-i", input_path,
        "-vn", "-ac", "1", "-ar", "48000", "-f", "wav", tmp_wav,
    ]
    try:
        subprocess.run(cmd, check=True, capture_output=True)
    except FileNotFoundError:
        raise RuntimeError("ffmpeg not found. Install ffmpeg and ensure it is on PATH.")
    except subprocess.CalledProcessError as e:
        raise RuntimeError(f"ffmpeg failed: {e.stderr.decode(errors='replace')}")
    return tmp_wav


def load_audio(path: str) -> tuple[int, np.ndarray]:
    """Load a WAV file, return (sample_rate, mono float64 samples in [-1, 1])."""
    sr, data = wavfile.read(path)
    data = data.astype(np.float64)

    # Convert to mono by averaging channels
    if data.ndim == 2:
        data = data.mean(axis=1)

    # Normalize to [-1, 1] based on dtype range
    max_val = np.iinfo(np.int16).max if data.max() <= 32767 and data.min() >= -32768 else np.abs(data).max()
    if max_val > 0:
        data = data / max_val

    return sr, data


def resolve_figure_output_path(
    input_path: str,
    output_path: str | None,
    export_figure: bool,
    figure_output: str | None,
) -> str | None:
    """Resolve the requested figure output path, if any."""
    if figure_output is not None:
        return figure_output
    if not export_figure:
        return None

    base_path = Path(output_path) if output_path is not None else Path(input_path).with_suffix("")
    return str(base_path.with_suffix(".png"))


def write_summary_figure(
    path: str,
    signal: np.ndarray,
    sr: int,
    trace_timestamps: np.ndarray,
    trace_freqs: np.ndarray,
    nominal: float,
) -> None:
    """Write a two-panel figure with a spectrogram and the extracted trace."""
    from matplotlib.backends.backend_agg import FigureCanvasAgg
    from matplotlib.figure import Figure

    figure_path = Path(path)
    figure_path.parent.mkdir(parents=True, exist_ok=True)

    if len(signal) < 2:
        raise RuntimeError("Not enough audio samples to render summary figure.")

    nfft = min(2048, max(64, len(signal) // 2))
    if nfft >= len(signal):
        nfft = len(signal) - 1
    noverlap = min(nfft // 2, nfft - 1)

    fig = Figure(figsize=(12, 4.5), constrained_layout=True)
    FigureCanvasAgg(fig)
    spec_ax, trace_ax = fig.subplots(1, 2)

    _, freqs, _, image = spec_ax.specgram(
        signal,
        NFFT=nfft,
        Fs=sr,
        noverlap=noverlap,
        cmap="viridis",
        vmin=-80,
        vmax=0,
    )
    spec_ax.set_title("Spectrogram")
    spec_ax.set_xlabel("Time (s)")
    spec_ax.set_ylabel("Frequency (Hz)")
    spec_ax.set_ylim(0.0, min(250.0, float(freqs[-1])))
    fig.colorbar(image, ax=spec_ax, label="Power (dB)")

    trace_ax.plot(trace_timestamps, trace_freqs, linewidth=1.5)
    trace_ax.set_title("Extracted ENF Trace")
    trace_ax.set_xlabel("Offset (s)")
    trace_ax.set_ylabel("Frequency (Hz)")
    trace_ax.axhline(nominal, color="tab:red", linestyle="--", linewidth=1.0)
    trace_ax.grid(True, alpha=0.3)

    if len(trace_timestamps) > 0:
        x0 = float(trace_timestamps[0])
        x1 = float(trace_timestamps[-1])
        if x0 == x1:
            x_margin = max(0.5, 0.1 * max(abs(x0), 1.0))
            trace_ax.set_xlim(x0 - x_margin, x1 + x_margin)
        else:
            trace_ax.set_xlim(x0, x1)

    freq_margin = 0.1
    if len(trace_freqs) > 0:
        low = min(float(np.min(trace_freqs)), nominal) - freq_margin
        high = max(float(np.max(trace_freqs)), nominal) + freq_margin
        if low < high:
            trace_ax.set_ylim(low, high)

    fig.savefig(figure_path, dpi=150)


def bandpass_filter(
    signal: np.ndarray, sr: int, nominal: float, bandwidth: float = 0.5
) -> np.ndarray:
    """Apply 4th-order Butterworth bandpass around the nominal frequency."""
    lo = nominal - bandwidth
    hi = nominal + bandwidth
    sos = scipy.signal.butter(4, [lo, hi], btype="bandpass", fs=sr, output="sos")
    return scipy.signal.sosfiltfilt(sos, signal)


def multitaper_spectrum(
    frame: np.ndarray,
    n_padded: int,
    nw: float = 3.0,
    k: int = 5,
) -> np.ndarray:
    """Estimate a frame spectrum with DPSS multi-taper averaging."""
    tapers = scipy.signal.windows.dpss(len(frame), nw, Kmax=k, sym=False)
    power = np.mean(
        [
            np.abs(np.fft.rfft(frame * taper, n=n_padded)) ** 2
            for taper in tapers
        ],
        axis=0,
    )
    return np.sqrt(power)


def estimate_frame_spectrum(
    frame: np.ndarray,
    n_padded: int,
    window: np.ndarray,
    estimator: str = "fft",
) -> np.ndarray:
    """Estimate a frame magnitude spectrum with the requested method."""
    if estimator == "multitaper":
        return multitaper_spectrum(frame, n_padded=n_padded)
    return np.abs(np.fft.rfft(frame * window, n=n_padded))


def track_spectral_ridge(
    scores: np.ndarray, jump_penalty: float = DEFAULT_DP_JUMP_PENALTY
) -> np.ndarray:
    """Track a smooth spectral ridge with dynamic programming."""
    if scores.ndim != 2:
        raise ValueError("scores must be a 2D array")
    if scores.shape[0] == 0:
        return np.array([], dtype=int)

    n_frames, n_bins = scores.shape
    cost = np.full((n_frames, n_bins), -np.inf, dtype=np.float64)
    backptr = np.zeros((n_frames, n_bins), dtype=int)
    cost[0] = scores[0]
    bin_idx = np.arange(n_bins, dtype=np.float64)

    for frame_idx in range(1, n_frames):
        jump_cost = jump_penalty * (bin_idx[:, None] - bin_idx[None, :]) ** 2
        transitions = cost[frame_idx - 1][None, :] - jump_cost
        best_prev = np.argmax(transitions, axis=1)
        cost[frame_idx] = scores[frame_idx] + transitions[np.arange(n_bins), best_prev]
        backptr[frame_idx] = best_prev

    ridge = np.zeros(n_frames, dtype=int)
    ridge[-1] = int(np.argmax(cost[-1]))
    for frame_idx in range(n_frames - 2, -1, -1):
        ridge[frame_idx] = backptr[frame_idx + 1, ridge[frame_idx + 1]]

    return ridge


def estimate_local_snr_weight(search_region: np.ndarray, peak_index: int) -> float:
    """Estimate a positive local harmonic quality weight from nearby bins."""
    peak_power = float(search_region[peak_index] ** 2)
    mask = np.ones(len(search_region), dtype=bool)
    mask[max(0, peak_index - 1) : min(len(search_region), peak_index + 2)] = False
    noise_samples = search_region[mask]
    if len(noise_samples) == 0:
        return NEUTRAL_SNR_WEIGHT

    noise_power = float(np.median(noise_samples**2))
    weight = peak_power / max(noise_power, 1e-12)
    return float(np.clip(weight, 1e-6, MAX_LOCAL_SNR_WEIGHT))


def qifft_extract(
    signal: np.ndarray,
    sr: int,
    nominal: float,
    frame_sec: float,
    overlap: float,
    pad_factor: int,
    bandwidth: float = 0.5,
    harmonic: int = 1,
    tracking_mode: str = "peak",
    spectrum_estimator: str = "fft",
) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """Run STFT with optional ridge tracking, return timestamps, estimates, and quality."""
    target_freq = nominal * harmonic
    frame_len = int(sr * frame_sec)
    hop = int(frame_len * (1 - overlap))
    n_frames = max(1, (len(signal) - frame_len) // hop + 1)

    window = np.hanning(frame_len)
    n_padded = frame_len * pad_factor
    freq_resolution = sr / n_padded
    max_bin = n_padded // 2

    # Bin range for [target - bandwidth, target + bandwidth] Hz
    bin_lo = max(0, int(np.floor((target_freq - bandwidth) / freq_resolution)))
    bin_hi = min(max_bin, int(np.ceil((target_freq + bandwidth) / freq_resolution)))
    expanded_bin_lo = max(0, bin_lo - 1)
    expanded_bin_hi = min(max_bin, bin_hi + 1)
    search_region_start = bin_lo - expanded_bin_lo
    search_region_stop = search_region_start + (bin_hi - bin_lo + 1)

    timestamps = np.empty(n_frames)
    freq_estimates = np.empty(n_frames)
    quality_scores = np.empty(n_frames)
    expanded_region_len = expanded_bin_hi - expanded_bin_lo + 1

    if tracking_mode == "dp":
        expanded_regions = np.empty((n_frames, expanded_region_len), dtype=np.float64)

        for i in range(n_frames):
            start = i * hop
            frame = signal[start : start + frame_len]

            mag = estimate_frame_spectrum(
                frame,
                n_padded=n_padded,
                window=window,
                estimator=spectrum_estimator,
            )
            expanded_regions[i] = mag[expanded_bin_lo : expanded_bin_hi + 1]
            timestamps[i] = (start + frame_len / 2) / sr

        search_regions = expanded_regions[:, search_region_start:search_region_stop]
        frame_rms = np.sqrt(np.mean(search_regions**2, axis=1, keepdims=True))
        normalized_scores = search_regions / np.maximum(frame_rms, 1e-12)
        peak_bins = track_spectral_ridge(
            normalized_scores, jump_penalty=DEFAULT_DP_JUMP_PENALTY
        )

        for i, k_local in enumerate(peak_bins):
            expanded_region = expanded_regions[i]
            search_region = expanded_region[search_region_start:search_region_stop]
            k_expanded = search_region_start + int(k_local)
            k = bin_lo + int(k_local)

            # QIFFT quadratic interpolation for sub-bin accuracy
            if 1 <= k_expanded < len(expanded_region) - 1:
                alpha = expanded_region[k_expanded - 1]
                beta = expanded_region[k_expanded]
                gamma = expanded_region[k_expanded + 1]
                denom = alpha - 2 * beta + gamma
                if abs(denom) > 1e-12:
                    delta = 0.5 * (alpha - gamma) / denom
                else:
                    delta = 0.0
            else:
                delta = 0.0

            # Divide by harmonic to get fundamental frequency
            freq_estimates[i] = (k + delta) * freq_resolution / harmonic
            quality_scores[i] = estimate_local_snr_weight(search_region, int(k_local))

        return timestamps, freq_estimates, quality_scores

    for i in range(n_frames):
        start = i * hop
        frame = signal[start : start + frame_len]

        mag = estimate_frame_spectrum(
            frame,
            n_padded=n_padded,
            window=window,
            estimator=spectrum_estimator,
        )
        timestamps[i] = (start + frame_len / 2) / sr
        expanded_region = mag[expanded_bin_lo : expanded_bin_hi + 1]
        search_region = expanded_region[search_region_start:search_region_stop]
        k_local = int(np.argmax(search_region))
        k_expanded = search_region_start + k_local
        k = bin_lo + k_local

        # QIFFT quadratic interpolation for sub-bin accuracy
        if 1 <= k_expanded < len(expanded_region) - 1:
            alpha = expanded_region[k_expanded - 1]
            beta = expanded_region[k_expanded]
            gamma = expanded_region[k_expanded + 1]
            denom = alpha - 2 * beta + gamma
            if abs(denom) > 1e-12:
                delta = 0.5 * (alpha - gamma) / denom
            else:
                delta = 0.0
        else:
            delta = 0.0

        # Divide by harmonic to get fundamental frequency
        freq_estimates[i] = (k + delta) * freq_resolution / harmonic
        quality_scores[i] = estimate_local_snr_weight(search_region, int(k_local))

    return timestamps, freq_estimates, quality_scores


def aggregate_series_to_one_hz(
    timestamps: np.ndarray,
    values: np.ndarray,
) -> tuple[np.ndarray, np.ndarray]:
    """Average values that fall within the same 1-second bin."""
    if len(timestamps) == 0:
        return timestamps, values

    max_time = timestamps[-1]
    n_bins = int(np.floor(max_time)) + 1
    agg_times = []
    agg_values = []

    for b in range(n_bins):
        mask = (timestamps >= b) & (timestamps < b + 1)
        if np.any(mask):
            agg_times.append(b + 0.5)
            agg_values.append(np.mean(values[mask]))

    return np.array(agg_times), np.array(agg_values)


def aggregate_to_one_hz(
    timestamps: np.ndarray, freq_estimates: np.ndarray
) -> tuple[np.ndarray, np.ndarray]:
    """Average estimates that fall within the same 1-second bin."""
    return aggregate_series_to_one_hz(timestamps, freq_estimates)


def apply_median_filter(freqs: np.ndarray, window: int) -> np.ndarray:
    """Apply median filter for smoothing. Window must be odd."""
    if window <= 0 or len(freqs) == 0:
        return freqs
    if window % 2 == 0:
        window += 1
    max_window = len(freqs) if len(freqs) % 2 == 1 else len(freqs) - 1
    if max_window <= 1:
        return freqs
    window = min(window, max_window)
    return scipy.signal.medfilt(freqs, kernel_size=window)


def estimate_fusion_confidence(values: np.ndarray) -> float:
    """Estimate 0-1 confidence from harmonic agreement."""
    if len(values) <= 1:
        return 1.0

    spread = float(np.max(values) - np.min(values))
    return max(0.0, min(1.0, 1.0 - (spread / 0.2)))


def snr_weight_to_confidence(quality_scores: np.ndarray) -> np.ndarray:
    """Map positive SNR-like quality weights onto a 0-1 confidence scale."""
    quality_scores = np.asarray(quality_scores, dtype=np.float64)
    quality_scores = np.clip(quality_scores, 0.0, None)
    return quality_scores / (quality_scores + NEUTRAL_SNR_WEIGHT)


def harmonic_weight(harmonic: int) -> float:
    """Return the default fusion weight for a harmonic."""
    return {2: 3.0, 3: 2.0, 1: 1.0}.get(harmonic, 1.0)


def harmonic_band_limits(nominal: float, bandwidth: float, harmonic: int) -> tuple[float, float]:
    """Return the bandpass limits for a harmonic."""
    target_freq = nominal * harmonic
    return target_freq - bandwidth, target_freq + bandwidth


def split_supported_harmonics(
    sr: int,
    nominal: float,
    bandwidth: float,
    harmonics: list[int],
) -> tuple[list[int], list[tuple[int, float, float]]]:
    """Split harmonics into supported and unsupported sets for the current sample rate."""
    nyquist = sr / 2.0
    supported: list[int] = []
    unsupported: list[tuple[int, float, float]] = []

    for harmonic in harmonics:
        lo, hi = harmonic_band_limits(nominal, bandwidth, harmonic)
        if lo <= 0.0 or hi >= nyquist:
            unsupported.append((harmonic, lo, hi))
        else:
            supported.append(harmonic)

    return supported, unsupported


def format_unsupported_harmonics(unsupported: list[tuple[int, float, float]], sr: int) -> str:
    """Format unsupported harmonics for a user-facing message."""
    nyquist = sr / 2.0
    parts = [
        f"{harmonic} ({lo:.1f}-{hi:.1f} Hz > Nyquist {nyquist:.1f} Hz)"
        for harmonic, lo, hi in unsupported
    ]
    return ", ".join(parts)


def vote_fused_frequency(harmonic_values: list[tuple[int, float]]) -> float:
    """Choose a real harmonic estimate using local agreement and harmonic priority."""
    vote_tolerance_hz = 0.05
    best_score: tuple[float, float, float] | None = None
    best_freq = 0.0

    for harmonic, freq in harmonic_values:
        support = sum(
            harmonic_weight(other_harmonic)
            for other_harmonic, other_freq in harmonic_values
            if abs(other_freq - freq) <= vote_tolerance_hz
        )
        score = (support, harmonic_weight(harmonic), -freq)
        if best_score is None or score > best_score:
            best_score = score
            best_freq = freq

    return float(best_freq)


def extract_harmonic_trace(
    signal: np.ndarray,
    sr: int,
    nominal: float,
    bandwidth: float,
    frame_sec: float,
    overlap: float,
    pad_factor: int,
    harmonic: int,
    tracking_mode: str = "peak",
    spectrum_estimator: str = "fft",
    return_quality: bool = False,
    quality_scale: str = "confidence",
) -> Trace | tuple[np.ndarray, np.ndarray, np.ndarray]:
    """Extract one harmonic using the existing single-harmonic pipeline."""
    target_freq = nominal * harmonic
    filtered = bandpass_filter(signal, sr, target_freq, bandwidth)
    timestamps, freq_estimates, quality_scores = qifft_extract(
        filtered,
        sr,
        nominal,
        frame_sec,
        overlap,
        pad_factor,
        bandwidth,
        harmonic,
        tracking_mode=tracking_mode,
        spectrum_estimator=spectrum_estimator,
    )

    estimates_per_sec = 1.0 / (frame_sec * (1 - overlap))
    if estimates_per_sec > 1.0:
        frame_timestamps = timestamps
        timestamps, freq_estimates = aggregate_to_one_hz(frame_timestamps, freq_estimates)
        _, quality_scores = aggregate_series_to_one_hz(frame_timestamps, quality_scores)

    if return_quality:
        if quality_scale == "raw":
            returned_quality = quality_scores
        elif quality_scale == "confidence":
            returned_quality = snr_weight_to_confidence(quality_scores)
        else:
            raise ValueError("quality_scale must be 'confidence' or 'raw'")
        return timestamps, freq_estimates, returned_quality
    return timestamps, freq_estimates


def fuse_harmonic_estimates(
    harmonic_traces: Mapping[int, Trace],
    method: str = "weighted",
    harmonic_quality: Mapping[int, np.ndarray] | None = None,
) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """Fuse per-harmonic ENF traces into one trace with confidence scores."""
    available = {
        harmonic: (np.asarray(timestamps), np.asarray(freqs))
        for harmonic, (timestamps, freqs) in harmonic_traces.items()
        if len(timestamps) > 0 and len(freqs) > 0
    }
    if not available:
        return np.array([]), np.array([]), np.array([])

    timestamp_to_values: dict[float, list[tuple[int, float, float]]] = {}
    for harmonic, (timestamps, freqs) in available.items():
        quality = None if harmonic_quality is None else harmonic_quality.get(harmonic)
        if quality is None or len(quality) != len(freqs):
            quality = np.ones(len(freqs), dtype=np.float64)
        for timestamp, freq, snr_weight in zip(timestamps, freqs, quality):
            timestamp_to_values.setdefault(float(timestamp), []).append(
                (harmonic, float(freq), float(snr_weight))
            )

    fused_times = []
    fused_freqs = []
    confidence_scores = []

    for timestamp in sorted(timestamp_to_values):
        harmonic_values = timestamp_to_values[timestamp]
        values = np.array([freq for _, freq, _ in harmonic_values], dtype=np.float64)

        if method == "mean":
            fused_freq = float(np.mean(values))
        elif method == "vote":
            fused_freq = vote_fused_frequency(
                [(harmonic, freq) for harmonic, freq, _ in harmonic_values]
            )
        elif method == "snr-weighted":
            weights = np.array(
                [max(snr_weight, 1e-6) for _, _, snr_weight in harmonic_values],
                dtype=np.float64,
            )
            fused_freq = float(np.average(values, weights=weights))
        else:
            weights = np.array(
                [harmonic_weight(harmonic) for harmonic, _, _ in harmonic_values],
                dtype=np.float64,
            )
            fused_freq = float(np.average(values, weights=weights))

        fused_times.append(timestamp)
        fused_freqs.append(fused_freq)
        confidence_scores.append(estimate_fusion_confidence(values))

    return (
        np.array(fused_times, dtype=np.float64),
        np.array(fused_freqs, dtype=np.float64),
        np.array(confidence_scores, dtype=np.float64),
    )


def extract_multi_harmonic(
    signal: np.ndarray,
    sr: int,
    nominal: float,
    bandwidth: float,
    frame_sec: float,
    overlap: float,
    pad_factor: int,
    harmonics: list[int],
    median_window: int,
    fusion_method: str = "weighted",
    tracking_mode: str = "peak",
    spectrum_estimator: str = "fft",
) -> tuple[np.ndarray, np.ndarray, np.ndarray, dict[int, Trace]]:
    """Extract and fuse multiple harmonics using the single-harmonic pipeline."""
    harmonic_traces: dict[int, Trace] = {}
    harmonic_quality: dict[int, np.ndarray] = {}
    for harmonic in harmonics:
        extracted = extract_harmonic_trace(
            signal,
            sr,
            nominal,
            bandwidth,
            frame_sec,
            overlap,
            pad_factor,
            harmonic,
            tracking_mode=tracking_mode,
            spectrum_estimator=spectrum_estimator,
            return_quality=fusion_method == "snr-weighted",
            quality_scale="raw",
        )
        if fusion_method == "snr-weighted":
            timestamps, freqs, quality_scores = extracted
            harmonic_traces[harmonic] = (timestamps, freqs)
            harmonic_quality[harmonic] = quality_scores
        else:
            timestamps, freqs = extracted
            harmonic_traces[harmonic] = (timestamps, freqs)

    if median_window > 0:
        harmonic_traces = {
            harmonic: (timestamps, apply_median_filter(freqs, median_window))
            for harmonic, (timestamps, freqs) in harmonic_traces.items()
        }

    timestamps, fused_freqs, confidence_scores = fuse_harmonic_estimates(
        harmonic_traces,
        method=fusion_method,
        harmonic_quality=harmonic_quality if fusion_method == "snr-weighted" else None,
    )
    return timestamps, fused_freqs, confidence_scores, harmonic_traces


def write_csv(
    path: str,
    timestamps: np.ndarray,
    freqs: np.ndarray,
    confidence_scores: np.ndarray | None = None,
) -> None:
    """Write results to CSV with header."""
    with open(path, "w", newline="") as f:
        if confidence_scores is None:
            f.write("offset_seconds,frequency_hz\n")
            for t, freq in zip(timestamps, freqs):
                f.write(f"{t:.6f},{freq:.6f}\n")
            return

        if len(confidence_scores) != len(freqs):
            raise ValueError("confidence_scores must match the number of frequency estimates")

        f.write("offset_seconds,frequency_hz,confidence_score\n")
        for t, freq, confidence in zip(timestamps, freqs, confidence_scores):
            f.write(f"{t:.6f},{freq:.6f},{confidence:.6f}\n")


def write_detail_csvs(output_path: str, harmonic_traces: Mapping[int, Trace]) -> None:
    """Write per-harmonic detail CSVs next to the main output."""
    output = Path(output_path)
    for harmonic, (timestamps, freqs) in sorted(harmonic_traces.items()):
        detail_path = output.with_name(f"{output.stem}_h{harmonic}{output.suffix}")
        write_csv(str(detail_path), timestamps, freqs)


def print_summary(
    input_path: str, duration: float, freqs: np.ndarray
) -> None:
    """Print a human-readable summary to stdout."""
    print(f"Input:      {input_path}")
    print(f"Duration:   {duration:.2f} s")
    print(f"Estimates:  {len(freqs)}")
    if len(freqs) > 0:
        print(f"Mean freq:  {np.mean(freqs):.4f} Hz")
        print(f"Std dev:    {np.std(freqs):.4f} Hz")
        print(f"Min freq:   {np.min(freqs):.4f} Hz")
        print(f"Max freq:   {np.max(freqs):.4f} Hz")


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    input_path = args.input

    if not os.path.isfile(input_path):
        print(f"Error: file not found: {input_path}", file=sys.stderr)
        return 1

    # Determine output path
    output_path = args.output
    if output_path is None:
        output_path = str(Path(input_path).with_suffix("")) + "_enf.csv"
    figure_output_path = resolve_figure_output_path(
        input_path=input_path,
        output_path=output_path,
        export_figure=args.export_figure,
        figure_output=args.figure_output,
    )

    # Extract audio from video if needed
    audio_extensions = {".wav", ".flac"}
    ext = Path(input_path).suffix.lower()
    tmp_wav = None

    try:
        if ext not in audio_extensions:
            tmp_wav = extract_audio_from_video(input_path)
            wav_path = tmp_wav
        else:
            wav_path = input_path

        sr, signal = load_audio(wav_path)
        duration = len(signal) / sr
        confidence_scores = None

        if args.multi_harmonic:
            harmonics = args.harmonics
            supported_harmonics, unsupported_harmonics = split_supported_harmonics(
                sr,
                args.nominal,
                args.bandwidth,
                harmonics,
            )
            if unsupported_harmonics:
                message = (
                    "unsupported harmonics: "
                    f"{format_unsupported_harmonics(unsupported_harmonics, sr)}"
                )
                if args.harmonics_explicit:
                    raise RuntimeError(message)
                print(f"Warning: Skipping {message}", file=sys.stderr)
            if not supported_harmonics:
                raise RuntimeError(
                    "no supported harmonics remain after Nyquist check"
                )

            timestamps, freq_estimates, confidence_scores, harmonic_traces = extract_multi_harmonic(
                signal,
                sr,
                args.nominal,
                args.bandwidth,
                args.frame_sec,
                args.overlap,
                args.pad_factor,
                supported_harmonics,
                args.median_window,
                args.harmonic_fusion,
                args.tracking_mode,
                args.spectrum_estimator,
            )
            if args.detail_output:
                write_detail_csvs(output_path, harmonic_traces)
        else:
            supported_harmonics, unsupported_harmonics = split_supported_harmonics(
                sr,
                args.nominal,
                args.bandwidth,
                [args.harmonic],
            )
            if not supported_harmonics:
                raise RuntimeError(
                    "Requested harmonic is unsupported at this sample rate: "
                    + format_unsupported_harmonics(unsupported_harmonics, sr)
                )
            extracted = extract_harmonic_trace(
                signal,
                sr,
                args.nominal,
                args.bandwidth,
                args.frame_sec,
                args.overlap,
                args.pad_factor,
                args.harmonic,
                args.tracking_mode,
                args.spectrum_estimator,
                return_quality=args.confidence_output,
            )
            if args.confidence_output:
                timestamps, freq_estimates, confidence_scores = extracted
            else:
                timestamps, freq_estimates = extracted
            freq_estimates = apply_median_filter(freq_estimates, args.median_window)

        if figure_output_path is not None:
            try:
                write_summary_figure(
                    path=figure_output_path,
                    signal=signal,
                    sr=sr,
                    trace_timestamps=timestamps,
                    trace_freqs=freq_estimates,
                    nominal=args.nominal,
                )
            except Exception as exc:
                raise RuntimeError(f"Failed to write summary figure: {exc}") from exc
        write_csv(
            output_path,
            timestamps,
            freq_estimates,
            confidence_scores=confidence_scores if args.confidence_output else None,
        )
        print_summary(input_path, duration, freq_estimates)
        print(f"Output:     {output_path}")
        if figure_output_path is not None:
            print(f"Figure:     {figure_output_path}")

    except RuntimeError as e:
        print(f"Error: {e}", file=sys.stderr)
        return 1
    finally:
        if tmp_wav and os.path.exists(tmp_wav):
            os.remove(tmp_wav)

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
