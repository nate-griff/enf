import unittest

import numpy as np
import pandas as pd

import enf_compare as ec


class ResampleGridSegmentsTests(unittest.TestCase):
    def test_splits_large_gaps_instead_of_interpolating_through_them(self) -> None:
        grid = pd.DataFrame(
            {
                "timestamp_utc": pd.to_datetime(
                    [
                        "2026-04-20T16:36:05Z",
                        "2026-04-20T16:36:06Z",
                        "2026-04-20T18:00:00Z",
                        "2026-04-20T18:00:01Z",
                    ],
                    utc=True,
                ),
                "frequency_hz": [59.99, 60.00, 60.01, 60.02],
            }
        )

        segments = ec.resample_grid_segments(grid, max_gap_seconds=5.0)

        self.assertEqual(len(segments), 2)
        self.assertEqual(list(segments[0].index), list(pd.to_datetime(["2026-04-20T16:36:05Z", "2026-04-20T16:36:06Z"], utc=True)))
        self.assertEqual(list(segments[1].index), list(pd.to_datetime(["2026-04-20T18:00:00Z", "2026-04-20T18:00:01Z"], utc=True)))


class StableCorrelationTests(unittest.TestCase):
    def test_fft_cross_correlation_matches_direct_pearson_for_low_variance_windows(self) -> None:
        rng = np.random.default_rng(0)
        query = 60.0 + rng.normal(0.0, 0.015, size=340)
        reference = np.linspace(59.9826, 59.9832, 5000)

        correlations = ec.fft_cross_correlation(query, reference)
        direct = np.array(
            [
                np.corrcoef(query, reference[offset : offset + len(query)])[0, 1]
                for offset in range(len(reference) - len(query) + 1)
            ]
        )

        np.testing.assert_allclose(correlations, direct, atol=1e-6)


class SegmentComparisonTests(unittest.TestCase):
    def test_compare_against_reference_segments_prefers_real_data_segment(self) -> None:
        query = np.array([59.99, 60.00, 60.01, 60.02], dtype=np.float64)
        grid = pd.DataFrame(
            {
                "timestamp_utc": pd.to_datetime(
                    [
                        "2026-04-20T00:00:00Z",
                        "2026-04-20T00:00:01Z",
                        "2026-04-20T12:00:00Z",
                        "2026-04-20T12:00:01Z",
                        "2026-04-20T12:00:02Z",
                        "2026-04-20T12:00:03Z",
                    ],
                    utc=True,
                ),
                "frequency_hz": [60.02, 60.01, 59.99, 60.00, 60.01, 60.02],
            }
        )

        segments = ec.resample_grid_segments(grid, max_gap_seconds=5.0)
        results = ec.compare_against_reference_segments(
            query,
            segments,
            threshold=0.005,
            top_n=1,
        )

        self.assertEqual(len(results), 1)
        self.assertEqual(results[0]["ref_start_utc"], pd.Timestamp("2026-04-20T12:00:00Z"))

    def test_compare_against_reference_segments_backfills_distinct_results(self) -> None:
        query = np.array([0.0, 1.0, 2.0, 3.0], dtype=np.float64)
        segment = ec.ReferenceSegment(
            index=pd.date_range("2026-04-20T00:00:00Z", periods=10, freq="1s"),
            values=np.arange(10, dtype=np.float64),
        )

        results = ec.compare_against_reference_segments(
            query,
            [segment],
            threshold=10.0,
            top_n=2,
            min_separation_sec=5.0,
        )

        self.assertEqual(len(results), 2)
        gap = abs(
            (pd.Timestamp(results[0]["ref_start_utc"]) - pd.Timestamp(results[1]["ref_start_utc"]))
            .total_seconds()
        )
        self.assertGreater(gap, 5.0)


class DistinctMatchSelectionTests(unittest.TestCase):
    def test_select_distinct_matches_keeps_best_then_skips_nearby_candidates(self) -> None:
        matches = [
            {"ref_start_utc": pd.Timestamp("2026-04-20T16:36:05Z"), "composite_score": 0.90},
            {"ref_start_utc": pd.Timestamp("2026-04-20T16:36:06Z"), "composite_score": 0.89},
            {"ref_start_utc": pd.Timestamp("2026-04-20T16:36:07Z"), "composite_score": 0.88},
            {"ref_start_utc": pd.Timestamp("2026-04-20T16:36:12Z"), "composite_score": 0.85},
            {"ref_start_utc": pd.Timestamp("2026-04-20T16:36:20Z"), "composite_score": 0.80},
        ]

        selected = ec.select_distinct_matches(matches, top_n=3, min_separation_sec=5.0)

        self.assertEqual(
            [pd.Timestamp(match["ref_start_utc"]) for match in selected],
            [
                pd.Timestamp("2026-04-20T16:36:05Z"),
                pd.Timestamp("2026-04-20T16:36:12Z"),
                pd.Timestamp("2026-04-20T16:36:20Z"),
            ],
        )


class ParseArgsTests(unittest.TestCase):
    def test_parse_args_defaults_min_separation_to_five_seconds(self) -> None:
        args = ec.parse_args(
            [
                "--trace",
                "trace.csv",
                "--grid-dir",
                "grid",
                "--region",
                "EI",
            ]
        )

        self.assertEqual(args.min_separation_sec, 5.0)


if __name__ == "__main__":
    unittest.main()
