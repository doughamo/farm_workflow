"""
Stage 7 - Prescription generation

Reads agronomist-assigned N rates from the Stage 6 zone summary CSV, joins
them to the zone polygons, adds AFS display attribute fields (RATE, DEF_RATE,
MIN_RATE, MAX_RATE), injects a reference strip geometry (Section 9 / 13), and
writes the final AFS-compatible prescription shapefile.

Also writes a structured recommendation JSON recording the full provenance of
this prescription event.

Inputs:
  data/outputs/zones/{paddock_id}_{season}_zones.shp
  data/outputs/handoff/{paddock_id}_{season}_zone_summary.csv
    (agronomist fills n_rate_kg_ha column before running this stage)
  data/raw/boundaries/fields.shp  (for reference strip geometry)

Outputs:
  data/outputs/prescriptions/{paddock_id}_{season}_prescription.shp
  data/outputs/prescriptions/{paddock_id}_{season}_recommendation.json

Config: config/paddock_config.yaml
"""

import csv
import json
import math
import sys
from datetime import date
from pathlib import Path

import geopandas as gpd
import numpy as np
from shapely.affinity import rotate
from shapely.geometry import box

from utils import ensure_output_dirs, load_config, log_run_entry, setup_logger


# ── Rate CSV helpers ──────────────────────────────────────────────────────────

def read_zone_rates(csv_path):
    """
    Read the agronomist-completed zone summary CSV.
    Returns dict {zone_id (int): rate (float)} for every row that has a value
    in n_rate_kg_ha.  Raises ValueError if the column is absent.
    """
    rates = {}
    with open(csv_path, newline="") as f:
        reader = csv.DictReader(f)
        if "n_rate_kg_ha" not in reader.fieldnames:
            raise ValueError(
                f"n_rate_kg_ha column not found in {csv_path}. "
                "Run Stage 6 to regenerate the zone summary CSV."
            )
        for row in reader:
            raw = row["n_rate_kg_ha"].strip()
            if raw:
                try:
                    rates[int(row["zone_id"])] = float(raw)
                except (ValueError, KeyError):
                    pass
    return rates


def validate_rates(zone_gdf, zone_rates, min_rate, max_rate, logger):
    """
    Check every zone has a rate assigned and rates are within [min_rate, max_rate].
    Returns False if validation fails.
    """
    ok = True
    all_zone_ids = set(zone_gdf["zone_id"].astype(int))
    for zid in all_zone_ids:
        if zid not in zone_rates:
            logger.error(
                f"Zone {zid} has no rate assigned in the zone summary CSV. "
                "Fill in n_rate_kg_ha for all zones and re-run Stage 7."
            )
            ok = False
        else:
            r = zone_rates[zid]
            if r < min_rate or r > max_rate:
                logger.error(
                    f"Zone {zid} rate {r} kg/ha is outside allowed range "
                    f"[{min_rate}, {max_rate}]."
                )
                ok = False
    return ok


# ── Reference strip helpers ───────────────────────────────────────────────────

def _strip_heading_from_boundary(boundary_poly):
    """
    Derive strip heading (degrees clockwise from north) from the longer axis
    of the paddock's minimum rotated bounding rectangle.
    Returns heading in [0, 180).
    """
    rect = boundary_poly.minimum_rotated_rectangle
    coords = list(rect.exterior.coords)
    # Pick the longer of the two sides
    dx0 = coords[1][0] - coords[0][0]
    dy0 = coords[1][1] - coords[0][1]
    dx1 = coords[2][0] - coords[1][0]
    dy1 = coords[2][1] - coords[1][1]
    len0 = math.hypot(dx0, dy0)
    len1 = math.hypot(dx1, dy1)
    if len1 > len0:
        dx0, dy0 = dx1, dy1
    # Convert vector to bearing (degrees clockwise from north)
    bearing = (math.degrees(math.atan2(dx0, dy0))) % 360
    # Normalise to [0, 180) — a strip is symmetric
    if bearing >= 180:
        bearing -= 180
    return bearing


