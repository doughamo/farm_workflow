# Farm Data Workflow — Project Constitution v0.5
# Status: Baseline — environment confirmed, SDK registration pending
# Last updated: 2026-06

---

## 1. Purpose

Build a local-first, modular Python pipeline that ingests, cleans,
analyses, and interprets farm spatial data to serve two related
analytical workflows:

  (A) Variable rate prescription generation — producing N prescription
      shapefiles compatible with Case IH AFS displays.

  (B) On-farm experiment (OFE) analysis — processing yield response
      to fertiliser treatments, estimating localised response via GWR,
      and calculating gross margins.

v1 scope: workflow A only (nitrogen VRA), single paddock, single season.
Workflow B is planned scope, to be added in v1.1.

An existing R Shiny application covers workflow B and operates in
parallel during v1. The Python pipeline will port this capability
(see Section 16 — Interop Strategy).

Designed so that additional paddocks, seasons, and prescription types
can be added without restructuring the codebase.

---

## 2. Users and Roles

| Role                  | Responsibility                                                      |
|-----------------------|---------------------------------------------------------------------|
| Farm operator         | Runs pipeline, reviews QA flags, delivers prescription              |
| Agronomist/consultant | Receives zone map, assigns N rates per zone                         |
| Claude Code           | Script development and architecture assistance                      |
| R Shiny OFE tool      | Parallel system — Path B interop, receives cleaned yield shapefile, |
|                       | produces GWR and gross margin outputs (temporary, v1 only)          |

The workflow produces spatial products.
Agronomic decisions remain with the agronomist.

---

## 3. Data Inputs

| Layer              | Format            | Source                     | Status      |
|--------------------|-------------------|----------------------------|-------------|
| Yield              | CN1 ZIP → .shp    | AFS Connect manual export  | v1 active   |
| Protein            | CSV               | CropScan meter             | v1 active   |
| Paddock boundaries | Shapefile         | Existing on-farm files     | v1 active   |
| Treatment zones    | Shapefile         | Agronomist / trial design  | v1.1 active |
| R OFE outputs      | GeoTIFF/Shapefile | R Shiny tool output        | v1 interop  |
| EM38               | TBD               | Third-party survey         | Deferred    |
| Gamma radiometrics | TBD               | Third-party survey         | Deferred    |
| Soil tests         | CSV               | Lab / agronomist           | Deferred    |
| NDVI / imagery     | GeoTIFF           | On-demand                  | Deferred    |

---

## 4. Data Outputs

| Output                      | Format    | Destination                  |
|-----------------------------|-----------|------------------------------|
| Cleaned yield layer         | Shapefile | data/processed/yield/        |
| Cleaned protein layer       | Shapefile | data/processed/protein/      |
| Normalised yield raster     | GeoTIFF   | data/processed/rasters/      |
| Normalised protein raster   | GeoTIFF   | data/processed/rasters/      |
| Management zone map         | Shapefile | data/outputs/zones/          |
| Zone summary statistics     | CSV       | data/outputs/handoff/        |
| Printable zone map          | PDF       | data/outputs/handoff/        |
| N prescription shapefile    | Shapefile | data/outputs/prescriptions/  |
| OFE yield-treatment summary | CSV       | data/outputs/ofe/            |
| Interop yield export        | Shapefile | data/interop/r_input/        |
| Run log                     | CSV       | logs/                        |

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

## 9. Workflow Stages

Stage 0   cn1_to_points    Convert CN1 ZIP → point shapefile
Stage 1   ingest           Validate inputs, log metadata
Stage 2   clean_yield      Per-machine normalisation + pyprecag
Stage 3   clean_protein    CropScan CSV → cleaned point shapefile
Stage 4   normalise        Interpolate to raster, z-score normalise
Stage 5   zones            K-Means clustering (k=3), spatial filter
Stage 6   handoff          Zone summary CSV + PDF map for agronomist
          [manual]         Agronomist assigns N rate per zone
Stage 7   prescription     Join rates → AFS-compatible shapefile
Stage 8   ofe_prep         [Path B interop] Write cleaned yield shapefile
                           to data/interop/r_input/. Validate R tool
                           outputs are present in data/interop/r_output/
                           before downstream reporting depends on them.

--- v1.1 (OFE analysis — pending port from R) ---

Stage 9   ofe_analysis     Assign yield observations to treatment zones;
                           per-zone yield summaries
Stage 10  gwr              Geographically weighted regression;
                           localised fertiliser response coefficients
                           (Python: mgwr)
Stage 11  gross_margin     Gross margin raster per pixel:
                           (yield × grain price) − (product × cost/t)
                           − installation cost

Stages 9–11 are stubs only in v1. They replace the R Shiny OFE tool
when Path B interop is retired.

---

## 10. Folder Structure

