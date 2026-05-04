import tempfile
import unittest
import warnings
from pathlib import Path

import enf_extract as ee
import numpy as np
import scipy.io.wavfile as wavfile


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


class ApplyMedianFilterTests(unittest.TestCase):
    def test_short_trace_does_not_warn_when_window_is_larger_than_series(self) -> None:
        freqs = np.array([59.99, 60.01], dtype=np.float64)

        with warnings.catch_warnings():
            warnings.simplefilter("error")
            filtered = ee.apply_median_filter(freqs, window=3)

        np.testing.assert_allclose(filtered, freqs)


if __name__ == "__main__":
    unittest.main()
