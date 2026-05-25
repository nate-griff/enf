import argparse
import contextlib
import gc
import io
import tempfile
import unittest
import warnings
import weakref
from pathlib import Path
from unittest import mock

import enf_extract as ee
import numpy as np
import scipy.io.wavfile as wavfile
import scipy.signal


class ParseArgsTests(unittest.TestCase):
    def test_parse_args_accepts_figure_export_flags(self) -> None:
        args = ee.parse_args(
            [
                "--input",
                "clip.wav",
                "--export-figure",
                "--figure-output",
                "clip_enf.png",
            ]
        )

        self.assertTrue(args.export_figure)
        self.assertEqual(args.figure_output, "clip_enf.png")

    def test_parse_args_accepts_multi_harmonic_flags(self) -> None:
        args = ee.parse_args(
            [
                "--input",
                "clip.wav",
                "--multi-harmonic",
                "--harmonics",
                "2,3",
                "--harmonic-fusion",
                "vote",
                "--confidence-output",
                "--detail-output",
            ]
        )

        self.assertTrue(args.multi_harmonic)
        self.assertEqual(args.harmonics, [2, 3])
        self.assertEqual(args.harmonic_fusion, "vote")
        self.assertTrue(args.confidence_output)
        self.assertTrue(args.detail_output)

    def test_parse_args_accepts_tracking_spectrum_and_snr_fusion_flags(self) -> None:
        args = ee.parse_args(
            [
                "--input",
                "clip.wav",
                "--multi-harmonic",
                "--harmonic-fusion",
                "snr-weighted",
                "--tracking-mode",
                "dp",
                "--spectrum-estimator",
                "multitaper",
            ]
        )

        self.assertEqual(args.harmonic_fusion, "snr-weighted")
        self.assertEqual(args.tracking_mode, "dp")
        self.assertEqual(args.spectrum_estimator, "multitaper")


class ParseHarmonicsSpecTests(unittest.TestCase):
    def test_accepts_valid_harmonic_list(self) -> None:
        self.assertEqual(ee.parse_harmonics_spec("1,2,3"), [1, 2, 3])

    def test_rejects_invalid_harmonic_values(self) -> None:
        with self.assertRaises(argparse.ArgumentTypeError):
            ee.parse_harmonics_spec("1,0,2")

        with self.assertRaises(argparse.ArgumentTypeError):
            ee.parse_harmonics_spec("2,2")


class FuseHarmonicEstimatesTests(unittest.TestCase):
    def test_weighted_vote_and_mean_are_deterministic(self) -> None:
        timestamps = np.array([0.5, 1.5], dtype=np.float64)
        per_harmonic = {
            1: (timestamps, np.array([60.10, 60.10], dtype=np.float64)),
            2: (timestamps, np.array([60.00, 60.00], dtype=np.float64)),
            3: (timestamps, np.array([60.05, 60.05], dtype=np.float64)),
        }

        weighted_times, weighted_freqs, weighted_confidence = ee.fuse_harmonic_estimates(
            per_harmonic, method="weighted"
        )
        mean_times, mean_freqs, mean_confidence = ee.fuse_harmonic_estimates(
            per_harmonic, method="mean"
        )
        vote_times, vote_freqs, vote_confidence = ee.fuse_harmonic_estimates(
            per_harmonic, method="vote"
        )

        np.testing.assert_allclose(weighted_times, timestamps)
        np.testing.assert_allclose(mean_times, timestamps)
        np.testing.assert_allclose(vote_times, timestamps)
        np.testing.assert_allclose(weighted_freqs, [60.033333, 60.033333], atol=1e-6)
        np.testing.assert_allclose(mean_freqs, [60.05, 60.05], atol=1e-6)
        np.testing.assert_allclose(vote_freqs, [60.00, 60.00], atol=1e-6)
        self.assertTrue(np.all(weighted_confidence > 0.0))
        self.assertTrue(np.all(mean_confidence > 0.0))
        self.assertTrue(np.all(vote_confidence > 0.0))

    def test_vote_returns_actual_estimate_for_two_harmonic_tie(self) -> None:
        timestamps = np.array([0.5], dtype=np.float64)
        per_harmonic = {
            2: (timestamps, np.array([60.00], dtype=np.float64)),
            3: (timestamps, np.array([60.10], dtype=np.float64)),
        }

        _, vote_freqs, _ = ee.fuse_harmonic_estimates(per_harmonic, method="vote")

        np.testing.assert_allclose(vote_freqs, [60.00], atol=1e-6)

    def test_snr_weighted_prefers_higher_quality_harmonic(self) -> None:
        timestamps = np.array([0.5], dtype=np.float64)
        per_harmonic = {
            1: (timestamps, np.array([60.10], dtype=np.float64)),
            2: (timestamps, np.array([60.00], dtype=np.float64)),
        }
        harmonic_quality = {
            1: np.array([9.0], dtype=np.float64),
            2: np.array([0.2], dtype=np.float64),
        }

        _, fused_freqs, _ = ee.fuse_harmonic_estimates(
            per_harmonic,
            method="snr-weighted",
            harmonic_quality=harmonic_quality,
        )

        expected = np.average([60.10, 60.00], weights=[9.0, 0.2])
        np.testing.assert_allclose(fused_freqs, [expected], atol=1e-6)


