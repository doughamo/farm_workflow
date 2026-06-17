# Farm Data Workflow — Project Constitution v0.9
# Status: Active development — Stages 0-6 implemented and audited
# (see CALCULATIONS_AUDIT.md); Stage 7 stub only; Phase 2 vision,
# zone map decision logic, OFE strip alignment, cropping recipes,
# crop plan integration, and FMS actuals architecture captured
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

CN1 SDK registration: complete. SDK and CN1 ADAPT plugin (Voyager2Plugin,
maps CN1 to open ADAPT data model) installed in sdk/cn1/ and in active use
since Stage 0 (see scripts/cn1_sdk.py).

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

Audit finding (2026-06, CALCULATIONS_AUDIT.md, risk item R-07): Stage 4
has now been built, and paddock_config.yaml currently declares the
interpolation method as "vesper". The code that actually runs performs
linear interpolation — neither pykrige nor VESPER are called. This does
not resolve the open item above; it adds evidence to it, and reveals
that the logged configuration is currently a false statement about what
the pipeline does. Until the open item above is resolved, the config
value should read "linear" so the provenance record is honest about
current behaviour. See Section 15.

Open item — pyprecag patch version control (2026-06, CALCULATIONS_AUDIT.md,
risk item R-05): pyprecag has been patched locally for this project, and
the patch is not currently tracked in version control. A conda/pip
environment rebuild — new machine, new analyst, multi-farm scaling —
would silently revert to unpatched upstream pyprecag behaviour with no
error raised. Resolution options: (1) fork pyprecag and pin the
environment to a specific commit of the fork; (2) extract the patched
logic out of the dependency entirely into utils.py as a post-processing
override, leaving the installed package untouched; (3) commit the patch
file into the repo with a documented reapplication step run after
install. Not yet decided. See Section 15.

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
| v0.6    | Phase 2 vision and architectural constraints captured (Section 17)   |
| v0.6    | Phase 2 open items logged — zone map logic, API liability, AI        |
|         | boundary, cooperative trigger (Section 17D)                          |
| v0.7    | Zone map decision logic: readiness gate model adopted as           |
|         | architectural pattern (Section 18A)                                |
| v0.7    | Zone map confidence: four-level gradient framework (Section 18B)   |
| v0.7    | Zone map input: nutrient-specific hierarchy confirmed (Section 18C) |
| v0.7    | Soil survey: enhancing layer for nitrogen; primary input for        |
|         | potassium, lime, gypsum, phosphorus (Section 18C)                  |
| v0.7    | Farmer input: first-class provenance object at Levels 1-2;         |
|         | advisory at Levels 3-4 (Section 18B)                               |
| v0.7    | Zone map recommendation: structured object, not just shapefile      |
|         | output; agronomist approves, modifies, or rejects (Section 18D)    |
| v0.7    | Competitive positioning: pipeline automation and accessibility,     |
|         | not analytical sophistication vs PCT Agcloud (Section 18B)         |
| v0.7    | Zone map open items logged: recommendation content, level           |
|         | thresholds, soil survey/nitrogen edge case, multi-nutrient         |
|         | conflicts, review triggers (Section 18E)                           |
| v0.7    | Next ideation session: product development planning — prototype     |
|         | scope and market validation approach                               |
| v0.9    | Calculation audit completed for Stages 0-6 (CALCULATIONS_AUDIT.md   |
|         | v1.0) — 29 calculation steps documented, 10 risk items logged        |
| v0.9    | Stage 4 interpolation discrepancy logged (R-07): config declares    |
|         | VESPER, code runs linear interpolation — pykrige vs VESPER open      |
|         | item updated with this evidence, not resolved (Section 11)          |
| v0.9    | New open item logged: pyprecag local patches not under version       |
|         | control (R-05) — resolution path (fork-and-pin / extract-to-utils /  |
|         | commit-patch-file) undecided (Section 11)                            |

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

---

## 17. Phase 2 Vision

### 17A — Strategic Intent

Phase 1 is a local-first pipeline operated by a single analyst on a
single machine. It is not scaleable and is restricted by who can access
and collaborate on the files it produces.

