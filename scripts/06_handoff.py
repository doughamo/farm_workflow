"""
Stage 6 - Agronomist handoff: zone summary CSV and printable PDF zone map

Outputs to data/outputs/handoff/:
  {paddock_id}_{season}_zone_summary.csv  — zone stats + blank N rate column
  {paddock_id}_{season}_zone_map.pdf      — A4 landscape printable zone map

Config: config/paddock_config.yaml
"""

import csv
import sys
from datetime import date
from pathlib import Path

import matplotlib
matplotlib.use("Agg")   # non-interactive backend — no display required
import matplotlib.patches as mpatches
import matplotlib.pyplot as plt
import geopandas as gpd
import numpy as np

from utils import ensure_output_dirs, load_config, log_run_entry, setup_logger

# Zone colours: traffic-light convention (low=red, mid=amber, high=green)
ZONE_COLOURS = {1: "#d73027", 2: "#fee090", 3: "#1a9850"}
# Extend for >3 zones if needed in future
EXTRA_COLOURS = ["#4575b4", "#74add1", "#abd9e9"]


def zone_colour(zone_id, n_zones):
    if n_zones <= len(ZONE_COLOURS):
        return ZONE_COLOURS.get(zone_id, "#999999")
    return EXTRA_COLOURS[zone_id - len(ZONE_COLOURS) - 1]


def write_csv(zone_gdf, out_path, protein_available):
    """Write zone summary CSV with blank N rate column for agronomist."""
    rows = []
    for _, row in zone_gdf.sort_values("zone_id").iterrows():
        r = {
            "zone_id": int(row["zone_id"]),
            "area_ha": float(row["area_ha"]),
            "mean_yield_tha": round(float(row["mean_yield"]), 3) if row["mean_yield"] else "",
            "yield_pts": int(row["yield_pts"]) if row["yield_pts"] else 0,
        }
        if protein_available:
            mp = row.get("mean_prot")
            r["mean_protein_pct"] = round(float(mp), 2) if mp and not np.isnan(float(mp)) else ""
            pp = row.get("prot_pts")
            r["protein_pts"] = int(pp) if pp and not np.isnan(float(pp)) else 0
        r["n_rate_kg_ha"] = ""
        r["notes"] = ""
        rows.append(r)

    with open(out_path, "w", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=list(rows[0].keys()))
        writer.writeheader()
        writer.writerows(rows)


def add_scale_bar(ax, minx, miny, maxx, maxy):
    """Draw a simple scale bar on the map axes."""
    extent_m = maxx - minx
    for bar_m in [50, 100, 200, 250, 500, 1000]:
        if bar_m <= extent_m * 0.25:
            scale_m = bar_m
    bar_x0 = minx + extent_m * 0.04
    bar_y  = miny + (maxy - miny) * 0.04
    ax.plot([bar_x0, bar_x0 + scale_m], [bar_y, bar_y],
            color="black", linewidth=2.5, solid_capstyle="butt")
    # Tick ends
    tick_h = (maxy - miny) * 0.005
    for xp in [bar_x0, bar_x0 + scale_m]:
        ax.plot([xp, xp], [bar_y - tick_h, bar_y + tick_h], color="black", linewidth=1.5)
    ax.text(bar_x0 + scale_m / 2, bar_y + tick_h * 2.5,
            f"{scale_m} m", ha="center", va="bottom", fontsize=7)


def add_north_arrow(ax):
    """Draw a north arrow in the upper-right corner of the map axes."""
    ax.annotate(
        "", xy=(0.96, 0.97), xytext=(0.96, 0.88),
        xycoords="axes fraction",
        arrowprops=dict(arrowstyle="-|>", color="black", lw=1.5,
                        mutation_scale=12),
    )
    ax.text(0.96, 0.98, "N", transform=ax.transAxes,
            ha="center", va="bottom", fontsize=9, fontweight="bold")