def generate_reference_strip(boundary_poly, width_m, heading_deg=None):
    """
    Auto-generate a reference strip polygon: a rectangle of given width
    running the full length of the paddock along the specified heading,
    centred on the paddock centroid, clipped to the paddock boundary.

    heading_deg: degrees clockwise from north; None = auto-derive from
                 the longest paddock axis.
    Returns a Shapely Polygon (or None if intersection is empty).
    """
    if heading_deg is None:
        heading_deg = _strip_heading_from_boundary(boundary_poly)

    centroid = boundary_poly.centroid
    minx, miny, maxx, maxy = boundary_poly.bounds
    # Length long enough to span the whole paddock
    length = math.hypot(maxx - minx, maxy - miny) * 1.2

    # Build a N-S oriented rectangle centred at origin, then rotate
    half_w = width_m / 2.0
    half_l = length / 2.0
    rect = box(-half_w, -half_l, half_w, half_l)

    # rotate() uses degrees anti-clockwise; heading_deg is CW from N (= CCW from E by 90-h)
    # shapely rotate: angle measured CCW; bearing from N CW = CCW angle = (90 - bearing)
    # We built the rect pointing N (along Y), so rotate by -heading_deg (CW)
    strip = rotate(rect, -heading_deg, origin=(0, 0))

    from shapely.affinity import translate
    strip = translate(strip, centroid.x, centroid.y)
    clipped = strip.intersection(boundary_poly)
    if clipped.is_empty:
        return None
    return clipped


# ── Prescription builder ──────────────────────────────────────────────────────

def build_prescription(zone_gdf, zone_rates, ref_strip_poly, ref_rate,
                       rx_cfg, ref_strip_enabled, logger):
    """
    Build the prescription GeoDataFrame.

    Columns added:
      RATE      — prescribed N rate (kg/ha)
      DEF_RATE  — default rate (covers any unzoned area and headlands)
      MIN_RATE  — minimum allowed rate (AFS display safety check)
      MAX_RATE  — maximum allowed rate (AFS display safety check)
      feature   — 'zone' or 'reference_strip'

    Returns the prescription GeoDataFrame.
    """
    rate_field  = rx_cfg["rate_field_name"]
    def_field   = rx_cfg["default_rate_field"]
    min_field   = rx_cfg["min_rate_field"]
    max_field   = rx_cfg["max_rate_field"]
    min_rate    = rx_cfg["min_rate"]
    max_rate    = rx_cfg["max_rate"]

    assigned_rates = [zone_rates[int(z)] for z in zone_gdf["zone_id"]]
    default_rate = rx_cfg.get("default_rate")
    if default_rate is None:
        default_rate = round(float(np.mean(assigned_rates)), 1)
        logger.info(f"default_rate derived as mean of zone rates: {default_rate} kg/ha")

    rows = []
    for _, row in zone_gdf.iterrows():
        zid  = int(row["zone_id"])
        rate = zone_rates[zid]
        r = dict(row)
        r[rate_field] = rate
        r[def_field]  = default_rate
        r[min_field]  = min_rate
        r[max_field]  = max_rate
        r["feature"]  = "zone"
        rows.append(r)

    if ref_strip_enabled and ref_strip_poly is not None:
        ref_row = {
            "zone_id":  0,
            "area_ha":  round(ref_strip_poly.area / 10_000, 3),
            rate_field: ref_rate,
            def_field:  default_rate,
            min_field:  min_rate,
            max_field:  max_rate,
            "feature":  "reference_strip",
            "geometry": ref_strip_poly,
        }
        rows.append(ref_row)
        logger.info(
            f"Reference strip injected: {ref_row['area_ha']:.3f} ha, "
            f"RATE={ref_rate} kg/ha"
        )

    return gpd.GeoDataFrame(rows, crs=zone_gdf.crs)


# ── Recommendation object ─────────────────────────────────────────────────────

