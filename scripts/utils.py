"""
Shared utilities for the farm data workflow pipeline.
Imported by all stage scripts.
"""

import yaml
import logging
import csv
from pathlib import Path
from datetime import datetime, timezone


def load_config(config_path: str = "config/paddock_config.yaml") -> dict:
    """Load and return the paddock configuration YAML."""
    path = Path(config_path)
    if not path.exists():
        raise FileNotFoundError(f"Config not found: {config_path}")
    with open(path, "r") as f:
        return yaml.safe_load(f)


def setup_logger(script_name: str, log_dir: str = "logs") -> logging.Logger:
    """
    Set up a logger that writes to both console and a dated log file.
    Returns the logger instance.
    """
    Path(log_dir).mkdir(parents=True, exist_ok=True)
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    log_file = Path(log_dir) / f"{timestamp}_{script_name}.log"

    logger = logging.getLogger(script_name)
    logger.setLevel(logging.DEBUG)

    # File handler — full detail
    fh = logging.FileHandler(log_file)
    fh.setLevel(logging.DEBUG)
    fh.setFormatter(logging.Formatter(
        "%(asctime)s [%(levelname)s] %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S"
    ))

    # Console handler — INFO and above
    ch = logging.StreamHandler()
    ch.setLevel(logging.INFO)
    ch.setFormatter(logging.Formatter("[%(levelname)s] %(message)s"))

    logger.addHandler(fh)
    logger.addHandler(ch)
    return logger


def log_run_entry(
    log_dir: str,
    script: str,
    paddock_id: str,
    inputs: dict,
    outputs: dict,
    flags: list,
    status: str
) -> None:
    """
    Append a structured run entry to the master run log CSV.
    Creates the file with headers if it does not exist.
    """
    log_path = Path(log_dir) / "run_log.csv"
    fieldnames = [
        "timestamp_utc", "script", "paddock_id",
        "inputs", "outputs", "flags", "status"
    ]
    row = {
        "timestamp_utc": datetime.now(timezone.utc).isoformat(),
        "script": script,
        "paddock_id": paddock_id,
        "inputs": str(inputs),
        "outputs": str(outputs),
        "flags": "; ".join(flags) if flags else "",
        "status": status
    }
    write_header = not log_path.exists()
    with open(log_path, "a", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        if write_header:
            writer.writeheader()
        writer.writerow(row)


def ensure_output_dirs(cfg: dict) -> None:
    """Create all output directories defined in config if they do not exist."""
    for key, path_str in cfg.get("outputs", {}).items():
        Path(path_str).mkdir(parents=True, exist_ok=True)
