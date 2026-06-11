# Farm Data Workflow — Project Constitution v0.3
# Status: Baseline — environment confirmed, SDK registration pending
# Last updated: 2026-06

---

## 1. Purpose

Build a local-first, modular Python pipeline that ingests, cleans,
analyses, and interprets farm spatial data to produce variable rate
prescription shapefiles compatible with Case IH AFS displays.

Initial scope: nitrogen VRA using yield and protein data, single
paddock, single season. Designed so that additional paddocks, seasons,
and prescription types can be added without restructuring the codebase.

---

## 2. Users and Roles

| Role                  | Responsibility                                        |
|-----------------------|-------------------------------------------------------|
| Farm operator         | Runs pipeline, reviews QA flags, delivers prescription|
| Agronomist/consultant | Receives zone map, assigns N rates per zone           |
| Claude Code           | Script development and architecture assistance        |

The workflow produces spatial products.
Agronomic decisions remain with the agronomist.

---

## 3. Data Inputs

| Layer              | Format           | Source                    | Status     |
|--------------------|------------------|---------------------------|------------|
| Yield              | CN1 ZIP → .shp   | AFS Connect manual export | v1 active  |
| Protein            | CSV              | CropScan meter            | v1 active  |
| Paddock boundaries | Shapefile        | Existing on-farm files    | v1 active  |
| EM38               | TBD              | Third-party survey        | Deferred   |
| Gamma radiometrics | TBD              | Third-party survey        | Deferred   |
| Soil tests         | CSV              | Lab / agronomist          | Deferred   |
| NDVI / imagery     | GeoTIFF          | On-demand                 | Deferred   |

---

## 4. Data Outputs

| Output                    | Format    | Destination                  |
|---------------------------|-----------|------------------------------|
| Cleaned yield layer       | Shapefile | data/processed/yield/        |
| Cleaned protein layer     | Shapefile | data/processed/protein/      |
| Normalised yield raster   | GeoTIFF   | data/processed/rasters/      |
| Normalised protein raster | GeoTIFF   | data/processed/rasters/      |
| Management zone map       | Shapefile | data/outputs/zones/          |
| Zone summary statistics   | CSV       | data/outputs/handoff/        |
| Printable zone map        | PDF       | data/outputs/handoff/        |
| N prescription shapefile  | Shapefile | data/outputs/prescriptions/  |
| Run log                   | CSV       | logs/                        |

---

## 5. Machine and Controller Integration

- Machine brand: Case IH
- Data platform: AFS Connect / CNH FieldOps
- Prescription delivery: shapefile imported via AFS Pro display
- Prescription format: polygon-based, rate as numeric attribute field
- Required AFS fields: rate, default rate, min rate, max rate
- Field attribute naming: to be confirmed against display expectation

---

## 6. Yield Data Format

Source: CN1 ZIP downloaded from AFS Connect.
Confirmed structure (from file inspection, Dec 2025 season):
  - Single combine: ID 04P1J, operator Barry
  - 22 harvest tasks across 3 farms, 18 paddocks
  - TLH (50 bytes/record): GPS + header status per second
  - TLT (47 bytes/record): timestamps
  - TLO (83 bytes/record): yield, moisture, speed sensor values
  - Largest paddock: 25,266 GPS point records

Ingestion path: CN1 SDK (.NET) called via pythonnet, outputs structured
data objects → converted to GeoDataFrame → written as point shapefile.

CN1 SDK registration: pending (develop.cnh.com)
Also request: CN1 ADAPT Plugin (maps CN1 to open ADAPT data model)

---

## 7. Protein Data Structure

Source: CropScan meter, CSV format.
Expected fields: latitude, longitude, protein %.
CRS: assumed WGS84 (GDA2020 equivalent for practical purposes).
Column name mapping configured in paddock_config.yaml — not hardcoded.

---

## 8. Multi-Harvester Calibration Correction

First-class workflow concern, not an edge case.
Dec 2025 season: single machine confirmed. Architecture supports
multi-machine from the outset for future seasons.

