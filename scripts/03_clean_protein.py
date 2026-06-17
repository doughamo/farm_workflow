"""
Stage 3 - CropScan protein CSV → cleaned point shapefile

Reads the -MAP CSV file configured in inputs.protein_csv.
Applies range filter, z-score outlier removal, and boundary clip.
Outputs a cleaned point shapefile to data/processed/protein/.

Config: config/paddock_config.yaml
"""

import sys
from pathlib import Path

import geopandas as gpd
import numpy as np
import pandas as pd
from shapely.geometry import Point

from utils import ensure_output_dirs, load_config, log_run_entry, setup_logger


def main():
    cfg = load_config()
    run = cfg["run"]
    paddock_id = run["paddock_id"]
    paddock_name = run["paddock_name"]
    season = run["season"]
    col = cfg["protein_columns"]
    pc = cfg["protein_cleaning"]

    logger = setup_logger("03_clean_protein")
    ensure_output_dirs(cfg)

    flags = []

    # ── Locate input CSV ──────────────────────────────────────────────────────
    csv_path = Path(cfg["inputs"]["protein_csv"])
    if not csv_path.exists():
        logger.error(f"Protein CSV not found: {csv_path}")
        log_run_entry(
            log_dir="logs",
            script="03_clean_protein.py",
            paddock_id=paddock_id,
            inputs={"protein_csv": str(csv_path)},
            outputs={},
            flags=[f"protein CSV missing: {csv_path}"],
            status="failed",
        )
        sys.exit(1)

    # Warn if this doesn't look like a MAP file
    if "-MAP" not in csv_path.name.upper():
        logger.warning(
            f"Input filename does not contain '-MAP': {csv_path.name}. "
            "Only CropScan MAP files should be used for spatial analysis."
        )
        flags.append(f"WARNING: input filename does not contain '-MAP': {csv_path.name}")

    # ── Load CSV ──────────────────────────────────────────────────────────────
    df = pd.read_csv(csv_path)
    n_loaded = len(df)
    logger.info(f"Loaded {n_loaded} rows from {csv_path.name}")
    flags.append(f"loaded={n_loaded}")

    # ── Column mapping and validation ─────────────────────────────────────────
    lat_col = col["latitude"]
    lon_col = col["longitude"]
    prot_col = col["protein"]
    moist_col = col.get("moisture")
    ts_col = col.get("timestamp")

    required = [lat_col, lon_col, prot_col]
    missing = [c for c in required if c not in df.columns]
    if missing:
        logger.error(f"Required columns missing from CSV: {missing}")
        log_run_entry(
            log_dir="logs",
            script="03_clean_protein.py",
            paddock_id=paddock_id,
            inputs={"protein_csv": str(csv_path)},
            outputs={},
            flags=flags + [f"missing columns: {missing}"],
            status="failed",
        )
        sys.exit(1)

    # ── Timestamp: use configured column, or combine Date + Time if present ──
    if ts_col and ts_col in df.columns:
        df["_timestamp"] = pd.to_datetime(df[ts_col], errors="coerce")
    elif "Date" in df.columns and "Time" in df.columns:
        df["_timestamp"] = pd.to_datetime(
            df["Date"].astype(str) + " " + df["Time"].astype(str),
            errors="coerce",
        )
        logger.info("Timestamp constructed from Date + Time columns")
    else:
        df["_timestamp"] = pd.NaT
        logger.warning("No timestamp column found; timestamps will be null")
        flags.append("WARNING: no timestamp column found")

    ts_valid = df["_timestamp"].dropna()
    if not ts_valid.empty:
        logger.info(f"Date range: {ts_valid.min()} to {ts_valid.max()}")
        flags.append(f"date_range={ts_valid.min()} to {ts_valid.max()}")

    # ── Drop rows missing lat/lon/protein ─────────────────────────────────────
    n_before = len(df)
    df = df.dropna(subset=[lat_col, lon_col, prot_col]).reset_index(drop=True)
    n_dropped_nan = n_before - len(df)
    if n_dropped_nan:
        logger.warning(f"Dropped {n_dropped_nan} rows with null lat/lon/protein")
        flags.append(f"dropped_null={n_dropped_nan}")

    # ── Protein range filter ──────────────────────────────────────────────────
    prot_min = pc["protein_min_pct"]
    prot_max = pc["protein_max_pct"]
    in_range = df[prot_col].between(prot_min, prot_max)
    n_out_range = (~in_range).sum()
    if n_out_range:
        logger.warning(
            f"Dropped {n_out_range} rows outside protein range "
            f"[{prot_min}, {prot_max}]%: "
            f"min={df[prot_col].min():.2f}, max={df[prot_col].max():.2f}"
        )
        flags.append(f"range_filter_dropped={n_out_range}")
    df = df.loc[in_range].reset_index(drop=True)
    n_range = len(df)
    logger.info(f"Range filter [{prot_min}, {prot_max}]%: kept {n_range}/{n_loaded}")

    # ── Z-score outlier removal ───────────────────────────────────────────────
    zscore_thresh = pc["zscore_flag_threshold"]
    mean = df[prot_col].mean()
    std = df[prot_col].std()
    zscores = (df[prot_col] - mean).abs() / std
    outlier_mask = zscores > zscore_thresh
    n_outliers = outlier_mask.sum()
    if n_outliers:
        logger.warning(
            f"Removed {n_outliers} z-score outliers (|z| > {zscore_thresh}): "
            f"mean={mean:.3f}, std={std:.3f}"
        )
        flags.append(f"zscore_outliers_removed={n_outliers}")
    df = df.loc[~outlier_mask].reset_index(drop=True)
    n_zscore = len(df)
    logger.info(f"Z-score filter (|z| > {zscore_thresh}): kept {n_zscore}/{n_range}")

    # ── Build GeoDataFrame, reproject to project CRS ──────────────────────────
    input_crs = cfg["crs"]["input_gps"]
    project_crs = cfg["crs"]["project"]

    geometry = [Point(lon, lat) for lon, lat in zip(df[lon_col], df[lat_col])]
    keep_cols = {
        "protein": df[prot_col].values,
        "timestamp": df["_timestamp"].values,
    }
    if moist_col and moist_col in df.columns:
        keep_cols["moisture"] = df[moist_col].values

    gdf = gpd.GeoDataFrame(keep_cols, geometry=geometry, crs=input_crs)
    gdf = gdf.to_crs(project_crs)
    logger.info(f"Reprojected from {input_crs} to {project_crs}")

    # ── Boundary clip ─────────────────────────────────────────────────────────
    boundary_path = Path(cfg["inputs"]["boundary_shp"])
    if not boundary_path.exists():
        logger.warning(f"Boundary shapefile not found: {boundary_path} — skipping clip")
        flags.append("WARNING: boundary shapefile missing — clip skipped")
    else:
        bdf = gpd.read_file(boundary_path)
        match = bdf[bdf["FIELD_NAME"] == paddock_name]
        if match.empty:
            logger.warning(
                f"No boundary polygon found for FIELD_NAME='{paddock_name}' — skipping clip"
            )
            flags.append(f"WARNING: no boundary polygon for '{paddock_name}' — clip skipped")
        else:
            poly_gdf = match.to_crs(project_crs)
            boundary = poly_gdf.geometry.union_all()

            buffer_m = cfg["ingest"]["boundary_buffer_m"]
            buffered = boundary.buffer(buffer_m)
            inside = gdf.geometry.within(buffered)
            pct_outside = (~inside).mean() * 100
            logger.info(
                f"{pct_outside:.2f}% of protein points fall outside the paddock "
                f"boundary (buffered {buffer_m}m)"
            )
            flags.append(f"pct_outside_boundary={pct_outside:.2f}")

            if pct_outside > cfg["ingest"]["max_outside_boundary_pct"]:
                logger.warning(
                    f"{pct_outside:.2f}% of protein points outside boundary exceeds "
                    f"threshold {cfg['ingest']['max_outside_boundary_pct']}%"
                )
                flags.append("WARNING: pct_outside_boundary exceeds threshold")

            n_before_clip = len(gdf)
            gdf = gdf.loc[inside].reset_index(drop=True)
            n_clipped = n_before_clip - len(gdf)
            if n_clipped:
                logger.info(f"Clipped {n_clipped} points outside boundary")
                flags.append(f"clipped_outside_boundary={n_clipped}")

    n_final = len(gdf)
    logger.info(f"Final protein point count: {n_final}")
    flags.append(f"final_record_count={n_final}")

    # ── Write output shapefile ────────────────────────────────────────────────
    output_dir = Path(cfg["outputs"]["processed_protein"])
    out_shp = output_dir / f"{paddock_id}_{season}_protein_clean.shp"

    gdf.to_file(out_shp)
    logger.info(f"Written: {out_shp}")

    log_run_entry(
        log_dir="logs",
        script="03_clean_protein.py",
        paddock_id=paddock_id,
        inputs={"protein_csv": str(csv_path)},
        outputs={
            "shapefile": str(out_shp),
            "record_count": n_final,
        },
        flags=flags,
        status="success",
    )


if __name__ == "__main__":
    main()