Phase 2 transitions the platform to a web-based, multi-user application
that solves both the access problem and the collaboration problem.

Core market thesis: agronomists and farm business consultants do not
currently spend time on paddock-level yield and protein analysis — not
because the insight is valueless, but because the data-to-insight
pipeline is too manual and time-expensive to convert into a billable
service. Automating the pipeline creates a new service category: a
routine paddock-level production analysis and OFE report that can be
delivered at scale by a Precision Agriculture Analyst working across
multiple farm clients.

Target user: trained Precision Agriculture Analyst, agronomist, or
competent farm operator. The platform is a professional tool requiring
spatial data literacy and domain training. It is not designed for
untrained end users. Attempting to serve both trained analysts and
untrained farmers in a single interface produces a tool that serves
neither — this tension is resolved by design, not deferred.

Cooperative transition pathway: the platform launches as a commercial
product. Once product-market fit is validated with real clients, the
platform transitions to a farm data cooperative structure. The working
assumption for the end state is a service provider model — the
cooperative owns member data; the company retains the contracted
technology role and derives revenue from the service contract, not
data ownership. Exact transition terms, timing, and governance
structure are to be negotiated with key clients and are explicitly
undecided at this stage. This uncertainty is preserved deliberately —
premature resolution of cooperative governance before product-market
fit is the identified failure mode of publicly-funded agtech
initiatives in Australia (WAFDS, AgriFood Data Exchange, OFT).

Governing principles: NFF Australian Farm Data Code and FAIR
(Findable, Accessible, Interoperable, Reusable) data principles apply
as architectural commitments from the first line of production code —
not as retrospective compliance.

---

### 17B — Confirmed Architectural Commitments

The following commitments are binding design constraints for Phase 2.
They are not open for reconsideration without a constitution update.

**1. Member-attributed data residency**
Every data record — yield files, protein maps, prescription histories,
trial results, weather observations — carries a farm business identifier
owned by the member, not the platform. Data is never pooled into a
shared schema in a way that makes member-level separation technically
difficult. This is the technical foundation of the cooperative data
model and must be enforced from the initial schema design.

**2. Data export as a first-class feature**
Any member must be able to retrieve their complete data history in an
interoperable format at any time, without friction or penalty. This is
not an afterthought — it is the technical proof of the value-based
retention claim. Farmers stay on the platform because it is better, not
because leaving is hard. If data export is not built in from the start,
it becomes commercially unappealing to implement later.

**3. Trial design as a persistent platform object**
On-farm experiment (OFE) trial strips are designed into prescription
files — not overlaid afterwards. The prescription itself includes
deliberate rate deviations functioning as treatment and control zones.
The trial design metadata (which zones are treatment, which are
control, target rates, nutrient type, season) must persist as a
first-class platform object from prescription generation through to
end-of-season OFE analysis. The platform holds state across an entire
season per paddock. Manual re-entry of trial design at analysis time
is not acceptable.

**4. Zone map provenance as a required data object**
The zone map used to generate a prescription is a derived output whose
inputs vary by nutrient type and data availability:
  - Nitrogen: averaged and normalised protein maps from prior seasons
  - Potassium: soil survey zones
  - Data-poor paddocks: fallback to single-season yield or imagery zones
The platform must record not just which zone map was used, but why —
from which input layers, by which selection logic, and at what date.
Zone map provenance is a distinct and necessary data object, not an
implicit consequence of storing the zone map file. The decision logic
for input layer selection when multiple layers are available for the
same nutrient is an open item (see Section 17D).

**5. Bidirectional machinery API integration**
Phase 2 integrates with John Deere Operations Center, AGCO, and CNH
FieldOps via API in both directions:
  - Read: yield monitor data, field operations records ingested
    directly from manufacturer platforms — removing manual
    export/import steps
  - Write: prescription files delivered directly to machine displays
    via API
These are architecturally distinct operations. The write direction
carries safety and liability implications not present in the read
direction. Liability position for incorrect prescription delivery via
API is an open item requiring legal input before this module is built
(see Section 17D).

