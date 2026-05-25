"""Benchmark ENF extraction techniques against built-in sample cases."""
from __future__ import annotations

import argparse
import csv
import json
import os
from dataclasses import asdict, dataclass, field
from datetime import datetime, time, timezone
from pathlib import Path
from typing import Any, Callable
from zoneinfo import ZoneInfo

import pandas as pd

import enf_compare as ec
import enf_extract as ee

REPO_ROOT = Path(__file__).resolve().parent
DEFAULT_GRID_DIR = REPO_ROOT / "source_data" / "grid_data"
OPTIONAL_CLASS_DEMO_MANIFEST = (
    REPO_ROOT / "sample_data" / "audio_samples" / "class_demo" / "benchmark_cases.json"
)
AUDIO_EXTENSIONS = {".wav", ".flac"}


@dataclass(frozen=True)
class LocalTimeWindow:
    local_date: str
    start_time: str
    end_time: str
    timezone_name: str = "America/New_York"


@dataclass(frozen=True)
class BenchmarkCase:
    name: str
    description: str
    input_path: Path
    region: str
    comparison_dates: tuple[str, ...]
    expected_target_utc: str | None = None
    target_tolerance_sec: float | None = None
    preferred_window: LocalTimeWindow | None = None
    notes: str = ""


@dataclass(frozen=True)
class TechniquePreset:
    name: str
    description: str
    extract_kwargs: dict[str, Any]


@dataclass(frozen=True)
class ExtractionArtifact:
    trace_path: Path
    query_length_seconds: int
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class ComparisonArtifact:
    results_path: Path
    matches: list[dict[str, Any]]
    metadata: dict[str, Any] = field(default_factory=dict)


def repo_relative_path(relative_path: str) -> Path:
    return REPO_ROOT.joinpath(*relative_path.split("\\"))


def normalize_manifest_path(raw_path: str) -> str:
    return raw_path.replace("\\", "/")


def resolve_manifest_relative_path(manifest_path: Path, raw_path: str) -> Path:
    candidate = Path(normalize_manifest_path(raw_path))
    if candidate.is_absolute():
        return candidate
    return (manifest_path.parent / candidate).resolve()


