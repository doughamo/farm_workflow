"""
Stage 4 - Interpolate yield and protein to raster, z-score normalise

For each layer (yield, protein):
  1. Interpolate cleaned point shapefile to a regular grid at raster.resolution_m
  2. Mask to paddock boundary
  3. Z-score normalise over unmasked cells
  4. Write as GeoTIFF to data/processed/rasters/

Protein layer also checks spatial coverage gaps:
  - Implement width is derived from area_m2 / dist_m in the raw yield shapefile
  - Distances from yield points to nearest protein point are computed via KDTree
  - Gap thresholds: warn = coverage_warn_passes × width, fail = coverage_fail_passes × width
  - Protein raster cells beyond the fail threshold are set to NaN (no extrapolation)
  - protein_coverage_status written to run log for Stage 5 to act on

Interpolation method is set by raster.interpolation_method in config:
  pykrige  - OrdinaryKriging (n_closest_points=20, spherical variogram)
  linear   - scipy griddata linear interpolation
  vesper   - not yet implemented; falls back to linear interpolation

Config: config/paddock_config.yaml
"""

import sys
from pathlib import Path

import geopandas as gpd
import numpy as np
import rasterio
from rasterio.features import geometry_mask
from rasterio.transform import from_origin
from scipy.spatial import cKDTree
from shapely.ops import unary_union

from utils import ensure_output_dirs, load_config, log_run_entry, setup_logger


def derive_implement_width(raw_yield_shp, logger):
    """
    Derive implement width (m) from area_m2 / dist_m in the raw yield shapefile.
    Returns (overall_median, per_machine_dict). Falls back to None if not derivable.
    """
    gdf = gpd.read_file(raw_yield_shp)
    valid = (
        gdf["area_m2"].notna() & gdf["dist_m"].notna() &
        (gdf["dist_m"] > 0) & (gdf["area_m2"] > 0)
    )
    gdf_v = gdf[valid].copy()
    gdf_v["impl_width"] = gdf_v["area_m2"] / gdf_v["dist_m"]
    gdf_v = gdf_v[gdf_v["impl_width"].between(2, 30)]

    if gdf_v.empty:
        logger.warning("Could not derive implement width — no plausible area_m2/dist_m values")
        return None, {}

    per_machine = {}
    for machine, grp in gdf_v.groupby("machine_id"):
        w = round(float(grp["impl_width"].median()), 2)
        per_machine[str(machine)] = w
        logger.info(f"Implement width — {machine}: {w}m")
    overall = round(float(gdf_v["impl_width"].median()), 2)
    logger.info(f"Implement width — overall median: {overall}m")
    return overall, per_machine


def check_protein_coverage(yield_gdf, protein_gdf, impl_width, warn_passes,
                            fail_passes, fail_area_pct, logger):
    """
    For each cleaned yield point find distance to nearest protein point.
    Returns (status, max_gap_m, p95_gap_m, pct_beyond_warn, pct_beyond_fail).
    status is 'ok', 'warn', or 'insufficient'.
    """
    warn_dist = warn_passes * impl_width
    fail_dist = fail_passes * impl_width

    px = protein_gdf.geometry.x.values
    py = protein_gdf.geometry.y.values
    tree = cKDTree(np.column_stack([px, py]))

    yx = yield_gdf.geometry.x.values
    yy_coord = yield_gdf.geometry.y.values
    distances, _ = tree.query(np.column_stack([yx, yy_coord]), k=1)

    n_total = len(distances)
    pct_warn = (distances > warn_dist).sum() / n_total * 100
    pct_fail = (distances > fail_dist).sum() / n_total * 100
    max_gap = float(distances.max())
    p95_gap = float(np.percentile(distances, 95))

    logger.info(
        f"Protein gap check: impl_width={impl_width}m  "
        f"warn>{warn_dist:.0f}m ({warn_passes} passes)  "
        f"fail>{fail_dist:.0f}m ({fail_passes} passes)"
    )
    logger.info(
        f"Protein gap check: max_gap={max_gap:.1f}m  p95_gap={p95_gap:.1f}m  "
        f"beyond_warn={pct_warn:.1f}%  beyond_fail={pct_fail:.1f}%"
    )

    if pct_fail > fail_area_pct:
        status = "insufficient"
        logger.warning(
            f"Protein coverage INSUFFICIENT: {pct_fail:.1f}% of harvested area "
            f">{fail_dist:.0f}m ({fail_passes} passes) from any protein measurement — "
            "Stage 5 will use yield-only zone delineation."
        )
    elif pct_warn > fail_area_pct:
        status = "warn"
        logger.warning(
            f"Protein coverage WARNING: {pct_warn:.1f}% of harvested area "
            f">{warn_dist:.0f}m ({warn_passes} passes) from any protein measurement — "
            "agronomist review recommended."
        )
    else:
        status = "ok"
        logger.info("Protein coverage: OK")

    return status, max_gap, p95_gap, pct_warn, pct_fail