class EstimateLocalSnrWeightTests(unittest.TestCase):
    def test_returns_neutral_weight_when_noise_samples_are_unavailable(self) -> None:
        weight = ee.estimate_local_snr_weight(np.array([3.0], dtype=np.float64), peak_index=0)

        self.assertEqual(weight, 1.0)

    def test_clamps_pathological_weight_when_noise_floor_is_tiny(self) -> None:
        weight = ee.estimate_local_snr_weight(
            np.array([1e-9, 0.5, 1.0, 0.5, 1e-9], dtype=np.float64),
            peak_index=2,
        )

        self.assertLessEqual(weight, 1_000.0)


class RidgeTrackingTests(unittest.TestCase):
    def test_track_spectral_ridge_default_matches_cli_tracking_regime(self) -> None:
        scores = np.array(
            [
                [0.0, 10.0, 0.0],
                [0.0, 8.0, 12.0],
                [0.0, 10.0, 0.0],
            ],
            dtype=np.float64,
        )

        ridge = ee.track_spectral_ridge(scores)

        np.testing.assert_array_equal(ridge, [1, 2, 1])


class QifftExtractMemoryTests(unittest.TestCase):
    class _TrackedSpectrum(np.ndarray):
        pass

    @staticmethod
    def _make_tracked_spectrum() -> np.ndarray:
        spectrum = np.ones(9, dtype=np.float64)
        spectrum[4] = 10.0
        return spectrum.view(QifftExtractMemoryTests._TrackedSpectrum)

    def test_peak_tracking_does_not_retain_all_frame_spectra(self) -> None:
        signal = np.zeros(12, dtype=np.float64)
        tracked_refs: list[weakref.ReferenceType[np.ndarray]] = []
        alive_counts: list[int] = []

        def fake_estimate_frame_spectrum(*args, **kwargs) -> np.ndarray:
            spectrum = self._make_tracked_spectrum()
            tracked_refs.append(weakref.ref(spectrum))
            return spectrum

        def fake_estimate_local_snr_weight(search_region: np.ndarray, peak_index: int) -> float:
            gc.collect()
            alive_counts.append(sum(1 for ref in tracked_refs if ref() is not None))
            return 1.0

        with (
            mock.patch.object(ee, "estimate_frame_spectrum", side_effect=fake_estimate_frame_spectrum),
            mock.patch.object(ee, "estimate_local_snr_weight", new=fake_estimate_local_snr_weight),
        ):
            ee.qifft_extract(
                signal=signal,
                sr=4,
                nominal=1.0,
                frame_sec=1.0,
                overlap=0.0,
                pad_factor=4,
                bandwidth=0.25,
                tracking_mode="peak",
            )

        self.assertTrue(alive_counts)
        self.assertLessEqual(max(alive_counts), 1)

    def test_dp_tracking_does_not_retain_all_frame_spectra(self) -> None:
        signal = np.zeros(12, dtype=np.float64)
        tracked_refs: list[weakref.ReferenceType[np.ndarray]] = []
        alive_counts: list[int] = []

        def fake_estimate_frame_spectrum(*args, **kwargs) -> np.ndarray:
            spectrum = self._make_tracked_spectrum()
            tracked_refs.append(weakref.ref(spectrum))
            return spectrum

        def fake_track_spectral_ridge(scores: np.ndarray, jump_penalty: float = 5.0) -> np.ndarray:
            gc.collect()
            alive_counts.append(sum(1 for ref in tracked_refs if ref() is not None))
            return np.argmax(scores, axis=1)

        with (
            mock.patch.object(ee, "estimate_frame_spectrum", side_effect=fake_estimate_frame_spectrum),
            mock.patch.object(ee, "track_spectral_ridge", side_effect=fake_track_spectral_ridge),
        ):
            ee.qifft_extract(
                signal=signal,
                sr=4,
                nominal=1.0,
                frame_sec=1.0,
                overlap=0.0,
                pad_factor=4,
                bandwidth=0.25,
                tracking_mode="dp",
            )

        self.assertEqual(len(alive_counts), 1)
        self.assertLessEqual(alive_counts[0], 1)