**6. Bidirectional financial system integration**
Phase 2 integrates with Xero and Figured in both directions:
  - Pull: live grain prices, input costs, and interest rates into the
    gross margin engine — replacing manually entered values
  - Push: prescription costs, trial results, and gross margin outputs
    back into farm financial records as farm management data
The gross margin engine (Stage 11 in v1.1) is the integration point.

**7. Agentic AI layer — data model boundary**
The platform accumulates paddock-specific response functions via
embedded OFE trial history and seasonal covariates (IoT weather
stations, public datasets including SILO and BOM gridded products).
The AI learning layer operates at two levels:
  - Farm-specific models: trained on individual farm data, proprietary
    to that farm business member
  - Collective models: trained on pooled, anonymised cross-farm data,
    owned by the cooperative
These two layers must be architecturally separate from initial design.
The technical mechanism for enforcing this boundary — not just
contractually stating it — is an open item (see Section 17D).
Cross-farm learning requires explicit member consent architecture;
the consent model is an open item.

---

### 17C — Phase 2 Decisions Log

| Version | Decision                                                              |
|---------|-----------------------------------------------------------------------|
| v0.6    | Phase 2 target: web-based multi-user platform                         |
| v0.6    | Design-centre user: trained PA Analyst — not untrained farmer         |
| v0.6    | Cooperative end state: service provider model (working assumption)     |
| v0.6    | Governing principles: NFF Farm Data Code + FAIR — architectural,      |
|         | not retrospective compliance                                          |
| v0.6    | Machinery API: bidirectional confirmed (read and write)                |
| v0.6    | Financial API: Xero/Figured bidirectional confirmed                   |
| v0.6    | Trial design: persistent first-class platform object                  |
| v0.6    | Zone map provenance: required data object, not implicit               |
| v0.6    | Member data residency: farm-business-attributed from schema design    |
| v0.6    | Data export: first-class feature, not afterthought                    |
| v0.6    | AI layer: farm-specific and collective model weights architecturally  |
|         | separated from initial design                                         |
| v0.6    | Cooperative transition terms: explicitly undecided — to be           |
|         | negotiated with key clients; premature resolution avoided             |

---

### 17D — Phase 2 Open Items

The following questions are unresolved and must be addressed before
their respective modules are designed or built. They follow the same
pattern as the pykrige vs VESPER open item in Section 11 — explicitly
flagged, not silently deferred.

**Open item 1 — Zone map decision logic and provenance architecture**
When multiple input layers are available for the same nutrient, what
is the selection logic? Does the agronomist select the input layer, or
does the platform recommend one? If the platform recommends, on what
basis? The answer encodes agronomic judgement into the platform and
must be resolved in consultation with agronomist partners before the
zone generation module is designed for Phase 2.
Blocks: zone map module design.

**Open item 2 — Prescription write liability**
When a prescription file is written via API directly to a John Deere
or AGCO display and results in an incorrect application, who carries
the liability — the platform, the agronomist, the PA Analyst, or the
farmer? This requires legal input specific to Australian agricultural
services before the write integration is architected.
Blocks: machinery API write module design.

**Open item 3 — AI layer data model boundary**
How is the boundary between farm-specific model weights and collective
model weights technically enforced — not just contractually stated?
What is the consent architecture for cross-farm learning — per data
type, per model, or per use case? This requires a conceptual
specification before the AI layer is designed.
Blocks: agentic AI module design.

**Open item 4 — Cooperative transition trigger**
What is the specific milestone — client count, data volume, revenue
threshold, or governance event — that triggers the cooperative
transition conversation with key clients? Without a defined trigger,
the transition remains an aspiration rather than a planned event.
Blocks: business planning and legal structure work; does not block
platform development.

---

## 18. Zone Map Decision Logic

### 18A — Architectural Pattern: Readiness Gate Model

The zone map recommendation engine does not present a menu of options.
It evaluates the current data state of a paddock for a given nutrient
and routes through a sequential gate structure. Each gate is only
reached if the prior gate is answered affirmatively.

Gate 1 — Machinery capability
  Is a variable rate capable display confirmed for this farm?
  If no: workflow ends. No prescription is generated.
  Captured at: farm onboarding (machine make, model, display type,
  VR capability).