def build_default_cases() -> list[BenchmarkCase]:
    return [
        BenchmarkCase(
            name="fan-iphone-apr20",
            description="Known matching iPhone fan recording with a strong anchored April 20 EI match.",
            input_path=repo_relative_path(
                "sample_data\\audio_samples\\samples_that_match\\Fan\\fan_on_iphone_apr20at11_36.wav"
            ),
            region="EI",
            comparison_dates=("2026-04-20",),
            expected_target_utc="2026-04-20T16:36:05+00:00",
            target_tolerance_sec=180.0,
            notes="Target UTC comes from the tracked exact-match artifact under Fan\\close-neighbors-rejected.",
        ),
        BenchmarkCase(
            name="fan-ipad-apr23",
            description="Known matching iPad fan recording with a strong anchored April 23 EI match.",
            input_path=repo_relative_path(
                "sample_data\\audio_samples\\samples_that_match\\fan2\\fan_on_ipad_apr23at11_18.m4a"
            ),
            region="EI",
            comparison_dates=("2026-04-23",),
            expected_target_utc="2026-04-23T16:18:37+00:00",
            target_tolerance_sec=180.0,
            notes="Target UTC comes from the tracked default result artifact under fan2\\default.",
        ),
        BenchmarkCase(
            name="bose-near-fan-may11",
            description="Weak May 11 headphone-near-fan recording evaluated as a same-day daytime-window case.",
            input_path=repo_relative_path(
                "sample_data\\audio_samples\\samples_that_dont_match\\bose_headphones_near_fan\\Bose_headphones_next_to_fan_May11at2_24.m4a"
            ),
            region="EI",
            comparison_dates=("2026-05-11",),
            preferred_window=LocalTimeWindow(
                local_date="2026-05-11",
                start_time="10:00",
                end_time="16:00",
                timezone_name="America/New_York",
            ),
            notes="Date and daytime window follow the repo research prompt guidance for difficult EI samples.",
        ),
        BenchmarkCase(
            name="bathroom-exhaust-may11",
            description="Weak May 11 bathroom exhaust recording evaluated as a same-day daytime-window case.",
            input_path=repo_relative_path(
                "sample_data\\audio_samples\\samples_that_dont_match\\macbook_pro_bathroom_exhaust_fan\\Exhaust_fan_in_bathroom_on_macbook_May11at2_26.m4a"
            ),
            region="EI",
            comparison_dates=("2026-05-11",),
            preferred_window=LocalTimeWindow(
                local_date="2026-05-11",
                start_time="10:00",
                end_time="16:00",
                timezone_name="America/New_York",
            ),
            notes="Date and daytime window follow the repo research prompt guidance for difficult EI samples.",
        ),
        BenchmarkCase(
            name="microwave-apr20",
            description="Date-window EI microwave recording from April 20.",
            input_path=repo_relative_path(
                "sample_data\\audio_samples\\samples_that_dont_match\\Microwave\\microwave_hum_apr20.m4a"
            ),
            region="EI",
            comparison_dates=("2026-04-20",),
            preferred_window=LocalTimeWindow(
                local_date="2026-04-20",
                start_time="10:00",
                end_time="16:00",
                timezone_name="America/New_York",
            ),
            notes="Date-only daytime window case from the benchmark notes in research\\research_prompt.md.",
        ),
        BenchmarkCase(
            name="transformer-apr23",
            description="Date-window EI outdoor transformer recording from April 23.",
            input_path=repo_relative_path(
                "sample_data\\audio_samples\\samples_that_dont_match\\Transformer\\Outdoor_transformer_station_apr23.m4a"
            ),
            region="EI",
            comparison_dates=("2026-04-23",),
            preferred_window=LocalTimeWindow(
                local_date="2026-04-23",
                start_time="10:00",
                end_time="16:00",
                timezone_name="America/New_York",
            ),
            notes="Date-only daytime window case from the benchmark notes in research\\research_prompt.md.",
        ),
        BenchmarkCase(
            name="dfor661-apr20",
            description="Date-only EI classroom recording from April 20.",
            input_path=repo_relative_path(
                "sample_data\\audio_samples\\samples_that_dont_match\\DFOR661\\Class_apr20.m4a"
            ),
            region="EI",
            comparison_dates=("2026-04-20",),
            notes="Tracked date-only case without a narrower preferred window.",
        ),
    ]


def build_technique_presets() -> list[TechniquePreset]:
    return [
        TechniquePreset(
            "baseline",
            "Current default extractor: harmonic 2 with peak tracking and FFT spectra.",
            {},
        ),
        TechniquePreset(
            "dp",
            "Dynamic-programming ridge tracking on the current single-harmonic pipeline.",
            {"tracking_mode": "dp"},
        ),
        TechniquePreset(
            "multitaper",
            "Current default tracker with the new multitaper spectrum estimator.",
            {"spectrum_estimator": "multitaper"},
        ),
        TechniquePreset(
            "dp-multitaper",
            "Dynamic-programming tracking with multitaper spectra.",
            {"tracking_mode": "dp", "spectrum_estimator": "multitaper"},
        ),
        TechniquePreset(
            "snr-multi",
            "SNR-weighted multi-harmonic extraction across harmonics 1, 2, and 3.",
            {
                "multi_harmonic": True,
                "harmonics": [1, 2, 3],
                "harmonic_fusion": "snr-weighted",
            },
        ),
        TechniquePreset(
            "dp-multitaper-snr-multi",
            "Combined DP + multitaper + SNR-weighted multi-harmonic preset.",
            {
                "multi_harmonic": True,
                "harmonics": [1, 2, 3],
                "harmonic_fusion": "snr-weighted",
                "tracking_mode": "dp",
                "spectrum_estimator": "multitaper",
            },
        ),
    ]


DEFAULT_BENCHMARK_CASES = build_default_cases()
DEFAULT_TECHNIQUE_PRESETS = build_technique_presets()