class MultiTaperSpectrumTests(unittest.TestCase):
    def test_multitaper_spectrum_averages_tapered_periodograms(self) -> None:
        frame = np.array([0.1, 0.4, -0.2, 0.3, -0.1, 0.2, -0.3, 0.5], dtype=np.float64)
        n_padded = 16

        spectrum = ee.multitaper_spectrum(frame, n_padded=n_padded, nw=2.5, k=3)

        tapers = scipy.signal.windows.dpss(len(frame), 2.5, Kmax=3, sym=False)
        expected = np.sqrt(
            np.mean(
                [
                    np.abs(np.fft.rfft(frame * taper, n=n_padded)) ** 2
                    for taper in tapers
                ],
                axis=0,
            )
        )
        np.testing.assert_allclose(spectrum, expected, atol=1e-12)


class ExtractMultiHarmonicTests(unittest.TestCase):
    def test_uses_requested_fusion_method(self) -> None:
        timestamps = np.array([0.5], dtype=np.float64)
        traces = [
            (timestamps, np.array([60.10], dtype=np.float64)),
            (timestamps, np.array([60.00], dtype=np.float64)),
            (timestamps, np.array([60.05], dtype=np.float64)),
        ]

        with mock.patch.object(ee, "extract_harmonic_trace", side_effect=traces):
            _, fused_freqs, _, _ = ee.extract_multi_harmonic(
                signal=np.array([], dtype=np.float64),
                sr=1000,
                nominal=60.0,
                bandwidth=0.5,
                frame_sec=1.0,
                overlap=0.5,
                pad_factor=16,
                harmonics=[1, 2, 3],
                median_window=0,
                fusion_method="vote",
            )

        np.testing.assert_allclose(fused_freqs, [60.00], atol=1e-6)

    def test_confidence_scores_track_smoothed_output(self) -> None:
        timestamps = np.array([0.5, 1.5, 2.5], dtype=np.float64)
        traces = [
            (timestamps, np.array([60.00, 61.00, 60.00], dtype=np.float64)),
            (timestamps, np.array([60.00, 61.20, 60.00], dtype=np.float64)),
        ]

        with mock.patch.object(ee, "extract_harmonic_trace", side_effect=traces):
            _, fused_freqs, confidence_scores, _ = ee.extract_multi_harmonic(
                signal=np.array([], dtype=np.float64),
                sr=1000,
                nominal=60.0,
                bandwidth=0.5,
                frame_sec=1.0,
                overlap=0.5,
                pad_factor=16,
                harmonics=[2, 3],
                median_window=3,
                fusion_method="weighted",
            )

        np.testing.assert_allclose(fused_freqs, [60.0, 60.0, 60.0], atol=1e-6)
        np.testing.assert_allclose(confidence_scores, [1.0, 1.0, 1.0], atol=1e-6)