def make_pdf(zone_gdf, boundary_gdf, paddock_id, paddock_name, season,
             protein_available, coverage_status, layers_used, out_path):
    """Produce A4 landscape PDF zone map."""
    n_zones = len(zone_gdf)
    colours = [zone_colour(int(z), n_zones) for z in zone_gdf.sort_values("zone_id")["zone_id"]]

    # ── Figure layout ─────────────────────────────────────────────────────────
    fig = plt.figure(figsize=(297 / 25.4, 210 / 25.4))   # A4 landscape (inches)

    # Title band
    fig.text(0.5, 0.96,
             f"{paddock_name}  —  {season} Season  —  Management Zone Map",
             ha="center", va="top", fontsize=13, fontweight="bold")
    fig.text(0.5, 0.92,
             f"Generated: {date.today().isoformat()}   |   "
             f"Layers: {layers_used}   |   "
             f"Method: K-Means k={n_zones}   |   "
             f"CRS: GDA94 / MGA Zone 50",
             ha="center", va="top", fontsize=7, color="#555555")

    # Map axes (left ~65%)
    ax_map = fig.add_axes([0.03, 0.08, 0.61, 0.80])
    # Legend panel (right ~30%)
    ax_leg = fig.add_axes([0.67, 0.08, 0.30, 0.80])
    ax_leg.axis("off")

    # ── Map ───────────────────────────────────────────────────────────────────
    zone_sorted = zone_gdf.sort_values("zone_id").copy()
    zone_sorted.plot(
        ax=ax_map,
        color=colours,
        edgecolor="white",
        linewidth=0.3,
    )
    boundary_gdf.boundary.plot(ax=ax_map, color="black", linewidth=1.2)

    minx, miny, maxx, maxy = zone_gdf.total_bounds
    pad_x = (maxx - minx) * 0.04
    pad_y = (maxy - miny) * 0.04
    ax_map.set_xlim(minx - pad_x, maxx + pad_x)
    ax_map.set_ylim(miny - pad_y, maxy + pad_y)

    ax_map.set_xlabel("Easting (m)", fontsize=7)
    ax_map.set_ylabel("Northing (m)", fontsize=7)
    ax_map.tick_params(labelsize=6)
    ax_map.ticklabel_format(style="plain")
    ax_map.grid(True, linestyle="--", linewidth=0.3, alpha=0.5)

    add_scale_bar(ax_map, minx, miny, maxx, maxy)
    add_north_arrow(ax_map)

    # ── Legend panel ──────────────────────────────────────────────────────────
    y = 0.97
    line_h = 0.065

    ax_leg.text(0, y, "ZONE SUMMARY", fontsize=9, fontweight="bold",
                transform=ax_leg.transAxes, va="top")
    y -= line_h * 0.8

    for _, row in zone_sorted.iterrows():
        zid = int(row["zone_id"])
        col = zone_colour(zid, n_zones)

        # Colour swatch
        patch = mpatches.FancyBboxPatch(
            (0, y - 0.032), 0.07, 0.044,
            boxstyle="round,pad=0.005",
            facecolor=col, edgecolor="black", linewidth=0.5,
            transform=ax_leg.transAxes,
        )
        ax_leg.add_patch(patch)

        # Zone heading
        ax_leg.text(0.10, y, f"Zone {zid}  —  {row['area_ha']:.1f} ha",
                    fontsize=8.5, fontweight="bold",
                    transform=ax_leg.transAxes, va="top")
        y -= line_h * 0.65

        # Yield stat
        if row["mean_yield"]:
            ax_leg.text(0.10, y, f"Yield:    {float(row['mean_yield']):.2f} t/ha",
                        fontsize=7.5, transform=ax_leg.transAxes, va="top",
                        color="#333333")
            y -= line_h * 0.55

        # Protein stat
        if protein_available and row.get("mean_prot") and not np.isnan(float(row["mean_prot"])):
            ax_leg.text(0.10, y, f"Protein: {float(row['mean_prot']):.2f} %",
                        fontsize=7.5, transform=ax_leg.transAxes, va="top",
                        color="#333333")
            y -= line_h * 0.55

        y -= line_h * 0.3   # gap between zones

    # Coverage warning
    if coverage_status in ("warn", "insufficient"):
        y -= line_h * 0.3
        warn_col = "#e67e00" if coverage_status == "warn" else "#c0392b"
        warn_txt = (
            "⚠  Protein coverage: WARN\n"
            "   Gap >2 header passes in part\n"
            "   of paddock. Agronomist review\n"
            "   recommended before finalising\n"
            "   protein-based zone boundaries."
        ) if coverage_status == "warn" else (
            "✖  Protein coverage: INSUFFICIENT\n"
            "   Zones based on yield only.\n"
            "   Protein meter gap exceeds\n"
            "   4 header passes."
        )
        ax_leg.text(0, y, warn_txt,
                    fontsize=7, color=warn_col, va="top",
                    transform=ax_leg.transAxes,
                    bbox=dict(boxstyle="round,pad=0.3", facecolor="#fff8e1",
                              edgecolor=warn_col, linewidth=0.8))
        y -= line_h * (3.5 if coverage_status == "warn" else 2.5)

    # N rate assignment section
    y -= line_h * 0.5
    # Separator line (axes fraction coords via ax.plot with transform)
    ax_leg.plot([0, 1], [y + line_h * 0.3, y + line_h * 0.3],
                color="#aaaaaa", linewidth=0.5, transform=ax_leg.transAxes,
                clip_on=False)
    y -= line_h * 0.2

    ax_leg.text(0, y, "N RATE ASSIGNMENT  (kg/ha)",
                fontsize=8, fontweight="bold",
                transform=ax_leg.transAxes, va="top")
    y -= line_h * 0.8

    for _, row in zone_sorted.iterrows():
        zid = int(row["zone_id"])
        ax_leg.text(0, y, f"Zone {zid}:",
                    fontsize=8, transform=ax_leg.transAxes, va="top")
        # Underline for writing
        ax_leg.plot([0.22, 0.95], [y - line_h * 0.28, y - line_h * 0.28],
                    color="black", linewidth=0.8, transform=ax_leg.transAxes,
                    clip_on=False)
        y -= line_h * 0.75

    # Footer
    fig.text(0.03, 0.03,
             f"Paddock ID: {paddock_id}   |   "
             f"Total zone area: {zone_gdf['area_ha'].sum():.1f} ha   |   "
             f"Protein coverage: {coverage_status.upper()}",
             fontsize=6.5, color="#666666", va="bottom")

    fig.savefig(out_path, format="pdf", bbox_inches="tight", dpi=150)
    plt.close(fig)