def load_optional_class_demo_cases(
    manifest_path: Path = OPTIONAL_CLASS_DEMO_MANIFEST,
) -> list[BenchmarkCase]:
    if not manifest_path.is_file():
        return []

    payload = json.loads(manifest_path.read_text(encoding="utf-8"))
    cases: list[BenchmarkCase] = []
    for raw_case in payload:
        preferred_window = raw_case.get("preferred_window")
        cases.append(
            BenchmarkCase(
                name=raw_case["name"],
                description=raw_case["description"],
                input_path=resolve_manifest_relative_path(
                    manifest_path, raw_case["input_path"]
                ),
                region=raw_case.get("region", "EI"),
                comparison_dates=tuple(raw_case["comparison_dates"]),
                expected_target_utc=raw_case.get("expected_target_utc"),
                target_tolerance_sec=raw_case.get("target_tolerance_sec"),
                preferred_window=(
                    LocalTimeWindow(**preferred_window)
                    if preferred_window is not None
                    else None
                ),
                notes=raw_case.get("notes", ""),
            )
        )
    return cases


def resolve_benchmark_cases(
    case_names: list[str] | None = None,
    include_class_demo: bool = False,
) -> list[BenchmarkCase]:
    all_cases = list(DEFAULT_BENCHMARK_CASES)
    if include_class_demo:
        all_cases.extend(load_optional_class_demo_cases())

    known_cases = {case.name: case for case in all_cases}
    if case_names is None:
        return [case for case in all_cases if case.input_path.is_file()]

    unknown = [name for name in case_names if name not in known_cases]
    if unknown:
        raise ValueError(f"Unknown benchmark case(s): {', '.join(unknown)}")

    selected_cases = [known_cases[name] for name in case_names]
    missing_inputs = [case.name for case in selected_cases if not case.input_path.is_file()]
    if missing_inputs:
        raise ValueError(
            f"Missing benchmark input file(s): {', '.join(missing_inputs)}"
        )
    return selected_cases


def resolve_technique_presets(
    technique_names: list[str] | None = None,
) -> list[TechniquePreset]:
    available = {preset.name: preset for preset in DEFAULT_TECHNIQUE_PRESETS}
    if technique_names is None:
        return list(DEFAULT_TECHNIQUE_PRESETS)

    unknown = [name for name in technique_names if name not in available]
    if unknown:
        raise ValueError(f"Unknown technique preset(s): {', '.join(unknown)}")
    return [available[name] for name in technique_names]


def parse_csv_list(value: str | None) -> list[str] | None:
    if value is None:
        return None
    items = [part.strip() for part in value.split(",") if part.strip()]
    return items or None


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Benchmark ENF extraction techniques against built-in sample cases."
    )
    parser.add_argument(
        "--grid-dir",
        type=Path,
        default=DEFAULT_GRID_DIR,
        help="Directory containing grid CSV files (default: source_data\\grid_data).",
    )
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=None,
        help="Directory to write benchmark artifacts and summaries.",
    )
    parser.add_argument(
        "--cases",
        default=None,
        help="Comma-separated benchmark case names. Defaults to the built-in tracked suite.",
    )
    parser.add_argument(
        "--techniques",
        default=None,
        help="Comma-separated technique preset names. Defaults to all built-in presets.",
    )
    parser.add_argument(
        "--include-class-demo",
        action="store_true",
        help="Include optional class_demo cases from sample_data\\audio_samples\\class_demo\\benchmark_cases.json when present.",
    )
    parser.add_argument(
        "--threshold",
        type=float,
        default=0.01,
        help="Threshold passed to enf_compare.py scoring (default: 0.01).",
    )
    parser.add_argument(
        "--top-n",
        type=int,
        default=5,
        help="How many top distinct matches to keep per run (default: 5).",
    )
    parser.add_argument(
        "--list-cases",
        action="store_true",
        help="Print available benchmark cases and exit.",
    )
    parser.add_argument(
        "--list-techniques",
        action="store_true",
        help="Print available technique presets and exit.",
    )
    return parser.parse_args(argv)


def timestamp_to_utc(value: str | pd.Timestamp) -> pd.Timestamp:
    ts = pd.Timestamp(value)
    if ts.tzinfo is None:
        return ts.tz_localize("UTC")
    return ts.tz_convert("UTC")