def write_recommendation_json(
    out_path, paddock_id, paddock_name, season, input_type,
    confidence_level, zone_method, zone_gdf, zone_rates,
    ref_strip_enabled, ref_strip_area_ha, ref_rate,
    flags, rx_cfg,
):
    """
    Write a structured JSON provenance record for this prescription event.
    Fields that require agronomist input (decision, farmer_confirmation) are
    written as null placeholders — to be updated by the review workflow.
    """
    zones_summary = []
    for _, row in zone_gdf[zone_gdf["feature"] == "zone"].iterrows():
        zid = int(row["zone_id"])
        z = {
            "zone_id":    zid,
            "zone_label": row.get("zone_label", ""),
            "area_ha":    float(row["area_ha"]),
            "n_rate_kg_ha": zone_rates.get(zid),
        }
        if row.get("mean_yield"):
            z["mean_yield_tha"] = round(float(row["mean_yield"]), 3)
        if row.get("mean_prot") and not np.isnan(float(row.get("mean_prot", float("nan")))):
            z["mean_protein_pct"] = round(float(row["mean_prot"]), 2)
        zones_summary.append(z)

    obj = {
        "paddock_id":       paddock_id,
        "paddock_name":     paddock_name,
        "season":           season,
        "generated_date":   date.today().isoformat(),
        "input_type":       input_type,
        "confidence_level": confidence_level,
        "zone_method":      zone_method,
        "n_zones":          len(zones_summary),
        "prescription_rates": {
            "unit": rx_cfg["rate_unit"],
            "min_allowed": rx_cfg["min_rate"],
            "max_allowed": rx_cfg["max_rate"],
            "default_rate": rx_cfg.get("default_rate"),
        },
        "zones":            zones_summary,
        "reference_strip":  {
            "enabled":   ref_strip_enabled,
            "area_ha":   ref_strip_area_ha,
            "rate_kg_ha": ref_rate,
        },
        "flags":            flags,
        # ── Fields to be completed by agronomist / platform UI ────────────────
        "farmer_confirmation":  None,  # required for Level 1 before delivery
        "agronomist_decision":  None,  # approved / modified / rejected
        "agronomist_id":        None,
        "agronomist_notes":     None,
        "boundary_modifications": None,
        "provenance_hash":      None,  # to be computed on approval
    }

    with open(out_path, "w") as f:
        json.dump(obj, f, indent=2)


# ── Main ──────────────────────────────────────────────────────────────────────

