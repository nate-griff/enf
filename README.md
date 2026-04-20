# enf
Electric Network Frequency Analysis tool

## Overview

This repository contains a local Electric Network Frequency (ENF) analysis tool that extracts ENF signatures from audio and video recordings, then compares them against known grid-frequency reference data from one of four North American grids:

- **EI** (Eastern Interconnection)
- **WECC** (Western Electricity Coordinating Council)
- **ERCOT** (Electric Reliability Council of Texas)
- **Quebec**

The tool is research-oriented and designed as an investigative aid to narrow down plausible time windows for human review, not as a standalone proof system.

## Quick Start

### Setup
```bash
# Create and activate virtual environment
python -m venv .venv
.\.venv\Scripts\activate          # Windows
source .venv/bin/activate         # Linux/macOS

# Install dependencies
pip install -r requirements.txt
```

### Basic Workflow

**1. Extract ENF from audio/video:**
```bash
python enf_extract.py --input recording.wav --output trace.csv
python enf_extract.py --input recording.mp4 --output trace.csv  # video auto-converts
```

**2. Compare against grid reference data:**
```bash
python enf_compare.py \
  --trace trace.csv \
  --grid-dir source_data/grid_data \
  --region EI \
  --date 2026-04-20 \
  --top-n 5 \
  --plot
```

**3. Inspect matches in GUI:**
```bash
python enf_view.py --results results.json
```

## Tools

### `enf_extract.py` — ENF Extraction from Audio/Video

Extracts Electric Network Frequency using Quadratically Interpolated FFT (QIFFT).

**Usage:**
```bash
python enf_extract.py --input FILE [--output OUTPUT.csv] [options]
```

**Key Arguments:**
- `--input` (required): Audio or video file
- `--output`: Output CSV path (default: `{input_stem}_enf.csv`)
- `--nominal`: Nominal grid frequency (default: 60 Hz)
- `--harmonic`: Which harmonic to extract (default: 2 — second harmonic at 120 Hz)
  - Harmonic 2 is recommended — much cleaner results with less noise contamination
  - Result is automatically divided back to fundamental (60 Hz)
- `--bandwidth`: Half-bandwidth in Hz around target (default: 0.5)
- `--frame-sec`: Frame duration in seconds (default: 1.0)
- `--overlap`: Frame overlap fraction 0–1 (default: 0.5)
- `--pad-factor`: Zero-padding multiplier for FFT (default: 16)
- `--median-window`: Median filter window size (default: 3, 0 to disable)

**Output CSV columns:**
- `offset_seconds`: Seconds from start of recording
- `frequency_hz`: Estimated ENF frequency

**Example:**
```bash
python enf_extract.py --input fan.wav --output fan_enf.csv --harmonic 2 --bandwidth 0.5
```

### `enf_compare.py` — Grid Matching

Compares an extracted ENF trace against grid reference data using FFT-based cross-correlation.

**Usage:**
```bash
python enf_compare.py --trace TRACE.csv --grid-dir DIR --region REGION [options]
```

**Key Arguments:**
- `--trace` (required): ENF trace CSV from `enf_extract.py`
- `--grid-dir` (required): Directory containing daily grid CSV files
- `--region` (required): Grid region (EI, WECC, ERCOT, or Quebec)
- `--date`: Filter grid data to specific date(s) (YYYY-MM-DD, comma-separated)
- `--top-n`: Number of top matches to return (default: 3)
- `--threshold`: Hz threshold for "close enough" scoring (default: 0.01)
- `--output`: JSON output path (default: `{trace_stem}_results.json`)
- `--plot`: Generate overlay PNG for each top match
- `--recording-time`: Known UTC start time (ISO format) for offset display

**Output JSON:**
Contains ranked matches with:
- `rank`: Match order
- `ref_start_utc`: Reference window start time
- `ref_end_utc`: Reference window end time
- `correlation`: Pearson correlation (0–1)
- `threshold_coverage`: Fraction of samples within threshold Hz (0–1)
- `composite_score`: Weighted score (60% correlation + 40% coverage)

**Scoring:**
The composite score combines:
- **Pearson correlation** (60%): Shape similarity, offset-invariant
- **Threshold coverage** (40%): Absolute frequency proximity

**Example:**
```bash
python enf_compare.py \
  --trace fan_enf.csv \
  --grid-dir source_data/grid_data \
  --region EI \
  --date 2026-04-20 \
  --top-n 5 \
  --threshold 0.01 \
  --plot \
  --recording-time "2026-04-20T16:36:00"
```

### `enf_view.py` — GUI Overlay Viewer

Interactive tkinter + matplotlib viewer for visual inspection of ENF matches.

**Usage:**
```bash
# Load from results JSON
python enf_view.py --results results.json

# Or load manually
python enf_view.py --trace trace.csv --grid-dir source_data/grid_data --region EI
```

**Features:**
- **Overlay display**: Query trace (blue) vs. matched reference (orange)
- **Match stepping**: Previous/Next buttons to cycle through top matches
- **Scroll/Zoom**: Log-scale zoom slider and time-position scroll
- **Score display**: Shows correlation, coverage %, and composite score
- **UTC info**: Displays reference time window in plot title

