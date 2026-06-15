"""
Stage 2 - Per-machine normalisation and pyprecag cleaning

Config: config/paddock_config.yaml
"""

import sys
from pathlib import Path

import geopandas as gpd
import pandas as pd
from pyprecag.processing import clean_trim_points

from utils import ensure_output_dirs, load_config, log_run_entry, setup_logger


def main():
    cfg = load_config()
    run = cfg["run"]
    paddock_id = run["paddock_id"]
    paddock_name = run["paddock_name"]
    season = run["season"]
    yc = cfg["yield_cleaning"]

    logger = setup_logger("02_clean_yield")
    ensure_output_dirs(cfg)

    flags = []

    # ── Load Stage 0 output ──────────────────────────────────────────────────
    input_path = Path("data/raw/yield") / f"{paddock_id}_{season}_yield_points.shp"
    if not input_path.exists():
        logger.error(f"Yield shapefile not found: {input_path}")
        log_run_entry(
            log_dir="logs",
            script="02_clean_yield.py",
            paddock_id=paddock_id,
            inputs={"yield_shp": str(input_path)},
            outputs={},
            flags=[f"yield shapefile missing: {input_path}"],
            status="failed",
        )
        sys.exit(1)

    gdf = gpd.read_file(input_path)
    n_loaded = len(gdf)
    logger.info(f"Loaded {n_loaded} yield points from {input_path}")

    # ── Speed filter ──────────────────────────────────────────────────────────
    gdf = gdf.sort_values(["ld_id", "timestamp"]).reset_index(drop=True)
    ts = pd.to_datetime(gdf["timestamp"])
    dt_seconds = ts.groupby(gdf["ld_id"]).diff().dt.total_seconds()
    speed_kmh = gdf["dist_m"] / dt_seconds * 3.6

    speed_min = yc["speed_min_kmh"]
    speed_max = yc["speed_max_kmh"]
    speed_ok = speed_kmh.between(speed_min, speed_max)
    gdf = gdf.loc[speed_ok].reset_index(drop=True)
    n_speed = len(gdf)
    logger.info(
        f"Speed filter [{speed_min}, {speed_max}] km/h: kept {n_speed}/{n_loaded} "
        f"({n_speed / n_loaded * 100:.1f}%)"
    )
    flags.append(f"speed_filter_kept={n_speed}/{n_loaded}")

    # ── Per-machine normalisation (Section 8) ───────────────────────────────
    machine_field = yc["machine_id_field"]
    if machine_field not in gdf.columns or gdf[machine_field].dropna().empty:
        logger.warning(
            f"machine_id field '{machine_field}' missing or empty; skipping normalisation"
        )
        gdf["yldtha"] = gdf["yield_tha"]
        flags.append(
            "WARNING: machine_id field missing or empty - normalisation skipped"
        )
    else:
        grand_mean = gdf["yield_tha"].mean()
        machine_means = gdf.groupby(machine_field)["yield_tha"].mean()
        warn_pct = yc["machine_offset_warn_pct"]
        offsets = {}
        for machine, mean in machine_means.items():
            offset = grand_mean - mean
            offsets[machine] = offset
            pct = abs(offset) / grand_mean * 100
            logger.info(
                f"Machine {machine}: mean={mean:.4f}, grand_mean={grand_mean:.4f}, "
                f"offset={offset:.4f} ({pct:.2f}% of grand mean)"
            )
            if pct > warn_pct:
                logger.warning(
                    f"Machine {machine} offset {pct:.2f}% exceeds "
                    f"machine_offset_warn_pct ({warn_pct}%)"
                )
                flags.append(f"WARNING: machine {machine} offset {pct:.2f}% > {warn_pct}%")

        gdf["yldtha"] = gdf["yield_tha"] + gdf[machine_field].map(offsets)
        flags.append(
            "machine_offsets=" + str({k: round(float(v), 4) for k, v in offsets.items()})
        )

    # ── Percentile trim ───────────────────────────────────────────────────────
    lower_pct = yc["yield_lower_pct"]
    upper_pct = yc["yield_upper_pct"]
    q_lower, q_upper = gdf["yldtha"].quantile([lower_pct / 100, upper_pct / 100])
    pct_ok = gdf["yldtha"].between(q_lower, q_upper)
    gdf = gdf.loc[pct_ok].reset_index(drop=True)
    n_pct = len(gdf)
    logger.info(
        f"Percentile trim [{lower_pct}, {upper_pct}] -> [{q_lower:.4f}, {q_upper:.4f}] "
        f"t/ha: kept {n_pct}/{n_speed}"
    )
    flags.append(f"percentile_trim_kept={n_pct}/{n_speed}")

    # ── Boundary polygon (required by pyprecag clean_trim_points) ───────────
    boundary_path = Path(cfg["inputs"]["boundary_shp"])
    if not boundary_path.exists():
        logger.error(f"Boundary shapefile not found: {boundary_path}")
        log_run_entry(
            log_dir="logs",
            script="02_clean_yield.py",
            paddock_id=paddock_id,
            inputs={"yield_shp": str(input_path), "boundary_shp": str(boundary_path)},
            outputs={},
            flags=flags + [f"boundary shapefile missing: {boundary_path}"],
            status="failed",
        )
        sys.exit(1)

    bdf = gpd.read_file(boundary_path)
    match = bdf[bdf["FIELD_NAME"] == paddock_name]
    if match.empty:
        logger.error(f"No boundary polygon found for FIELD_NAME='{paddock_name}'")
        log_run_entry(
            log_dir="logs",
            script="02_clean_yield.py",
            paddock_id=paddock_id,
            inputs={"yield_shp": str(input_path), "boundary_shp": str(boundary_path)},
            outputs={},
            flags=flags + [f"no boundary polygon for FIELD_NAME='{paddock_name}'"],
            status="failed",
        )
        sys.exit(1)

    poly_gdf = match.to_crs(gdf.crs)

    # ── pyprecag clean/trim ───────────────────────────────────────────────────
    output_dir = Path(cfg["outputs"]["processed_yield"])
    # pyprecag redirects relative shapefile paths into a TEMPDIR, so use absolute paths
    out_keep = (output_dir / f"{paddock_id}_{season}_yield_clean.shp").resolve()
    out_removed = (output_dir / f"{paddock_id}_{season}_yield_removed.shp").resolve()
    out_csv = (output_dir / f"{paddock_id}_{season}_yield_clean.csv").resolve()

    gdf_clean, _ = clean_trim_points(
        points_geodataframe=gdf,
        points_crs=None,
        process_column="yldtha",
        output_csvfile=str(out_csv),
        poly_geodataframe=poly_gdf,
        out_keep_shapefile=str(out_keep),
        out_removed_shapefile=str(out_removed),
        remove_zeros=True,
        stdevs=yc["outlier_stdevs"],
        iterative=True,
        thin_dist_m=yc["thin_dist_m"],
    )

    n_clean = len(gdf_clean)
    logger.info(f"pyprecag clean_trim_points: kept {n_clean}/{n_pct}")
    flags.append(f"pyprecag_kept={n_clean}/{n_pct}")
    flags.append(f"final_record_count={n_clean}")

    log_run_entry(
        log_dir="logs",
        script="02_clean_yield.py",
        paddock_id=paddock_id,
        inputs={
            "yield_shp": str(input_path),
            "boundary_shp": str(boundary_path),
        },
        outputs={
            "shapefile": str(out_keep),
            "removed_shapefile": str(out_removed),
            "csv": str(out_csv),
            "record_count": n_clean,
        },
        flags=flags,
        status="success",
    )


if __name__ == "__main__":
    main()
