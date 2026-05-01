# Copilot Instructions

## Commands

```powershell
python -m venv .venv
.\.venv\Scripts\activate
pip install -r requirements.txt
```

- Run the main ENF workflow as standalone scripts:
  - `python enf_extract.py --input recording.wav --output trace.csv`
  - `python enf_compare.py --trace trace.csv --grid-dir source_data\grid_data --region EI --date 2026-04-20 --top-n 5 --plot`
  - `python enf_view.py --results trace_results.json`
- Run the reference-data processing workflow as standalone scripts:
  - `python freqgauge_extract.py --input source_data\scraped_images --output source_data\grid_data\merged.csv -j 8`
  - `python freqgauge_view_csv.py source_data\grid_data\merged.csv`
  - `python collect_freqgauge_service.py --outdir source_data\scraped_images --interval 38.6`
- There is no dedicated build step, lint target, automated test suite, or single-test command defined in the current repository state. Do not assume `pytest`, `ruff`, or packaging commands exist unless the user adds them.

## High-level architecture

- This repository is organized around **two connected pipelines**:
  1. **Reference-grid pipeline:** `collect_freqgauge_service.py` downloads FNET gauge images into UTC day folders, `freqgauge_extract.py` converts those images into grid-frequency CSV data, and `freqgauge_view_csv.py` is the inspection GUI for those CSVs.
  2. **ENF analysis pipeline:** `enf_extract.py` extracts a query ENF trace from audio or video into CSV, `enf_compare.py` compares that trace against reference CSV data in `source_data\grid_data`, and `enf_view.py` visualizes ranked matches from the comparison JSON.
- In practice, collection is not always run from this checkout. Historic images may be gathered on another server, then processed here into CSVs and manually uploaded into this repo for later comparison work.
- `source_data\grid_data` is the local handoff point between the reference-data workflow and the ENF matcher. `enf_compare.py` assumes grid CSVs live there or in another directory passed with `--grid-dir`.
- The GUI scripts are viewers, not primary processing engines. `freqgauge_view_csv.py` inspects extracted grid CSVs, and `enf_view.py` inspects comparison results produced by `enf_compare.py`.

## Key conventions

- Keep the project **script-first**. The repository is intentionally a small set of top-level Python scripts that are meant to run as `python <script>.py`, not as a packaged application with entry points.
- Preserve the existing data contracts between scripts:
  - `enf_extract.py` writes query traces with `offset_seconds,frequency_hz`.
  - `freqgauge_extract.py` writes reference data with `timestamp_utc,region,frequency_hz` by default, and may append `pixel_x` and `source_path` when `--verbose-csv` is used.
  - `enf_compare.py` writes JSON with ranked `matches`, including `ref_start_utc`, `ref_end_utc`, `correlation`, `threshold_coverage`, `composite_score`, and `ref_offset_index`.
- Treat time semantics carefully:
  - Grid/image timestamps are UTC throughout the collector and reference-data pipeline.
  - Query traces use relative `offset_seconds`, not absolute timestamps.
  - `enf_compare.py --recording-time` is for display/validation only; matching still runs on relative trace offsets.
- Use the fixed region vocabulary everywhere: `EI`, `WECC`, `ERCOT`, and `Quebec`. Downstream viewers and filters assume those exact names.
- Prefer the default **second harmonic** extraction path in `enf_extract.py` (`--harmonic 2`). The script is tuned around extracting the 120 Hz harmonic and dividing back to the 60 Hz fundamental.
- `freqgauge_extract.py` is calibrated to FNET gauge screenshots: it expects collector-style filenames like `freqgauge_YYYY-MM-DDTHH-MM-SS.ffffffZ.png` and, unless `--skip-shape-check` is used, the current hard-coded image layout. Do not casually change filename handling, region boxes, or color calibration without checking the full image-processing pipeline.
- `enf_view.py` tries to auto-discover `source_data\grid_data` by walking parent directories from the results JSON. Keep results near the repo layout when possible, or pass an explicit grid directory.
- Keep the repo posture consistent with the README and project plan: this is a **research / investigative aid** for narrowing plausible match windows, not a forensic proof system.
