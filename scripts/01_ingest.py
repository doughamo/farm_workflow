"""
Stage 1 - Validate inputs and log run metadata

Config: config/paddock_config.yaml
"""

import sys
from pathlib import Path

import geopandas as gpd
import pandas as pd

from utils import ensure_output_dirs, load_config, log_run_entry, setup_logger


def main():
    cfg = load_config()
    run = cfg["run"]
    paddock_id = run["paddock_id"]
    paddock_name = run["paddock_name"]
    season = run["season"]

    logger = setup_logger("01_ingest")
    ensure_output_dirs(cfg)

    flags = []

    # ── Yield points (Stage 0 output) ───────────────────────────────────────
    yield_path = Path("data/raw/yield") / f"{paddock_id}_{season}_yield_points.shp"
    if not yield_path.exists():
        logger.error(f"Yield shapefile not found: {yield_path}")
        log_run_entry(
            log_dir="logs",
            script="01_ingest.py",
            paddock_id=paddock_id,
            inputs={"yield_shp": str(yield_path)},
            outputs={},
            flags=[f"yield shapefile missing: {yield_path}"],
            status="failed",
        )
        sys.exit(1)

    ydf = gpd.read_file(yield_path)
    logger.info(f"Loaded {len(ydf)} yield points from {yield_path}")
    flags.append(f"yield_record_count={len(ydf)}")

    yield_epsg = ydf.crs.to_epsg()
    expected_crs = cfg["crs"]["project"]
    expected_epsg = int(expected_crs.split(":")[1])
    flags.append(f"yield_crs=EPSG:{yield_epsg}")
    if yield_epsg != expected_epsg:
        logger.warning(
            f"Yield CRS EPSG:{yield_epsg} does not match configured "
            f"crs.project {expected_crs}"
        )
        flags.append("WARNING: yield CRS mismatch with crs.project")

    # machine_id field (Section 8 step 1)
    machine_field = cfg["yield_cleaning"]["machine_id_field"]
    if machine_field not in ydf.columns or ydf[machine_field].dropna().empty:
        logger.warning(f"machine_id field '{machine_field}' missing or empty")
        flags.append(f"WARNING: machine_id field '{machine_field}' missing or empty")
    else:
        counts = ydf[machine_field].value_counts().to_dict()
        logger.info(f"Machine ID record counts: {counts}")
        flags.append(f"machine_ids={counts}")

    # timestamp range
    if "timestamp" in ydf.columns and not ydf["timestamp"].dropna().empty:
        ts_min = ydf["timestamp"].min()
        ts_max = ydf["timestamp"].max()
        logger.info(f"Timestamp range: {ts_min} .. {ts_max}")
        flags.append(f"date_range={ts_min}..{ts_max}")
    else:
        logger.warning("timestamp field missing or empty")
        flags.append("WARNING: timestamp field missing or empty")

    # ── Boundary check ───────────────────────────────────────────────────────
    boundary_path = Path(cfg["inputs"]["boundary_shp"])
    if not boundary_path.exists():
        logger.warning(f"Boundary shapefile not found: {boundary_path}")
        flags.append(f"WARNING: boundary shapefile missing: {boundary_path}")
    else:
        bdf = gpd.read_file(boundary_path)
        match = bdf[bdf["FIELD_NAME"] == paddock_name]
        if match.empty:
            logger.warning(
                f"No boundary polygon found for FIELD_NAME='{paddock_name}'"
            )
            flags.append(
                f"WARNING: no boundary polygon for FIELD_NAME='{paddock_name}'"
            )
        else:
            boundary = match.to_crs(ydf.crs).geometry.union_all()
            buffer_m = cfg["ingest"]["boundary_buffer_m"]
            buffered = boundary.buffer(buffer_m)
            inside = ydf.geometry.within(buffered)
            pct_outside = (~inside).mean() * 100
            logger.info(
                f"{pct_outside:.2f}% of yield points fall outside the paddock "
                f"boundary (buffered {buffer_m}m)"
            )
            flags.append(f"pct_outside_boundary={pct_outside:.2f}")
            if pct_outside > cfg["ingest"]["max_outside_boundary_pct"]:
                logger.warning(
                    f"{pct_outside:.2f}% of yield points outside boundary exceeds "
                    f"threshold {cfg['ingest']['max_outside_boundary_pct']}%"
                )
                flags.append("WARNING: pct_outside_boundary exceeds threshold")

    # ── Protein CSV check ────────────────────────────────────────────────────
    protein_path = Path(cfg["inputs"]["protein_csv"])
    if not protein_path.exists():
        logger.warning(f"Protein CSV not provided: {protein_path}")
        flags.append("protein_csv=not provided")
    else:
        pdf_cols = pd.read_csv(protein_path, nrows=0).columns
        required = [v for v in cfg["protein_columns"].values() if v]
        missing_cols = [c for c in required if c not in pdf_cols]
        if missing_cols:
            logger.warning(f"Protein CSV missing expected columns: {missing_cols}")
            flags.append(f"WARNING: protein_csv missing columns {missing_cols}")
        else:
            logger.info(f"Protein CSV columns OK: {required}")
            flags.append("protein_csv=ok")

    log_run_entry(
        log_dir="logs",
        script="01_ingest.py",
        paddock_id=paddock_id,
        inputs={
            "yield_shp": str(yield_path),
            "boundary_shp": cfg["inputs"]["boundary_shp"],
            "protein_csv": cfg["inputs"]["protein_csv"],
        },
        outputs={},
        flags=flags,
        status="success",
    )


if __name__ == "__main__":
    main()
