# Farm Data Workflow

Variable rate prescription pipeline for grain farming operations.
Converts raw CN1 harvest data and CropScan protein data into
nitrogen VRA prescription shapefiles compatible with Case IH AFS displays.

## Status
v0.1 — scaffold only. CN1 SDK registration pending.

## Quick start
1. Activate environment: `conda activate farmworkflow`
2. Copy CN1 ZIP exports to `data/raw/yield/`
3. Copy CropScan CSV exports to `data/raw/protein/`
4. Copy paddock boundary shapefiles to `data/raw/boundaries/`
5. Edit `config/paddock_config.yaml` for the target paddock
6. Run scripts in order: 00 → 01 → 02 → 03 → 04 → 05 → 06 → 07

## Script overview
| Script | Stage | Description |
|--------|-------|-------------|
| 00_cn1_to_points.py | Pre-ingest | Convert CN1 ZIP → point shapefile |
| 01_ingest.py | 1 | Validate inputs, log metadata |
| 02_clean_yield.py | 2 | Per-machine normalisation + pyprecag cleaning |
| 03_clean_protein.py | 3 | CropScan CSV cleaning and georeferencing |
| 04_normalise.py | 4 | Interpolate to raster, z-score normalise |
| 05_zones.py | 5 | K-Means management zone delineation |
| 06_handoff.py | 6 | Zone summary CSV + printable PDF map |
| 07_prescription.py | 7 | Join agronomist rates → prescription shapefile |

## Dependencies
See environment setup in constitution/CONSTITUTION.md

## Data
Raw input files in data/raw/ are never modified.
All processing writes to data/processed/ or data/outputs/.