**Controls:**
- **Match combobox**: Jump to any top match
- **Scroll slider**: Move time window across the traces
- **Zoom slider**: Change visible time range (log scale, narrow ← → wide)
- **Prev/Next buttons**: Step through ranked matches

## Project Structure

```
.
├── enf_extract.py           # ENF extraction (audio/video → CSV)
├── enf_compare.py           # Grid matching (CSV → JSON results)
├── enf_view.py              # GUI viewer (JSON → overlay display)
├── freqgauge_view_csv.py    # CSV viewer for grid reference data
├── freqgauge_extract.py     # Extract grid data from FNET images
├── collect_freqgauge_service.py  # Continuous FNET image collection
├── requirements.txt         # Python dependencies
├── Project-Plan.md          # Detailed technical plan
└── source_data/
    ├── audio_samples/       # Test recordings
    ├── grid_data/           # Daily grid CSVs from FNET
    └── scraped_images/      # FNET frequency gauge images (from collector)
```

## Technical Details

### ENF Extraction Method

The `enf_extract.py` script uses **Quadratically Interpolated FFT (QIFFT)** for sub-bin frequency precision:

1. **Bandpass filter**: 4th-order Butterworth filter around target frequency
2. **Windowing**: Hanning window on each frame
3. **FFT**: Zero-padded (default 16×) for fine bin spacing
4. **Peak finding**: Locate maximum magnitude in expected frequency range
5. **QIFFT interpolation**: Quadratic fit on peak and neighbors for sub-bin accuracy
6. **Aggregation**: Average multiple estimates per second to match grid cadence
7. **Smoothing**: Optional median filter for noise reduction

**Formula:** For magnitude bins `α`, `β`, `γ` at peak `k`:
```
δ = 0.5 × (α - γ) / (α - 2β + γ)
f_est = (k + δ) × (fs / N)
```

### Matching Algorithm

The `enf_compare.py` script uses FFT-based cross-correlation for speed:

1. **Load & resample**: Grid data resampled to regular 1-second intervals
2. **Normalize**: Query and reference windows normalized (zero mean, unit std)
3. **FFT correlation**: Fast cross-correlation using `numpy.correlate`
4. **Candidate selection**: Top 50 by correlation score
5. **Threshold coverage**: Count samples within Hz threshold for top candidates
6. **Composite scoring**: 0.6 × correlation + 0.4 × coverage
7. **Ranking**: Sort by composite score descending

### Default Settings

- **Harmonic**: 2 (second harmonic at 120 Hz, much cleaner than fundamental)
- **Bandwidth**: 0.5 Hz
- **Frame size**: 1.0 second
- **Overlap**: 50% (0.5 second hop)
- **Zero-padding**: 16× (48 kHz × 1s = 48000 points → 768000 points)
- **Median filter**: 3-sample window
- **Threshold**: 0.01 Hz
- **Composite weights**: 60% correlation, 40% coverage

## Dependencies

```
numpy>=1.24.0
pandas>=2.0.0
scipy>=1.11.0
matplotlib>=3.7.0
opencv-python>=4.8.0  # For image extraction (freqgauge_extract.py)
requests>=2.28.0      # For image collection (collect_freqgauge_service.py)
```

## Data Sources

### Reference Grid Data

Daily CSV files are generated by processing FNET frequency gauge images. Each CSV contains:
```
timestamp_utc,region,frequency_hz
2026-04-20 16:36:12.457984+00:00,EI,59.980379
```

**Grid regions:**
- **EI**: Eastern Interconnection (US East)
- **WECC**: Western Electricity Coordinating Council (US West)
- **ERCOT**: Electric Reliability Council of Texas (Texas)
- **Quebec**: Hydro-Québec system (Quebec/Eastern Canada)

### Image Collection

Use `collect_freqgauge_service.py` to continuously download FNET gauge images:

```bash
python collect_freqgauge_service.py \
  --outdir source_data/scraped_images \
  --interval 38.6
```

### Image Processing

Extract frequency traces from collected images:

```bash
python freqgauge_extract.py \
  --input source_data/scraped_images \
  --output source_data/grid_data/merged.csv \
  -j 8
```

View and explore extracted data:

```bash
python freqgauge_view_csv.py source_data/grid_data/merged.csv
```

## Validation & Testing

The tool was validated end-to-end with:
- **Test recording**: `fan.wav` — 340 seconds, 48 kHz stereo, recorded 2026-04-20 12:36 PM EST
- **Reference data**: EI grid data for 2026-04-20
- **Result**: Top match found at **16:36:05 UTC** (within 5 seconds of true time)
  - Correlation: 0.713
  - Coverage: 57%
  - Composite score: 0.657
  - All top 5 matches within ±7 seconds of correct time

## Future Work

From the project plan:
- Expand to 50 Hz grids (international support)
- Automated geographic grid detection
- Web-based deployment
- Large-scale benchmark dataset
- Forensic-grade confidence metrics
- GPU-accelerated matching for large datasets

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