def parse_wall_clock(value: str) -> time:
    parts = value.split(":")
    if len(parts) == 2:
        hours, minutes = parts
        seconds = "00"
    elif len(parts) == 3:
        hours, minutes, seconds = parts
    else:
        raise ValueError(f"Invalid wall-clock time: {value}")
    return time(int(hours), int(minutes), int(seconds))


def evaluate_case_match(
    case: BenchmarkCase,
    best_match: dict[str, Any] | None,
) -> dict[str, Any]:
    metrics: dict[str, Any] = {
        "best_match_utc": None,
        "expected_date_hit": None,
        "preferred_window_hit": None,
        "delta_to_target_seconds": None,
        "within_target_tolerance": None,
    }
    if best_match is None:
        return metrics

    best_match_utc = timestamp_to_utc(best_match["ref_start_utc"])
    metrics["best_match_utc"] = best_match_utc.isoformat()
    metrics["expected_date_hit"] = (
        best_match_utc.strftime("%Y-%m-%d") in case.comparison_dates
        if case.comparison_dates
        else None
    )

    if case.expected_target_utc is not None:
        target = timestamp_to_utc(case.expected_target_utc)
        delta_seconds = abs((best_match_utc - target).total_seconds())
        metrics["delta_to_target_seconds"] = float(delta_seconds)
        if case.target_tolerance_sec is not None:
            metrics["within_target_tolerance"] = delta_seconds <= case.target_tolerance_sec

    if case.preferred_window is not None:
        local_timestamp = best_match_utc.tz_convert(
            ZoneInfo(case.preferred_window.timezone_name)
        )
        start_time = parse_wall_clock(case.preferred_window.start_time)
        end_time = parse_wall_clock(case.preferred_window.end_time)
        metrics["preferred_window_hit"] = (
            local_timestamp.strftime("%Y-%m-%d") == case.preferred_window.local_date
            and start_time <= local_timestamp.timetz().replace(tzinfo=None) <= end_time
        )

    return metrics


def serialize_case(case: BenchmarkCase) -> dict[str, Any]:
    return {
        "name": case.name,
        "description": case.description,
        "input_path": str(case.input_path),
        "region": case.region,
        "comparison_dates": list(case.comparison_dates),
        "expected_target_utc": case.expected_target_utc,
        "target_tolerance_sec": case.target_tolerance_sec,
        "preferred_window": asdict(case.preferred_window) if case.preferred_window else None,
        "notes": case.notes,
    }


def serialize_technique(preset: TechniquePreset) -> dict[str, Any]:
    return {
        "name": preset.name,
        "description": preset.description,
        "extract_kwargs": preset.extract_kwargs,
    }


def output_timestamp() -> str:
    return datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")


def default_output_dir() -> Path:
    return REPO_ROOT / "benchmark_runs" / output_timestamp()


