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

import numpy as np
import scipy.io.wavfile as wavfile
import scipy.signal


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Extract ENF from audio/video using QIFFT."
    )
    parser.add_argument("--input", required=True, help="Path to audio or video file.")
    parser.add_argument("--output", default=None, help="Output CSV path (default: {input_stem}_enf.csv).")
    parser.add_argument("--nominal", type=float, default=60.0, help="Nominal grid frequency in Hz.")
    parser.add_argument("--bandwidth", type=float, default=0.5, help="Half-bandwidth in Hz around nominal for bandpass filter and peak search (default: 0.5).")
    parser.add_argument("--harmonic", type=int, default=2, help="Which harmonic to extract (1=fundamental 60Hz, 2=second harmonic 120Hz, recommended). Result is divided back to fundamental.")
    parser.add_argument("--frame-sec", type=float, default=1.0, help="Frame duration in seconds.")
    parser.add_argument("--overlap", type=float, default=0.5, help="Frame overlap fraction 0-1.")
    parser.add_argument("--pad-factor", type=int, default=16, help="Zero-padding multiplier for FFT.")
    parser.add_argument("--median-window", type=int, default=3, help="Median filter window size (0=disable).")
    return parser.parse_args(argv)


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


def bandpass_filter(
    signal: np.ndarray, sr: int, nominal: float, bandwidth: float = 0.5
) -> np.ndarray:
    """Apply 4th-order Butterworth bandpass around the nominal frequency."""
    lo = nominal - bandwidth
    hi = nominal + bandwidth
    sos = scipy.signal.butter(4, [lo, hi], btype="bandpass", fs=sr, output="sos")
    return scipy.signal.sosfiltfilt(sos, signal)


def qifft_extract(
    signal: np.ndarray,
    sr: int,
    nominal: float,
    frame_sec: float,
    overlap: float,
    pad_factor: int,
    bandwidth: float = 0.5,
    harmonic: int = 1,
) -> tuple[np.ndarray, np.ndarray]:
    """Run STFT with QIFFT interpolation, return (timestamps, freq_estimates)."""
    target_freq = nominal * harmonic
    frame_len = int(sr * frame_sec)
    hop = int(frame_len * (1 - overlap))
    n_frames = max(1, (len(signal) - frame_len) // hop + 1)

    window = np.hanning(frame_len)
    n_padded = frame_len * pad_factor
    freq_resolution = sr / n_padded

    # Bin range for [target - bandwidth, target + bandwidth] Hz
    bin_lo = int(np.floor((target_freq - bandwidth) / freq_resolution))
    bin_hi = int(np.ceil((target_freq + bandwidth) / freq_resolution))

    timestamps = np.empty(n_frames)
    freq_estimates = np.empty(n_frames)

    for i in range(n_frames):
        start = i * hop
        frame = signal[start : start + frame_len] * window

        spectrum = np.fft.rfft(frame, n=n_padded)
        mag = np.abs(spectrum)

        # Find peak bin within the nominal frequency range
        search_region = mag[bin_lo : bin_hi + 1]
        k_local = np.argmax(search_region)
        k = bin_lo + k_local

        # QIFFT quadratic interpolation for sub-bin accuracy
        if 1 <= k < len(mag) - 1:
            alpha = mag[k - 1]
            beta = mag[k]
            gamma = mag[k + 1]
            denom = alpha - 2 * beta + gamma
            if abs(denom) > 1e-12:
                delta = 0.5 * (alpha - gamma) / denom
            else:
                delta = 0.0
        else:
            delta = 0.0

        # Divide by harmonic to get fundamental frequency
        freq_estimates[i] = (k + delta) * freq_resolution / harmonic
        timestamps[i] = (start + frame_len / 2) / sr

    return timestamps, freq_estimates


def aggregate_to_one_hz(
    timestamps: np.ndarray, freq_estimates: np.ndarray
) -> tuple[np.ndarray, np.ndarray]:
    """Average estimates that fall within the same 1-second bin."""
    if len(timestamps) == 0:
        return timestamps, freq_estimates

    max_time = timestamps[-1]
    n_bins = int(np.floor(max_time)) + 1
    agg_times = []
    agg_freqs = []

    for b in range(n_bins):
        mask = (timestamps >= b) & (timestamps < b + 1)
        if np.any(mask):
            agg_times.append(b + 0.5)
            agg_freqs.append(np.mean(freq_estimates[mask]))

    return np.array(agg_times), np.array(agg_freqs)


def apply_median_filter(freqs: np.ndarray, window: int) -> np.ndarray:
    """Apply median filter for smoothing. Window must be odd."""
    if window <= 0:
        return freqs
    if window % 2 == 0:
        window += 1
    return scipy.signal.medfilt(freqs, kernel_size=window)


def write_csv(path: str, timestamps: np.ndarray, freqs: np.ndarray) -> None:
    """Write results to CSV with header."""
    with open(path, "w", newline="") as f:
        f.write("offset_seconds,frequency_hz\n")
        for t, freq in zip(timestamps, freqs):
            f.write(f"{t:.6f},{freq:.6f}\n")


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

        target_freq = args.nominal * args.harmonic
        filtered = bandpass_filter(signal, sr, target_freq, args.bandwidth)

        timestamps, freq_estimates = qifft_extract(
            filtered, sr, args.nominal,
            args.frame_sec, args.overlap, args.pad_factor,
            args.bandwidth, args.harmonic,
        )

        # Aggregate to ~1 Hz cadence if overlap produces multiple estimates per second
        estimates_per_sec = 1.0 / (args.frame_sec * (1 - args.overlap))
        if estimates_per_sec > 1.0:
            timestamps, freq_estimates = aggregate_to_one_hz(timestamps, freq_estimates)

        freq_estimates = apply_median_filter(freq_estimates, args.median_window)

        write_csv(output_path, timestamps, freq_estimates)
        print_summary(input_path, duration, freq_estimates)
        print(f"Output:     {output_path}")

    except RuntimeError as e:
        print(f"Error: {e}", file=sys.stderr)
        return 1
    finally:
        if tmp_wav and os.path.exists(tmp_wav):
            os.remove(tmp_wav)

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