def build_gap_mask(protein_gdf, fail_dist, transform, shape):
    """
    Returns a boolean array (nrows, ncols) that is True where a raster cell
    is within fail_dist of at least one protein measurement point.
    Cells outside this radius are genuine data gaps — do not interpolate into them.
    """
    nrows, ncols = shape
    res = transform.a
    xs = np.array([transform.c + (c + 0.5) * res for c in range(ncols)])
    ys = np.array([transform.f - (r + 0.5) * res for r in range(nrows)])
    grid_x, grid_y = np.meshgrid(xs, ys)

    px = protein_gdf.geometry.x.values
    py = protein_gdf.geometry.y.values
    tree = cKDTree(np.column_stack([px, py]))

    grid_pts = np.column_stack([grid_x.ravel(), grid_y.ravel()])
    distances, _ = tree.query(grid_pts, k=1)
    return (distances <= fail_dist).reshape(shape)


def interpolate_to_grid(x, y, z, grid_x, grid_y, method, logger):
    """Interpolate scattered points onto a regular grid. Returns 2-D array (rows, cols)."""
    if method == "linear":
        from scipy.interpolate import griddata
        pts = np.column_stack([x, y])
        xi = np.column_stack([grid_x.ravel(), grid_y.ravel()])
        result = griddata(pts, z, xi, method="linear")
        return result.reshape(grid_x.shape)

    if method == "vesper":
        # VESPER subprocess integration not yet implemented (open item — Section 11).
        # Fall back to scipy linear interpolation, which is appropriate for dense
        # CropScan and yield monitor point data at 10m grid resolution.
        logger.warning(
            "raster.interpolation_method='vesper': VESPER subprocess integration "
            "is not yet implemented (open item — Section 11). "
            "Falling back to scipy linear interpolation."
        )
        from scipy.interpolate import griddata
        pts = np.column_stack([x, y])
        xi = np.column_stack([grid_x.ravel(), grid_y.ravel()])
        result = griddata(pts, z, xi, method="linear")
        return result.reshape(grid_x.shape)

    if method == "pykrige":
        from pykrige.ok import OrdinaryKriging
        # Subsample for variogram fitting — full pairwise distance matrix on
        # large datasets exceeds available memory. 3000 points gives good
        # variogram coverage for a 174ha paddock at 10m resolution.
        max_variogram_pts = 3000
        if len(x) > max_variogram_pts:
            rng = np.random.default_rng(42)
            idx = rng.choice(len(x), size=max_variogram_pts, replace=False)
            xs_v, ys_v, zs_v = x[idx], y[idx], z[idx]
            logger.info(
                f"pykrige: subsampled {max_variogram_pts} of {len(x):,} points "
                "for variogram fitting"
            )
        else:
            xs_v, ys_v, zs_v = x, y, z
        # pykrige execute('grid') takes 1-D coordinate arrays
        xs_1d = grid_x[0, :]   # unique column centres
        ys_1d = grid_y[:, 0]   # unique row centres (top-to-bottom)
        ok = OrdinaryKriging(
            xs_v, ys_v, zs_v,
            variogram_model="spherical",
            nlags=20,
            verbose=False,
            enable_plotting=False,
        )
        z_grid, _ = ok.execute("grid", xs_1d, ys_1d, n_closest_points=20, backend="loop")
        # pykrige returns (n_y, n_x) with ys low-to-high; flip to raster convention
        return np.flipud(z_grid.data)

    raise ValueError(f"Unknown interpolation method: {method!r}")