def extract_trace_for_case(
    case: BenchmarkCase,
    technique: TechniquePreset,
    artifact_dir: Path,
) -> ExtractionArtifact:
    artifact_dir.mkdir(parents=True, exist_ok=True)
    trace_path = artifact_dir / f"{technique.name}_trace.csv"
    extract_kwargs = technique.extract_kwargs
    tmp_wav: str | None = None

    try:
        if case.input_path.suffix.lower() not in AUDIO_EXTENSIONS:
            tmp_wav = ee.extract_audio_from_video(str(case.input_path))
            wav_path = tmp_wav
        else:
            wav_path = str(case.input_path)

        sr, signal = ee.load_audio(wav_path)

        nominal = float(extract_kwargs.get("nominal", 60.0))
        bandwidth = float(extract_kwargs.get("bandwidth", 0.5))
        frame_sec = float(extract_kwargs.get("frame_sec", 1.0))
        overlap = float(extract_kwargs.get("overlap", 0.5))
        pad_factor = int(extract_kwargs.get("pad_factor", 16))
        median_window = int(extract_kwargs.get("median_window", 3))
        tracking_mode = str(extract_kwargs.get("tracking_mode", "peak"))
        spectrum_estimator = str(extract_kwargs.get("spectrum_estimator", "fft"))

        confidence_scores = None
        if extract_kwargs.get("multi_harmonic", False):
            harmonics = list(extract_kwargs.get("harmonics", [1, 2, 3]))
            supported, unsupported = ee.split_supported_harmonics(
                sr, nominal, bandwidth, harmonics
            )
            if unsupported:
                raise RuntimeError(
                    "Unsupported harmonic preset for benchmark: "
                    + ee.format_unsupported_harmonics(unsupported, sr)
                )
            timestamps, freq_estimates, confidence_scores, _ = ee.extract_multi_harmonic(
                signal=signal,
                sr=sr,
                nominal=nominal,
                bandwidth=bandwidth,
                frame_sec=frame_sec,
                overlap=overlap,
                pad_factor=pad_factor,
                harmonics=supported,
                median_window=median_window,
                fusion_method=str(extract_kwargs.get("harmonic_fusion", "weighted")),
                tracking_mode=tracking_mode,
                spectrum_estimator=spectrum_estimator,
            )
        else:
            harmonic = int(extract_kwargs.get("harmonic", 2))
            supported, unsupported = ee.split_supported_harmonics(
                sr, nominal, bandwidth, [harmonic]
            )
            if not supported:
                raise RuntimeError(
                    "Unsupported harmonic preset for benchmark: "
                    + ee.format_unsupported_harmonics(unsupported, sr)
                )
            extracted = ee.extract_harmonic_trace(
                signal=signal,
                sr=sr,
                nominal=nominal,
                bandwidth=bandwidth,
                frame_sec=frame_sec,
                overlap=overlap,
                pad_factor=pad_factor,
                harmonic=harmonic,
                tracking_mode=tracking_mode,
                spectrum_estimator=spectrum_estimator,
                return_quality=bool(extract_kwargs.get("confidence_output", False)),
            )
            if extract_kwargs.get("confidence_output", False):
                timestamps, freq_estimates, confidence_scores = extracted
            else:
                timestamps, freq_estimates = extracted
            freq_estimates = ee.apply_median_filter(freq_estimates, median_window)

        ee.write_csv(
            str(trace_path),
            timestamps,
            freq_estimates,
            confidence_scores=confidence_scores,
        )
        return ExtractionArtifact(
            trace_path=trace_path,
            query_length_seconds=int(len(freq_estimates)),
            metadata={"estimate_count": int(len(freq_estimates))},
        )
    finally:
        if tmp_wav and os.path.exists(tmp_wav):
            os.remove(tmp_wav)


def prepare_reference_segments(
    case: BenchmarkCase,
    grid_dir: Path,
    reference_cache: dict[tuple[str, tuple[str, ...]], list[ec.ReferenceSegment]],
) -> list[ec.ReferenceSegment]:
    cache_key = (case.region, case.comparison_dates)
    if cache_key not in reference_cache:
        grid = ec.load_grid_data(grid_dir, case.region, list(case.comparison_dates))
        reference_cache[cache_key] = ec.resample_grid_segments(grid)
    return reference_cache[cache_key]


def compare_trace_for_case(
    case: BenchmarkCase,
    technique: TechniquePreset,
    artifact_dir: Path,
    extraction: ExtractionArtifact,
    grid_dir: Path,
    threshold: float,
    top_n: int,
    reference_cache: dict[tuple[str, tuple[str, ...]], list[ec.ReferenceSegment]],
) -> ComparisonArtifact:
    results_path = artifact_dir / f"{technique.name}_results.json"
    query = ec.load_trace(extraction.trace_path)
    reference_segments = prepare_reference_segments(case, grid_dir, reference_cache)
    matches = ec.compare_against_reference_segments(
        query=query,
        reference_segments=reference_segments,
        threshold=threshold,
        top_n=top_n,
    )
    ec.write_json(
        results=matches,
        query_length=len(query),
        trace_path=extraction.trace_path,
        region=case.region,
        threshold=threshold,
        output_path=results_path,
    )
    return ComparisonArtifact(
        results_path=results_path,
        matches=matches,
    )


