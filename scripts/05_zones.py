"""
Stage 5 - Management zone delineation

Two methods, selected by confidence level based on data availability for v1:

Level 1 (yield only, no protein):
  K-Means clustering (k=3) on yield z-score raster.
  Output: _zones.shp (3 zones, labelled Low/Mid/High yield)

Level 2 (yield + protein, single season):
  Primary — Protein Yield Correlation Matrix (2x2 quadrant, Section 18C):
    Yield axis: median split OR French and Schultz water-limited potential yield
                (method set via protein_yield_matrix.yield_axis_method in config)
    Protein axis: critical_protein_threshold (default 10.5%, configurable)
    Zone IDs: 1=LY-LP, 2=LY-HP, 3=HY-LP, 4=HY-HP
    Output: _zones.shp (primary, up to 4 zones)
  Secondary — K-Means yield+protein composite for reference
    Output: _zones_kmeans.shp (3 zones)

Both methods apply spatial filter (min_zone_area_ha), majority-filter smoothing,
vectorise to polygons, and compute zone statistics via spatial join to cleaned
point files.

Confidence level is provisional for v1 (single-season only). Multi-season
pooling eligibility gate (Section 13) is deferred to v1.1.

Config: config/paddock_config.yaml
"""

import csv
import sys
from pathlib import Path

import geopandas as gpd
import numpy as np
import rasterio
from rasterio.features import shapes
from scipy.ndimage import distance_transform_edt, label, uniform_filter
from shapely.geometry import shape
from sklearn.cluster import KMeans

from utils import ensure_output_dirs, load_config, log_run_entry, setup_logger

# Zone labels for K-Means (sorted low→high yield)
KMEANS_LABELS = {1: "Low", 2: "Mid", 3: "High"}

# Zone labels for Protein Yield Correlation Matrix
MATRIX_LABELS = {1: "LY-LP", 2: "LY-HP", 3: "HY-LP", 4: "HY-HP"}


def majority_filter(zone_arr, size=3, n_passes=2):
    """
    Smooth zone boundaries by replacing each pixel with the majority zone
    in a (size × size) moving window.  Pixels outside the boundary (value 0)
    are excluded from voting and restored afterwards.
    Fast vectorised implementation: one uniform_filter per zone per pass.
    """
    result = zone_arr.copy()
    outside = zone_arr == 0
    for _ in range(n_passes):
        n = int(result.max())
        votes = np.stack([
            uniform_filter((result == z).astype(np.float32), size=size)
            for z in range(1, n + 1)
        ])
        winner = np.argmax(votes, axis=0) + 1   # 1-indexed
        winner[outside] = 0                      # restore outside-boundary mask
        result = winner.astype(np.int32)
    return result


def read_coverage_status(log_dir, paddock_id):
    """Parse the most recent Stage 4 run log entry and return protein_coverage_status."""
    log_path = Path(log_dir) / "run_log.csv"
    if not log_path.exists():
        return None
    entries = []
    with open(log_path, newline="") as f:
        for row in csv.DictReader(f):
            if row["script"] == "04_normalise.py" and row["paddock_id"] == paddock_id:
                entries.append(row)
    if not entries:
        return None
    for part in entries[-1]["flags"].split(";"):
        part = part.strip()
        if part.startswith("protein_coverage_status="):
            return part.split("=", 1)[1].strip()
    return None


def read_raster_stats(log_dir, paddock_id):
    """
    Parse yield/protein raw mean and std from the most recent Stage 4 log entry.
    Returns a dict with keys yield_raw_mean, yield_raw_std, protein_raw_mean,
    protein_raw_std (floats), or an empty dict if not found.
    """
    log_path = Path(log_dir) / "run_log.csv"
    if not log_path.exists():
        return {}
    entries = []
    with open(log_path, newline="") as f:
        for row in csv.DictReader(f):
            if row["script"] == "04_normalise.py" and row["paddock_id"] == paddock_id:
                entries.append(row)
    if not entries:
        return {}
    stats = {}
    for part in entries[-1]["flags"].split(";"):
        part = part.strip()
        for key in ("yield_raw_mean", "yield_raw_std", "protein_raw_mean", "protein_raw_std"):
            if part.startswith(f"{key}="):
                try:
                    stats[key] = float(part.split("=", 1)[1])
                except ValueError:
                    pass
    return stats