def process_layer(name, gdf, value_col, boundary_geom, transform, shape, method,
                  logger, gap_mask=None):
    """
    Interpolate one point layer, mask to boundary, z-score normalise.
    gap_mask: optional boolean array (True = coverage adequate). When provided,
    cells where gap_mask is False are set to NaN before z-score normalisation —
    they hold interpolated fiction across a genuine data gap.
    """
    nrows, ncols = shape
    res = transform.a  # pixel width

    # Grid cell centres (easting, northing)
    xs = np.array([transform.c + (c + 0.5) * res for c in range(ncols)])
    ys = np.array([transform.f - (r + 0.5) * res for r in range(nrows)])
    grid_x, grid_y = np.meshgrid(xs, ys)

    x = gdf.geometry.x.values
    y = gdf.geometry.y.values
    z = gdf[value_col].values.astype(float)

    valid = np.isfinite(z)
    if valid.sum() < 10:
        logger.error(f"{name}: fewer than 10 valid point values — cannot interpolate")
        return None

    x, y, z = x[valid], y[valid], z[valid]
    logger.info(f"{name}: interpolating {len(z):,} points onto {nrows}×{ncols} grid")

    grid = interpolate_to_grid(x, y, z, grid_x, grid_y, method, logger)

    # Mask cells outside paddock boundary
    boundary_mask = geometry_mask(
        [boundary_geom],
        transform=transform,
        invert=True,          # True = inside boundary
        out_shape=shape,
    )
    grid = np.where(boundary_mask, grid, np.nan).astype(np.float32)

    # Mask cells in genuine coverage gaps (protein layer only)
    if gap_mask is not None:
        n_gap_cells = (~gap_mask & boundary_mask).sum()
        if n_gap_cells:
            logger.info(
                f"{name}: masking {n_gap_cells:,} cells in coverage gaps "
                "(distance to nearest measurement exceeds fail threshold)"
            )
        grid = np.where(gap_mask, grid, np.nan).astype(np.float32)

    # Z-score normalise over cells that are inside boundary, in coverage, and finite
    valid_mask = boundary_mask & np.isfinite(grid)
    if gap_mask is not None:
        valid_mask = valid_mask & gap_mask
    vals = grid[valid_mask]
    if len(vals) == 0:
        logger.error(f"{name}: no valid values inside boundary after masking")
        return None
    mean, std = vals.mean(), vals.std()
    if std == 0:
        logger.warning(f"{name}: zero std — all values identical, z-score will be zero")
        std = 1.0
    grid_z = np.where(np.isfinite(grid), (grid - mean) / std, np.nan).astype(np.float32)

    logger.info(
        f"{name}: raw mean={mean:.4f} std={std:.4f} | "
        f"z-score range [{np.nanmin(grid_z):.3f}, {np.nanmax(grid_z):.3f}]"
    )
    return grid_z, mean, std