class ExtractHarmonicTraceTests(unittest.TestCase):
    def test_returned_confidence_scores_are_normalized_to_unit_interval(self) -> None:
        timestamps = np.array([0.5, 1.5], dtype=np.float64)
        freqs = np.array([60.01, 59.99], dtype=np.float64)
        quality_scores = np.array([0.25, 3.0], dtype=np.float64)

        with (
            mock.patch.object(ee, "bandpass_filter", return_value=np.array([], dtype=np.float64)),
            mock.patch.object(ee, "qifft_extract", return_value=(timestamps, freqs, quality_scores)),
        ):
            _, _, confidence_scores = ee.extract_harmonic_trace(
                signal=np.array([], dtype=np.float64),
                sr=1000,
                nominal=60.0,
                bandwidth=0.5,
                frame_sec=1.0,
                overlap=0.0,
                pad_factor=16,
                harmonic=2,
                return_quality=True,
            )

        self.assertTrue(np.all(confidence_scores >= 0.0))
        self.assertTrue(np.all(confidence_scores <= 1.0))
        self.assertGreater(confidence_scores[1], confidence_scores[0])


class WriteCsvTests(unittest.TestCase):
    def test_write_csv_only_appends_confidence_when_requested(self) -> None:
        timestamps = np.array([0.5], dtype=np.float64)
        freqs = np.array([60.01], dtype=np.float64)
        confidence = np.array([0.8], dtype=np.float64)

        with tempfile.TemporaryDirectory() as tmpdir:
            base_path = Path(tmpdir)
            plain_path = base_path / "plain.csv"
            confidence_path = base_path / "confidence.csv"

            ee.write_csv(str(plain_path), timestamps, freqs)
            ee.write_csv(str(confidence_path), timestamps, freqs, confidence_scores=confidence)

            self.assertEqual(
                plain_path.read_text().splitlines(),
                ["offset_seconds,frequency_hz", "0.500000,60.010000"],
            )
            self.assertEqual(
                confidence_path.read_text().splitlines(),
                ["offset_seconds,frequency_hz,confidence_score", "0.500000,60.010000,0.800000"],
            )


class DetailOutputTests(unittest.TestCase):
    def test_writes_per_harmonic_csvs_with_default_contract(self) -> None:
        timestamps = np.array([0.5, 1.5], dtype=np.float64)
        per_harmonic = {
            1: (timestamps, np.array([60.01, 60.02], dtype=np.float64)),
            3: (timestamps, np.array([59.99, 60.00], dtype=np.float64)),
        }

        with tempfile.TemporaryDirectory() as tmpdir:
            output_path = Path(tmpdir) / "trace.csv"

            ee.write_detail_csvs(str(output_path), per_harmonic)

            harmonic_1 = output_path.with_name("trace_h1.csv")
            harmonic_3 = output_path.with_name("trace_h3.csv")
            self.assertTrue(harmonic_1.exists())
            self.assertTrue(harmonic_3.exists())
            self.assertEqual(
                harmonic_1.read_text().splitlines()[0],
                "offset_seconds,frequency_hz",
            )
            self.assertEqual(
                harmonic_3.read_text().splitlines()[0],
                "offset_seconds,frequency_hz",
            )


class ResolveFigureOutputPathTests(unittest.TestCase):
    def test_auto_names_figure_from_csv_output_stem(self) -> None:
        figure_path = ee.resolve_figure_output_path(
            input_path="clip.wav",
            output_path=str(Path("results") / "clip_enf.csv"),
            export_figure=True,
            figure_output=None,
        )

        self.assertEqual(figure_path, str(Path("results") / "clip_enf.png"))