Gate 2 — Data availability
  What data has been collected and exists for this paddock?
  Evaluated against: yield, protein, soil survey, imagery, OFE history.

Gate 3 — Data accessibility and reliability
  Of the available data, what is accessible in the platform and of
  sufficient quality to use?
  Filters for: format compatibility, calibration status, vintage,
  cleaning pass rate, cloud mask coverage (imagery).

Gate 4 — Prescription delivery method
  Machinery API write, or manual shapefile export.
  Captured at: farm onboarding.

Gate 5 — OFE strip eligibility
  Has the paddock reached sufficient data maturity to embed trial
  strips and interpret response meaningfully?
  Determined by: confidence level assignment (see Section 18B).

---

### 18B — Confidence Level Framework

Zone map confidence is assigned per paddock per nutrient per season.
It is not a fixed property of the paddock — it is re-evaluated each
season as data accumulates.

Confidence is a function of two variables in low-data situations:
  (1) Statistical data quantity and quality
  (2) Farmer validation — whether the zone pattern makes sense given
      the farmer's direct observation of that paddock over time

As data accumulates across seasons and OFE trial strip history builds,
farmer validation transitions from co-validating to advisory. In
high-data situations the recommendation is data-driven and does not
require farmer validation to be defensible. This transition is gradual,
not binary.

**Level 1 — Minimum viable**
Data state:   One season of yield data only. No protein, soil survey,
              or imagery trend analysis available or reliable.
Zone method:  Single-season yield clustering, k=3.
Farmer input: Required and recorded as a named provenance input before
              any prescription is generated. Farmer must confirm the
              zone pattern is consistent with their paddock knowledge.
OFE strips:   Not eligible — insufficient baseline to interpret
              treatment response.

**Level 2 — Developing**
Data state:   Two or more seasons of yield; or one season of yield
              plus one season of protein; or multi-year imagery trend
              analysis confirming consistent spatial patterns.
Zone method:  Averaged and normalised yield layers, or yield-protein
              composite clustering. Zone boundary consistency assessed
              across available seasons.
Farmer input: Recommended. Platform flags any zone boundary that
              shifted substantially between seasons and invites farmer
              comment before finalising recommendation.
OFE strips:   Conditional. Eligible if zone boundaries show reasonable
              consistency across available seasons. Response
              interpretation will carry wider confidence intervals than
              Level 3 or 4 — this is stated explicitly in outputs.

**Level 3 — Established**
Data state:   Three or more seasons of yield and protein. Consistent
              zone behaviour visible across seasons. At least one season
              of embedded OFE trial strip data processed through the
              pipeline.
Zone method:  Multi-season averaged and normalised protein maps as
              primary nitrogen zone input. Soil survey incorporated as
              enhancing layer if available. Zone boundaries treated as
              stable unless flagged for review.
Farmer input: Advisory. Farmer review offered; platform recommendation
              is data-driven and defensible without farmer override.
OFE strips:   Standard practice. Embedded as routine; reviewed at
              end of season via automated OFE analysis pipeline.

**Level 4 — High confidence**
Data state:   Soil survey zones delineated by soil type. Multiple
              seasons of yield and protein confirming consistent zone
              behaviour. Multi-season OFE trial strip history showing
              nitrogen conversion efficiency by zone and rotation.
Zone method:  Soil survey zones as structural foundation, validated
              and refined by accumulated yield, protein, and OFE
              response data. Zone boundaries stable unless anomalous
              season or soil management change triggers review.
Farmer input: Informational. Farmer receives zone map and supporting
              evidence as a report, not a validation request.
OFE strips:   Expected across main soil types by rotation.

Expected market distribution at onboarding: most farms will arrive at
Level 2 or crossing the Level 2→3 boundary. Level 4 is currently
underserved — not due to farmer disengagement but due to poor data
management practices at farm level, the cost of cleaning and processing,
the expense of dedicated tools (e.g. PCT Agcloud), and a lack of
trained practitioners. The platform addresses this gap through pipeline
automation and interpretation support, not by competing on analytical
sophistication against specialist tools.

