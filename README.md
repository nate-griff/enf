# enf
Electric Network Frequency Analysis tool for Autopsy
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

### Plot Details
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