def main():
    cfg = load_config()
    run = cfg["run"]
    paddock_id = run["paddock_id"]
    paddock_name = run["paddock_name"]
    season = run["season"]

    logger = setup_logger("06_handoff")
    ensure_output_dirs(cfg)
    flags = []

    # ── Load zone shapefile ───────────────────────────────────────────────────
    zones_shp = (
        Path(cfg["outputs"]["zones"])
        / f"{paddock_id}_{season}_zones.shp"
    )
    if not zones_shp.exists():
        logger.error(f"Zone shapefile not found: {zones_shp} — run Stage 5 first")
        sys.exit(1)

    zone_gdf = gpd.read_file(zones_shp)
    n_zones = len(zone_gdf)
    coverage_status = zone_gdf["cov_status"].iloc[0]
    layers_used = zone_gdf["layers"].iloc[0]
    protein_available = "protein" in layers_used

    logger.info(
        f"Loaded {n_zones} zones from {zones_shp.name}  |  "
        f"layers={layers_used}  coverage={coverage_status}"
    )

    # ── Load paddock boundary ─────────────────────────────────────────────────
    bdf = gpd.read_file(cfg["inputs"]["boundary_shp"])
    boundary = bdf[bdf["FIELD_NAME"] == paddock_name].to_crs(zone_gdf.crs)
    if boundary.empty:
        logger.warning(f"Boundary not found for '{paddock_name}' — map will show zones only")

    # ── Outputs ───────────────────────────────────────────────────────────────
    out_dir = Path(cfg["outputs"]["handoff"])
    out_csv = out_dir / f"{paddock_id}_{season}_zone_summary.csv"
    out_pdf = out_dir / f"{paddock_id}_{season}_zone_map.pdf"

    # ── Write CSV ─────────────────────────────────────────────────────────────
    write_csv(zone_gdf, out_csv, protein_available)
    logger.info(f"Written: {out_csv}")
    flags.append(f"csv={out_csv.name}")

    # ── Write PDF ─────────────────────────────────────────────────────────────
    make_pdf(
        zone_gdf=zone_gdf,
        boundary_gdf=boundary if not boundary.empty else zone_gdf,
        paddock_id=paddock_id,
        paddock_name=paddock_name,
        season=season,
        protein_available=protein_available,
        coverage_status=coverage_status,
        layers_used=layers_used,
        out_path=out_pdf,
    )
    logger.info(f"Written: {out_pdf}")
    flags.append(f"pdf={out_pdf.name}")

    # ── Zone summary to log ───────────────────────────────────────────────────
    for _, row in zone_gdf.sort_values("zone_id").iterrows():
        zid = int(row["zone_id"])
        parts = [f"zone{zid}_area={row['area_ha']:.1f}ha"]
        if row["mean_yield"]:
            parts.append(f"yield={float(row['mean_yield']):.3f}t/ha")
        mp = row.get("mean_prot")
        if protein_available and mp and not np.isnan(float(mp)):
            parts.append(f"protein={float(mp):.2f}%")
        flags.append("  ".join(parts))

    log_run_entry(
        log_dir="logs",
        script="06_handoff.py",
        paddock_id=paddock_id,
        inputs={"zones_shp": str(zones_shp)},
        outputs={"csv": str(out_csv), "pdf": str(out_pdf)},
        flags=flags,
        status="success",
    )


if __name__ == "__main__":
    main()
