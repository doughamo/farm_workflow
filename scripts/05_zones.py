"""
Stage 5 - K-Means management zone delineation

Inputs:  yield_norm.tif and (if coverage adequate) protein_norm.tif from Stage 4
Process: K-Means clustering → spatial filter → vectorise to polygons
Output:  Management zone shapefile with zone statistics

Protein layer inclusion is governed by protein_coverage_status logged in Stage 4:
  ok / warn   → yield + protein composite clustering
  insufficient → yield-only clustering; protein excluded

Gap cells (protein NaN from Stage 4 gap mask) are assigned to the zone whose
yield z-score centroid is nearest — they are never left unassigned.

Spatial filter removes patches smaller than zones.min_zone_area_ha by
reassigning them to the zone of their spatially nearest large-patch neighbour.

Zones are sorted by ascending mean yield z-score so zone 1 = lowest yield,
zone N = highest yield — consistent across seasons and paddocks.

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


def load_raster(path):
    """Return (data array float32, transform, nodata mask). NaN = nodata."""
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

    # Reassign removed patches to spatially nearest surviving zone pixel
    removed = (result == 0) & (zone_arr > 0)  # was a zone, now removed
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


def main():
    cfg = load_config()
    run = cfg["run"]
    paddock_id = run["paddock_id"]
    paddock_name = run["paddock_name"]
    season = run["season"]
    zones_cfg = cfg["zones"]
    n_zones = zones_cfg["n_zones"]
    min_zone_area_ha = zones_cfg["min_zone_area_ha"]
    random_seed = zones_cfg["random_seed"]
    smooth_passes = zones_cfg.get("smooth_passes", 2)
    res = cfg["raster"]["resolution_m"]

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
    raster_dir = Path(cfg["outputs"]["rasters"])
    yield_raster_path = raster_dir / f"{paddock_id}_{season}_yield_norm.tif"
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
        logger.info(
            f"Protein raster loaded ({coverage_status} status) — "
            "using yield + protein composite clustering"
        )
        flags.append("layers=yield+protein")
        flags.append(f"protein_coverage_status={coverage_status}")
    else:
        protein_z = None
        protein_available = False
        reason = "coverage status=insufficient" if not use_protein else "raster missing"
        logger.warning(
            f"Protein excluded ({reason}) — yield-only clustering"
        )
        flags.append("layers=yield_only")
        flags.append(f"protein_coverage_status={coverage_status}")

    # ── Build feature arrays ──────────────────────────────────────────────────
    min_pixels = int(np.ceil(min_zone_area_ha * 10_000 / res**2))
    logger.info(
        f"K-Means: k={n_zones}, min_zone_area={min_zone_area_ha}ha "
        f"({min_pixels} pixels), seed={random_seed}"
    )

    if protein_available:
        protein_valid = np.isfinite(protein_z)
        # Full coverage: both yield and protein valid
        full_mask = boundary_mask & protein_valid
        # Gap cells: yield valid but protein absent (gap mask applied in Stage 4)
        gap_mask = boundary_mask & ~protein_valid

        X_full = np.column_stack([
            yield_z[full_mask],
            protein_z[full_mask],
        ])
        logger.info(
            f"Clustering {full_mask.sum():,} full-coverage cells "
            f"({gap_mask.sum():,} gap cells to be assigned post-hoc)"
        )
    else:
        full_mask = boundary_mask
        gap_mask = np.zeros_like(boundary_mask)
        X_full = yield_z[full_mask].reshape(-1, 1)
        logger.info(f"Clustering {full_mask.sum():,} cells (yield only)")

    # ── K-Means ───────────────────────────────────────────────────────────────
    kmeans = KMeans(n_clusters=n_zones, random_state=random_seed, n_init=10)
    raw_labels = kmeans.fit_predict(X_full)

    # Sort zones by ascending mean yield z-score → zone 1 = lowest, zone N = highest
    centers = kmeans.cluster_centers_
    yield_col = 0
    zone_order = np.argsort(centers[:, yield_col])   # low to high yield
    remap = {int(old): new + 1 for new, old in enumerate(zone_order)}
    sorted_labels = np.array([remap[int(l)] for l in raw_labels], dtype=np.int32)

    # ── Assign gap cells using yield z-score nearest centroid ─────────────────
    zone_arr = np.zeros((nrows, ncols), dtype=np.int32)
    zone_arr[full_mask] = sorted_labels

    if protein_available and gap_mask.any():
        # Yield z-score of each zone centroid (in sorted order)
        yield_centers = centers[zone_order, yield_col]
        yield_gap_vals = yield_z[gap_mask].reshape(-1, 1)
        dist_to_centers = np.abs(yield_gap_vals - yield_centers.reshape(1, -1))
        gap_zone_labels = np.argmin(dist_to_centers, axis=1) + 1   # 1-indexed
        zone_arr[gap_mask] = gap_zone_labels
        logger.info(
            f"Gap cells assigned by yield centroid proximity: "
            + ", ".join(
                f"zone {z}={int((gap_zone_labels == z).sum()):,}"
                for z in range(1, n_zones + 1)
            )
        )

    # ── Spatial filter ────────────────────────────────────────────────────────
    pre_filter_counts = {z: int((zone_arr == z).sum()) for z in range(1, n_zones + 1)}
    zone_arr = spatial_filter(zone_arr, min_pixels)
    post_filter_counts = {z: int((zone_arr == z).sum()) for z in range(1, n_zones + 1)}

    for z in range(1, n_zones + 1):
        net_change = post_filter_counts[z] - pre_filter_counts[z]
        area_ha = post_filter_counts[z] * res**2 / 10_000
        change_str = f"+{net_change:,}" if net_change >= 0 else f"{net_change:,}"
        logger.info(
            f"Zone {z}: {post_filter_counts[z]:,} cells ({area_ha:.1f}ha) "
            f"[net {change_str} after spatial filter]"
        )

    # ── Majority-filter smoothing ─────────────────────────────────────────────
    if smooth_passes > 0:
        zone_arr = majority_filter(zone_arr, size=3, n_passes=smooth_passes)
        logger.info(f"Boundary smoothing: {smooth_passes} majority-filter pass(es), 3×3 window")
        flags.append(f"smooth_passes={smooth_passes}")
    else:
        logger.info("Boundary smoothing: disabled (smooth_passes=0)")
        flags.append("smooth_passes=0")

    # ── Vectorise ─────────────────────────────────────────────────────────────
    zone_gdf = vectorise_zones(zone_arr, transform, crs)
    if zone_gdf is None:
        logger.error("Vectorisation produced no polygons")
        sys.exit(1)

    logger.info(
        f"Vectorised: {len(zone_gdf)} zone polygons, "
        f"total area {zone_gdf['area_ha'].sum():.1f}ha"
    )

    # ── Zone statistics from cleaned point files ──────────────────────────────
    yield_clean_shp = (
        Path(cfg["outputs"]["processed_yield"])
        / f"{paddock_id}_{season}_yield_clean.shp"
    )
    protein_clean_shp = (
        Path(cfg["outputs"]["processed_protein"])
        / f"{paddock_id}_{season}_protein_clean.shp"
    )

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

    zone_gdf["layers"] = "yield+protein" if protein_available else "yield_only"
    zone_gdf["cov_status"] = coverage_status

    # ── Log zone summary ──────────────────────────────────────────────────────
    for _, row in zone_gdf.iterrows():
        parts = [f"zone {int(row['zone_id'])}: {row['area_ha']:.1f}ha"]
        if "mean_yield" in row and row["mean_yield"] is not None:
            parts.append(f"yield={row['mean_yield']:.3f}t/ha")
        if "mean_prot" in row and row["mean_prot"] is not None:
            parts.append(f"protein={row['mean_prot']:.2f}%")
        logger.info("  " + "  ".join(parts))

    # ── Write output shapefile ────────────────────────────────────────────────
    out_dir = Path(cfg["outputs"]["zones"])
    out_shp = out_dir / f"{paddock_id}_{season}_zones.shp"
    zone_gdf.to_file(out_shp)
    logger.info(f"Written: {out_shp}")

    for z in range(1, n_zones + 1):
        row = zone_gdf[zone_gdf["zone_id"] == z].iloc[0]
        flags.append(f"zone{z}_area_ha={row['area_ha']:.1f}")
        if "mean_yield" in row:
            flags.append(f"zone{z}_mean_yield={row['mean_yield']:.3f}")
        if "mean_prot" in zone_gdf.columns and not zone_gdf[zone_gdf["zone_id"] == z]["mean_prot"].isna().all():
            flags.append(f"zone{z}_mean_prot={row['mean_prot']:.2f}")

    log_run_entry(
        log_dir="logs",
        script="05_zones.py",
        paddock_id=paddock_id,
        inputs={
            "yield_raster": str(yield_raster_path),
            "protein_raster": str(protein_raster_path) if protein_available else "excluded",
            "boundary_shp": cfg["inputs"]["boundary_shp"],
        },
        outputs={"zones_shp": str(out_shp), "n_zones": n_zones},
        flags=flags,
        status="success",
    )


if __name__ == "__main__":
    main()