---

### 18C — Nutrient-Specific Zone Map Input Hierarchy

Zone map input selection is nutrient-specific. The same paddock will
use different primary input layers depending on which nutrient is
being prescribed. This is scientifically correct, not a simplification.

Nitrogen:
  Primary input:   Averaged and normalised protein maps from prior
                   seasons (reflects actual crop nitrogen response
                   under real seasonal conditions)
  Enhancing layer: Soil survey (where available)
  Fallback:        Single-season yield (Level 1) or multi-year
                   imagery trend (Level 2 where protein absent)

Potassium:
  Primary input:   Soil survey zones delineated by soil type
  Enhancing layer: Multi-season yield (confirms zone behaviour)
  Fallback:        Multi-year imagery trend (where soil survey absent)

Lime / Gypsum:
  Primary input:   Soil survey zones (pH, sodicity — structural
                   soil constraint inputs)
  Enhancing layer: Yield response history where available

Phosphorus:
  Primary input:   Soil survey zones (baseline fertility)
  Enhancing layer: Multi-season yield response

Implication for platform architecture: nutrient type is a primary
field in the zone map provenance record. The question "which zone
map should be used" is only answerable once the nutrient is specified.
Multi-nutrient prescriptions on the same paddock may use different
zone maps — each is generated and recorded independently.

---

### 18D — Zone Map Recommendation Object

The platform produces a structured recommendation object, not just a
zone map shapefile. This object is what the agronomist reviews and
approves, modifies, or rejects. Their decision — including any
boundary modifications — becomes part of the zone map provenance record.

Minimum required fields in recommendation object:
  - Paddock ID and season
  - Nutrient being prescribed
  - Confidence level assigned (1–4) and basis for assignment
  - Data layers used: type, vintage, quality flag for each
  - Zone method applied
  - Farmer input recorded (if Level 1 or 2)
  - Soil survey status: used as primary / used as enhancing / absent
  - Data quality flags (calibration warnings, coverage gaps, etc.)
  - Agronomist decision: approved / modified / rejected
  - If modified: boundary changes recorded with agronomist ID and date
  - Zone map provenance hash: unique identifier linking this
    recommendation object to the prescription file generated from it

Note: the minimum structured content required for the agronomist to
exercise genuine professional judgement without creating excessive
workflow friction is an open item deferred to market validation
(see Section 18E, Open Item 1).

---

### 18E — Zone Map Open Items

**Open item 1 — Recommendation object minimum content**
What is the minimum structured content of the recommendation object
that allows an agronomist to exercise genuine professional judgement,
without creating so much review friction that the efficiency gain is
lost? There is a genuine tension between auditability and usability
here. Resolution requires prototype testing with early adopter
agronomists under realistic workflow conditions.
Blocks: recommendation object UI design and agronomist review workflow.

**Open item 2 — Data quality thresholds for level assignment**
What is the precise threshold that moves a paddock from Level 1 to
Level 2? Is it purely season count, or does data quality (calibration
status, spatial coverage, cleaning pass rate) factor into the level
assignment? A paddock with three seasons of poorly calibrated yield
data may be less reliable than one with one season of well-calibrated
data.
Blocks: automated level assignment logic.

**Open item 3 — Soil survey present but yield/protein absent (nitrogen)**
How does the platform handle a paddock where soil survey exists but
no yield or protein data is available, for a nitrogen prescription?
Does it fall back to Level 1 with soil survey as a visual reference
only, or does soil survey presence elevate the confidence level for
nitrogen even though it is not the primary nitrogen input layer?
Blocks: Level 1/2 boundary logic for nitrogen specifically.

**Open item 4 — Multi-nutrient zone map conflicts**
When nitrogen and potassium zone maps for the same paddock are derived
from different input layers and produce different zone boundaries, how
is a multi-nutrient prescription handled? Options include: separate
prescriptions with separate zone maps per nutrient; a composite zone
map negotiated between the two; or agronomist selection of which zone
map to use as the spatial framework for all nutrients in that season.
Blocks: multi-nutrient prescription module design.