def build_result_row(
    case: BenchmarkCase,
    technique: TechniquePreset,
    extraction: ExtractionArtifact,
    comparison: ComparisonArtifact,
) -> dict[str, Any]:
    best_match = comparison.matches[0] if comparison.matches else None
    metrics = evaluate_case_match(case, best_match)
    return {
        "case": case.name,
        "technique": technique.name,
        "input_path": str(case.input_path),
        "region": case.region,
        "comparison_dates": ",".join(case.comparison_dates),
        "query_length_seconds": extraction.query_length_seconds,
        "top_composite_score": (
            round(float(best_match["composite_score"]), 6) if best_match else None
        ),
        "top_correlation": (
            round(float(best_match["correlation"]), 6) if best_match else None
        ),
        "top_threshold_coverage": (
            round(float(best_match["threshold_coverage"]), 6) if best_match else None
        ),
        "best_match_utc": metrics["best_match_utc"],
        "expected_date_hit": metrics["expected_date_hit"],
        "preferred_window_hit": metrics["preferred_window_hit"],
        "delta_to_target_seconds": metrics["delta_to_target_seconds"],
        "within_target_tolerance": metrics["within_target_tolerance"],
        "trace_path": str(extraction.trace_path),
        "results_path": str(comparison.results_path),
        "status": "ok",
        "error_stage": None,
        "error_message": None,
    }


def build_failed_result_row(
    case: BenchmarkCase,
    technique: TechniquePreset,
    stage: str,
    error: Exception,
    *,
    trace_path: Path | None = None,
    results_path: Path | None = None,
) -> dict[str, Any]:
    return {
        "case": case.name,
        "technique": technique.name,
        "input_path": str(case.input_path),
        "region": case.region,
        "comparison_dates": ",".join(case.comparison_dates),
        "query_length_seconds": None,
        "top_composite_score": None,
        "top_correlation": None,
        "top_threshold_coverage": None,
        "best_match_utc": None,
        "expected_date_hit": None,
        "preferred_window_hit": None,
        "delta_to_target_seconds": None,
        "within_target_tolerance": None,
        "trace_path": str(trace_path) if trace_path is not None else None,
        "results_path": str(results_path) if results_path is not None else None,
        "status": f"{stage}_failed",
        "error_stage": stage,
        "error_message": str(error),
    }


def write_summary_files(output_dir: Path, summary: dict[str, Any]) -> tuple[Path, Path]:
    output_dir.mkdir(parents=True, exist_ok=True)
    json_path = output_dir / "benchmark_summary.json"
    csv_path = output_dir / "benchmark_summary.csv"

    with json_path.open("w", encoding="utf-8") as handle:
        json.dump(summary, handle, indent=2)

    fields = [
        "case",
        "technique",
        "input_path",
        "region",
        "comparison_dates",
        "query_length_seconds",
        "top_composite_score",
        "top_correlation",
        "top_threshold_coverage",
        "best_match_utc",
        "expected_date_hit",
        "preferred_window_hit",
        "delta_to_target_seconds",
        "within_target_tolerance",
        "trace_path",
        "results_path",
        "status",
        "error_stage",
        "error_message",
    ]
    with csv_path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields)
        writer.writeheader()
        for row in summary["results"]:
            writer.writerow({field: row.get(field) for field in fields})

    return json_path, csv_path


def print_available_cases(include_class_demo: bool) -> None:
    for case in resolve_benchmark_cases(include_class_demo=include_class_demo):
        print(f"{case.name}: {case.description}")


def print_available_techniques() -> None:
    for preset in resolve_technique_presets():
        print(f"{preset.name}: {preset.description}")


def print_run_summary(summary: dict[str, Any]) -> None:
    print()
    print(f"Benchmark results: {len(summary['results'])} rows")
    summary_json = summary.get("summary_json")
    summary_csv = summary.get("summary_csv")
    if summary_json is not None:
        print(f"JSON summary: {summary_json}")
    if summary_csv is not None:
        print(f"CSV summary:  {summary_csv}")
    print()
    header = (
        f"{'Case':<24} {'Technique':<28} {'Status':<16} {'Score':>8} {'Corr':>8} {'Cover':>8} {'Window':>8}"
    )
    print(header)
    print("-" * len(header))
    for row in summary["results"]:
        print(
            f"{row['case']:<24} {row['technique']:<28} {row.get('status', 'ok'):<16} "
            f"{(row['top_composite_score'] if row['top_composite_score'] is not None else float('nan')):>8.4f} "
            f"{(row['top_correlation'] if row['top_correlation'] is not None else float('nan')):>8.4f} "
            f"{(row['top_threshold_coverage'] if row['top_threshold_coverage'] is not None else float('nan')):>8.4f} "
            f"{str(row['preferred_window_hit']):>8}"
        )
        if row.get("error_message"):
            print(f"{'':<24} {'':<28} {'':<16} ERROR: {row['error_message']}")


