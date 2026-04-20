"""
FNET Frequency Gauge Continuous Collector
=========================================

Downloads the current FNET frequency gauge image on a fixed interval and stores
it under UTC calendar-day folders.

Features:
- Fetches https://fnetpublic.utk.edu/freqgauge.php every N seconds (default: 50)
- Creates one folder per UTC day (YYYY-MM-DD)
- Saves files with UTC timestamp in filename
- Logs failures and operational events to file and stdout
- Graceful shutdown for systemd/service usage

Example:
    python collect_freqgauge_service.py --outdir /var/lib/freqgauge/images
"""

from __future__ import annotations

import argparse
import logging
import os
import signal
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

import requests


BASE_URL = "https://fnetpublic.utk.edu/freqgauge.php"
DEFAULT_INTERVAL_SECONDS = 38.6 
DEFAULT_TIMEOUT_SECONDS = 20
DEFAULT_LOG_FILE = "freqgauge_collector.log"

_STOP_REQUESTED = False


def _handle_stop_signal(signum: int, _frame: object) -> None:
    """Signal handler for graceful shutdown."""
    global _STOP_REQUESTED
    _STOP_REQUESTED = True
    logging.info("Received signal %s, stopping collector...", signum)


def configure_logging(log_file: Path, verbose: bool) -> None:
    """Configure logger to write to stdout and a log file."""
    log_file.parent.mkdir(parents=True, exist_ok=True)

    level = logging.DEBUG if verbose else logging.INFO
    formatter = logging.Formatter(
        "%(asctime)s %(levelname)s %(message)s",
        datefmt="%Y-%m-%dT%H:%M:%SZ",
    )
    formatter.converter = time.gmtime

    root = logging.getLogger()
    root.setLevel(level)

    file_handler = logging.FileHandler(log_file, encoding="utf-8")
    file_handler.setLevel(level)
    file_handler.setFormatter(formatter)

    stream_handler = logging.StreamHandler(sys.stdout)
    stream_handler.setLevel(level)
    stream_handler.setFormatter(formatter)

    root.handlers.clear()
    root.addHandler(file_handler)
    root.addHandler(stream_handler)


def extension_from_content_type(content_type: str) -> str:
    """Map Content-Type to extension, defaulting to png for unknown values."""
    token = (content_type or "image/png").split(";", 1)[0].strip().lower()
    mapping = {
        "image/jpeg": "jpg",
        "image/jpg": "jpg",
        "image/png": "png",
        "image/gif": "gif",
        "image/webp": "webp",
    }
    return mapping.get(token, "png")


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


def target_path(base_dir: Path, now_utc: datetime, extension: str) -> Path:
    """Build output path using UTC date folder and timestamped filename."""
    day_folder = now_utc.strftime("%Y-%m-%d")
    timestamp = now_utc.strftime("%Y-%m-%dT%H-%M-%S.%fZ")
    out_dir = base_dir / day_folder
    out_dir.mkdir(parents=True, exist_ok=True)
    return out_dir / f"freqgauge_{timestamp}.{extension}"


def download_once(session: requests.Session, outdir: Path, timeout: int, interval: int) -> bool:
    """Download one current gauge image and save to disk."""
    now_utc = utc_now()

    try:
        response = session.get(BASE_URL, timeout=timeout)
        response.raise_for_status()
    except requests.Timeout:
        logging.error("Download timed out")
        return False
    except requests.RequestException as exc:
        logging.error("Download failed: %s", exc)
        return False

    extension = extension_from_content_type(response.headers.get("Content-Type", ""))
    output_file = target_path(outdir, now_utc, extension)
    tmp_file = output_file.with_suffix(output_file.suffix + ".tmp")

    try:
        with open(tmp_file, "wb") as f:
            f.write(response.content)
        os.replace(tmp_file, output_file)
        logging.info("Saved %s", output_file)
        return True
    except OSError as exc:
        logging.error("Failed to write image %s: %s", output_file, exc)
        try:
            if tmp_file.exists():
                tmp_file.unlink()
        except OSError:
            pass
        return False


def wait_with_interrupt(total_seconds: int) -> None:
    """Sleep in short steps so stop signals are handled quickly."""
    end = time.monotonic() + total_seconds
    while not _STOP_REQUESTED:
        remaining = end - time.monotonic()
        if remaining <= 0:
            break
        time.sleep(min(1.0, remaining))


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Continuously collect FNET gauge images at a fixed interval.",
    )
    parser.add_argument(
        "--outdir",
        type=Path,
        required=True,
        help="Base output directory. Images are stored under UTC day folders.",
    )
    parser.add_argument(
        "--interval",
        type=int,
        default=DEFAULT_INTERVAL_SECONDS,
        help=f"Seconds between downloads (default: {DEFAULT_INTERVAL_SECONDS}).",
    )
    parser.add_argument(
        "--timeout",
        type=int,
        default=DEFAULT_TIMEOUT_SECONDS,
        help=f"HTTP timeout in seconds (default: {DEFAULT_TIMEOUT_SECONDS}).",
    )
    parser.add_argument(
        "--log-file",
        type=Path,
        default=Path(DEFAULT_LOG_FILE),
        help=f"Path to log file (default: {DEFAULT_LOG_FILE}).",
    )
    parser.add_argument(
        "--once",
        action="store_true",
        help="Download one image and exit.",
    )
    parser.add_argument(
        "--verbose",
        action="store_true",
        help="Enable verbose (DEBUG) logging.",
    )

    args = parser.parse_args()
    if args.interval < 1:
        parser.error("--interval must be >= 1")
    if args.timeout < 1:
        parser.error("--timeout must be >= 1")
    return args


def main() -> int:
    args = parse_args()
    configure_logging(args.log_file, args.verbose)

    signal.signal(signal.SIGINT, _handle_stop_signal)
    signal.signal(signal.SIGTERM, _handle_stop_signal)

    outdir: Path = args.outdir
    outdir.mkdir(parents=True, exist_ok=True)

    logging.info("Starting FNET collector")
    logging.info("Output directory: %s", outdir)
    logging.info("Interval: %ss", args.interval)
    logging.info("Timeout: %ss", args.timeout)

    with requests.Session() as session:
        while not _STOP_REQUESTED:
            download_once(session, outdir, args.timeout, args.interval)

            if args.once:
                break

            wait_with_interrupt(args.interval)

    logging.info("Collector stopped")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