class WriteSummaryFigureTests(unittest.TestCase):
    def test_writes_png_with_spectrogram_and_trace(self) -> None:
        sr = 1000
        timestamps = np.arange(0.0, 2.0, 1.0 / sr)
        signal = np.sin(2 * np.pi * 60.0 * timestamps) + 0.2 * np.sin(2 * np.pi * 120.0 * timestamps)
        trace_times = np.array([0.5, 1.5], dtype=np.float64)
        trace_freqs = np.array([59.98, 60.02], dtype=np.float64)

        with tempfile.TemporaryDirectory() as tmpdir:
            figure_path = Path(tmpdir) / "trace.png"

            ee.write_summary_figure(
                path=str(figure_path),
                signal=signal,
                sr=sr,
                trace_timestamps=trace_times,
                trace_freqs=trace_freqs,
                nominal=60.0,
            )

            self.assertTrue(figure_path.exists())
            self.assertGreater(figure_path.stat().st_size, 0)

    def test_short_signal_does_not_emit_spectrogram_warning(self) -> None:
        sr = 1000
        timestamps = np.arange(0.0, 2.0, 1.0 / sr)
        signal = np.sin(2 * np.pi * 60.0 * timestamps)
        trace_times = np.array([0.5, 1.5], dtype=np.float64)
        trace_freqs = np.array([59.98, 60.02], dtype=np.float64)

        with tempfile.TemporaryDirectory() as tmpdir:
            figure_path = Path(tmpdir) / "trace.png"

            with warnings.catch_warnings():
                warnings.simplefilter("error")
                ee.write_summary_figure(
                    path=str(figure_path),
                    signal=signal,
                    sr=sr,
                    trace_timestamps=trace_times,
                    trace_freqs=trace_freqs,
                    nominal=60.0,
                )

            self.assertTrue(figure_path.exists())

    def test_single_point_trace_does_not_emit_xlim_warning(self) -> None:
        sr = 1000
        timestamps = np.arange(0.0, 2.0, 1.0 / sr)
        signal = np.sin(2 * np.pi * 60.0 * timestamps)
        trace_times = np.array([0.5], dtype=np.float64)
        trace_freqs = np.array([59.98], dtype=np.float64)

        with tempfile.TemporaryDirectory() as tmpdir:
            figure_path = Path(tmpdir) / "trace.png"

            with warnings.catch_warnings():
                warnings.simplefilter("error")
                ee.write_summary_figure(
                    path=str(figure_path),
                    signal=signal,
                    sr=sr,
                    trace_timestamps=trace_times,
                    trace_freqs=trace_freqs,
                    nominal=60.0,
                )

            self.assertTrue(figure_path.exists())


class MainFigureExportTests(unittest.TestCase):
    def test_figure_output_path_implies_export(self) -> None:
        sr = 2000
        timestamps = np.arange(0.0, 2.0, 1.0 / sr)
        waveform = 0.6 * np.sin(2 * np.pi * 120.0 * timestamps)

        with tempfile.TemporaryDirectory() as tmpdir:
            tmpdir_path = Path(tmpdir)
            input_path = tmpdir_path / "clip.wav"
            output_path = tmpdir_path / "clip_enf.csv"
            figure_path = tmpdir_path / "custom.png"

            wavfile.write(
                input_path,
                sr,
                np.int16(np.clip(waveform, -1.0, 1.0) * np.iinfo(np.int16).max),
            )

            exit_code = ee.main(
                [
                    "--input",
                    str(input_path),
                    "--output",
                    str(output_path),
                    "--figure-output",
                    str(figure_path),
                ]
            )

            self.assertEqual(exit_code, 0)
            self.assertTrue(output_path.exists())
            self.assertTrue(figure_path.exists())

    def test_figure_export_failure_returns_clean_error_without_writing_csv(self) -> None:
        sr = 2000
        timestamps = np.arange(0.0, 2.0, 1.0 / sr)
        waveform = 0.6 * np.sin(2 * np.pi * 120.0 * timestamps)

        with tempfile.TemporaryDirectory() as tmpdir:
            tmpdir_path = Path(tmpdir)
            input_path = tmpdir_path / "clip.wav"
            output_path = tmpdir_path / "clip_enf.csv"
            figure_path = tmpdir_path / "custom.png"

            wavfile.write(
                input_path,
                sr,
                np.int16(np.clip(waveform, -1.0, 1.0) * np.iinfo(np.int16).max),
            )

            stderr = io.StringIO()
            with contextlib.redirect_stderr(stderr):
                with mock.patch.object(ee, "write_summary_figure", side_effect=OSError("disk full")):
                    exit_code = ee.main(
                        [
                            "--input",
                            str(input_path),
                            "--output",
                            str(output_path),
                            "--figure-output",
                            str(figure_path),
                        ]
                    )

            self.assertEqual(exit_code, 1)
            self.assertFalse(output_path.exists())
            self.assertIn("Failed to write summary figure", stderr.getvalue())