Method: per-machine mean normalisation before pyprecag cleaning.
  1. Check for machine ID field at ingestion. Warn if absent.
  2. Compute per-machine mean yield across paddock.
  3. Compute paddock-wide grand mean.
  4. Apply scalar offset per machine to align to grand mean.
  5. Log offset per machine. Flag if offset > 15% of grand mean.
  6. Then run pyprecag clean/trim/normalise.

---

## 9. Workflow Stages — v1

Stage 0  cn1_to_points     Convert CN1 ZIP → point shapefile
Stage 1  ingest            Validate inputs, log metadata
Stage 2  clean_yield       Per-machine normalisation + pyprecag
Stage 3  clean_protein     CropScan CSV → cleaned point shapefile
Stage 4  normalise         Interpolate to raster, z-score normalise
Stage 5  zones             K-Means clustering (k=3), spatial filter
Stage 6  handoff           Zone summary CSV + PDF map for agronomist
         [manual]          Agronomist assigns N rate per zone
Stage 7  prescription      Join rates → AFS-compatible shapefile

---

## 10. Folder Structure

farm_workflow/
├── constitution/         <- this file and decision log
├── data/
│   ├── raw/
│   │   ├── yield/        <- CN1 ZIPs and converted point shapefiles
│   │   ├── protein/      <- CropScan CSV files
│   │   ├── boundaries/   <- paddock boundary shapefiles
│   │   └── soil/         <- reserved
│   ├── processed/
│   │   ├── yield/        <- cleaned point shapefiles
│   │   ├── protein/      <- cleaned point shapefiles
│   │   └── rasters/      <- normalised GeoTIFFs
│   └── outputs/
│       ├── zones/        <- management zone shapefiles
│       ├── handoff/      <- CSVs and PDFs for agronomist
│       └── prescriptions/<- final AFS prescription shapefiles
├── scripts/              <- numbered pipeline scripts
├── config/               <- paddock_config.yaml
├── logs/                 <- run logs
├── sdk/
│   └── cn1/             <- CN1 SDK DLLs (not committed to git)
└── README.md

---

## 11. Python Environment

Manager: Conda (Miniforge)
Environment name: farmworkflow
Python version: 3.11

Core libraries (conda-forge):
  gdal, fiona, rasterio, geopandas, numpy, pandas,
  scikit-learn, pykrige, matplotlib, pyyaml

Additional (pip):
  pyprecag, pythonnet

External tools:
  VESPER — kriging engine, Windows standalone
  CN1 SDK — .NET Standard 2.0, via pythonnet bridge

IDE: VS Code + Python extension
Version control: Git + GitHub

---

## 12. Design Constraints

- Raw input files are never modified.
- Every run produces a structured log entry.
- All thresholds and parameters in paddock_config.yaml.
- Scripts parameterised by paddock ID — no hardcoded values.
- Outputs include version timestamp for traceability.

---

## 13. Multi-Year Handling (Deferred)

Per-year z-score normalisation before cross-year aggregation.
Single year only for v1.

---

## 14. Out of Scope — v1

FieldOps API integration, EM38, gamma, NDVI, seeding/lime/fungicide
prescriptions, multi-year analysis, cloud storage, multi-user access,
automated agronomist rate assignment.

---

## 15. Decisions Log

| Version | Decision                                                  |
|---------|-----------------------------------------------------------|
| v0.1    | pyprecag as default cleaning standard                     |
| v0.1    | Zone inputs: yield + protein only                         |
| v0.1    | Zone count: 3 (fixed, v1)                                 |
| v0.1    | Agronomist handoff: exploratory — CSV + PDF map           |
| v0.1    | Multi-year: single year for v1; z-score norm deferred     |
| v0.2    | Multi-harvester: per-machine mean offset correction       |
| v0.2    | Protein source: CropScan CSV                              |
| v0.3    | Python env: Conda Miniforge + pip for pyprecag            |
| v0.3    | Yield format: CN1 binary confirmed, SDK path chosen       |
| v0.3    | CN1 SDK via pythonnet (.NET bridge)                       |
| v0.3    | Environment and GitHub confirmed — ready to build         |
