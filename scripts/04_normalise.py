"""
Stage 4 - Interpolate to raster and z-score normalise

Status: STUB - not yet implemented
Dependencies: See constitution/CONSTITUTION.md
Config: config/paddock_config.yaml
"""

import yaml
import sys
from pathlib import Path

# ── Load config ───────────────────────────────────────────────────────────────
def load_config(config_path: str = "config/paddock_config.yaml") -> dict:
    with open(config_path, "r") as f:
        return yaml.safe_load(f)


def main():
    cfg = load_config()
    paddock_id = cfg["run"]["paddock_id"]
    print(f"[04_normalise.py] paddock: {paddock_id} — NOT YET IMPLEMENTED")
    sys.exit(0)


if __name__ == "__main__":
    main()