def run_benchmark(
    cases: list[BenchmarkCase],
    techniques: list[TechniquePreset],
    output_dir: Path,
    grid_dir: Path,
    top_n: int = 5,
    threshold: float = 0.01,
    extract_runner: Callable[[BenchmarkCase, TechniquePreset, Path], ExtractionArtifact] | None = None,
    compare_runner: Callable[
        [BenchmarkCase, TechniquePreset, Path, ExtractionArtifact, Path, float, int],
        ComparisonArtifact,
    ] | None = None,
    write_summary: bool = True,
) -> dict[str, Any]:
    extract_runner = extract_runner or extract_trace_for_case
    reference_cache: dict[tuple[str, tuple[str, ...]], list[ec.ReferenceSegment]] = {}

    if compare_runner is None:
        def default_compare_runner(
            case: BenchmarkCase,
            technique: TechniquePreset,
            artifact_dir: Path,
            extraction: ExtractionArtifact,
            grid_dir: Path,
            threshold: float,
            top_n: int,
        ) -> ComparisonArtifact:
            return compare_trace_for_case(
                case=case,
                technique=technique,
                artifact_dir=artifact_dir,
                extraction=extraction,
                grid_dir=grid_dir,
                threshold=threshold,
                top_n=top_n,
                reference_cache=reference_cache,
            )

        compare_runner = default_compare_runner

    results: list[dict[str, Any]] = []
    for case in cases:
        artifact_dir = output_dir / case.name
        artifact_dir.mkdir(parents=True, exist_ok=True)
        for technique in techniques:
            try:
                extraction = extract_runner(case, technique, artifact_dir)
            except Exception as exc:
                results.append(
                    build_failed_result_row(case, technique, "extract", exc)
                )
                continue

            try:
                comparison = compare_runner(
                    case,
                    technique,
                    artifact_dir,
                    extraction,
                    grid_dir,
                    threshold,
                    top_n,
                )
            except Exception as exc:
                results.append(
                    build_failed_result_row(
                        case,
                        technique,
                        "compare",
                        exc,
                        trace_path=extraction.trace_path,
                    )
                )
                continue

            results.append(build_result_row(case, technique, extraction, comparison))

    summary: dict[str, Any] = {
        "generated_at_utc": datetime.now(timezone.utc).isoformat(),
        "grid_dir": str(grid_dir),
        "threshold_hz": threshold,
        "top_n": top_n,
        "cases": [serialize_case(case) for case in cases],
        "techniques": [serialize_technique(technique) for technique in techniques],
        "results": results,
    }
    if write_summary:
        json_path, csv_path = write_summary_files(output_dir, summary)
        summary["summary_json"] = str(json_path)
        summary["summary_csv"] = str(csv_path)
    return summary


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)

    if args.list_cases:
        print_available_cases(include_class_demo=args.include_class_demo)
        return 0
    if args.list_techniques:
        print_available_techniques()
        return 0
    if not args.grid_dir.is_dir():
        print(f"ERROR: Grid directory not found: {args.grid_dir}")
        return 1

    case_names = parse_csv_list(args.cases)
    technique_names = parse_csv_list(args.techniques)
    try:
        cases = resolve_benchmark_cases(
            case_names=case_names,
            include_class_demo=args.include_class_demo,
        )
        techniques = resolve_technique_presets(technique_names=technique_names)
    except ValueError as exc:
        print(f"ERROR: {exc}")
        return 1
    output_dir = args.output_dir or default_output_dir()

    summary = run_benchmark(
        cases=cases,
        techniques=techniques,
        output_dir=output_dir,
        grid_dir=args.grid_dir,
        top_n=args.top_n,
        threshold=args.threshold,
    )
    print_run_summary(summary)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