def main():
    cfg = load_config()
    run = cfg["run"]
    paddock_id = run["paddock_id"]
    paddock_name = run["paddock_name"]
    season = run["season"]
    raster_cfg = cfg["raster"]
    res = raster_cfg["resolution_m"]
    method = raster_cfg["interpolation_method"]

    logger = setup_logger("04_normalise")
    ensure_output_dirs(cfg)

    flags = []

    # ── Load paddock boundary ─────────────────────────────────────────────────
    boundary_path = Path(cfg["inputs"]["boundary_shp"])
    bdf = gpd.read_file(boundary_path)
    match = bdf[bdf["FIELD_NAME"] == paddock_name].to_crs(cfg["crs"]["project"])
    if match.empty:
        logger.error(f"No boundary polygon found for FIELD_NAME='{paddock_name}'")
        sys.exit(1)
    boundary_geom = unary_union(match.geometry)

    # Snap bbox to resolution grid
    minx, miny, maxx, maxy = boundary_geom.bounds
    minx = np.floor(minx / res) * res
    miny = np.floor(miny / res) * res
    maxx = np.ceil(maxx / res) * res
    maxy = np.ceil(maxy / res) * res
    ncols = int(round((maxx - minx) / res))
    nrows = int(round((maxy - miny) / res))
    transform = from_origin(minx, maxy, res, res)
    logger.info(
        f"Raster grid: {nrows} rows × {ncols} cols at {res}m resolution "
        f"({nrows*ncols:,} cells)"
    )
    flags.append(f"grid={nrows}x{ncols}_at_{res}m")
    flags.append(f"interpolation_method={method}")

    crs_str = cfg["crs"]["project"]
    output_dir = Path(cfg["outputs"]["rasters"])
    raster_meta = {
        "driver": "GTiff",
        "dtype": "float32",
        "width": ncols,
        "height": nrows,
        "count": 1,
        "crs": crs_str,
        "transform": transform,
        "nodata": np.nan,
    }

    yield_clean_shp = (
        Path(cfg["outputs"]["processed_yield"])
        / f"{paddock_id}_{season}_yield_clean.shp"
    )
    protein_clean_shp = (
        Path(cfg["outputs"]["processed_protein"])
        / f"{paddock_id}_{season}_protein_clean.shp"
    )
    raw_yield_shp = Path("data/raw/yield") / f"{paddock_id}_{season}_yield_points.shp"

    # ── Derive implement width from raw yield shapefile ───────────────────────
    pc = cfg["protein_cleaning"]
    impl_width, impl_by_machine = derive_implement_width(raw_yield_shp, logger)
    if impl_width is not None:
        flags.append(f"implement_width_m={impl_width}")
        flags.append(f"implement_width_by_machine={impl_by_machine}")

    # ── Protein coverage gap check ────────────────────────────────────────────
    protein_coverage_status = "ok"
    gap_mask = None

    if not protein_clean_shp.exists():
        logger.warning("Protein shapefile not found — skipping coverage check")
        flags.append("protein_coverage_status=skipped")
    elif impl_width is None:
        logger.warning("Implement width unknown — skipping coverage gap check")
        flags.append("protein_coverage_status=skipped")
    elif not yield_clean_shp.exists():
        logger.warning("Yield shapefile not found — skipping coverage check")
        flags.append("protein_coverage_status=skipped")
    else:
        yield_gdf = gpd.read_file(yield_clean_shp)
        protein_gdf = gpd.read_file(protein_clean_shp)

        warn_passes = pc["coverage_warn_passes"]
        fail_passes = pc["coverage_fail_passes"]
        fail_area_pct = pc["coverage_fail_area_pct"]
        fail_dist = fail_passes * impl_width

        protein_coverage_status, max_gap, p95_gap, pct_warn, pct_fail = (
            check_protein_coverage(
                yield_gdf, protein_gdf, impl_width,
                warn_passes, fail_passes, fail_area_pct, logger,
            )
        )
        flags.append(f"protein_coverage_status={protein_coverage_status}")
        flags.append(f"protein_gap_max_m={max_gap:.1f}")
        flags.append(f"protein_gap_p95_m={p95_gap:.1f}")
        flags.append(f"protein_gap_pct_beyond_warn={pct_warn:.1f}")
        flags.append(f"protein_gap_pct_beyond_fail={pct_fail:.1f}")

        # Build gap mask for protein raster regardless of status — always prevent
        # interpolation from crossing genuine gaps, even in 'warn' cases.
        gap_mask = build_gap_mask(protein_gdf, fail_dist, transform, (nrows, ncols))
        n_gap_cells = (~gap_mask).sum()
        logger.info(
            f"Gap mask: {n_gap_cells:,} raster cells beyond fail threshold "
            f"({fail_dist:.0f}m) will be set to NaN in protein raster"
        )

    layers = {
        "yield": {
            "shp": yield_clean_shp,
            "col": "yldtha",
            "out": output_dir / f"{paddock_id}_{season}_yield_norm.tif",
            "gap_mask": None,
        },
        "protein": {
            "shp": protein_clean_shp,
            "col": "protein",
            "out": output_dir / f"{paddock_id}_{season}_protein_norm.tif",
            "gap_mask": gap_mask,
        },
    }

    outputs = {}
    for name, spec in layers.items():
        if not spec["shp"].exists():
            logger.error(f"{name}: input shapefile not found: {spec['shp']}")
            flags.append(f"ERROR: {name} shapefile missing")
            continue

        gdf = gpd.read_file(spec["shp"])
        logger.info(f"{name}: loaded {len(gdf):,} points from {spec['shp'].name}")

        result = process_layer(
            name, gdf, spec["col"], boundary_geom, transform,
            (nrows, ncols), method, logger, gap_mask=spec["gap_mask"],
        )
        if result is None:
            flags.append(f"ERROR: {name} interpolation failed")
            continue

        grid_z, raw_mean, raw_std = result
        with rasterio.open(spec["out"], "w", **raster_meta) as dst:
            dst.write(grid_z, 1)
        logger.info(f"{name}: written to {spec['out']}")
        flags.append(f"{name}_raw_mean={raw_mean:.4f}")
        flags.append(f"{name}_raw_std={raw_std:.4f}")
        outputs[f"{name}_raster"] = str(spec["out"])

    if not outputs:
        log_run_entry(
            log_dir="logs", script="04_normalise.py", paddock_id=paddock_id,
            inputs={}, outputs={}, flags=flags, status="failed",
        )
        sys.exit(1)

    log_run_entry(
        log_dir="logs",
        script="04_normalise.py",
        paddock_id=paddock_id,
        inputs={
            "yield_shp": str(layers["yield"]["shp"]),
            "protein_shp": str(layers["protein"]["shp"]),
            "boundary_shp": str(boundary_path),
        },
        outputs=outputs,
        flags=flags,
        status="success",
    )


if __name__ == "__main__":
    main()