def load_raster(path):
    """Return (data array float32, transform, crs). NaN = nodata."""
    with rasterio.open(path) as src:
        data = src.read(1).astype(np.float32)
        transform = src.transform
        crs = src.crs
    return data, transform, crs


def spatial_filter(zone_arr, min_pixels):
    """
    Remove zone patches smaller than min_pixels cells, reassigning them to
    the zone of the spatially nearest large-patch pixel.
    Returns the cleaned zone array (dtype int32, 0 = outside boundary).
    """
    result = zone_arr.copy()
    n_zones = int(result.max())

    for zone_id in range(1, n_zones + 1):
        zone_binary = result == zone_id
        labeled, n_comps = label(zone_binary)
        for comp in range(1, n_comps + 1):
            if (labeled == comp).sum() < min_pixels:
                result[labeled == comp] = 0   # mark for reassignment

    removed = (result == 0) & (zone_arr > 0)
    if removed.any() and (result > 0).any():
        _, nearest = distance_transform_edt(result == 0, return_indices=True)
        result[removed] = result[tuple(nearest[:, removed])]

    return result


def vectorise_zones(zone_arr, transform, crs):
    """Convert zone raster to dissolved polygon GeoDataFrame."""
    polys = []
    for geom, val in shapes(zone_arr.astype(np.int32), mask=(zone_arr > 0), transform=transform):
        polys.append({"geometry": shape(geom), "zone_id": int(val)})
    if not polys:
        return None
    gdf = gpd.GeoDataFrame(polys, crs=crs)
    gdf = gdf.dissolve(by="zone_id").reset_index()
    gdf["area_ha"] = (gdf.geometry.area / 10_000).round(2)
    return gdf


def attach_point_stats(zone_gdf, yield_clean_shp, protein_clean_shp,
                       protein_available, logger):
    """Spatial-join cleaned point files to zone polygons and append mean stats."""
    if yield_clean_shp.exists():
        yield_pts = gpd.read_file(yield_clean_shp)
        joined = gpd.sjoin(
            yield_pts[["yldtha", "geometry"]],
            zone_gdf[["zone_id", "geometry"]],
            how="left",
            predicate="within",
        )
        stats = joined.groupby("zone_id")["yldtha"].agg(
            mean_yield="mean", yield_pts="count"
        ).round(3)
        zone_gdf = zone_gdf.join(stats, on="zone_id")
        logger.info("Zone yield statistics computed from cleaned yield points")

    if protein_available and protein_clean_shp.exists():
        prot_pts = gpd.read_file(protein_clean_shp)
        joined_p = gpd.sjoin(
            prot_pts[["protein", "geometry"]],
            zone_gdf[["zone_id", "geometry"]],
            how="left",
            predicate="within",
        )
        prot_stats = joined_p.groupby("zone_id")["protein"].agg(
            mean_prot="mean", prot_pts="count"
        ).round(3)
        zone_gdf = zone_gdf.join(prot_stats, on="zone_id")
        logger.info("Zone protein statistics computed from cleaned protein points")

    return zone_gdf


def french_schultz_threshold(gsrainfall_mm, wue_t_ha_mm):
    """
    Yield threshold per French and Schultz (1984).
    threshold = (GSR_mm - 110) x WUE x 0.8
    The 0.8 factor is GRDC's economic yield convention for nitrogen budgeting.
    Returns None if GSR <= 110 mm (attainable yield undefined).
    """
    attainable = (gsrainfall_mm - 110.0) * wue_t_ha_mm
    if attainable <= 0:
        return None
    return attainable * 0.8