class MainMultiHarmonicModeTests(unittest.TestCase):
    def test_confidence_output_in_single_harmonic_mode_writes_normalized_confidence_scores(self) -> None:
        sr = 2000
        sample_timestamps = np.arange(0.0, 2.0, 1.0 / sr)
        waveform = 0.6 * np.sin(2 * np.pi * 120.0 * sample_timestamps)
        trace_timestamps = np.array([0.5, 1.5], dtype=np.float64)
        trace_freqs = np.array([60.01, 59.99], dtype=np.float64)
        trace_quality = np.array([0.25, 3.0], dtype=np.float64)

        with tempfile.TemporaryDirectory() as tmpdir:
            tmpdir_path = Path(tmpdir)
            input_path = tmpdir_path / "clip.wav"
            output_path = tmpdir_path / "clip_enf.csv"

            wavfile.write(
                input_path,
                sr,
                np.int16(np.clip(waveform, -1.0, 1.0) * np.iinfo(np.int16).max),
            )

            with (
                mock.patch.object(
                    ee,
                    "bandpass_filter",
                    autospec=True,
                    return_value=np.array([], dtype=np.float64),
                ),
                mock.patch.object(
                    ee,
                    "qifft_extract",
                    autospec=True,
                    return_value=(trace_timestamps, trace_freqs, trace_quality),
                ),
                mock.patch.object(ee, "apply_median_filter", side_effect=lambda values, window: values),
            ):
                exit_code = ee.main(
                    [
                        "--input",
                        str(input_path),
                        "--output",
                        str(output_path),
                        "--confidence-output",
                    ]
                )

            self.assertEqual(exit_code, 0)
            lines = output_path.read_text().splitlines()
            self.assertEqual(lines[0], "offset_seconds,frequency_hz,confidence_score")
            self.assertEqual(lines[1:], ["0.500000,60.010000,0.200000", "1.500000,59.990000,0.750000"])

    def test_detail_output_requires_multi_harmonic_mode(self) -> None:
        stderr = io.StringIO()
        with contextlib.redirect_stderr(stderr):
            with self.assertRaises(SystemExit) as exc:
                ee.parse_args(
                    [
                        "--input",
                        "clip.wav",
                        "--detail-output",
                    ]
                )

        self.assertEqual(exc.exception.code, 2)
        self.assertIn("--detail-output requires --multi-harmonic", stderr.getvalue())

    def test_harmonics_requires_multi_harmonic_mode(self) -> None:
        stderr = io.StringIO()
        with contextlib.redirect_stderr(stderr):
            with self.assertRaises(SystemExit) as exc:
                ee.parse_args(
                    [
                        "--input",
                        "clip.wav",
                        "--harmonics",
                        "2,3",
                    ]
                )

        self.assertEqual(exc.exception.code, 2)
        self.assertIn("--harmonics requires --multi-harmonic", stderr.getvalue())

    def test_harmonic_fusion_requires_multi_harmonic_mode(self) -> None:
        stderr = io.StringIO()
        with contextlib.redirect_stderr(stderr):
            with self.assertRaises(SystemExit) as exc:
                ee.parse_args(
                    [
                        "--input",
                        "clip.wav",
                        "--harmonic-fusion",
                        "vote",
                    ]
                )

        self.assertEqual(exc.exception.code, 2)
        self.assertIn("--harmonic-fusion requires --multi-harmonic", stderr.getvalue())

    def test_default_multi_harmonic_mode_skips_unsupported_harmonics(self) -> None:
        sr = 320
        timestamps = np.arange(0.0, 2.0, 1.0 / sr)
        waveform = 0.6 * np.sin(2 * np.pi * 120.0 * timestamps)

        with tempfile.TemporaryDirectory() as tmpdir:
            tmpdir_path = Path(tmpdir)
            input_path = tmpdir_path / "clip.wav"
            output_path = tmpdir_path / "clip_enf.csv"

            wavfile.write(
                input_path,
                sr,
                np.int16(np.clip(waveform, -1.0, 1.0) * np.iinfo(np.int16).max),
            )

            stderr = io.StringIO()
            with contextlib.redirect_stderr(stderr):
                exit_code = ee.main(
                    [
                        "--input",
                        str(input_path),
                        "--output",
                        str(output_path),
                        "--multi-harmonic",
                    ]
                )

            self.assertEqual(exit_code, 0)
            self.assertTrue(output_path.exists())
            self.assertIn("Skipping unsupported harmonics", stderr.getvalue())
            self.assertIn("3", stderr.getvalue())

    def test_explicit_unsupported_harmonic_fails_cleanly(self) -> None:
        sr = 320
        timestamps = np.arange(0.0, 2.0, 1.0 / sr)
        waveform = 0.6 * np.sin(2 * np.pi * 120.0 * timestamps)

        with tempfile.TemporaryDirectory() as tmpdir:
            tmpdir_path = Path(tmpdir)
            input_path = tmpdir_path / "clip.wav"
            output_path = tmpdir_path / "clip_enf.csv"

            wavfile.write(
                input_path,
                sr,
                np.int16(np.clip(waveform, -1.0, 1.0) * np.iinfo(np.int16).max),
            )

            stderr = io.StringIO()
            with contextlib.redirect_stderr(stderr):
                exit_code = ee.main(
                    [
                        "--input",
                        str(input_path),
                        "--output",
                        str(output_path),
                        "--multi-harmonic",
                        "--harmonics",
                        "3",
                    ]
                )

            self.assertEqual(exit_code, 1)
            self.assertFalse(output_path.exists())
            self.assertIn("unsupported harmonic", stderr.getvalue())
            self.assertIn("3", stderr.getvalue())

    def test_single_harmonic_mode_fails_cleanly_for_unsupported_harmonic(self) -> None:
        sr = 320
        timestamps = np.arange(0.0, 2.0, 1.0 / sr)
        waveform = 0.6 * np.sin(2 * np.pi * 120.0 * timestamps)

        with tempfile.TemporaryDirectory() as tmpdir:
            tmpdir_path = Path(tmpdir)
            input_path = tmpdir_path / "clip.wav"
            output_path = tmpdir_path / "clip_enf.csv"

            wavfile.write(
                input_path,
                sr,
                np.int16(np.clip(waveform, -1.0, 1.0) * np.iinfo(np.int16).max),
            )

            stderr = io.StringIO()
            with contextlib.redirect_stderr(stderr):
                exit_code = ee.main(
                    [
                        "--input",
                        str(input_path),
                        "--output",
                        str(output_path),
                        "--harmonic",
                        "3",
                    ]
                )

            self.assertEqual(exit_code, 1)
            self.assertFalse(output_path.exists())
            self.assertIn("Requested harmonic is unsupported", stderr.getvalue())
            self.assertIn("3", stderr.getvalue())

    def test_main_accepts_dp_tracking_multitaper_and_snr_weighted_fusion(self) -> None:
        sr = 2000
        timestamps = np.arange(0.0, 2.0, 1.0 / sr)
        waveform = (
            0.5 * np.sin(2 * np.pi * 60.0 * timestamps)
            + 0.6 * np.sin(2 * np.pi * 120.0 * timestamps)
        )

        with tempfile.TemporaryDirectory() as tmpdir:
            tmpdir_path = Path(tmpdir)
            input_path = tmpdir_path / "clip.wav"
            output_path = tmpdir_path / "clip_enf.csv"

            wavfile.write(
                input_path,
                sr,
                np.int16(np.clip(waveform, -1.0, 1.0) * np.iinfo(np.int16).max),
            )

            exit_code = ee.main(
                [
                    "--input",
                    str(input_path),
                    "--output",
                    str(output_path),
                    "--multi-harmonic",
                    "--harmonic-fusion",
                    "snr-weighted",
                    "--tracking-mode",
                    "dp",
                    "--spectrum-estimator",
                    "multitaper",
                ]
            )

            self.assertEqual(exit_code, 0)
            self.assertEqual(output_path.read_text().splitlines()[0], "offset_seconds,frequency_hz")


class ApplyMedianFilterTests(unittest.TestCase):
    def test_short_trace_does_not_warn_when_window_is_larger_than_series(self) -> None:
        freqs = np.array([59.99, 60.01], dtype=np.float64)

        with warnings.catch_warnings():
            warnings.simplefilter("error")
            filtered = ee.apply_median_filter(freqs, window=3)

        np.testing.assert_allclose(filtered, freqs)


if __name__ == "__main__":
    unittest.main()