**Open item 5 — Zone boundary review triggers at Level 3 and 4**
What triggers a zone boundary review in a high-confidence paddock?
Candidates include: an anomalous season result, a change in soil
management practice, a new soil survey, a prescribed number of seasons
elapsed, or agronomist discretion. Who initiates the review — the
platform, the agronomist, or the farmer?
Blocks: zone map versioning and review workflow design.

---

## 19. OFE Trial Strip Alignment and Australian Cropping Context

### 19A — Machinery Run Line Alignment

Trial strip placement must be parallel to the machinery run line heading for the
paddock. A strip oriented perpendicular to the run line direction cannot be
implemented by the operator without deviating from their operational heading and
is therefore not a valid strip design.

Machinery run line heading is a required input to the OFE trial strip design
workflow. It cannot be derived from the zone map alone.

Required field added to trial strip design object:
  - machinery_run_line_heading: primary heading in degrees from north, or
    cardinal/intercardinal classification
  - run_line_consistency: flag for paddocks where multiple headings exist due
    to irregular shape, internal obstacles, or slope adjustments. Where
    consistency is uncertain, the strip placement is flagged for agronomist
    review before finalisation.

Data source for run line heading (in order of preference):
  1. Extracted from CN1 harvest GPS tracks (indirect dependency on yield
     ingestion pipeline being operational)
  2. Recorded at farm onboarding by PA Analyst
  3. Manually entered by agronomist at strip design time

Open item — Run line heading source dependency: if derived from CN1 tracks,
the OFE strip design workflow cannot be validated until at least one season
of yield data has been ingested for the paddock. This dependency must be
stated explicitly in the strip design workflow and surfaced to the agronomist
at design time if CN1 data is absent.

---

### 19B — Australian Grain Cropping Operational Recipes

Grain cropping in the Australian broadacre context has a well-defined base
operational recipe per season. Complexity layers onto this base according to
rainfall zone, farm management sophistication, and data availability. The
platform must understand this recipe structure to automate prescription
recommendations by paddock and management zone.

Base seasonal recipe (in sequence):
  1. Summer weed management — herbicide
  2. Pre-planting weed management — herbicide
  3. Planting — seed + starter phosphorus + starter nitrogen
  4. In-crop herbicide
  5. Top-up nitrogen
  6. Fungicide
  7. Harvest

Complexity layers — additional inputs:
  Additional nitrogen applications (rainfall-driven), lime, gypsum, fungicide
  (additional passes), plant growth regulators, soil wetting agents, potassium,
  additional phosphorus.

Complexity layers — physical management practices:
  Deep ripping, mouldboard ploughing, spading, stubble burning, stubble
  mulching. These are periodic interventions triggered by identified soil
  constraints, not annual events.

Complexity layers — data-driven inputs:
  Soil testing, plant tissue testing, soil penetrometer readings. These
  generate prescriptions for constraint remediation: subsoil compaction
  (physical intervention), acidity (lime), sodicity (gypsum).

Rainfall zone axis:

  Low rainfall environment:
    Primary constraint:   Water availability
    Risk posture:         Conservative
    Input rates:          Lower
    Paddock traversals:   Fewer
    Crop rotation:        Narrow — predominantly wheat and barley
    Fallowing:            Common — moisture conservation between seasons
    Farm scale:           Larger area

  High rainfall environment:
    Primary constraint:   Management practice
    Risk posture:         Less conservative
    Input rates:          Higher
    Paddock traversals:   More frequent
    Crop rotation:        Broader and more diverse
    Fallowing:            Uncommon
    Farm scale:           Smaller area

Rainfall zone is a required field at farm onboarding. It is not a derived
value. It contextualises prescription rate plausibility checking — a rate
that is agronomically normal in a high rainfall environment may be anomalous
in a low rainfall environment and should be flagged accordingly.

Architectural implication — in-season input history:
  The platform must carry a complete input history for each paddock within a
  season. Each prescription generation event must have access to what has
  already been applied in that paddock that season — product, rate, date,
  method. A nitrogen top-up recommendation generated without knowledge of the
  starter nitrogen rate applied at planting is agronomically incomplete.
  In-season input history is a required data model component, not optional
  context.

