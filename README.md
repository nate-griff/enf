# enf
Electric Network Frequency Analysis tool

## Overview

This repository is for a local Electric Network Frequency analysis tool focused on matching media against known grid-frequency behavior. The goal is to take an audio or video file, isolate its ENF content around 60 Hz, and compare that fluctuation pattern against recorded reference data from one of four North American grids:

- EI
- WECC
- ERCOT
- Quebec

The project is being developed as a research-oriented investigative aid. It is meant to help narrow down plausible candidate time windows for human review, not to serve as a standalone proof system.

## Current State

The repository already contains the upstream reference-data pipeline:

- Continuous collection of FNET frequency gauge images.
- Extraction of regional frequency traces from those images into CSV.
- A GUI viewer for exploring extracted CSV data by region with scroll and zoom controls.

This means the current codebase already supports reference-data collection and inspection. The main work still ahead is building the media-side ENF extraction, comparison logic, and unified analysis workflow.

## Planned Tool Direction

The expected project direction is:

1. Keep collecting and merging reference grid data from the existing image pipeline.
2. Add support for ingesting local audio and video files.
3. Extract ENF-relevant waveform data from those media files.
4. Compare the extracted waveform against a selected grid's reference data.
5. Score and rank the best candidate matches.
6. Provide both CLI-driven results and a GUI workflow for visual inspection.

The CLI is expected to come first. The GUI will follow as a comparison and inspection tool that can overlay the query waveform against top candidate matches and support scrollable, zoomable review.

## Expected CLI Scope

The planned CLI should eventually be able to:

- Accept an input audio or video file.
- Extract audio from video internally.
- Select one of the four supported grids.
- Return the top matching candidate windows.
- Support an optional top-N output count, defaulting to 3.
- Support an optional score or accuracy threshold cutoff.
- Export structured results.
- Optionally export an image showing overlapping waveforms.

## Matching Approach

The exact matching algorithm is still an open project task. The current plan is to compare several candidate methods before choosing the initial matcher. The score is expected to combine:

- Percent of the query waveform that stays within a configurable Hz threshold of the reference waveform.
- Shape similarity between the query and candidate reference window.

This part of the project is still under active design and will likely evolve as more recordings and test cases become available.

## Roadmap Summary

The near-term roadmap is:

1. Normalize and merge the reference CSV data.
2. Build query-side ENF extraction from audio and video.
3. Run algorithm comparison across candidate matching methods.
4. Build the first CLI matcher.
5. Add evaluation workflows and output artifacts.
6. Build the waveform comparison GUI.

More detailed planning lives in `Project-Plan.md`.

## Data Sources
Data was scraped from FNET's live grid data
<details>
<summary>Scraping the Images</summary>

Use `collect_freqgauge_service.py` to continuously download the current image from:

`https://fnetpublic.utk.edu/freqgauge.php`

### What it does

- Downloads one image every 38.6 seconds (default)
- Saves images under a UTC day folder (`YYYY-MM-DD`)
- Adds a UTC timestamp to each filename
- Logs failures and status messages to a log file and stdout

### Install dependency

```bash
python3 -m pip install requests
```

### Run manually

```bash
python3 collect_freqgauge_service.py \
	--outdir /var/lib/freqgauge/images \
	--log-file /var/log/freqgauge/collector.log
```

Optional flags:

- `--interval 50` (seconds between polls)
- `--timeout 20` (HTTP timeout)
- `--once` (download one image and exit)
- `--verbose` (debug logging)

### systemd service setup

1. Create a service user (optional but recommended):

```bash
sudo useradd --system --no-create-home --shell /usr/sbin/nologin freqgauge
```

2. Create directories and permissions:

```bash
sudo mkdir -p /var/lib/freqgauge/images
sudo mkdir -p /var/log/freqgauge
sudo chown -R freqgauge:freqgauge /var/lib/freqgauge /var/log/freqgauge
```

3. Create `/etc/systemd/system/freqgauge-collector.service`:

```ini
[Unit]
Description=FNET Frequency Gauge Collector
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
User=freqgauge
Group=freqgauge
WorkingDirectory=/opt/enf
ExecStart=/usr/bin/python3 /opt/enf/collect_freqgauge_service.py --outdir /var/lib/freqgauge/images --log-file /var/log/freqgauge/collector.log --interval 50
Restart=always
RestartSec=5

[Install]
WantedBy=multi-user.target
```

4. Enable and start:

```bash
sudo systemctl daemon-reload
sudo systemctl enable freqgauge-collector
sudo systemctl start freqgauge-collector
```

5. Check status/logs:

```bash
sudo systemctl status freqgauge-collector
sudo journalctl -u freqgauge-collector -f
```
</details>
<details>
<summary>Processing the Images</summary>

### Extract traces to CSV (`freqgauge_extract.py`)

Install image stack into the same venv you use for scraping:

```powershell
.\.venv\Scripts\pip.exe install -r requirements.txt
```

One image (one row per x-column × four regions):

```powershell
.\.venv\Scripts\python.exe freqgauge_extract.py `
  --input testdata\freqgauge_2026-03-20T22-39-56.541331Z.png `
  --output out\sample.csv
```

Whole tree (recursive `freqgauge_*.png` / `.jpg`, including `YYYY-MM-DD` day folders from the collector). With **two or more** images, `--dedupe-ms` bins timestamps and averages `frequency_hz` for overlapping windows:

```powershell
.\.venv\Scripts\python.exe freqgauge_extract.py `
  --input path\to\images `
  --output out\merged.csv `
  --dedupe-ms 1000
```

**Debug overlays** (cropped plot + binary mask side‑by‑side) to tune color detection and margins:

```powershell
.\.venv\Scripts\python.exe freqgauge_extract.py `
  --input testdata\some.png `
  --debug-dir out\debug
```

Useful flags: `--window-seconds` (default 55), `--skip-shape-check` if resolution changes, `--morphology 0` to disable mask cleanup. For large batches, `-j` / `--jobs N` runs extraction in **N parallel processes** (default 1); try 4–8 on a multi-core machine—each worker holds one full image in RAM, and `--debug-dir` still runs sequentially after the pool finishes. CSV columns are `timestamp_utc`, `region`, `frequency_hz` by default; add `--verbose-csv` to include `pixel_x` and `source_path`.

**Time axis:** columns map linearly from `(capture_time − window)` on the left to `capture_time` on the right, using the UTC timestamp in the filename. **Frequency:** 59.95 Hz at the bottom of the inner plot, 60.05 Hz at the top (`FREQ_MIN_HZ` / `FREQ_MAX_HZ` in the script).

### View extracted CSV (`freqgauge_view_csv.py`)

Requires `matplotlib` (included in `requirements.txt`). The viewer expects columns `timestamp_utc`, `region`, and `frequency_hz` (extra columns such as `pixel_x` / `source_path` are ignored).

```powershell
.\.venv\Scripts\python.exe freqgauge_view_csv.py
.\.venv\Scripts\python.exe freqgauge_view_csv.py out\merged.csv
```

Use **Open CSV** (or pass a path on the command line), pick **one region** at a time, set **time zoom** with the **dropdown** (common widths), the **log-scale width slider**, and/or **−** / **+**. The dropdown switches to **Custom** when the slider doesn’t match a preset. **Scroll time** moves the visible window along the UTC axis. **Reset view** shows the full time range.

### Plot Details (calibration)
**Regions** 
```
PLOT_REGIONS = {
    "EI":     {"x1": 100, "x2": 1180, "y1": 43,  "y2": 220},
    "WECC":   {"x1": 100, "x2": 1180, "y1": 342, "y2": 520},
    "ERCOT":  {"x1": 100, "x2": 1180, "y1": 642, "y2": 820},
    "Quebec": {"x1": 100, "x2": 1180, "y1": 942, "y2": 1120},
}
```
**Color Codes (RGB)**
EI: 5.1, 55.7, 87.1
WECC: 2.0, 58.5, 16.9
ERCOT: 88.6, 26.3, 11.8
Quebec: 82.0, 1.6, 79.2
</details>
