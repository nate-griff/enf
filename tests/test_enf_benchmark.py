import csv
import io
import json
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from unittest import mock

import enf_benchmark as eb


class TechniquePresetTests(unittest.TestCase):
    def test_resolve_technique_presets_includes_required_variants(self) -> None:
        presets = eb.resolve_technique_presets()

        self.assertTrue(
            {
                "baseline",
                "dp",
                "multitaper",
                "dp-multitaper",
                "snr-multi",
                "dp-multitaper-snr-multi",
            }.issubset({preset.name for preset in presets})
        )

    def test_resolve_technique_presets_raises_value_error_for_unknown_names(self) -> None:
        with self.assertRaisesRegex(ValueError, "Unknown technique preset"):
            eb.resolve_technique_presets(["missing-preset"])


class CaseSelectionTests(unittest.TestCase):
    def test_resolve_benchmark_cases_uses_tracked_defaults_only(self) -> None:
        with mock.patch.object(Path, "is_file", autospec=True, return_value=True):
            cases = eb.resolve_benchmark_cases()

        self.assertEqual(
            [case.name for case in cases],
            [
                "fan-iphone-apr20",
                "fan-ipad-apr23",
                "bose-near-fan-may11",
                "bathroom-exhaust-may11",
                "microwave-apr20",
                "transformer-apr23",
                "dfor661-apr20",
            ],
        )
        self.assertTrue(all("sample_data" in str(case.input_path) for case in cases))
        self.assertNotIn("class-demo", {case.name for case in cases})

    def test_resolve_benchmark_cases_raises_value_error_for_unknown_names(self) -> None:
        with self.assertRaisesRegex(ValueError, "Unknown benchmark case"):
            eb.resolve_benchmark_cases(["missing-case"])

    def test_resolve_benchmark_cases_reports_missing_input_separately_from_unknown(
        self,
    ) -> None:
        missing_case = eb.DEFAULT_BENCHMARK_CASES[0]

        def fake_is_file(path: Path) -> bool:
            return path != missing_case.input_path

        with (
            mock.patch.object(Path, "is_file", autospec=True, side_effect=fake_is_file),
            self.assertRaisesRegex(
                ValueError,
                f"Missing benchmark input file\\(s\\): {missing_case.name}",
            ),
        ):
            eb.resolve_benchmark_cases([missing_case.name])

    def test_load_optional_class_demo_cases_resolves_relative_paths_from_manifest_dir(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            manifest_dir = Path(tmpdir) / "class_demo"
            manifest_dir.mkdir()
            recording_path = manifest_dir / "recordings" / "demo.m4a"
            recording_path.parent.mkdir()
            recording_path.write_bytes(b"demo")
            manifest_path = manifest_dir / "benchmark_cases.json"
            manifest_path.write_text(
                json.dumps(
                    [
                        {
                            "name": "class-demo",
                            "description": "Class demo case",
                            "input_path": "recordings\\demo.m4a",
                            "region": "EI",
                            "comparison_dates": ["2026-04-20"],
                        }
                    ]
                ),
                encoding="utf-8",
            )

            cases = eb.load_optional_class_demo_cases(manifest_path=manifest_path)

        self.assertEqual(cases[0].input_path, recording_path)

    def test_resolve_manifest_relative_path_normalizes_backslashes_before_building_path(
        self,
    ) -> None:
        manifest_path = Path("sample_data") / "benchmark_cases.json"
        seen_raw_paths = []

        class FakePath:
            def __init__(self, raw_path: str) -> None:
                seen_raw_paths.append(raw_path)
                self._raw_path = raw_path

            def is_absolute(self) -> bool:
                return False

            def __fspath__(self) -> str:
                return self._raw_path

        with mock.patch.object(eb, "Path", FakePath):
            resolved = eb.resolve_manifest_relative_path(
                manifest_path, "recordings\\demo.m4a"
            )

        self.assertEqual(seen_raw_paths, ["recordings/demo.m4a"])
        self.assertEqual(
            resolved, (manifest_path.parent / "recordings" / "demo.m4a").resolve()
        )


class CaseEvaluationTests(unittest.TestCase):
    def test_evaluate_case_match_scores_target_and_local_window(self) -> None:
        case = eb.BenchmarkCase(
            name="anchored",
            description="Anchored case",
            input_path=Path("sample_data\\audio_samples\\samples_that_match\\Fan\\fan.wav"),
            region="EI",
            comparison_dates=("2026-04-20",),
            expected_target_utc="2026-04-20T16:36:05+00:00",
            target_tolerance_sec=180.0,
            preferred_window=eb.LocalTimeWindow(
                local_date="2026-04-20",
                start_time="12:00",
                end_time="13:00",
                timezone_name="America/New_York",
            ),
        )

        metrics = eb.evaluate_case_match(
            case,
            {
                "ref_start_utc": "2026-04-20T16:38:00+00:00",
                "correlation": 0.71,
                "threshold_coverage": 0.58,
                "composite_score": 0.63,
            },
        )

        self.assertEqual(metrics["best_match_utc"], "2026-04-20T16:38:00+00:00")
        self.assertEqual(metrics["delta_to_target_seconds"], 115.0)
        self.assertTrue(metrics["within_target_tolerance"])
        self.assertTrue(metrics["preferred_window_hit"])
        self.assertTrue(metrics["expected_date_hit"])


class ReferencePreparationTests(unittest.TestCase):
    def test_prepare_reference_segments_searches_all_dates_and_reuses_region_cache(self) -> None:
        first_case = eb.BenchmarkCase(
            name="first",
            description="First case",
            input_path=Path("sample_data\\audio_samples\\first.wav"),
            region="EI",
            comparison_dates=("2026-04-20",),
        )
        second_case = eb.BenchmarkCase(
            name="second",
            description="Second case",
            input_path=Path("sample_data\\audio_samples\\second.wav"),
            region="EI",
            comparison_dates=("2026-05-11",),
        )
        sentinel_segments = [mock.sentinel.segment]
        cache = {}

        with (
            mock.patch.object(eb.ec, "load_grid_data", return_value=mock.sentinel.grid) as load_grid_data,
            mock.patch.object(
                eb.ec,
                "resample_grid_segments",
                return_value=sentinel_segments,
            ) as resample_grid_segments,
        ):
            first = eb.prepare_reference_segments(first_case, Path("source_data\\grid_data"), cache)
            second = eb.prepare_reference_segments(second_case, Path("source_data\\grid_data"), cache)

        self.assertIs(first, sentinel_segments)
        self.assertIs(second, sentinel_segments)
        load_grid_data.assert_called_once_with(Path("source_data\\grid_data"), "EI", None)
        resample_grid_segments.assert_called_once_with(mock.sentinel.grid)


class SummaryWritingTests(unittest.TestCase):
    def test_write_summary_files_outputs_machine_readable_json_and_csv(self) -> None:
        summary = {
            "generated_at_utc": "2026-05-24T00:00:00+00:00",
            "results": [
                {
                    "case": "fan-iphone-apr20",
                    "technique": "baseline",
                    "top_composite_score": 0.63,
                    "top_correlation": 0.71,
                    "top_threshold_coverage": 0.58,
                    "best_match_utc": "2026-04-20T16:36:05+00:00",
                    "expected_date_hit": True,
                    "preferred_window_hit": True,
                    "delta_to_target_seconds": 0.0,
                }
            ],
        }

        with tempfile.TemporaryDirectory() as tmpdir:
            json_path, csv_path = eb.write_summary_files(Path(tmpdir), summary)

            payload = json.loads(json_path.read_text(encoding="utf-8"))
            self.assertEqual(payload["results"][0]["case"], "fan-iphone-apr20")

            with csv_path.open("r", encoding="utf-8", newline="") as handle:
                rows = list(csv.DictReader(handle))
            self.assertEqual(rows[0]["technique"], "baseline")
            self.assertEqual(rows[0]["best_match_utc"], "2026-04-20T16:36:05+00:00")

    def test_print_run_summary_omits_summary_paths_when_not_present(self) -> None:
        summary = {
            "results": [
                {
                    "case": "fan-iphone-apr20",
                    "technique": "baseline",
                    "top_composite_score": 0.63,
                    "top_correlation": 0.71,
                    "top_threshold_coverage": 0.58,
                    "preferred_window_hit": True,
                }
            ]
        }

        buffer = io.StringIO()
        with redirect_stdout(buffer):
            eb.print_run_summary(summary)

        output = buffer.getvalue()
        self.assertIn("Benchmark results: 1 rows", output)
        self.assertNotIn("JSON summary:", output)
        self.assertNotIn("CSV summary:", output)


class RunBenchmarkTests(unittest.TestCase):
    def test_run_benchmark_executes_case_technique_matrix(self) -> None:
        cases = [
            eb.BenchmarkCase(
                name="case-a",
                description="Case A",
                input_path=Path("sample_data\\audio_samples\\a.wav"),
                region="EI",
                comparison_dates=("2026-04-20",),
            ),
            eb.BenchmarkCase(
                name="case-b",
                description="Case B",
                input_path=Path("sample_data\\audio_samples\\b.wav"),
                region="EI",
                comparison_dates=("2026-04-21",),
            ),
        ]
        techniques = [
            eb.TechniquePreset("baseline", "Baseline", {}),
            eb.TechniquePreset("dp", "Dynamic-programming tracker", {"tracking_mode": "dp"}),
        ]
        extract_calls = []
        compare_calls = []

        def fake_extract(case, technique, artifact_dir):
            extract_calls.append((case.name, technique.name, artifact_dir.name))
            return eb.ExtractionArtifact(
                trace_path=artifact_dir / "trace.csv",
                query_length_seconds=123,
            )

        def fake_compare(case, technique, artifact_dir, extraction, grid_dir, threshold, top_n):
            compare_calls.append((case.name, technique.name, extraction.query_length_seconds, grid_dir))
            return eb.ComparisonArtifact(
                results_path=artifact_dir / "results.json",
                matches=[
                    {
                        "ref_start_utc": "2026-04-20T16:36:05+00:00",
                        "correlation": 0.7,
                        "threshold_coverage": 0.6,
                        "composite_score": 0.64,
                    }
                ],
            )

        with tempfile.TemporaryDirectory() as tmpdir:
            summary = eb.run_benchmark(
                cases=cases,
                techniques=techniques,
                output_dir=Path(tmpdir),
                grid_dir=Path("source_data\\grid_data"),
                extract_runner=fake_extract,
                compare_runner=fake_compare,
                write_summary=False,
            )

        self.assertEqual(len(summary["results"]), 4)
        self.assertEqual(
            extract_calls,
            [
                ("case-a", "baseline", "case-a"),
                ("case-a", "dp", "case-a"),
                ("case-b", "baseline", "case-b"),
                ("case-b", "dp", "case-b"),
            ],
        )
        self.assertEqual(len(compare_calls), 4)

    def test_run_benchmark_records_extract_failures_and_continues(self) -> None:
        cases = [
            eb.BenchmarkCase(
                name="missing-ffmpeg",
                description="Needs ffmpeg",
                input_path=Path("sample_data\\audio_samples\\a.m4a"),
                region="EI",
                comparison_dates=("2026-04-20",),
            ),
            eb.BenchmarkCase(
                name="wav-ok",
                description="Direct wav",
                input_path=Path("sample_data\\audio_samples\\b.wav"),
                region="EI",
                comparison_dates=("2026-04-20",),
            ),
        ]
        techniques = [eb.TechniquePreset("baseline", "Baseline", {})]

        def fake_extract(case, technique, artifact_dir):
            if case.name == "missing-ffmpeg":
                raise RuntimeError("ffmpeg not found")
            return eb.ExtractionArtifact(
                trace_path=artifact_dir / "trace.csv",
                query_length_seconds=42,
            )

        def fake_compare(case, technique, artifact_dir, extraction, grid_dir, threshold, top_n):
            return eb.ComparisonArtifact(
                results_path=artifact_dir / "results.json",
                matches=[],
            )

        with tempfile.TemporaryDirectory() as tmpdir:
            summary = eb.run_benchmark(
                cases=cases,
                techniques=techniques,
                output_dir=Path(tmpdir),
                grid_dir=Path("source_data\\grid_data"),
                extract_runner=fake_extract,
                compare_runner=fake_compare,
                write_summary=False,
            )

        self.assertEqual(len(summary["results"]), 2)
        failed_row = next(row for row in summary["results"] if row["case"] == "missing-ffmpeg")
        ok_row = next(row for row in summary["results"] if row["case"] == "wav-ok")
        self.assertEqual(failed_row["status"], "extract_failed")
        self.assertEqual(failed_row["error_stage"], "extract")
        self.assertIn("ffmpeg not found", failed_row["error_message"])
        self.assertEqual(ok_row["status"], "ok")
        self.assertIsNone(ok_row["error_message"])


class MainTests(unittest.TestCase):
    def test_main_returns_error_code_for_unknown_case_names(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            buffer = io.StringIO()
            with redirect_stdout(buffer):
                exit_code = eb.main(["--grid-dir", tmpdir, "--cases", "missing-case"])

        self.assertEqual(exit_code, 1)
        self.assertIn("Unknown benchmark case(s): missing-case", buffer.getvalue())


if __name__ == "__main__":
    unittest.main()