def compute_quadrant_zones(yield_raw, protein_raw, boundary_mask,
                           yield_threshold, protein_threshold, logger):
    """
    Classify raster cells into the 2x2 Protein Yield Correlation Matrix
    (Engel et al. 1999, Long et al. 2000).

    Zone IDs (constant regardless of paddock):
      1 = LY-LP  low yield,  low protein
      2 = LY-HP  low yield,  high protein
      3 = HY-LP  high yield, low protein
      4 = HY-HP  high yield, high protein

    Gap cells (yield finite, protein NaN from Stage 4 gap mask) are assigned
    from the spatially nearest classified cell via distance transform.
    """
    zone_arr = np.zeros(yield_raw.shape, dtype=np.int32)

    full_mask    = boundary_mask & np.isfinite(yield_raw) & np.isfinite(protein_raw)
    high_yield   = yield_raw   >= yield_threshold
    high_protein = protein_raw >= protein_threshold

    zone_arr[full_mask & ~high_yield & ~high_protein] = 1  # LY-LP
    zone_arr[full_mask & ~high_yield &  high_protein] = 2  # LY-HP
    zone_arr[full_mask &  high_yield & ~high_protein] = 3  # HY-LP
    zone_arr[full_mask &  high_yield &  high_protein] = 4  # HY-HP

    gap_cells = boundary_mask & np.isfinite(yield_raw) & ~np.isfinite(protein_raw)
    if gap_cells.any() and (zone_arr > 0).any():
        _, nearest = distance_transform_edt(zone_arr == 0, return_indices=True)
        zone_arr[gap_cells] = zone_arr[tuple(nearest[:, gap_cells])]
        logger.info(f"Matrix gap cells assigned from nearest classified cell: {gap_cells.sum():,}")

    for z, lbl in MATRIX_LABELS.items():
        count = int((zone_arr == z).sum())
        logger.info(f"  Matrix zone {z} ({lbl}): {count:,} cells")

    return zone_arr


def run_kmeans_zones(
    yield_z, protein_z, protein_available, boundary_mask,
    n_zones, random_seed, min_pixels, smooth_passes,
    transform, crs,
    yield_clean_shp, protein_clean_shp, coverage_status,
    logger,
):
    """
    Run K-Means zone delineation. Returns a GeoDataFrame with zone polygons
    and attached point statistics, or None if vectorisation produces no polygons.

    Zones are sorted by ascending mean yield z-score (zone 1 = lowest yield).
    """
    if protein_available and protein_z is not None:
        protein_valid = np.isfinite(protein_z)
        full_mask = boundary_mask & protein_valid
        gap_mask  = boundary_mask & ~protein_valid
        X_full = np.column_stack([yield_z[full_mask], protein_z[full_mask]])
        logger.info(
            f"K-Means: {full_mask.sum():,} full-coverage cells "
            f"({gap_mask.sum():,} gap cells to be assigned post-hoc)"
        )
    else:
        full_mask = boundary_mask
        gap_mask  = np.zeros_like(boundary_mask)
        X_full = yield_z[full_mask].reshape(-1, 1)
        logger.info(f"K-Means: {full_mask.sum():,} cells (yield only)")

    kmeans = KMeans(n_clusters=n_zones, random_state=random_seed, n_init=10)
    raw_labels = kmeans.fit_predict(X_full)

    centers   = kmeans.cluster_centers_
    yield_col = 0
    zone_order   = np.argsort(centers[:, yield_col])
    remap        = {int(old): new + 1 for new, old in enumerate(zone_order)}
    sorted_labels = np.array([remap[int(l)] for l in raw_labels], dtype=np.int32)

    nrows, ncols = yield_z.shape
    zone_arr = np.zeros((nrows, ncols), dtype=np.int32)
    zone_arr[full_mask] = sorted_labels

    if protein_available and gap_mask.any():
        yield_centers   = centers[zone_order, yield_col]
        yield_gap_vals  = yield_z[gap_mask].reshape(-1, 1)
        dist_to_centers = np.abs(yield_gap_vals - yield_centers.reshape(1, -1))
        gap_zone_labels = np.argmin(dist_to_centers, axis=1) + 1
        zone_arr[gap_mask] = gap_zone_labels
        logger.info(
            "K-Means gap cells assigned by yield centroid proximity: "
            + ", ".join(
                f"zone {z}={int((gap_zone_labels == z).sum()):,}"
                for z in range(1, n_zones + 1)
            )
        )

    pre_counts = {z: int((zone_arr == z).sum()) for z in range(1, n_zones + 1)}
    zone_arr   = spatial_filter(zone_arr, min_pixels)
    post_counts = {z: int((zone_arr == z).sum()) for z in range(1, n_zones + 1)}

    for z in range(1, n_zones + 1):
        net  = post_counts[z] - pre_counts[z]
        sign = "+" if net >= 0 else ""
        logger.info(
            f"K-Means zone {z}: {post_counts[z]:,} cells "
            f"[{sign}{net:,} after spatial filter]"
        )

    if smooth_passes > 0:
        zone_arr = majority_filter(zone_arr, size=3, n_passes=smooth_passes)

    zone_gdf = vectorise_zones(zone_arr, transform, crs)
    if zone_gdf is None:
        return None

    zone_gdf = attach_point_stats(
        zone_gdf, yield_clean_shp, protein_clean_shp, protein_available, logger
    )

    layers = "yield+protein" if protein_available else "yield_only"
    zone_gdf["layers"]     = layers
    zone_gdf["cov_status"] = coverage_status
    zone_gdf["method"]     = "kmeans"
    zone_gdf["zone_label"] = zone_gdf["zone_id"].map(
        {z: KMEANS_LABELS.get(z, str(z)) for z in zone_gdf["zone_id"]}
    )

    return zone_gdf