Architectural implication — physical intervention prescriptions:
  The current zone map framework (Section 18C) addresses fertility and nutrient
  prescriptions. Physical management practices (deep ripping zones, spading
  zones) require a distinct spatial layer — typically soil penetrometer data
  or known compaction maps — that is neither a yield layer nor a nutrient
  fertility layer. v1 scope boundary decision required: see Open Item 1 below.

---

### 19C — Crop Plan as Seasonal Prescription Context

Farmers begin each season with a paddock-level crop plan derived primarily
from the previous crop rotation. This plan defines the expected operational
recipe and yield target for that paddock and season.

The crop plan is not static. It is revised in-season in response to rainfall
deviation from expectation:
  - Rainfall above expectation: yield potential increases, nitrogen budget
    increases, additional input passes may be added to the recipe
  - Rainfall below expectation: yield potential contracts, planned input
    passes may be reduced or eliminated to protect margin

The platform cannot treat the crop plan as a static input read once at
season start. It is a living document that must be confirmed current at
each prescription generation event mid-season.

Crop plan content relevant to prescription generation (minimum):
  - Crop species and variety (determines yield potential ceiling, nitrogen
    response curve, disease susceptibility, fungicide program requirements)
  - Intended planting date and seeding rate
  - Planned input program — starter rates, top-up timing and rates,
    herbicide program
  - Expected yield target (basis for nitrogen budgeting)
  - Rotation history — prior seasons' crops, disease break logic,
    nitrogen carry-over assumptions

Yield target is a required named field in the prescription recommendation
object (Section 18D). Required provenance fields alongside it:
  - Source: FMS-sourced / analyst-entered / defaulted
  - Date last confirmed

Agronomist role in crop planning:
  The agronomist will typically create the initial crop plan and budget on
  behalf of the grower. The platform must support this workflow — the
  agronomist is the primary plan author, not the farmer.

FMS integration approach:
  Farm Management Software (AgWorld, BackPaddock, and equivalents) is the
  standard repository for crop plans in the target market. Not all FMS
  platforms expose APIs. Integration feasibility must be assessed per
  platform and per client at onboarding.

  Where FMS API is available: read crop plan at season start; monitor for
  in-season updates; write completed prescription records back as activity
  records (bidirectional).

  Where FMS API is unavailable: PA Analyst confirms or updates crop plan
  context manually before each prescription generation event. The platform
  must not treat FMS connectivity as a hard dependency.

---

### 19D — FMS Data Reliability and Actuals Recording Architecture

FMS records cannot be relied upon as accurate records of in-season
applications, crop species, or variety. Farmers and farm workers frequently
do not update actuals into the FMS during the season. The FMS holds a plan;
it does not reliably hold a record of what happened.

This is a structural behaviour problem, not a technology problem. The
incentive to record actuals is weak and deferred; the cost of not recording
is invisible until the data is needed. Any solution that relies on improving
farmer recording discipline as its primary mechanism will fail.

Authoritative actuals source: machinery telematics.

Telematics records the physical operation as a byproduct of machine
operation — no deliberate data entry required. Coverage by operation type:
  - Harvest:     GPS track, yield, timing (CN1 — already in pipeline)
  - Planting:    Seeding rate, GPS track, variety (if programmed into
                 controller)
  - Fertiliser:  Application rate, GPS track
  - Spraying:    Product (if programmed), rate, GPS track
  - Tillage:     GPS track, timing

Telematics limitation: records the physical operation but not always the
agronomic intent. Product in the bin, target rate rationale, and whether
an application was planned or corrective require resolution beyond what
telematics can confirm.

AI agent — actuals reconciliation mediator:
  The agent detects a telematics-confirmed operation, compares it against
  the crop plan, identifies data gaps, and resolves them through targeted
  dialogue with the farmer or farm worker who performed the operation.
  Resolved actuals are written back to the FMS, maintaining an accurate
  season history without requiring the farmer to initiate recording.

  The agent's function here is narrow and deterministic: it is resolving
  known data gaps through structured questions, not making agronomic
  judgements. This is a low-risk agentic application.

  Required conditions for reliable operation:
    1. Consistent telematics coverage across the farm's machinery fleet
    2. Timely query delivery — questions must reach the operator while
       recall is accurate, ideally within 24 hours of the operation
    3. Consistent use across seasons — the actuals record compounds in
       value over multiple seasons of consistent capture

