"""
Stage 0 - Convert CN1 harvest data to point shapefile via CN1 SDK

Config: config/paddock_config.yaml
"""

import os
import sys
import time
from datetime import date
from pathlib import Path

import geopandas as gpd
from shapely.geometry import Point

import cn1_sdk
from utils import ensure_output_dirs, load_config, log_run_entry, setup_logger

# Maps ADAPT representation codes to output shapefile field names (<=10 chars for DBF)
CODE_TO_COLUMN = {
    "vrYieldWetMass": "yield_kg",
    "vrDeltaArea": "area_m2",
    "vrHarvestMoisture": "moist_pct",
    "vrConstituentCrudeProtein": "protn_pct",
    "vrDistanceTraveled": "dist_m",
    "vrElevation": "elev_m",
    "vrHeading": "heading",
    "vrNumSatellites": "num_sat",
    "dtSignalType": "signal",
    "dtRecordingStatus": "rec_status",
}

# 16-bit-unsigned "no data" sentinel (65535 / 10) seen on unconfigured sensors
PROTEIN_SENTINEL = 6553.5


def main():
    cfg = load_config()
    run = cfg["run"]
    paddock_id = run["paddock_id"]
    paddock_name = run["paddock_name"]
    season = run["season"]

    logger = setup_logger("00_cn1_to_points")
    ensure_output_dirs(cfg)

    # WA grain harvest runs through the southern-hemisphere summer (Oct-Jan),
    # so a season's harvest can span into the following calendar year.
    season_year = int(season)
    season_start = date(season_year, 10, 1)
    season_end = date(season_year + 1, 1, 31)

    cn1_path = cfg["inputs"]["cn1_path"]
    logger.info(f"Importing CN1 data from {cn1_path}")
    t0 = time.time()
    adm = cn1_sdk.import_adm(cn1_path)
    logger.info(f"Import took {time.time() - t0:.1f}s")

    cat = adm.Catalog
    fields = {f.Id.ReferenceId: f.Description for f in cat.Fields}

    rows = []
    matched_ops = 0
    protein_sentinel_count = 0
    machine_ids = set()

    for ld in adm.Documents.LoggedData:
        if fields.get(ld.FieldId) != paddock_name:
            continue
        for op in ld.OperationData:
            if str(op.OperationType) != "Harvesting":
                continue

            first_sr = next(iter(op.GetSpatialRecords()))
            first_ts = first_sr.Timestamp
            op_date = date(first_ts.Year, first_ts.Month, first_ts.Day)
            if not (season_start <= op_date <= season_end):
                logger.info(
                    f"Skipping LoggedData {ld.Id.ReferenceId} / OperationData "
                    f"{op.Id.ReferenceId}: date {op_date} outside season window "
                    f"{season_start}..{season_end}"
                )
                continue

            matched_ops += 1
            machine_id = cn1_sdk.resolve_machine_id(cat, op)
            if machine_id is None:
                logger.warning(
                    f"Could not resolve machine_id for LoggedData "
                    f"{ld.Id.ReferenceId} / OperationData {op.Id.ReferenceId}"
                )
            else:
                machine_ids.add(machine_id)

            deu = list(op.GetDeviceElementUses(0))[0]
            wds = list(deu.GetWorkingDatas())

            for sr in op.GetSpatialRecords():
                ts = sr.Timestamp
                row = {col: None for col in CODE_TO_COLUMN.values()}
                row.update(
                    {
                        "timestamp": (
                            f"{ts.Year:04d}-{ts.Month:02d}-{ts.Day:02d}T"
                            f"{ts.Hour:02d}:{ts.Minute:02d}:{ts.Second:02d}"
                        ),
                        "geometry": Point(sr.Geometry.X, sr.Geometry.Y),
                        "ld_id": ld.Id.ReferenceId,
                        "machine_id": machine_id,
                    }
                )

                for wd in wds:
                    column = CODE_TO_COLUMN.get(wd.Representation.Code)
                    if column is None:
                        continue
                    value, _unit = cn1_sdk.decode_meter_value(sr, wd)
                    row[column] = value

                if row["protn_pct"] == PROTEIN_SENTINEL:
                    row["protn_pct"] = None
                    protein_sentinel_count += 1

                yield_kg = row["yield_kg"]
                area_m2 = row["area_m2"]
                row["yield_tha"] = (yield_kg / area_m2 * 10) if area_m2 else None

                rows.append(row)

    if matched_ops == 0:
        logger.error(f"No Harvesting OperationData found for field '{paddock_name}'")
        sys.exit(1)

    logger.info(f"Matched {matched_ops} harvest operation(s), {len(rows)} spatial records")
    logger.info(f"Protein sentinel (no-data) values filtered: {protein_sentinel_count}")
    logger.info(f"Machine IDs: {sorted(machine_ids) or 'none resolved'}")

    gdf = gpd.GeoDataFrame(rows, geometry="geometry", crs=cfg["crs"]["input_gps"])
    gdf = gdf.to_crs(cfg["crs"]["project"])

    output_dir = Path("data/raw/yield")
    output_dir.mkdir(parents=True, exist_ok=True)
    output_path = output_dir / f"{paddock_id}_{season}_yield_points.shp"
    gdf.to_file(output_path)
    logger.info(f"Wrote {len(gdf)} points to {output_path}")

    log_run_entry(
        log_dir="logs",
        script="00_cn1_to_points.py",
        paddock_id=paddock_id,
        inputs={"cn1_path": cn1_path, "paddock_name": paddock_name},
        outputs={"shapefile": str(output_path), "record_count": len(gdf)},
        flags=[
            f"protein_sentinel_filtered={protein_sentinel_count}",
            f"machine_ids={sorted(machine_ids)}",
        ],
        status="success",
    )


if __name__ == "__main__":
    main()
    # The CN1/ADAPT SDK (loaded via pythonnet) can leave .NET runtime threads
    # that hang the normal Python interpreter shutdown for hours. All output
    # files and logs are flushed/closed by this point, so force-exit instead.
    os._exit(0)