def main():
    cfg = load_config()
    run = cfg["run"]
    paddock_id   = run["paddock_id"]
    paddock_name = run["paddock_name"]
    season       = run["season"]
    zones_cfg    = cfg["zones"]
    n_zones        = zones_cfg["n_zones"]
    min_zone_area_ha = zones_cfg["min_zone_area_ha"]
    random_seed    = zones_cfg["random_seed"]
    smooth_passes  = zones_cfg.get("smooth_passes", 2)
    res            = cfg["raster"]["resolution_m"]
    pym_cfg        = cfg.get("protein_yield_matrix", {})

    logger = setup_logger("05_zones")
    ensure_output_dirs(cfg)
    flags = []

    # ── Resolve protein coverage status from Stage 4 log ─────────────────────
    coverage_status = read_coverage_status("logs", paddock_id)
    if coverage_status is None:
        logger.warning(
            "protein_coverage_status not found in run log — assuming 'ok'. "
            "Run Stage 4 before Stage 5."
        )
        coverage_status = "ok"
    logger.info(f"Protein coverage status from Stage 4: {coverage_status}")
    use_protein = coverage_status != "insufficient"

    # ── Load rasters ──────────────────────────────────────────────────────────
    raster_dir         = Path(cfg["outputs"]["rasters"])
    yield_raster_path  = raster_dir / f"{paddock_id}_{season}_yield_norm.tif"
    protein_raster_path = raster_dir / f"{paddock_id}_{season}_protein_norm.tif"

    if not yield_raster_path.exists():
        logger.error(f"Yield raster not found: {yield_raster_path}")
        sys.exit(1)

    yield_z, transform, crs = load_raster(yield_raster_path)
    nrows, ncols = yield_z.shape
    boundary_mask = np.isfinite(yield_z)
    logger.info(f"Yield raster: {nrows}×{ncols}, {boundary_mask.sum():,} valid cells")

    if use_protein and protein_raster_path.exists():
        protein_z, _, _ = load_raster(protein_raster_path)
        protein_available = True
        logger.info(f"Protein raster loaded ({coverage_status} status)")
        flags.append("layers=yield+protein")
        flags.append(f"protein_coverage_status={coverage_status}")
    else:
        protein_z = None
        protein_available = False
        reason = "coverage status=insufficient" if not use_protein else "raster missing"
        logger.warning(f"Protein excluded ({reason}) — yield-only method")
        flags.append("layers=yield_only")
        flags.append(f"protein_coverage_status={coverage_status}")

    min_pixels = int(np.ceil(min_zone_area_ha * 10_000 / res**2))

    # ── Confidence level (v1: provisional single-season assignment) ───────────
    confidence_level = 2 if protein_available else 1
    logger.info(f"Confidence level: {confidence_level} (v1 single-season provisional)")
    flags.append(f"confidence_level={confidence_level}")

    # ── Cleaned point file paths (shared by both methods) ────────────────────
    yield_clean_shp = (
        Path(cfg["outputs"]["processed_yield"])
        / f"{paddock_id}_{season}_yield_clean.shp"
    )
    protein_clean_shp = (
        Path(cfg["outputs"]["processed_protein"])
        / f"{paddock_id}_{season}_protein_clean.shp"
    )

    out_dir = Path(cfg["outputs"]["zones"])

    # =========================================================================
    # Level 2 — Protein Yield Correlation Matrix (primary) + K-Means (secondary)
    # =========================================================================
    if confidence_level >= 2:
        logger.info("Level 2: running Protein Yield Correlation Matrix as primary method")

        # ── Read raw raster stats from Stage 4 log ────────────────────────────
        raster_stats = read_raster_stats("logs", paddock_id)
        required_keys = ("yield_raw_mean", "yield_raw_std",
                         "protein_raw_mean", "protein_raw_std")

        if not all(k in raster_stats for k in required_keys):
            logger.warning(
                "Incomplete raster stats in Stage 4 log — cannot run matrix method. "
                "Falling back to K-Means as primary."
            )
            confidence_level = 1
        else:
            yield_mean_raw   = raster_stats["yield_raw_mean"]
            yield_std_raw    = raster_stats["yield_raw_std"]
            protein_mean_raw = raster_stats["protein_raw_mean"]
            protein_std_raw  = raster_stats["protein_raw_std"]

            # Back-calculate raw values from z-score rasters
            yield_raw = np.where(
                np.isfinite(yield_z),
                yield_z * yield_std_raw + yield_mean_raw,
                np.nan,
            ).astype(np.float32)
            protein_raw = np.where(
                np.isfinite(protein_z),
                protein_z * protein_std_raw + protein_mean_raw,
                np.nan,
            ).astype(np.float32)

            # ── Yield threshold ───────────────────────────────────────────────
            yield_axis_method = pym_cfg.get("yield_axis_method", "median")

            if yield_axis_method == "french_schultz":
                gsrainfall = pym_cfg.get("french_schultz_gsrainfall_mm")
                wue        = pym_cfg.get("french_schultz_wue_t_ha_mm", 0.015)
                if gsrainfall is None:
                    logger.warning(
                        "yield_axis_method='french_schultz' but "
                        "french_schultz_gsrainfall_mm not set — falling back to median"
                    )
                    yield_axis_method = "median"
                else:
                    fs = french_schultz_threshold(float(gsrainfall), float(wue))
                    if fs is None:
                        logger.warning(
                            f"French-Schultz threshold undefined (GSR={gsrainfall}mm "
                            "<= 110mm) — falling back to median"
                        )
                        yield_axis_method = "median"
                    else:
                        yield_threshold = fs
                        logger.info(
                            f"French-Schultz yield threshold: {yield_threshold:.3f} t/ha "
                            f"(GSR={gsrainfall}mm, WUE={wue} t/ha/mm)"
                        )

            if yield_axis_method == "median":
                valid_yield = yield_raw[boundary_mask & np.isfinite(yield_raw)]
                yield_threshold = float(np.median(valid_yield))
                logger.info(f"Yield axis median threshold: {yield_threshold:.3f} t/ha")

            protein_threshold = pym_cfg.get("critical_protein_threshold", 10.5)
            logger.info(
                f"Protein Yield Correlation Matrix: "
                f"yield_threshold={yield_threshold:.3f} t/ha ({yield_axis_method}), "
                f"protein_threshold={protein_threshold}%"
            )
            flags.append(f"matrix_yield_method={yield_axis_method}")
            flags.append(f"matrix_yield_threshold={yield_threshold:.3f}")
            flags.append(f"matrix_protein_threshold={protein_threshold}")

            # ── Build quadrant zone array ─────────────────────────────────────
            zone_arr_matrix = compute_quadrant_zones(
                yield_raw, protein_raw, boundary_mask,
                yield_threshold, protein_threshold, logger,
            )

            zone_arr_matrix = spatial_filter(zone_arr_matrix, min_pixels)

            if smooth_passes > 0:
                zone_arr_matrix = majority_filter(
                    zone_arr_matrix, size=3, n_passes=smooth_passes
                )

            zone_gdf_matrix = vectorise_zones(zone_arr_matrix, transform, crs)
            if zone_gdf_matrix is None:
                logger.error("Matrix vectorisation produced no polygons")
                sys.exit(1)

            zone_gdf_matrix = attach_point_stats(
                zone_gdf_matrix, yield_clean_shp, protein_clean_shp,
                protein_available=True, logger=logger,
            )

            zone_gdf_matrix["layers"]     = "yield+protein"
            zone_gdf_matrix["cov_status"] = coverage_status
            zone_gdf_matrix["method"]     = f"matrix_{yield_axis_method}"
            zone_gdf_matrix["zone_label"] = zone_gdf_matrix["zone_id"].map(MATRIX_LABELS)

            # Primary output
            out_shp_primary = out_dir / f"{paddock_id}_{season}_zones.shp"
            zone_gdf_matrix.to_file(out_shp_primary)
            logger.info(
                f"Matrix zones (primary): {len(zone_gdf_matrix)} zones, "
                f"{zone_gdf_matrix['area_ha'].sum():.1f}ha → {out_shp_primary.name}"
            )
            flags.append(f"primary_method=matrix_{yield_axis_method}")
            flags.append(f"primary_n_zones={len(zone_gdf_matrix)}")

            for _, row in zone_gdf_matrix.iterrows():
                zid  = int(row["zone_id"])
                lbl  = MATRIX_LABELS.get(zid, "")
                parts = [f"matrix_zone{zid} ({lbl}): {row['area_ha']:.1f}ha"]
                if row.get("mean_yield") is not None:
                    parts.append(f"yield={row['mean_yield']:.3f}t/ha")
                if row.get("mean_prot") is not None:
                    parts.append(f"protein={row['mean_prot']:.2f}%")
                logger.info("  " + "  ".join(parts))
                flags.append(f"matrix_zone{zid}_area_ha={row['area_ha']:.1f}")

            # ── K-Means secondary (reference) ─────────────────────────────────
            logger.info("Running K-Means as secondary reference output")
            zone_gdf_km = run_kmeans_zones(
                yield_z, protein_z, protein_available, boundary_mask,
                n_zones, random_seed, min_pixels, smooth_passes,
                transform, crs,
                yield_clean_shp, protein_clean_shp, coverage_status, logger,
            )
            if zone_gdf_km is not None:
                out_shp_km = out_dir / f"{paddock_id}_{season}_zones_kmeans.shp"
                zone_gdf_km.to_file(out_shp_km)
                logger.info(
                    f"K-Means zones (secondary reference): {len(zone_gdf_km)} zones "
                    f"→ {out_shp_km.name}"
                )
                flags.append(f"secondary_zones_shp={out_shp_km.name}")

            log_run_entry(
                log_dir="logs",
                script="05_zones.py",
                paddock_id=paddock_id,
                inputs={
                    "yield_raster": str(yield_raster_path),
                    "protein_raster": str(protein_raster_path),
                    "boundary_shp": cfg["inputs"]["boundary_shp"],
                },
                outputs={
                    "zones_shp": str(out_shp_primary),
                    "n_zones": len(zone_gdf_matrix),
                    "method": f"matrix_{yield_axis_method}",
                },
                flags=flags,
                status="success",
            )
            return   # Level 2 path complete

    # =========================================================================
    # Level 1 — K-Means yield-only (primary, only output)
    # =========================================================================
    logger.info("Level 1: running K-Means yield-only as primary method")
    flags.append("primary_method=kmeans_yield_only")

    zone_gdf = run_kmeans_zones(
        yield_z, None, False, boundary_mask,
        n_zones, random_seed, min_pixels, smooth_passes,
        transform, crs,
        yield_clean_shp, protein_clean_shp, coverage_status, logger,
    )
    if zone_gdf is None:
        logger.error("Vectorisation produced no polygons")
        sys.exit(1)

    logger.info(
        f"Vectorised: {len(zone_gdf)} zones, "
        f"total area {zone_gdf['area_ha'].sum():.1f}ha"
    )

    for _, row in zone_gdf.iterrows():
        parts = [f"zone {int(row['zone_id'])}: {row['area_ha']:.1f}ha"]
        if row.get("mean_yield") is not None:
            parts.append(f"yield={row['mean_yield']:.3f}t/ha")
        logger.info("  " + "  ".join(parts))

    out_shp = out_dir / f"{paddock_id}_{season}_zones.shp"
    zone_gdf.to_file(out_shp)
    logger.info(f"Written: {out_shp}")

    for z in range(1, n_zones + 1):
        rows_z = zone_gdf[zone_gdf["zone_id"] == z]
        if rows_z.empty:
            continue
        row = rows_z.iloc[0]
        flags.append(f"zone{z}_area_ha={row['area_ha']:.1f}")
        if "mean_yield" in zone_gdf.columns:
            flags.append(f"zone{z}_mean_yield={row['mean_yield']:.3f}")

    log_run_entry(
        log_dir="logs",
        script="05_zones.py",
        paddock_id=paddock_id,
        inputs={
            "yield_raster": str(yield_raster_path),
            "protein_raster": "excluded",
            "boundary_shp": cfg["inputs"]["boundary_shp"],
        },
        outputs={"zones_shp": str(out_shp), "n_zones": n_zones, "method": "kmeans_yield_only"},
        flags=flags,
        status="success",
    )


if __name__ == "__main__":
    main()