def main():
    cfg = load_config()
    run = cfg["run"]
    paddock_id   = run["paddock_id"]
    paddock_name = run["paddock_name"]
    season       = run["season"]
    rx_cfg       = cfg["prescription"]
    rs_cfg       = cfg.get("reference_strip", {})

    logger = setup_logger("07_prescription")
    ensure_output_dirs(cfg)
    flags = []

    input_type = rx_cfg.get("input_type", "nitrogen")
    if input_type != "nitrogen":
        logger.warning(
            f"input_type='{input_type}' is not supported in v1 — "
            "only 'nitrogen' is in scope. Exiting."
        )
        sys.exit(0)

    # ── Load zone shapefile ───────────────────────────────────────────────────
    zones_dir = Path(cfg["outputs"]["zones"])
    zones_shp = zones_dir / f"{paddock_id}_{season}_zones.shp"
    if not zones_shp.exists():
        logger.error(f"Zone shapefile not found: {zones_shp} — run Stage 5 first")
        sys.exit(1)

    zone_gdf = gpd.read_file(zones_shp)
    n_zones  = len(zone_gdf)
    confidence_level = int(zone_gdf["cov_status"].iloc[0] != "insufficient") + 1 \
        if "cov_status" in zone_gdf.columns else 1
    # Pull confidence_level attribute if written by Stage 5 (preferred)
    if "conf_lvl" in zone_gdf.columns:
        confidence_level = int(zone_gdf["conf_lvl"].iloc[0])
    # Stage 5 writes it to the run log flags, not the shapefile — so derive from method
    zone_method = zone_gdf["method"].iloc[0] if "method" in zone_gdf.columns else "unknown"
    layers_used = zone_gdf["layers"].iloc[0] if "layers" in zone_gdf.columns else "unknown"
    if "protein" in layers_used:
        confidence_level = 2
    else:
        confidence_level = 1

    logger.info(
        f"Zones: {n_zones} zones  |  method={zone_method}  |  "
        f"confidence_level={confidence_level}"
    )
    flags.append(f"confidence_level={confidence_level}")
    flags.append(f"zone_method={zone_method}")

    # ── Read agronomist-assigned rates ────────────────────────────────────────
    handoff_dir = Path(cfg["outputs"]["handoff"])
    summary_csv = handoff_dir / f"{paddock_id}_{season}_zone_summary.csv"
    if not summary_csv.exists():
        logger.error(
            f"Zone summary CSV not found: {summary_csv} — run Stage 6 first"
        )
        sys.exit(1)

    try:
        zone_rates = read_zone_rates(summary_csv)
    except ValueError as e:
        logger.error(str(e))
        sys.exit(1)

    if not zone_rates:
        logger.error(
            f"No rates found in {summary_csv.name}. "
            "Fill in the n_rate_kg_ha column for each zone and re-run Stage 7."
        )
        sys.exit(1)

    min_rate = rx_cfg.get("min_rate", 0)
    max_rate = rx_cfg.get("max_rate", 200)
    if not validate_rates(zone_gdf, zone_rates, min_rate, max_rate, logger):
        sys.exit(1)

    for zid, r in sorted(zone_rates.items()):
        logger.info(f"  Zone {zid} rate: {r} kg/ha")
        flags.append(f"zone{zid}_rate_kg_ha={r}")

    # ── Load boundary (needed for reference strip) ────────────────────────────
    bdf = gpd.read_file(cfg["inputs"]["boundary_shp"])
    boundary_row = bdf[bdf["FIELD_NAME"] == paddock_name]
    if boundary_row.empty:
        logger.warning(
            f"Boundary not found for '{paddock_name}' — reference strip auto-generation will fail"
        )
        boundary_poly = None
    else:
        boundary_poly = (
            boundary_row.to_crs(zone_gdf.crs).union_all()
        )

    # ── Reference strip ───────────────────────────────────────────────────────
    ref_enabled    = rs_cfg.get("enabled", True)
    ref_rate       = float(rs_cfg.get("rate_kg_ha", 0))
    ref_width      = float(rs_cfg.get("width_m", 24))
    ref_heading    = rs_cfg.get("heading_deg", None)
    ref_geom_shp   = rs_cfg.get("geometry_shp", None)
    ref_strip_poly = None
    ref_strip_area = 0.0

    if ref_enabled:
        if ref_geom_shp:
            # User-provided strip geometry
            rs_path = Path(ref_geom_shp)
            if not rs_path.exists():
                logger.warning(
                    f"reference_strip.geometry_shp not found: {rs_path} — "
                    "falling back to auto-generation"
                )
                ref_geom_shp = None

        if ref_geom_shp:
            rs_gdf = gpd.read_file(ref_geom_shp).to_crs(zone_gdf.crs)
            ref_strip_poly = rs_gdf.union_all()
            if boundary_poly is not None:
                ref_strip_poly = ref_strip_poly.intersection(boundary_poly)
            ref_strip_area = round(ref_strip_poly.area / 10_000, 3)
            flags.append("reference_strip_source=user_shapefile")
            logger.info(
                f"Reference strip loaded from shapefile: {ref_strip_area:.3f} ha"
            )
        elif boundary_poly is not None:
            ref_strip_poly = generate_reference_strip(
                boundary_poly, ref_width, ref_heading
            )
            if ref_strip_poly is not None:
                ref_strip_area = round(ref_strip_poly.area / 10_000, 3)
                heading_used = (
                    ref_heading if ref_heading is not None
                    else round(_strip_heading_from_boundary(boundary_poly), 1)
                )
                flags.append(f"reference_strip_source=auto_generated")
                flags.append(f"reference_strip_heading_deg={heading_used}")
                logger.info(
                    f"Reference strip auto-generated: {ref_strip_area:.3f} ha, "
                    f"heading={heading_used}°, width={ref_width} m"
                )
            else:
                logger.warning(
                    "Reference strip auto-generation produced empty geometry — "
                    "strip omitted from prescription"
                )
                ref_enabled = False
        else:
            logger.warning(
                "No boundary available for reference strip generation — "
                "strip omitted from prescription"
            )
            ref_enabled = False
    else:
        logger.info("Reference strip disabled in config — omitted from prescription")
        flags.append("reference_strip_source=disabled")

    flags.append(f"reference_strip_enabled={ref_enabled}")
    flags.append(f"reference_strip_area_ha={ref_strip_area:.3f}")

    # ── Build prescription GeoDataFrame ──────────────────────────────────────
    prx_gdf = build_prescription(
        zone_gdf, zone_rates, ref_strip_poly, ref_rate,
        rx_cfg, ref_enabled, logger,
    )

    # Update default_rate in rx_cfg if it was derived
    if rx_cfg.get("default_rate") is None:
        assigned_rates = [zone_rates[int(z)] for z in zone_gdf["zone_id"]]
        rx_cfg["default_rate"] = round(float(np.mean(assigned_rates)), 1)

    # ── Write prescription shapefile ──────────────────────────────────────────
    out_dir = Path(cfg["outputs"]["prescriptions"])
    out_shp = out_dir / f"{paddock_id}_{season}_prescription.shp"
    out_json = out_dir / f"{paddock_id}_{season}_recommendation.json"

    prx_gdf.to_file(out_shp)
    logger.info(f"Prescription shapefile written: {out_shp}")
    flags.append(f"prescription_shp={out_shp.name}")
    flags.append(f"prescription_features={len(prx_gdf)}")

    # ── Write recommendation JSON ─────────────────────────────────────────────
    write_recommendation_json(
        out_json,
        paddock_id=paddock_id,
        paddock_name=paddock_name,
        season=season,
        input_type=input_type,
        confidence_level=confidence_level,
        zone_method=zone_method,
        zone_gdf=prx_gdf,
        zone_rates=zone_rates,
        ref_strip_enabled=ref_enabled,
        ref_strip_area_ha=ref_strip_area,
        ref_rate=ref_rate,
        flags=flags,
        rx_cfg=rx_cfg,
    )
    logger.info(f"Recommendation object written: {out_json}")
    flags.append(f"recommendation_json={out_json.name}")

    # ── Rate summary ──────────────────────────────────────────────────────────
    rate_field = rx_cfg["rate_field_name"]
    zone_rows = prx_gdf[prx_gdf["feature"] == "zone"]
    total_area = float(zone_rows["area_ha"].sum())
    weighted_rate = float(
        (zone_rows["area_ha"] * zone_rows[rate_field]).sum() / total_area
    ) if total_area > 0 else 0.0
    logger.info(
        f"Prescription summary: {n_zones} zones, "
        f"total area {total_area:.1f} ha, "
        f"area-weighted mean rate {weighted_rate:.1f} kg/ha"
    )
    flags.append(f"total_zone_area_ha={total_area:.1f}")
    flags.append(f"weighted_mean_rate_kg_ha={weighted_rate:.1f}")

    if confidence_level == 1:
        logger.warning(
            "Level 1 prescription — farmer confirmation is required before "
            "delivery. Record confirmation in recommendation.json "
            "(farmer_confirmation field) and seek agronomist sign-off."
        )
        flags.append("farmer_confirmation_required=true")

    log_run_entry(
        log_dir="logs",
        script="07_prescription.py",
        paddock_id=paddock_id,
        inputs={
            "zones_shp": str(zones_shp),
            "zone_summary_csv": str(summary_csv),
        },
        outputs={
            "prescription_shp": str(out_shp),
            "recommendation_json": str(out_json),
            "n_zones": n_zones,
            "reference_strip_enabled": ref_enabled,
        },
        flags=flags,
        status="success",
    )


if __name__ == "__main__":
    main()