Notification and response channel:
  The farm worker interaction pattern is reactive, mobile, and time-
  sensitive. A workflow requiring farm workers to log into a desktop
  platform to respond to actuals queries will fail for the same reasons
  FMS self-recording currently fails. The outbound query channel must
  reach the operator in the field — SMS or minimal mobile interface is
  the target; full platform login is not acceptable as the primary path.

User role implication:
  Two distinct interaction patterns are required:

  Agronomist / PA Analyst:
    Creates and manages crop plans; reviews prescription recommendations;
    approves zone map recommendation objects; interprets OFE outputs.
    Primary platform interface: desktop, full feature access.

  Farmer / farm worker:
    Responds to actuals queries; confirms operations; receives
    prescription delivery notifications.
    Primary interface: SMS or minimal mobile — reactive, not initiating.

  This role distinction is an architectural commitment, not a UX
  preference. It must be stated before Phase 2 UI design begins as it
  materially affects the notification architecture and mobile interface
  requirements.

FMS write-back architecture:
  Agent-mediated write-back (telematics + dialogue resolution) is the
  preferred actuals recording architecture over direct FMS API
  integration. It produces a more reliable record by incorporating
  telematics confirmation and resolving ambiguities through dialogue,
  and it bypasses the API availability constraint.

  FMS API integration is pursued where APIs exist and client
  concentration makes it worthwhile. It is not a platform dependency.

  The provenance record must distinguish between:
    - Telematics-confirmed actuals
    - Agent-dialogue-resolved actuals
    - Agronomist-entered actuals (farm visit reconciliation)
    - Unresolved actuals (escalated to agronomist)

  Unresolved actuals queries — where the farmer has not responded
  within a defined window — must escalate to the agronomist. Silent
  propagation of unresolved data gaps into the season input history
  is not permitted.

---

### 19E — Section 19 Open Items

**Open item 1 — Physical intervention prescription scope boundary**
Does the platform scope to spatial prescription of physical management
practices (deep ripping zones, spading zones) in v1, or is v1 explicitly
scoped to fertility and nutrient prescriptions only? If the former, Section
18C requires an additional input hierarchy for physical constraint management
(penetrometer data, compaction maps). If the latter, the boundary must be
stated explicitly.
Blocks: Section 18C extension and physical constraint data model.

**Open item 2 — Run line heading data source dependency**
Where run line heading is derived from CN1 harvest GPS tracks, the OFE
strip design workflow cannot be validated until at least one season of yield
data has been ingested. What is the fallback for new paddocks with no CN1
history — manual entry only, or is onboarding data collection sufficient?
Blocks: OFE strip design workflow for new farm onboarding.

**Open item 3 — In-season input history data model**
What is the data structure for the in-season input history per paddock —
specifically, how are telematics-confirmed actuals, agent-resolved actuals,
and manually-entered actuals stored and distinguished in the same record?
Blocks: prescription generation context and actuals reconciliation agent.

**Open item 4 — Actuals query response window and escalation threshold**
What is the maximum time window before an unanswered actuals query
escalates from farmer/farm worker to agronomist? What is the escalation
mechanism — platform notification, email, or SMS to agronomist?
Blocks: actuals reconciliation agent workflow design.

**Open item 5 — Telematics coverage profile at onboarding**
What proportion of farms in the target client base have telematics-enabled
machinery with accessible data? Where coverage is absent or partial, what
is the fallback actuals recording pathway and how is it distinguished in
the provenance record?
Blocks: actuals reconciliation agent viability assessment at launch.

**Open item 6 — FMS API availability by platform**
Which FMS platforms are used by the agronomist consulting partner's current
client portfolio? Of these, which expose usable APIs for read and write?
Client concentration in one platform would simplify the integration roadmap.
Blocks: FMS integration prioritisation for Phase 2 build.