farm_workflow/
├── constitution/
│   └── CONSTITUTION.md       <- this file
├── data/
│   ├── raw/
│   │   ├── yield/            <- CN1 ZIPs and converted point shapefiles
│   │   ├── protein/          <- CropScan CSV files
│   │   ├── boundaries/       <- paddock boundary shapefiles
│   │   ├── treatments/       <- treatment zone shapefiles (v1.1)
│   │   └── soil/             <- reserved
│   ├── processed/
│   │   ├── yield/            <- cleaned point shapefiles
│   │   ├── protein/          <- cleaned point shapefiles
│   │   └── rasters/          <- normalised GeoTIFFs
│   ├── interop/
│   │   ├── r_input/          <- Python writes here; R tool reads from here
│   │   └── r_output/         <- R tool writes here; Python reads from here
│   └── outputs/
│       ├── zones/            <- management zone shapefiles
│       ├── handoff/          <- CSVs and PDFs for agronomist
│       ├── ofe/              <- OFE analysis outputs (v1.1)
│       └── prescriptions/    <- final AFS prescription shapefiles
├── scripts/                  <- Python pipeline (numbered, sequential)
│   ├── utils.py              <- shared logging and run tracking
│   ├── 00_cn1_to_points.py   <- stub
│   ├── 01_ingest.py          <- stub
│   ├── 02_clean_yield.py     <- stub
│   ├── 03_clean_protein.py   <- stub
│   ├── 04_normalise.py       <- stub
│   ├── 05_zones.py           <- stub
│   ├── 06_handoff.py         <- stub
│   ├── 07_prescription.py    <- stub
│   ├── 08_ofe_prep.py        <- stub (to be created)
│   ├── 09_ofe_analysis.py    <- stub (to be created)
│   ├── 10_gwr.py             <- stub (to be created)
│   └── 11_gross_margin.py    <- stub (to be created)
├── r_ofe/                    <- R Shiny OFE tool (Path B, temporary)
│   ├── colors.R              <- main Shiny app (launch this file)
│   ├── grid.R                <- colour and legend utilities
│   └── myReadOGR.R           <- kriging interpolation functions
├── config/
│   └── paddock_config.yaml   <- all pipeline parameters
├── logs/                     <- run logs
├── sdk/
│   └── cn1/                  <- CN1 SDK DLLs (not committed to git)
└── README.md

Note: app.R removed. The Shiny app launches from colors.R directly
via the shinyApp(ui, server) call at the bottom of that file.

---

## 11. Python Environment

Manager: Conda (Miniforge)
Environment name: farmworkflow
Python version: 3.11

Core libraries (conda-forge):
  gdal, fiona, rasterio, geopandas, numpy, pandas,
  scikit-learn, pykrige, matplotlib, pyyaml

Additional (pip):
  pyprecag, pythonnet, mgwr

External tools:
  VESPER — kriging engine, Windows standalone
  CN1 SDK — .NET Standard 2.0, via pythonnet bridge

Note: pykrige (conda-forge) and VESPER are both listed as kriging
options. The relationship between them — which is canonical for which
stage — is an open decision to be resolved and logged before Stage 4
is built. See Section 15.

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

OFE analysis — v1 only. Planned for v1.1. R Shiny tool used in
parallel via Path B interop during v1.

---

## 15. Decisions Log

| Version | Decision                                                             |
|---------|----------------------------------------------------------------------|
| v0.1    | pyprecag as default cleaning standard                                |
| v0.1    | Zone inputs: yield + protein only                                    |
| v0.1    | Zone count: 3 (fixed, v1)                                            |
| v0.1    | Agronomist handoff: exploratory — CSV + PDF map                      |
| v0.1    | Multi-year: single year for v1; z-score norm deferred                |
| v0.2    | Multi-harvester: per-machine mean offset correction                  |
| v0.2    | Protein source: CropScan CSV                                         |
| v0.3    | Python env: Conda Miniforge + pip for pyprecag                       |
| v0.3    | Yield format: CN1 binary confirmed, SDK path chosen                  |
| v0.3    | CN1 SDK via pythonnet (.NET bridge)                                  |
| v0.3    | Environment and GitHub confirmed — ready to build                    |
| v0.4    | Platform scope expanded: two workflows — VRA (A) and OFE (B)        |
| v0.4    | Interop strategy: Path B (file-based handoff) during porting period  |
| v0.4    | Runtime interop via rpy2 rejected — dependency management cost       |
| v0.4    | R-to-Python port targets: gstat → pykrige, GWmodel → mgwr           |
| v0.4    | mgwr added to pip dependencies for Stage 10 (GWR port)              |
| v0.4    | Kriging tool decision (pykrige vs VESPER) deferred — open item       |
| v0.5    | R scripts placed in r_ofe/ (top-level, separate from scripts/)       |
| v0.5    | app.R removed — Shiny app launches from colors.R directly            |
| v0.5    | rgdal and rgeos removed from R packages; readOGR → sf::st_read      |
| v0.5    | Circular source() bug in colors.R fixed                              |
| v0.5    | Stage stubs 08–11 identified as not yet created in repo              |

---

## 16. Interop Strategy (Path B — temporary)

The R Shiny OFE tool operates in parallel with the Python pipeline
during v1. Integration is file-based only — no runtime dependency
between the two environments.

Data contract:

  Python → R:  Cleaned yield point shapefile
               Location:        data/interop/r_input/
               CRS:             UTM Zone 50 South (EPSG:32750)
               Required fields: E, N, yield (t/ha), machine_id
               Filename:        {paddock_id}_{season}_yield_clean.shp

  R → Python:  GWR response raster (GeoTIFF)
               Gross margin raster (GeoTIFF)
               Location:        data/interop/r_output/
               CRS:             UTM Zone 50 South (EPSG:32750)
               Filename:        {paddock_id}_{season}_gwr_response.tif
                                {paddock_id}_{season}_gross_margin.tif

Stage 8 (ofe_prep.py) manages this handoff and validates both sides
before any downstream stage proceeds.

R Shiny tool status: rgdal and rgeos dependencies removed (v0.5).
readOGR replaced with sf::st_read. Circular source() bug fixed.
R tool is runnable on modern R installations. Path B is unblocked.

Path B is temporary scaffolding. It is retired when Stages 9–11 pass
output validation against R tool results on the same input data.
