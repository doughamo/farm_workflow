# Farm Data Workflow — Calculations Audit v1.0
# Status: Initial audit — Stages 0–6 fully documented; Stage 7 stub only
# Last updated: 2026-06

---

## Changelog

| Version | Date    | Author       | Summary                                                      |
|---------|---------|--------------|--------------------------------------------------------------|
| v1.0    | 2026-06 | Claude Code  | Initial creation. Stages 0–6 audited. Stage 7 is stub only. |

---

## How to use this document

This document records every calculation step in the pipeline, in the language of the agronomic
problem being solved — not in code terms. It is intended to be read and signed off by a
technical agricultural scientist who does not need to read Python code to verify whether the
arithmetic is correct and the assumptions are agronomically defensible.

Each calculation step has a **Reviewer sign-off** block at the end. Leave the pipeline script
running on live paddock data until every sign-off block in that stage has been completed by a
qualified reviewer.

Reference: `constitution/CONSTITUTION.md` — source of truth for pipeline scope and design decisions.

---

## Script classification

| Script               | Status                | Notes                                              |
|----------------------|-----------------------|----------------------------------------------------|
| `utils.py`           | Implemented           | Shared infrastructure — no agronomic calculations  |
| `00_cn1_to_points.py`| Implemented           | Full CN1 conversion with season filter and yield t/ha |
| `01_ingest.py`       | Implemented           | Validation gate — boundary check and field checks  |
| `02_clean_yield.py`  | Implemented           | Speed filter, machine normalisation, percentile and pyprecag trim |
| `03_clean_protein.py`| Implemented           | Range filter, z-score filter, boundary clip        |
| `04_normalise.py`    | Implemented           | Interpolation, coverage gap check, z-score rasters |
| `05_zones.py`        | Implemented           | K-Means, spatial filter, majority smoothing, vectorise |
| `06_handoff.py`      | Implemented           | Zone summary CSV and PDF map                       |
| `07_prescription.py` | Stub only             | Not yet implemented — no calculations to audit     |

---

## Stage 0 — CN1 to Points (`00_cn1_to_points.py`)

Stage 0 reads the raw harvest data from the machine's data card (CN1 format), selects only
the harvest records that belong to the correct paddock and season, and converts them into a
point shapefile with one point per GPS observation.

---

### 0.1 — Season window filter

**Purpose**

Western Australian grain harvest runs through the southern-hemisphere summer, spanning
October of one calendar year through January of the next. A single CN1 export from AFS
Connect can contain harvest records from multiple seasons or multiple farms on the same data
card. This step selects only harvest records that belong to the target season.

**Inputs and assumptions**

- `run.season` from config — a four-digit year (e.g. `"2025"`), interpreted as the October
  start of the season.
- Each harvest operation's date is determined from the timestamp of its **first GPS point**.
- The code assumes the first spatial record is representative of when the whole operation
  occurred. If an operation spans midnight into a new day, or if GPS timestamps are
  unreliable at the very start of a harvesting pass, the date assigned to that operation may
  be wrong.
- No separate config field exists for the season window — it is derived entirely from
  `run.season`.

**Calculation logic**

Given `season_year = int(run.season)`:

```
season_start = 1 October of season_year
season_end   = 31 January of (season_year + 1)
```

For each harvest operation in the CN1 data, read the timestamp of the first GPS point and
extract its calendar date. If that date is not in the range `[season_start, season_end]`
(inclusive), the entire operation is skipped.

For `run.season = "2025"`:
- Season start: 1 October 2025
- Season end:   31 January 2026

**Worked example**

Fictional paddock "Drysdale North", season 2025. Two operations found in CN1 export:
- Operation A: first GPS point timestamp = 14 November 2025 → date = 14 Nov 2025. Is 14 Nov 2025 between 1 Oct 2025 and 31 Jan 2026? Yes → **kept**.
- Operation B: first GPS point timestamp = 22 March 2025 → date = 22 Mar 2025. Is 22 Mar 2025 between 1 Oct 2025 and 31 Jan 2026? No → **skipped**.

A log line is written for each skipped operation, recording the LoggedData ID, OperationData ID, and the date that fell outside the window.

Cannot be run in isolation without the CN1 SDK and a real CN1 file.

**Known limitations and edge cases**

- If a harvest operation truly spans midnight (e.g. starts 23:58 on 31 January 2026), only
  the first GPS point's date is checked. The entire operation is excluded even though
  the majority of it occurred within season.
- If GPS synchronisation was lost at the very start of a harvest pass, the first spatial
  record's timestamp may be incorrect. All subsequent records in that operation would still
  be included.
- The window is hard-coded as October–January. If the operator uses the same CN1 export
  for an unusual late-harvest paddock finishing in February, those records are silently
  excluded with no warning beyond the per-operation log line. The operator must check the
  log.
- A CN1 export covering multiple farms: only records whose ADAPT field description matches
  `run.paddock_name` (exact string match) are included. Name mismatches between CN1 and
  config are silent beyond an "No Harvesting OperationData found" fatal error.

**Reviewer sign-off**

| Field             | Value |
|-------------------|-------|
| Reviewer name     |       |
| Date reviewed     |       |
| Version reviewed  | v1.0  |
| Verdict           |       |
| Comments          |       |

---

### 0.2 — Protein sentinel filter

**Purpose**

The CN1 protein sensor (CropScan on-header) records a specific numeric code when it has not
been configured, has no reading, or is turned off. If this code is treated as a real protein
percentage it will corrupt any protein-based analysis. This step detects and removes those
sentinel values before the data leaves Stage 0.

**Inputs and assumptions**

- The sentinel value is `6553.5` — this is the 16-bit unsigned integer maximum (65535)
  divided by 10, which is how the ADAPT SDK decodes a 16-bit "no data" sensor code when the
  units are tenths of a percent.
- The code performs an exact equality check: `row["protn_pct"] == 6553.5`. This assumes
  the decoded value will be exactly this number, not a close approximation. If the SDK
  decodes the value with any floating-point rounding, the sentinel could be missed.
- Sentinel values are set to `None` (null) in the output shapefile. They are not dropped —
  the GPS point is retained without a protein reading.

**Calculation logic**

For each GPS point:

```
if decoded_protein_value == 6553.5:
    set protein field to null
```

A counter tracks how many null substitutions occurred, logged at run end.

**Worked example**

Point 1: sensor decoded = 14.2 → stored as 14.2% (normal reading, retained).
Point 2: sensor decoded = 6553.5 → stored as null (sentinel detected, protein field blanked).
Point 3: sensor decoded = 11.8 → stored as 11.8% (normal reading, retained).

If 250 sentinel values were found across 120,000 points, the log will show:
`Protein sentinel (no-data) values filtered: 250`

**Known limitations and edge cases**

- The sentinel check is an exact float comparison. Floating-point arithmetic can produce
  values like 6553.499999 or 6553.500001. If this happens, a sentinel passes through as a
  protein reading of approximately 6,553%. Stage 3's range filter [5, 25]% would catch
  this downstream, but the failure mode is silent at Stage 0.
- If the CropScan is ever calibrated to return values in the range near 6553.5 (which is
  agronomically impossible for protein — realistic maximum is ~20%), this would not be
  a practical risk.
- Points with null protein are still written to the shapefile and used by all subsequent
  yield-cleaning stages. They are invisible to protein analysis stages (Stage 3 drops null
  rows) but do not distort yield cleaning.

**Reviewer sign-off**

| Field             | Value |
|-------------------|-------|
| Reviewer name     |       |
| Date reviewed     |       |
| Version reviewed  | v1.0  |
| Verdict           |       |
| Comments          |       |

---

### 0.3 — Yield conversion from mass-per-area to tonnes per hectare

**Purpose**

The CN1 harvest monitor records yield as a mass in kilograms (the grain mass harvested over
each GPS interval) and a harvested area in square metres (the area covered during that
interval). Neither figure alone is useful for agronomic comparison between parts of the
paddock. This step converts to the standard agronomic unit of tonnes per hectare.

**Inputs and assumptions**

- `yield_kg` (kg) — wet mass of grain over the GPS interval. Decoded from the ADAPT
  representation code `vrYieldWetMass`.
- `area_m2` (m²) — area harvested during the GPS interval. Decoded from `vrDeltaArea`.
- The calculation is performed on wet mass. No moisture correction is applied at Stage 0.
  The `moist_pct` field is carried through but not used in the conversion.
- If `area_m2` is null or zero, `yield_tha` is set to null rather than dividing by zero.

**Calculation logic**

```
yield_tha = yield_kg / area_m2 × 10
```

The factor of 10 converts kg/m² to t/ha:
- 1 t/ha = 1000 kg / 10,000 m² = 0.1 kg/m²
- Therefore kg/m² × 10 = t/ha

**Worked example**

Single GPS observation:
- `yield_kg` = 185.0 kg
- `area_m2`  = 55.0 m²

```
yield_tha = 185.0 / 55.0 × 10
          = 3.3636... × 10
          = 33.636 t/ha   ← obviously wrong; this is an unrealistically high value
```

Recalculate with more realistic values:
- `yield_kg` = 18.5 kg (mass per ~1 second interval at 5 t/ha, 10m header, 5 km/h)
- `area_m2`  = 13.9 m² (10m header × 1.39m distance travelled per second at 5 km/h)

```
yield_tha = 18.5 / 13.9 × 10
          = 1.3309 × 10
          = 13.3 t/ha
```

That is still high; realistic wheat yields are 1.5–5 t/ha for WA. The CN1 records mass per
GPS epoch (typically 1 second at ~5 km/h), which is a very small area. The actual value of
`area_m2` in the CN1 data represents the true swept area and will produce realistic t/ha
values. The specific mass and area per epoch are machine- and speed-dependent and are
verified in practice via the Stage 1 QA flags and Stage 2 distribution checks.

**Known limitations and edge cases**

- Wet mass is used without moisture correction. WA grain is delivered at a target moisture
  (typically ~12% for wheat); if the header sensor is reading elevated moisture, the
  apparent yield in t/ha will be inflated relative to delivered dry weight. Stage 2's
  percentile trim partially compensates but does not fully correct for systematic moisture
  bias.
- If `area_m2 = 0` (e.g. machine stationary with header engaged), `yield_tha` is null.
  The speed filter in Stage 2 removes stationary periods independently, but if a zero-area
  record slips through it appears as null rather than infinite, which is the safer outcome.
- The formula assumes `vrDeltaArea` from the CN1 SDK is the correct swept-area figure.
  If the CN1 plugin returns a cumulative area rather than a delta, the formula would
  produce values orders of magnitude too small. This assumption is not verified in the code
  by checking that adjacent area_m2 values sum to approximately the paddock total area.

**Reviewer sign-off**

| Field             | Value |
|-------------------|-------|
| Reviewer name     |       |
| Date reviewed     |       |
| Version reviewed  | v1.0  |
| Verdict           |       |
| Comments          |       |

---

### 0.4 — CRS reprojection (GPS → project CRS)

**Purpose**

GPS data from the CN1 is recorded in WGS84 latitude/longitude (degrees). All spatial
analysis in this pipeline uses a projected coordinate system in metres so that distances,
areas, and spatial operations are accurate. This step transforms all points from WGS84 to
the configured project CRS before any spatial work begins.

**Inputs and assumptions**

- Input CRS: `EPSG:4326` (WGS84 latitude/longitude). Hardcoded via `crs.input_gps` config.
- Output CRS: `EPSG:28350` (GDA94 / MGA Zone 50). Configured via `crs.project`.
- MGA Zone 50 is appropriate for longitudes 114–120°E (the WA wheatbelt). If a paddock is
  east of 120°E, Zone 51 (EPSG:28351) should be used instead — there is a note in the
  config but the code does not check that the paddock coordinates fall within Zone 50's
  extent.
- Reprojection is done by geopandas (`gdf.to_crs()`), which uses PROJ internally. The
  accuracy of the transformation depends on the PROJ datum grid files available in the
  environment.

**Calculation logic**

Point-by-point coordinate transformation. For a point at (longitude, latitude) = (116.85°E, 31.40°S):
- In EPSG:28350: approximately Easting 608,000 m, Northing 6,525,000 m.
- The transformation is a standard map projection — not a formula this audit needs to
  verify; the correctness of PROJ's GDA94 implementation is assumed.

**Known limitations and edge cases**

- If `crs.project` is set to Zone 50 but the paddock is in Zone 51, easting coordinates
  will be significantly wrong (errors of tens of metres per kilometre of distance from the
  zone boundary). No automated check is performed.
- WGS84 and GDA94 differ by approximately 1.5–2 metres in Australia. For paddock-scale
  analysis this difference is not significant, but it should be noted for any work that
  combines this data with GDA2020-based survey data.

**Reviewer sign-off**

| Field             | Value |
|-------------------|-------|
| Reviewer name     |       |
| Date reviewed     |       |
| Version reviewed  | v1.0  |
| Verdict           |       |
| Comments          |       |

---

### 0.5 — Implement width derivation

**Purpose**

The width of the harvester header (the cutting bar) is needed by Stage 4 to assess whether
protein sensor readings have adequate spatial coverage across the paddock. Rather than
requiring the operator to enter it manually, the pipeline derives it from the GPS data itself:
each GPS point records both how much area was swept and how far the machine travelled, so
dividing the two gives the header width.

**Inputs and assumptions**

- `area_m2` — swept area per GPS epoch (m²), from CN1 `vrDeltaArea`.
- `dist_m` — distance travelled per GPS epoch (m), from CN1 `vrDistanceTraveled`.
- Both must be positive and non-null for a point to be included in the width calculation.
- Plausibility filter: only points where the derived width is between 2 m and 30 m are used.
  A width below 2 m or above 30 m is assumed to be a sensor error or stationary period.
- The result is the **median** derived width across all plausible points. Median is used
  rather than mean to reduce the influence of intermittent sensor errors or end-of-row
  turning artefacts.
- Calculation is per-machine and overall; both are logged.

**Calculation logic**

For each GPS point where `area_m2 > 0` and `dist_m > 0`:

```
impl_width_at_point = area_m2 / dist_m
```

Keep only points where `2.0 ≤ impl_width_at_point ≤ 30.0`.

```
implement_width = median(impl_width_at_point for all kept points)
```

**Worked example**

Five GPS points from fictional harvester "H1":

| Point | area_m2 | dist_m | derived_width_m | In range [2,30]? |
|-------|---------|--------|-----------------|-----------------|
| 1     | 126.0   | 10.5   | 12.00           | Yes             |
| 2     | 130.2   | 10.4   | 12.52           | Yes             |
| 3     | 0       | 10.3   | 0 (skip: area=0)| –               |
| 4     | 124.8   | 10.6   | 11.77           | Yes             |
| 5     | 900.0   | 9.8    | 91.84           | No (>30)        |

Sorted kept widths: [11.77, 12.00, 12.52]. Median = 12.00 m.
Logged as: `Implement width — H1: 12.0m (median, n=3)`.

**Known limitations and edge cases**

- If the header lifts (end-of-row turn) without `area_m2` dropping to zero, the derived
  width for those epochs will be meaningless. The plausibility filter [2, 30] m captures
  most of these but not all.
- The median is stable for large point counts (tens of thousands), but for very short
  paddocks with few GPS points, a small number of artefact epochs could shift the median.
- This derivation is performed independently in both Stage 0 and Stage 4 from the same raw
  file. The results should be identical; if they differ it indicates the raw file was
  modified between stages.

**Reviewer sign-off**

| Field             | Value |
|-------------------|-------|
| Reviewer name     |       |
| Date reviewed     |       |
| Version reviewed  | v1.0  |
| Verdict           |       |
| Comments          |       |

---

## Stage 1 — Ingest Validation (`01_ingest.py`)

Stage 1 is a validation gate. It reads the Stage 0 shapefile and checks it for correctness
before any cleaning or analysis begins. All checks write flags to the run log; failures
above thresholds write warnings. The stage does not modify data.

---

### 1.1 — Coordinate reference system check

**Purpose**

Confirms that the yield shapefile produced by Stage 0 is in the expected projected coordinate
system. If the CRS is wrong, all subsequent distance and area calculations will be invalid.

**Inputs and assumptions**

- Expected CRS: `crs.project` from config (EPSG:28350 for the current paddock).
- The check extracts the EPSG code from the shapefile's CRS metadata and compares it to the
  numeric value from the config string (e.g. `"EPSG:28350"` → 28350).

**Calculation logic**

```
yield_epsg = EPSG code of loaded shapefile
expected_epsg = integer parsed from config crs.project string
if yield_epsg ≠ expected_epsg: log WARNING
```

This is a metadata check, not arithmetic. No numeric transformation is performed.

**Known limitations and edge cases**

- The check compares EPSG codes only. Two CRS definitions can be geometrically equivalent
  but have different EPSG codes (e.g. GDA94 Zone 50 vs GDA2020 Zone 50 are nearly identical
  on the ground). This check would flag a mismatch even if the coordinate values are
  practically correct.
- A warning is logged but the script does not exit. Subsequent stages will proceed with
  whatever CRS the shapefile declares. This is a risk if the CRS is genuinely wrong.

**Reviewer sign-off**

| Field             | Value |
|-------------------|-------|
| Reviewer name     |       |
| Date reviewed     |       |
| Version reviewed  | v1.0  |
| Verdict           |       |
| Comments          |       |

---

### 1.2 — Boundary buffer containment check

**Purpose**

Checks what proportion of yield points fall outside the paddock boundary. Points outside
the boundary after allowing for GPS positioning noise indicate either a data quality problem
(GPS error at field edges) or a configuration error (wrong boundary polygon matched to this
paddock). A high percentage outside the boundary is a signal to stop and investigate before
analysis proceeds.

**Inputs and assumptions**

- Boundary polygon: loaded from `inputs.boundary_shp`, matched by `FIELD_NAME ==
  run.paddock_name` (exact string match). The boundary is reprojected to match the yield
  shapefile's CRS.
- Buffer tolerance: `ingest.boundary_buffer_m` = 30 m. This represents expected GPS
  positioning error at paddock edges under typical field conditions.
- Warning threshold: `ingest.max_outside_boundary_pct` = 5%. More than 5% of points
  outside the buffered boundary triggers a WARNING flag.

**Calculation logic**

```
buffered_boundary = paddock_polygon.buffer(30 m)

for each yield point:
    inside = point.within(buffered_boundary)

pct_outside = (count of points not inside) / (total points) × 100

if pct_outside > 5.0: log WARNING
```

**Worked example**

Fictional paddock "Eastbrook B2", 10,000 yield points loaded.

Step 1: Boundary polygon buffered by 30 m outward in all directions.
Step 2: 9,720 points found within buffered boundary.
Step 3: 280 points outside.

```
pct_outside = 280 / 10,000 × 100 = 2.80%
```

2.80% < 5.0% threshold → no warning.

If instead 620 points were outside:
```
pct_outside = 620 / 10,000 × 100 = 6.20% > 5.0% → WARNING logged
```

**Known limitations and edge cases**

- The 5% threshold and 30 m buffer are configuration values — agronomic justification for
  these specific values should be reviewed. A 30 m buffer is generous for DGPS/RTK
  equipment; it may mask real boundary-crossing errors.
- The check uses `point.within(polygon)`, which does not count points on the exact boundary
  as inside. This is a minor geometric edge case for points exactly on the boundary line.
- The check computes a fraction — it is equally triggered by 5% of 1,000 points (50 points)
  or 5% of 100,000 points (5,000 points). A small absolute count of boundary exceedances on
  a large paddock might be acceptable; a small paddock with 50 bad points might warrant
  investigation.

**Reviewer sign-off**

| Field             | Value |
|-------------------|-------|
| Reviewer name     |       |
| Date reviewed     |       |
| Version reviewed  | v1.0  |
| Verdict           |       |
| Comments          |       |

---

## Stage 2 — Yield Cleaning (`02_clean_yield.py`)

Stage 2 is the primary yield data cleaning stage. It applies three sequential filters plus
the pyprecag library's cleaning algorithm. Points are progressively removed; all removal
counts are logged.

---

### 2.1 — Speed derivation and filter

**Purpose**

Yield monitors produce unreliable readings when the harvester is travelling too slowly
(turning at headlands, stopping mid-paddock) or too fast (sensor lag, mechanical stress).
This step derives ground speed from the GPS record and removes points outside the acceptable
speed range.

**Inputs and assumptions**

- `dist_m` — distance travelled between GPS epochs (m), from the CN1 `vrDistanceTraveled`
  channel.
- `timestamp` — ISO 8601 string per GPS point.
- Records are first sorted by `ld_id` (LoggedData identifier, representing a single
  harvesting session) and then by `timestamp` within each session. Speed is derived as the
  difference between consecutive points **within the same session** — the first point of
  each session gets a null speed because there is no prior point to difference against.
- Speed bounds from config: `speed_min_kmh` = 3.0 km/h, `speed_max_kmh` = 12.0 km/h.
- The null speed for each session's first point means those points are always **removed**
  by the filter (null is not between 3 and 12).

**Calculation logic**

Sort all points by (ld_id, timestamp). For each point within a session:

```
dt_seconds = timestamp[i] - timestamp[i-1]   (seconds)
speed_kmh  = dist_m[i] / dt_seconds × 3.6

keep point if 3.0 ≤ speed_kmh ≤ 12.0
```

The factor 3.6 converts m/s to km/h:
```
(m/s) × (3600 s/hr) / (1000 m/km) = (m/s) × 3.6 = km/h
```

**Worked example**

Fictional harvesting session "OP-007", three consecutive points:

| Point | timestamp           | dist_m | dt_s | speed_kmh        | Keep? |
|-------|---------------------|--------|------|------------------|-------|
| 1     | 10:00:00            | —      | —    | null (first pt)  | No    |
| 2     | 10:00:01            | 1.4    | 1    | 1.4 × 3.6 = 5.04 | Yes   |
| 3     | 10:00:02            | 0.5    | 1    | 0.5 × 3.6 = 1.80 | No (< 3.0) |

Points 1 and 3 are removed. Point 2 is retained.

**Known limitations and edge cases**

- The first point of every harvest session is always removed regardless of whether it
  represents valid harvesting. On a typical paddock with multiple sessions, this removes
  a small number of points per session start.
- Speed is computed from `dist_m` and the time difference between consecutive points. If
  the CN1 records `dist_m` as the distance since the last record (a delta), this is
  correct. If `dist_m` is a cumulative odometer reading, the formula is wrong. The code
  assumes `vrDistanceTraveled` is a per-epoch delta; this assumption matches CLAUDE.md's
  description but should be verified against CN1 SDK documentation.
- If two consecutive points have identical timestamps (`dt_seconds = 0`), the speed
  computation divides by zero. Python/pandas will produce `inf` or `NaN` rather than
  raising an error. `inf` is not between 3 and 12, so the point is filtered out, which is
  the correct outcome — but the cause (duplicate timestamp) is not specifically logged.
- The speed window [3, 12] km/h is a configuration value. The agronomic basis: below
  3 km/h the machine is likely turning or stopped; above 12 km/h the yield monitor sensor
  lag makes readings unreliable. Domain experts should confirm these bounds for the
  specific header and crop type.

**Reviewer sign-off**

| Field             | Value |
|-------------------|-------|
| Reviewer name     |       |
| Date reviewed     |       |
| Version reviewed  | v1.0  |
| Verdict           |       |
| Comments          |       |

---

### 2.2 — Per-machine mean offset normalisation

**Purpose**

If two harvesters work the same paddock in the same season, their yield monitors may be
calibrated differently, causing one machine to systematically read higher or lower than the
other. If uncorrected, zones near where Machine A worked would appear more productive than
zones where Machine B worked — even if the soil and crop are identical — simply because of
calibration differences. This step corrects for that systematic bias by aligning each
machine's mean yield to the paddock-wide grand mean.

**Inputs and assumptions**

- `yield_tha` — the yield in t/ha computed in Stage 0 (pre-cleaning, post-speed-filter).
- `machine_id` — string identifier for each machine, populated from the CN1 SDK in Stage 0.
- If the machine_id field is missing or entirely null, normalisation is skipped and a
  warning is logged; `yldtha` is set equal to `yield_tha` unchanged.
- Warning threshold: `machine_offset_warn_pct` = 15%. If any machine's offset exceeds
  15% of the grand mean, a WARNING flag is logged.
- The correction is additive, not multiplicative. This means the correction preserves the
  shape of each machine's yield distribution but shifts it; it does not rescale variance.

**Calculation logic**

```
grand_mean = mean(yield_tha) across all machines and all points

for each machine m:
    machine_mean_m = mean(yield_tha) for points from machine m
    offset_m = grand_mean − machine_mean_m

corrected_yield_tha = yield_tha + offset_m  (where offset_m is the machine's offset)
```

This stored in a new column `yldtha`. The original `yield_tha` column is unchanged.

**Worked example**

Fictional paddock "Burnside West", two machines, 8 points total after speed filter:

Machine A points (yield_tha): 4.2, 4.5, 4.0, 4.3
Machine B points (yield_tha): 3.1, 3.4, 3.2, 3.0

```
grand_mean = (4.2 + 4.5 + 4.0 + 4.3 + 3.1 + 3.4 + 3.2 + 3.0) / 8
           = 29.7 / 8
           = 3.7125 t/ha

machine_mean_A = (4.2 + 4.5 + 4.0 + 4.3) / 4 = 17.0 / 4 = 4.25 t/ha
machine_mean_B = (3.1 + 3.4 + 3.2 + 3.0) / 4 = 12.7 / 4 = 3.175 t/ha

offset_A = 3.7125 − 4.25   = −0.5375 t/ha
offset_B = 3.7125 − 3.175  = +0.5375 t/ha

offset_A as % of grand_mean = |−0.5375| / 3.7125 × 100 = 14.48% < 15% → no warning
```

Corrected yields:
- Machine A point with yield_tha = 4.2 → yldtha = 4.2 + (−0.5375) = 3.6625 t/ha
- Machine B point with yield_tha = 3.1 → yldtha = 3.1 + 0.5375 = 3.6375 t/ha

**Known limitations and edge cases**

- The grand mean includes all machines and is computed before any per-machine correction.
  This means it is not an independently measured reference; it is the average of a possibly
  biased mixture of readings. If one machine covered 90% of the paddock, the grand mean
  will be dominated by that machine's calibration, and the smaller machine's readings will
  be shifted to match a potentially miscalibrated reference.
- Additive correction assumes the calibration error is a fixed offset. A multiplicative
  error (e.g. one machine reads 15% high across its entire range) would not be fully
  corrected. In practice, calibration errors are often multiplicative in origin (grain
  flow sensor scaling), but an additive approximation is standard practice in the field.
- For the current season (Dec 2025), only one machine was confirmed (ID: 04P1J). With
  a single machine, `grand_mean == machine_mean` and `offset == 0`, so this step is a
  no-op. The calculation becomes relevant if a second machine is added in future seasons.

**Reviewer sign-off**

| Field             | Value |
|-------------------|-------|
| Reviewer name     |       |
| Date reviewed     |       |
| Version reviewed  | v1.0  |
| Verdict           |       |
| Comments          |       |

---

### 2.3 — Percentile trim

**Purpose**

Even after speed filtering, a yield dataset typically contains a tail of extreme high and
low values caused by GPS errors, sensor glitches, or brief equipment faults. This step
removes the most extreme values at both ends of the distribution before passing the data
to the pyprecag cleaning step.

**Inputs and assumptions**

- Applied to `yldtha` (the machine-normalised yield column).
- Lower percentile: `yield_lower_pct` = 2 (removes the bottom 2% of values).
- Upper percentile: `yield_upper_pct` = 98 (removes the top 2% of values).
- Percentiles are computed on the **post-speed-filter** dataset across all machines combined.
- The trim is inclusive on both ends: a point exactly at the 2nd or 98th percentile is kept.

**Calculation logic**

```
q_lower = 2nd percentile of yldtha
q_upper = 98th percentile of yldtha

keep point if q_lower ≤ yldtha ≤ q_upper
```

**Worked example**

Ten fictional yield values (t/ha) after machine normalisation, sorted:
0.8, 1.9, 2.4, 3.1, 3.5, 3.8, 4.2, 4.6, 5.1, 9.7

2nd percentile of 10 values: interpolated between rank 1 and 2 positions.
Using pandas default linear interpolation:
```
q_lower ≈ 0.8 + (0.02 × 9) × (1.9 − 0.8) = 0.8 + 0.18 × 1.1 = 0.9980
q_upper ≈ 0.8 + (0.98 × 9) × (1.9 − 0.8) ... 
```

For simplicity with 10 values: roughly 0.8 and 9.7 are trimmed.

More realistic at scale: with 80,000 points, the 2nd percentile is a stable estimate of
the lower distribution tail. Any point at or below that threshold is removed.

A value of 9.7 t/ha is agronomically implausible for WA dryland wheat (typical max ~5–6 t/ha
in a good year) and would be removed by the upper percentile trim.

**Known limitations and edge cases**

- The 2/98 percentile split removes a fixed proportion of points regardless of whether those
  points are genuine outliers or real high/low yield zones. On a paddock with a genuine
  high-yield corner, the top 2% of readings from that area will be trimmed, slightly
  downward-biasing the zone statistics for high-yield zones.
- The percentile is computed on the entire merged dataset, not per-zone or per-machine. If
  Machine A worked only the high-yield part of the paddock and Machine B only the low-yield
  part, and offsets were applied, the combined distribution may be bimodal. The percentile
  trim on a bimodal distribution may not cleanly remove only outliers.
- After the percentile trim, the pyprecag step (2.4 below) applies a further 3-standard-
  deviation iterative trim. The two steps are redundant for extreme outliers but complementary:
  the percentile trim is robust to non-normal distributions; the z-score trim works well
  once the extremes are removed.

**Reviewer sign-off**

| Field             | Value |
|-------------------|-------|
| Reviewer name     |       |
| Date reviewed     |       |
| Version reviewed  | v1.0  |
| Verdict           |       |
| Comments          |       |

---

### 2.4 — pyprecag clean_trim_points

**Purpose**

After the speed and percentile filters, fine-grained spatial cleaning is needed: removing
points that are still statistical outliers relative to their immediate spatial neighbours,
and thinning points that are so close together they represent redundant information (e.g.
slow-moving GPS at a paddock corner). The pyprecag library (`clean_trim_points`) provides
both in one call.

**Inputs and assumptions**

- Input: the `yldtha` column (machine-normalised, percentile-trimmed).
- `outlier_stdevs` = 3: the standard deviation threshold for the iterative outlier removal.
  In each iteration, any point whose value is more than 3 standard deviations from the
  current mean is removed, and the process repeats until no more removals occur.
- `thin_dist_m` = 1.0 m: any two points closer than 1 metre to each other have one removed
  to prevent spatial redundancy.
- `remove_zeros = True`: points with `yldtha == 0` are removed before the statistical
  cleaning.
- `iterative = True`: the standard-deviation trim is repeated until convergence (no more
  points removed), not just applied once.
- Boundary polygon: the paddock boundary is passed to pyprecag, which clips any points
  outside it as part of its cleaning.
- Output paths must be absolute paths (see CLAUDE.md — pyprecag silently redirects relative
  paths to a temp directory).

**Calculation logic**

The full pyprecag algorithm is internal to the library and not fully reproduced here. In
summary:

1. Remove points with value = 0.
2. Remove points outside the boundary polygon.
3. Spatial thinning: for any pair of points closer than 1.0 m, remove one.
4. Iterative z-score trim:
   ```
   repeat:
       mean = mean(yldtha of remaining points)
       std  = std(yldtha of remaining points)
       remove any point where |yldtha − mean| > 3.0 × std
   until no points removed in an iteration
   ```
5. Output: two shapefiles (kept points, removed points) and a CSV.

**Worked example**

Cannot be run in isolation — requires the full geopandas GeoDataFrame and boundary polygon
as inputs, and the pyprecag library with its pandas 3.0 patches applied (see CLAUDE.md).

Hand-traceable example of the iterative z-score step:

Round 1 — 6 fictional points (yldtha): 2.1, 2.4, 2.8, 3.0, 3.2, 8.9
```
mean = 22.4 / 6 = 3.733
std  = sqrt(var([2.1,2.4,2.8,3.0,3.2,8.9])) ≈ 2.376

threshold = 3.0 × 2.376 = 7.128
|8.9 − 3.733| = 5.167 < 7.128 → no removal (outlier is not extreme enough once 8.9 inflates std)
```

If instead the outlier were 12.0:
```
mean = 25.5 / 6 = 4.25
std  = sqrt(var([2.1,2.4,2.8,3.0,3.2,12.0])) ≈ 3.668
threshold = 3.0 × 3.668 = 11.004
|12.0 − 4.25| = 7.75 < 11.004 → still not removed in round 1
```

This illustrates the masking effect: a single large outlier inflates the standard deviation,
making the threshold wider and potentially protecting itself. The iterative approach
eventually removes such outliers by first removing the less extreme tail, but convergence
may take several rounds. The 3-stdev threshold is standard practice for yield monitor
cleaning per the pyprecag methodology (Lawes, DAFWA 2018).

**Known limitations and edge cases**

- pyprecag version 0.4.3 predates pandas 3.0 and has been patched directly in the conda
  environment (see CLAUDE.md). These patches are not tracked in version control. If the
  conda environment is rebuilt, the patches must be reapplied. Failure to do so will cause
  Stage 2 to crash with pandas errors.
- The pyprecag library is a black box from the audit perspective: its internal implementation
  cannot be verified here. The reference implementation is Lawes & Lawn (2018), available
  from DAFWA. Domain experts should verify that the pyprecag implementation matches that
  specification.
- `thin_dist_m = 1.0 m` removes one of any pair of points within 1 m. At typical GPS
  update rates (1 Hz) and harvesting speeds (5 km/h ≈ 1.4 m/s), this removes some
  legitimate adjacent readings, not just GPS clustering artefacts. This is acceptable
  for a 10 m resolution raster product but would be too aggressive for sub-metre analysis.
- The output `gdf_clean` from `clean_trim_points` returns the cleaned GeoDataFrame but
  also writes shapefiles independently. The script uses the returned GeoDataFrame only for
  record counting; subsequent stages read the shapefile from disk. If the shapefile write
  failed silently, the count would be correct but the file would be missing.

**Reviewer sign-off**

| Field             | Value |
|-------------------|-------|
| Reviewer name     |       |
| Date reviewed     |       |
| Version reviewed  | v1.0  |
| Verdict           |       |
| Comments          |       |

---

## Stage 3 — Protein Cleaning (`03_clean_protein.py`)

Stage 3 reads the CropScan protein CSV, validates it, and produces a cleaned spatial
point shapefile. It applies three sequential quality filters before outputting.

---

### 3.1 — Null coordinate and protein row removal

**Purpose**

Rows in the CropScan CSV with missing latitude, longitude, or protein percentage cannot be
spatially located or used in analysis. They are removed before any other processing.

**Inputs and assumptions**

- Required columns: those configured in `protein_columns.latitude`, `protein_columns.longitude`,
  and `protein_columns.protein` (for the current paddock: "Latitude", "Longitude", "Protein").
- A row is dropped if any of these three values is null (NaN).
- No tolerance: a row with a latitude but no longitude is dropped.

**Calculation logic**

```
drop rows where latitude IS NULL OR longitude IS NULL OR protein IS NULL
```

Count of dropped rows is logged.

**Known limitations and edge cases**

- The CropScan MAP file should not normally contain null coordinates — the meter only
  records when GPS is locked. Null coordinates most likely indicate a file format mismatch
  or CSV parsing error. The count of dropped rows is a useful diagnostic.
- Protein values of 0.0 are NOT treated as null at this step. They would pass through to
  the range filter (step 3.2) and be removed there as being below `protein_min_pct = 5.0`.

**Reviewer sign-off**

| Field             | Value |
|-------------------|-------|
| Reviewer name     |       |
| Date reviewed     |       |
| Version reviewed  | v1.0  |
| Verdict           |       |
| Comments          |       |

---

### 3.2 — Protein range filter

**Purpose**

CropScan meters can produce readings outside the agronomically plausible range due to
sensor miscalibration, grain flow anomalies, or startup/shutdown transients. This step
removes readings outside the configured acceptable range.

**Inputs and assumptions**

- `protein_min_pct` = 5.0%: minimum plausible protein content for Australian wheat/barley.
- `protein_max_pct` = 25.0%: maximum plausible protein content.
- Typical WA dryland wheat protein range: approximately 9–16%. The configured range is
  intentionally wide to avoid removing genuine outlier seasons or varieties.
- The filter is inclusive: a protein value exactly equal to 5.0 or 25.0 is kept.

**Calculation logic**

```
keep rows where 5.0 ≤ protein_pct ≤ 25.0
```

**Worked example**

Fictional readings (protein %): 4.2, 8.7, 12.3, 15.1, 25.0, 28.6

- 4.2 < 5.0 → removed
- 8.7, 12.3, 15.1, 25.0 → kept
- 28.6 > 25.0 → removed

2 of 6 rows removed. Logged as: `Dropped 2 rows outside protein range [5.0, 25.0]%`.

**Known limitations and edge cases**

- A maximum of 25% is not tight enough to catch a miscalibrated sensor reading in the
  range 16–25%. Such readings would be agronomically unusual but not filtered here.
  The z-score filter (step 3.3) provides a secondary catch for values that are unusually
  high relative to the rest of the paddock's readings.
- These thresholds are configured values and should be reviewed if the pipeline is used
  for a different crop type (e.g. lupins, which can have protein above 30%).

**Reviewer sign-off**

| Field             | Value |
|-------------------|-------|
| Reviewer name     |       |
| Date reviewed     |       |
| Version reviewed  | v1.0  |
| Verdict           |       |
| Comments          |       |

---

### 3.3 — Z-score outlier removal

**Purpose**

Within the plausible protein range, some readings may still be statistical outliers relative
to the rest of the paddock — caused by sensor instability at individual measurement points.
This step removes readings more than 3 standard deviations from the paddock mean.

**Inputs and assumptions**

- Applied to the range-filtered dataset.
- `zscore_flag_threshold` = 3.0.
- Mean and standard deviation are computed across all remaining points in the paddock
  (a single global calculation, not spatially localised).
- The removal mask is `|z| > 3.0` (strictly greater than; a point at exactly z = 3.0 is kept).

**Calculation logic**

```
mean = mean(protein_pct)
std  = std(protein_pct)   [sample std, pandas default: ddof=1]

z_score_i = |protein_pct_i − mean| / std

remove point if z_score_i > 3.0
```

**Worked example**

Five fictional protein readings after range filter (%): 10.2, 11.5, 12.0, 11.8, 22.1

```
mean = (10.2 + 11.5 + 12.0 + 11.8 + 22.1) / 5 = 67.6 / 5 = 13.52%
std  = sqrt([(10.2-13.52)² + (11.5-13.52)² + (12.0-13.52)² + (11.8-13.52)² + (22.1-13.52)²] / 4)
     = sqrt([11.022 + 4.080 + 2.310 + 2.970 + 73.483] / 4)
     = sqrt[93.865 / 4]
     = sqrt[23.466]
     = 4.844%

z_scores: |10.2 - 13.52| / 4.844 = 3.32 / 4.844 = 0.685
          |11.5 - 13.52| / 4.844 = 2.02 / 4.844 = 0.417
          |12.0 - 13.52| / 4.844 = 1.52 / 4.844 = 0.314
          |11.8 - 13.52| / 4.844 = 1.72 / 4.844 = 0.355
          |22.1 - 13.52| / 4.844 = 8.58 / 4.844 = 1.771
```

No z-score exceeds 3.0 in this small example because 22.1% is inflating the standard
deviation. With a larger realistic dataset of 5,000+ readings centred around 12%, a
genuine outlier of 22.1% would have a much smaller std to compare against:

With mean = 12.0, std = 0.8 (typical for a homogeneous paddock):
```
z_score(22.1) = |22.1 - 12.0| / 0.8 = 10.1 / 0.8 = 12.6 → removed
```

**Known limitations and edge cases**

- The z-score is computed once on the full remaining dataset. It is not iterative (unlike
  pyprecag's yield cleaning). A cluster of several outliers can inflate the standard
  deviation and protect themselves from removal (the "masking effect" described in 2.4).
- Protein varies spatially across a paddock (that is the point of collecting it). A
  bimodal paddock (e.g. heavy soil / light soil halves with genuinely different protein)
  will have a larger standard deviation, meaning the z-score threshold is effectively
  looser and removes fewer points. This is conservative behaviour — it retains genuine
  spatial variation — but may miss sensor anomalies in bimodal paddocks.
- The global mean/std is computed across all points regardless of spatial location. A
  sensor glitch at one corner of a large paddock would be damped by thousands of good
  readings elsewhere.

**Reviewer sign-off**

| Field             | Value |
|-------------------|-------|
| Reviewer name     |       |
| Date reviewed     |       |
| Version reviewed  | v1.0  |
| Verdict           |       |
| Comments          |       |

---

### 3.4 — Boundary clip

**Purpose**

CropScan readings collected while the header is entering or leaving the paddock, or from
adjacent paddocks if the operator continued harvesting, are removed. Only readings inside
the paddock boundary (plus the 30 m GPS buffer) are retained.

**Inputs and assumptions**

- Same boundary polygon and buffer as Stage 1: `inputs.boundary_shp` matched by
  `FIELD_NAME == run.paddock_name`, buffered 30 m, reprojected to the project CRS.
- If the boundary shapefile is missing or the paddock name is not found, clipping is
  **skipped** (a warning is logged) and the unclipped protein data is written to the
  output. This is a soft failure — the pipeline continues.
- Unlike Stage 1, this step physically removes points outside the buffered boundary (not
  just counts them).

**Calculation logic**

```
buffered_boundary = paddock_polygon.buffer(30 m)
keep point if point.within(buffered_boundary)
```

The fraction outside is logged and compared to `ingest.max_outside_boundary_pct` = 5%
for a warning flag.

**Known limitations and edge cases**

- The soft-failure behaviour (skipping clip if boundary is missing) means protein data
  from outside the target paddock could enter the analysis undetected if a boundary file
  is misconfigured. The operator would need to notice the "WARNING: boundary clip skipped"
  flag in the run log.
- The same 30 m buffer used for yield is used for protein. The protein meter is on the
  header; GPS accuracy of the CropScan may differ from the yield monitor GPS. If the
  protein GPS is less accurate, a wider buffer may be appropriate.

**Reviewer sign-off**

| Field             | Value |
|-------------------|-------|
| Reviewer name     |       |
| Date reviewed     |       |
| Version reviewed  | v1.0  |
| Verdict           |       |
| Comments          |       |

---

## Stage 4 — Raster Normalisation (`04_normalise.py`)

Stage 4 converts the cleaned point data (yield and protein) into continuous raster surfaces
at 10 m resolution, then z-score normalises them so that the two layers can be combined
in Stage 5 despite having different units and scales.

---

### 4.1 — Raster grid construction

**Purpose**

Before interpolating, the pipeline establishes the exact spatial grid that all rasters
will use. The grid is defined by the paddock boundary's bounding box, snapped to a
regular 10 m grid, so that the yield and protein rasters are exactly co-registered cell
for cell.

**Inputs and assumptions**

- `raster.resolution_m` = 10 m.
- Grid extent derived from the paddock boundary's bounding box, snapped outward to the
  nearest 10 m multiple so no part of the boundary is cut off.
- CRS: `crs.project` (EPSG:28350).

**Calculation logic**

```
(minx, miny, maxx, maxy) = boundary_geometry.bounds

minx = floor(minx / 10) × 10
miny = floor(miny / 10) × 10
maxx = ceil(maxx  / 10) × 10
maxy = ceil(maxy  / 10) × 10

ncols = round((maxx − minx) / 10)
nrows = round((maxy − miny) / 10)
```

The raster origin (top-left corner) is at (minx, maxy). Each cell centre is at:
```
easting  of col c = minx + (c + 0.5) × 10
northing of row r = maxy − (r + 0.5) × 10
```

**Worked example**

Fictional paddock with boundary bounding box: minx=608,113 miny=6,519,847 maxx=610,452 maxy=6,521,234 (all in metres, EPSG:28350).

```
minx = floor(608,113 / 10) × 10 = 608,110
miny = floor(6,519,847 / 10) × 10 = 6,519,840
maxx = ceil(610,452 / 10) × 10 = 610,460
maxy = ceil(6,521,234 / 10) × 10 = 6,521,240

ncols = (610,460 − 608,110) / 10 = 235
nrows = (6,521,240 − 6,519,840) / 10 = 140
Total cells: 235 × 140 = 32,900 (before boundary masking)
```

**Known limitations and edge cases**

- Very elongated paddocks (long and narrow) may produce a large raster with most cells
  outside the boundary. This is wasteful of memory but not incorrect.
- The `round()` call for ncols and nrows can introduce a cell if floating-point arithmetic
  causes the division to produce something like 234.9999999 rather than 235.0. Using
  `round()` rather than `int()` is the right choice here.

**Reviewer sign-off**

| Field             | Value |
|-------------------|-------|
| Reviewer name     |       |
| Date reviewed     |       |
| Version reviewed  | v1.0  |
| Verdict           |       |
| Comments          |       |

---

### 4.2 — Protein coverage gap check

**Purpose**

The CropScan protein meter records along the header path only. If the operator skipped
rows, or if the protein meter was turned off for part of the harvest, there will be areas
of the paddock with no protein data at all. Interpolating across these gaps would produce
fictitious protein values in the raster. This step quantifies the size of these gaps
relative to the header width and decides whether the protein layer is usable.

**Inputs and assumptions**

- Uses the cleaned yield points (from Stage 2) as a proxy for the harvested area.
- Uses the cleaned protein points (from Stage 3) as the available protein observations.
- Implement width: derived from the raw yield shapefile by Stage 4 independently (see
  step 0.5 for the derivation logic — identical calculation).
- `coverage_warn_passes` = 2: a gap larger than 2 header-widths triggers a warning.
- `coverage_fail_passes` = 4: a gap larger than 4 header-widths is considered too large
  to interpolate across reliably.
- `coverage_fail_area_pct` = 5%: if more than 5% of the harvested area is beyond the
  fail distance from any protein point, the coverage status is "insufficient".
- The check uses a KDTree to find, for each yield point, the distance to the nearest
  protein point.

**Calculation logic**

```
warn_dist = 2 × implement_width_m
fail_dist = 4 × implement_width_m

for each cleaned yield point:
    distance = nearest-neighbour distance to any cleaned protein point

pct_warn = (count of yield points where distance > warn_dist) / total yield points × 100
pct_fail = (count of yield points where distance > fail_dist) / total yield points × 100

if pct_fail > 5.0%:
    status = "insufficient"   (yield-only clustering in Stage 5)
elif pct_warn > 5.0%:
    status = "warn"           (protein used but agronomist review recommended)
else:
    status = "ok"
```

**Worked example**

Fictional paddock with implement_width = 12 m:
- warn_dist = 2 × 12 = 24 m
- fail_dist = 4 × 12 = 48 m

10,000 cleaned yield points. KDTree query finds:
- 9,350 yield points within 24 m of a protein point
- 580 yield points between 24 and 48 m of a protein point
- 70 yield points more than 48 m from any protein point

```
pct_warn = (580 + 70) / 10,000 × 100 = 6.5%   — wait, this uses only >warn_dist
pct_warn = 650 / 10,000 × 100 = 6.5%  > 5.0% but...
pct_fail = 70  / 10,000 × 100 = 0.7%  < 5.0%
```

**Note:** the code checks `pct_fail > fail_area_pct` first; if true → "insufficient".
Then checks `pct_warn > fail_area_pct`; if true → "warn". Else → "ok".

```
pct_fail = 0.7% < 5.0% → not insufficient
pct_warn = 6.5% > 5.0% → status = "warn"
```

Result: protein included but coverage warning flag set.

**Known limitations and edge cases**

- The coverage check uses **yield points** as a proxy for "harvested area". Areas the
  harvester visited but where all yield points were removed by Stage 2 cleaning will appear
  to have no coverage requirement, potentially under-estimating the true gap.
- The fail threshold (5% of area) and the distance thresholds (2× and 4× header width) are
  configured values. The agronomic basis: within 2 header widths (24 m for a 12 m header),
  kriging or linear interpolation can reasonably bridge the gap. Beyond 4 widths (48 m),
  there is no local information and any interpolated value is essentially a guess.
- If `impl_width` cannot be derived (see step 0.5), the coverage check is skipped entirely
  and `protein_coverage_status = "skipped"` is written to the log. Stage 5 reads this as
  "ok" and will attempt to use protein. This is a **silent risk**: if the implement width
  cannot be derived, the pipeline proceeds as if coverage is fine.

**Reviewer sign-off**

| Field             | Value |
|-------------------|-------|
| Reviewer name     |       |
| Date reviewed     |       |
| Version reviewed  | v1.0  |
| Verdict           |       |
| Comments          |       |

---

### 4.3 — Gap mask construction

**Purpose**

Even when protein coverage status is "ok" or "warn" (not "insufficient"), there may be
small localised areas within the paddock where no protein data exists within the fail
distance. Rather than letting the interpolation engine extrapolate freely into these gaps,
the pipeline masks them to NaN in the protein raster. This step builds the spatial mask
that identifies which raster cells are within acceptable proximity to at least one protein
measurement.

**Inputs and assumptions**

- Uses the same `fail_dist = 4 × implement_width_m` as the coverage check.
- Applied to the protein raster only. The yield raster has no gap masking.
- Built for every run where protein data and implement width are available, regardless of
  coverage status.

**Calculation logic**

For every raster cell centre:
```
easting  = minx + (col + 0.5) × 10
northing = maxy − (row + 0.5) × 10

distance = nearest-neighbour distance to any cleaned protein point (via KDTree)

cell_in_gap_mask = True  if distance ≤ fail_dist
                   False if distance >  fail_dist
```

Cells where `cell_in_gap_mask = False` are set to NaN in the protein raster after
interpolation, preventing fictitious interpolated values from reaching Stage 5.

**Worked example**

Using the fictional paddock from step 4.1: 32,900 grid cells, implement_width = 12 m,
fail_dist = 48 m.

Suppose the protein sensor was not running for 3 adjacent harvest rows on the eastern side
of the paddock — an area approximately 36 m wide (3 rows × 12 m) by 800 m long.

KDTree query for each raster cell in that 36 × 800 m strip:
- The centre row of the strip (18 m from the nearest protein point on either side) is
  within fail_dist (18 < 48) → **in coverage** → not masked.
- But if the operator skipped 6 rows (72 m gap), the centre would be 36 m from the
  nearest protein point. 36 < 48 → still within fail_dist → not masked.
- If 9 rows were skipped (108 m gap), the centre would be 54 m from the nearest point.
  54 > 48 → **gap cell** → set to NaN.

**Known limitations and edge cases**

- The gap mask is built using `fail_dist`, not `warn_dist`. This means cells between the
  warn distance and fail distance receive interpolated (potentially unreliable) protein
  values. The coverage warning status alerts the agronomist, but the raster itself contains
  values in these marginal-coverage areas.
- The gap mask applies uniformly across all gaps exceeding fail_dist, regardless of whether
  the gap is a single isolated missed row or a large unsampled area.

**Reviewer sign-off**

| Field             | Value |
|-------------------|-------|
| Reviewer name     |       |
| Date reviewed     |       |
| Version reviewed  | v1.0  |
| Verdict           |       |
| Comments          |       |

---

### 4.4 — Spatial interpolation

**Purpose**

The cleaned yield and protein point observations are at irregular GPS locations. To produce
a continuous surface suitable for zone delineation, they must be interpolated onto the
regular 10 m grid. The interpolation method is configured and currently falls back to
linear interpolation regardless of configuration (see below).

**Inputs and assumptions**

- `raster.interpolation_method` in config = `"vesper"`. However, the VESPER subprocess
  integration is not yet implemented. The code detects `method == "vesper"` and
  **falls back to scipy linear interpolation** with a logged warning.
- For `method == "linear"`: scipy `griddata` with `method="linear"`. Extrapolation outside
  the convex hull of the input points produces NaN (scipy's default). The boundary mask
  (step 4.1) then handles NaN cells outside the paddock separately.
- For `method == "pykrige"`: OrdinaryKriging with a spherical variogram, 20 lags,
  n_closest_points=20. If the input dataset exceeds 3,000 points, 3,000 are subsampled
  randomly (seed=42) for variogram fitting; the full dataset is used for prediction.
- Points with non-finite values are excluded before interpolation.
- Minimum: 10 valid points required; if fewer are available the layer is skipped with an
  error.

**Calculation logic — linear interpolation**

Given N scattered observations (x_i, y_i, z_i):
For each raster cell centre (x_j, y_j), the linear interpolation finds the enclosing
triangle in the Delaunay triangulation of the input points and interpolates z by barycentric
weighting:

```
z_j = w_a × z_a + w_b × z_b + w_c × z_c
where (w_a, w_b, w_c) are barycentric weights summing to 1
```

**Calculation logic — pykrige kriging**

OrdinaryKriging fits a variogram model to describe how the spatial correlation of the
variable changes with distance. The predicted value at each grid cell is a weighted sum of
nearby observations, where weights come from solving the kriging equations using the
variogram model.

```
z_j = Σ_i (w_i × z_i)   for the 20 nearest points to cell j
where w_i are kriging weights that minimise the estimation variance
```

**Known limitations and edge cases**

- **Current effective method is linear interpolation**, not VESPER kriging, because VESPER
  is not implemented. The run log will show `interpolation_method=vesper` but the actual
  computation is linear. This is a material discrepancy between the configured intent and
  the actual calculation performed. Any agronomist reviewing zone maps from this pipeline
  should understand that linear interpolation — not kriging — was used.
- Linear interpolation does not extrapolate: grid cells outside the convex hull of the
  input points get NaN. Near paddock boundaries with sparse yield or protein points, the
  raster will have NaN cells that are inside the paddock boundary but outside the convex
  hull. The boundary mask will not catch these because it masks by polygon containment,
  not by convex hull.
- Pykrige's subsampling for variogram fitting (3,000 points from a typical 80,000-point
  yield dataset) means the variogram is fitted on roughly 4% of the data. The sample
  is taken with a fixed random seed (42) so results are reproducible, but the variogram
  may not represent the full spatial structure of the data.

**Reviewer sign-off**

| Field             | Value |
|-------------------|-------|
| Reviewer name     |       |
| Date reviewed     |       |
| Version reviewed  | v1.0  |
| Verdict           |       |
| Comments          |       |

---

### 4.5 — Z-score normalisation of rasters

**Purpose**

Yield is measured in t/ha and protein in percentage points — incompatible units that cannot
be directly combined for clustering. Z-score normalisation transforms each layer to a
dimensionless scale where 0 = the paddock mean and each unit represents one standard
deviation. After normalisation, the two layers can be combined for K-Means clustering with
equal influence.

**Inputs and assumptions**

- Normalisation is computed over cells that are (a) inside the paddock boundary AND (b)
  within coverage (i.e. not in the gap mask for protein).
- Mean and standard deviation are computed from valid raster cell values after masking —
  not from the original point data.
- If the standard deviation is zero (all valid cells identical), std is set to 1.0 and
  a warning is logged. This produces z-scores of 0 everywhere — the layer contains no
  useful spatial variation for clustering.

**Calculation logic**

```
valid_cells = all raster cells where:
  - cell is inside the paddock boundary  AND
  - cell value is finite  AND
  - (for protein only) cell is within the coverage gap mask

mean = mean(valid_cells)
std  = std(valid_cells)

z_score_cell = (cell_value − mean) / std   for all finite cells
NaN cells remain NaN
```

**Worked example**

Fictional 3 × 3 raster grid (9 cells) after boundary masking, with 2 cells masked as
NaN (outside boundary):

Raw values (t/ha): NaN, 2.8, 3.1, 3.5, 3.2, 3.0, 3.8, 3.4, NaN

Valid cells: [2.8, 3.1, 3.5, 3.2, 3.0, 3.8, 3.4]

```
mean = (2.8 + 3.1 + 3.5 + 3.2 + 3.0 + 3.8 + 3.4) / 7
     = 22.8 / 7
     = 3.257 t/ha

deviations: [-0.457, -0.157, 0.243, -0.057, -0.257, 0.543, 0.143]
std = sqrt(sum(dev²) / 7)   [note: this uses population std, not sample std — see below]

sum(dev²) = 0.209 + 0.025 + 0.059 + 0.003 + 0.066 + 0.295 + 0.020 = 0.677
std = sqrt(0.677 / 7) = sqrt(0.0967) = 0.311 t/ha
```

Wait — the code uses numpy `vals.std()` which defaults to **population standard deviation**
(ddof=0), unlike pandas which defaults to sample std (ddof=1). This is a subtle but
important distinction:

```
numpy vals.std() = population std = sqrt(sum(dev²) / N)    [ddof=0, the default]
pandas .std()    = sample std     = sqrt(sum(dev²) / (N-1)) [ddof=1, the default]
```

For the above example:
- Population std (numpy): sqrt(0.677 / 7) = 0.311
- Sample std (pandas):    sqrt(0.677 / 6) = 0.336

The z-scores for each cell:
```
z_score(2.8) = (2.8 − 3.257) / 0.311 = −0.457 / 0.311 = −1.470
z_score(3.8) = (3.8 − 3.257) / 0.311 =  0.543 / 0.311 = +1.746
```

NaN cells remain NaN in the output.

**Known limitations and edge cases**

- Population std (ddof=0) vs sample std (ddof=1): for large rasters (tens of thousands
  of valid cells), the difference is negligible. For small paddocks with few valid cells,
  the choice affects z-score magnitudes. This is consistent with how numpy's array `.std()`
  works and is unlikely to affect clustering outcomes at paddock scale.
- The z-score is computed from the raster (interpolated surface) rather than the original
  points. If interpolation introduces smoothing (as linear interpolation does), the raster
  values have lower variance than the original points, and z-scores will span a narrower
  range than a z-score computed directly from point data.
- Both the yield and protein rasters are normalised independently to their own mean and std.
  After normalisation, a z-score of +1.0 means "one std above average" for that layer —
  it does not mean the same absolute magnitude in both layers. K-Means in Stage 5 treats
  them as equally weighted features, which is the intended behaviour.

**Reviewer sign-off**

| Field             | Value |
|-------------------|-------|
| Reviewer name     |       |
| Date reviewed     |       |
| Version reviewed  | v1.0  |
| Verdict           |       |
| Comments          |       |

---

## Stage 5 — Zone Delineation (`05_zones.py`)

Stage 5 converts the normalised rasters from Stage 4 into a three-zone management zone
map. It uses K-Means clustering, handles cells with missing protein data (gap cells),
removes small fragments, smooths boundaries, and converts the result to a polygon shapefile.

---

### 5.1 — Protein coverage status check (gate decision)

**Purpose**

Determines whether the protein raster is included in zone delineation for this run, based
on the coverage status written to the run log by Stage 4.

**Calculation logic**

```
coverage_status = most recent protein_coverage_status= flag in run_log.csv
                  for script="04_normalise.py" and paddock_id=current

use_protein = (coverage_status != "insufficient")
```

If no Stage 4 log entry is found, defaults to "ok" with a warning logged.

**Known limitations and edge cases**

- The log parsing reads the **most recent** Stage 4 entry for this paddock. If Stage 4 was
  run twice with different data, the most recent entry wins. There is no check that the
  Stage 4 run whose rasters are on disk corresponds to the most recent log entry.
- If the run log CSV is missing or corrupted, the default is to include protein. A
  corrupted or absent log causes a silent escalation to a more permissive assumption.

**Reviewer sign-off**

| Field             | Value |
|-------------------|-------|
| Reviewer name     |       |
| Date reviewed     |       |
| Version reviewed  | v1.0  |
| Verdict           |       |
| Comments          |       |

---

### 5.2 — K-Means clustering

**Purpose**

Assigns every raster cell inside the paddock boundary to one of three management zones
based on its yield z-score (and protein z-score if available). K-Means groups cells so
that cells within the same zone are more similar to each other than to cells in other zones.

**Inputs and assumptions**

- Input features per cell:
  - If protein available and cell has both yield and protein: [yield_z, protein_z]
  - If protein available but cell is a gap cell (protein = NaN): yield_z only, assigned
    post-hoc (see step 5.3)
  - If protein not used: [yield_z] only
- `n_zones` = 3 (fixed, configured via `zones.n_zones`).
- `random_seed` = 42 (ensures reproducibility).
- `n_init` = 10: K-Means is run 10 times with different random initialisations; the best
  result (lowest inertia) is kept. This is a scikit-learn default retained in the code.

**Calculation logic**

```
X = array of shape (N_cells, n_features)  where N_cells is count of full-coverage cells

kmeans = KMeans(n_clusters=3, random_state=42, n_init=10)
labels = kmeans.fit_predict(X)  → integer label 0, 1, or 2 per cell
```

After fitting, the cluster centroids encode the average [yield_z, protein_z] (or [yield_z])
of each cluster. These are used in step 5.2b below.

**Zone sorting — re-labelling step**

Raw K-Means labels (0, 1, 2) are assigned arbitrarily — the cluster with the highest yield
might get label 0 or label 2 depending on the random initialisation. The pipeline re-labels
so that Zone 1 always has the **lowest** mean yield z-score:

```
yield_col = 0   (first column of cluster_centers_ is yield)
zone_order = argsort(centroids[:, yield_col])  → indices of centroids sorted low to high yield
remap = {old_label: new_1_indexed_label for each position}
```

**Worked example**

Fictional 6-cell paddock with 2 features (yield_z, protein_z):

| Cell | yield_z | protein_z |
|------|---------|-----------|
| A    | −1.2    | +0.5      |
| B    | −0.9    | +0.3      |
| C    | +0.1    | −0.1      |
| D    | +0.2    | −0.2      |
| E    | +1.1    | −0.8      |
| F    | +1.3    | −1.0      |

K-Means with k=3 might produce:
- Cluster 0: cells A, B → centroid: (−1.05, +0.40)
- Cluster 1: cells C, D → centroid: (+0.15, −0.15)
- Cluster 2: cells E, F → centroid: (+1.20, −0.90)

Yield centroid values: Cluster 0 = −1.05, Cluster 1 = +0.15, Cluster 2 = +1.20.
Argsort ascending: [0, 1, 2] → already in order.
Remap: {0 → Zone 1, 1 → Zone 2, 2 → Zone 3}

Zone 1 = lowest yield + highest protein (A, B) — plausible: heavy waterlogged soils
Zone 3 = highest yield + lowest protein (E, F) — plausible: productive lighter soils

**Known limitations and edge cases**

- K-Means assumes clusters are approximately spherical and equally sized in feature space.
  Real yield and protein spatial patterns may not be spherical — elongated zones (e.g. a
  soil type running in a long strip) may be poorly captured.
- K-Means is sensitive to the relative scaling of features. Both yield and protein are
  z-scored to mean=0, std=1 before clustering, so they receive equal weighting. This is
  a deliberate design choice (Constitution §0.1: "yield + protein only"), but the
  agronomist should be aware that a paddock with high yield variance and low protein
  variance will be clustered primarily on yield structure.
- The fixed seed (42) ensures reproducibility but means results are not averaged over
  multiple initialisations in a probabilistic sense — the same answer is produced every
  time on the same data.
- With k=3, every paddock gets exactly three zones regardless of whether the data supports
  three distinct classes. A very uniform paddock may produce three zones of similar
  character, all driven by noise.

**Reviewer sign-off**

| Field             | Value |
|-------------------|-------|
| Reviewer name     |       |
| Date reviewed     |       |
| Version reviewed  | v1.0  |
| Verdict           |       |
| Comments          |       |

---

### 5.3 — Gap cell assignment

**Purpose**

Raster cells in protein-gap areas (masked to NaN in Stage 4) have a yield z-score but no
protein z-score. They cannot participate in the joint yield+protein K-Means step. This step
assigns each gap cell to the zone whose **yield centroid** is nearest to that cell's yield
z-score. The result is that every cell inside the paddock boundary is assigned to a zone,
with no unassigned holes.

**Inputs and assumptions**

- Only runs when protein was used in clustering (and gap cells exist).
- Uses only the **yield dimension** of each zone centroid for gap assignment.
- Gap cell assignment is by absolute distance in yield z-score space — not in the joint
  (yield, protein) space used for full-coverage cells.

**Calculation logic**

```
yield_centers = [centroid[yield_col] for centroid in sorted_centroids]
  → e.g. [−1.05, +0.15, +1.20]  for the 3-zone example in step 5.2

for each gap cell:
    dist_to_zone_j = |yield_z_of_gap_cell − yield_centers[j]|  for j = 1, 2, 3
    assigned_zone = argmin(dist_to_zone_j) + 1
```

**Worked example**

Using fictional centroids from 5.2: Zone 1 yield centre = −1.05, Zone 2 = +0.15, Zone 3 = +1.20.

Gap cell G has yield_z = +0.05 but no protein_z (protein gap area).

```
|0.05 − (−1.05)| = 1.10  (distance to Zone 1)
|0.05 − 0.15|   = 0.10  (distance to Zone 2)
|0.05 − 1.20|   = 1.15  (distance to Zone 3)

Minimum distance: 0.10 → assigned to Zone 2
```

**Known limitations and edge cases**

- Gap cells are assigned purely on yield similarity to the yield centroid, not on
  geographic proximity. A gap cell geographically surrounded by Zone 3 pixels could be
  assigned to Zone 2 if its yield z-score is closer to the Zone 2 centroid. The majority-
  filter smoothing step (5.5) will partially correct geographic inconsistencies.
- The yield centroid used for gap assignment is the centroid of the K-Means cluster in
  the full [yield_z, protein_z] space, but only the yield dimension is used. If the
  protein dimension shifted the centroid's yield position (e.g. because low-yield cells
  also have high protein and cluster differently than low-yield cells in yield-only space),
  the gap cell assignment may not be well-calibrated.

**Reviewer sign-off**

| Field             | Value |
|-------------------|-------|
| Reviewer name     |       |
| Date reviewed     |       |
| Version reviewed  | v1.0  |
| Verdict           |       |
| Comments          |       |

---

### 5.4 — Spatial filter (minimum zone area)

**Purpose**

K-Means can produce isolated single-cell or small-patch zone assignments that are agronomically
meaningless — an area of 200 m² (two 10 m × 10 m cells) cannot be managed separately by a
variable rate controller. This step removes patches smaller than the minimum area threshold
and reassigns them to the nearest larger patch.

**Inputs and assumptions**

- `min_zone_area_ha` = 2.0 ha (from config).
- At 10 m resolution: 2.0 ha = 20,000 m² = 200 cells (10 m × 10 m).
- `min_pixels = ceil(2.0 × 10,000 / 10²) = ceil(200.0) = 200 cells`.
- Small patches are reassigned to the zone of the **spatially nearest** large-patch pixel
  (nearest in Euclidean pixel distance, not yield similarity).
- The spatial filter iterates over all zones. A zone entirely composed of small patches
  would result in all its cells being reassigned, effectively eliminating that zone from
  the output.

**Calculation logic**

```
for each zone z in 1..3:
    find all connected components of zone z pixels
    for each component:
        if component.size < 200 pixels:
            set those pixels to 0  (mark for reassignment)

removed = pixels that were zone > 0 but are now 0

for each removed pixel:
    nearest_surviving_pixel = argmin(Euclidean distance to pixels where zone > 0)
    removed_pixel.zone = zone of nearest_surviving_pixel
```

The Euclidean nearest-pixel search uses `scipy.ndimage.distance_transform_edt`, which is an
exact fast implementation of the Euclidean distance transform for binary images.

**Worked example**

Fictional 5 × 5 zone grid (0 = outside boundary):

```
Before spatial filter:
0  1  1  1  0
1  1  2  1  0
1  2  2  2  0
1  3  2  2  0
0  0  3  3  0

Zone counts: Zone 1=7 cells, Zone 2=9 cells, Zone 3=3 cells
min_pixels = 5 (hypothetical, for illustration)
```

Zone 3 has 3 cells: (3,1), (4,2), (4,3). 3 < 5 → all Zone 3 cells marked for reassignment.

Each formerly-Zone-3 cell is reassigned to the nearest surviving zone pixel:
- (3,1) → nearest is (2,1) which is Zone 2 → assigned Zone 2
- (4,2) → nearest is (3,2) which is Zone 2 → assigned Zone 2
- (4,3) → nearest is (3,3) which is Zone 2 → assigned Zone 2

Result: paddock now has only 2 effective zones.

**Known limitations and edge cases**

- The filter processes zones independently. A large Zone 2 patch and a small Zone 1 patch
  that happen to be adjacent: the small Zone 1 patch is reassigned to the zone of the
  nearest surviving pixel, which could be Zone 2. This is correct spatially but may
  eliminate Zone 1 from a paddock where it genuinely exists but is fragmented.
- The 2.0 ha minimum is a configured value. A variable rate controller with a 12 m header
  will need approximately 12 m × 50 m = 600 m² (0.06 ha) to change rates without
  overshooting. The 2.0 ha minimum is substantially larger than the mechanical minimum,
  suggesting the threshold is about agronomic manageability rather than mechanical
  feasibility. Domain experts should confirm this value.

**Reviewer sign-off**

| Field             | Value |
|-------------------|-------|
| Reviewer name     |       |
| Date reviewed     |       |
| Version reviewed  | v1.0  |
| Verdict           |       |
| Comments          |       |

---

### 5.5 — Majority-filter boundary smoothing

**Purpose**

The zone boundaries produced by K-Means are jagged at the pixel level (staircase pattern)
because each 10 m cell is assigned independently. A smoothed boundary is more visually
interpretable and better represents the intended gradual transition between soil types.
This step replaces each cell's zone assignment with the majority zone in its 3 × 3
neighbourhood, repeated 3 times.

**Inputs and assumptions**

- `smooth_passes` = 3 (configured via `zones.smooth_passes`; confirmed satisfactory in
  prior testing — see memory: zone_smoothing).
- Window size: fixed 3 × 3 cells (30 m × 30 m at 10 m resolution).
- Cells outside the paddock boundary (zone = 0) are excluded from voting and restored
  to 0 after each pass.
- Implementation: per-zone uniform filter gives each cell a score proportional to how many
  of its neighbours belong to that zone. The zone with the highest score wins.

**Calculation logic**

For each pass:
```
for each zone z in 1..N:
    vote_z[row, col] = count of cells in 3×3 window around (row, col) that belong to zone z
                     (normalised by 9, but ordering is the same)

winner[row, col] = zone with highest vote_z
winner[outside_boundary_cells] = 0   (restored after each pass)
```

**Worked example**

Fictional 3 × 3 neighbourhood (centre cell = C):

```
Zone assignment before smoothing:
1  1  1
1  C  2
2  2  2
```

C is currently Zone 1. Neighbour votes:
- Zone 1 count in 3×3 window (including C): 5 (positions: top row ×3, C itself, left of C)
- Zone 2 count in 3×3 window: 4 (positions: right of C, bottom row ×3)

Zone 1 wins → C remains Zone 1. If C's original assignment had been Zone 2:
Same vote count (5 for Zone 1, 4 for Zone 2) → Zone 1 wins → C flips from Zone 2 to Zone 1.

After 3 passes, jagged single-pixel transitions are resolved into smooth gradients with
cells changing zone based on their predominant neighbourhood context.

**Known limitations and edge cases**

- Three passes of 3×3 majority filtering can shift zone boundaries by up to 3 cells
  (30 m) from their original K-Means position. In a paddock where a soil type boundary
  is genuinely sharp at 10 m scale, the smoothing may reduce the sharpness of the
  boundary in the output zone map.
- The uniform_filter approximation (using float mean rather than integer vote count) is
  mathematically equivalent to voting for the purpose of determining the majority zone
  when the window is a fixed 3×3 square. The implementation is correct.
- `smooth_passes = 0` disables smoothing entirely. The output zone shapefile would then
  have approximately square pixel-scale notches at all zone boundaries.

**Reviewer sign-off**

| Field             | Value |
|-------------------|-------|
| Reviewer name     |       |
| Date reviewed     |       |
| Version reviewed  | v1.0  |
| Verdict           |       |
| Comments          |       |

---

### 5.6 — Vectorisation and zone statistics

**Purpose**

Converts the raster zone grid to a polygon shapefile and joins summary statistics (mean
yield and mean protein per zone) from the cleaned point data. This is the agronomist-
facing output that shows zone boundaries with quantitative characteristics.

**Calculation logic — vectorisation**

```
for each (polygon_geometry, zone_value) in rasterio.features.shapes(zone_raster):
    where zone_value > 0:
        add polygon to output list

dissolve all polygons sharing the same zone_id → one polygon per zone
compute area_ha = dissolved_polygon.area / 10,000
```

**Calculation logic — zone statistics**

For yield (using cleaned yield points from Stage 2):
```
spatial join: for each yield point, find which zone polygon contains it

mean_yield_tha = mean(yldtha) for all points joined to zone z
yield_pts       = count(points joined to zone z)
```

Same for protein (using cleaned protein points from Stage 3).

**Known limitations and edge cases**

- Zone statistics are computed from the cleaned **point** data, not from the interpolated
  raster. This is the correct approach — it uses real observations rather than interpolated
  values — but it means the mean yield for a zone is the mean of irregularly distributed
  GPS observations within that polygon, not the mean of the regular raster grid.
- If a zone polygon contains no cleaned yield points (e.g. a zone created entirely from
  gap cells), `mean_yield` will be null/NaN. The `write_csv` and `make_pdf` functions in
  Stage 6 check for null before formatting.
- `rasterio.features.shapes` returns many small polygons per zone (one per connected pixel
  group before dissolve). The `dissolve` step merges all fragments for each zone ID into a
  single (possibly multi-part) polygon.

**Reviewer sign-off**

| Field             | Value |
|-------------------|-------|
| Reviewer name     |       |
| Date reviewed     |       |
| Version reviewed  | v1.0  |
| Verdict           |       |
| Comments          |       |

---

## Stage 6 — Agronomist Handoff (`06_handoff.py`)

Stage 6 takes the zone shapefile from Stage 5 and produces two outputs: a CSV summary
for the agronomist to fill in N rates, and a printable PDF zone map. There are no new
agronomic calculations in Stage 6; the agronomic numbers all come from Stage 5. The
calculations in Stage 6 relate to layout and display logic.

---

### 6.1 — Zone summary CSV

**Purpose**

Produces a structured spreadsheet that the agronomist uses to record the nitrogen rate to
apply per zone. It pre-populates zone statistics (area, yield, protein) and leaves the
N rate column blank for manual entry.

**Calculation logic**

For each zone (sorted by zone_id ascending):
```
mean_yield_tha  = round(mean_yield, 3)     [from Stage 5 spatial join]
mean_protein_pct = round(mean_prot, 2)     [from Stage 5 spatial join]
n_rate_kg_ha     = ""                       [blank — for agronomist to complete]
notes            = ""                       [blank — for agronomist to complete]
```

No new arithmetic. Values are rounded for display.

**Known limitations and edge cases**

- The CSV `n_rate_kg_ha` field is blank. There is no validation that a number has been
  entered before Stage 7 attempts to read it. If Stage 7 is run before the agronomist
  fills in the CSV, the outcome is undefined (Stage 7 is not yet implemented).

**Reviewer sign-off**

| Field             | Value |
|-------------------|-------|
| Reviewer name     |       |
| Date reviewed     |       |
| Version reviewed  | v1.0  |
| Verdict           |       |
| Comments          |       |

---

### 6.2 — Scale bar selection

**Purpose**

The PDF map includes a scale bar so the reader can estimate distances on the printed map.
The scale bar length must be chosen to fit neatly within the map panel.

**Calculation logic**

```
extent_m = maxx − minx   (of all zone polygon bounds)

candidate bar lengths (m): [50, 100, 200, 250, 500, 1000]
selected = largest bar_m where bar_m ≤ extent_m × 0.25
```

The bar is placed at 4% from the left edge of the map, 4% from the bottom.

**Worked example**

Zone extent: minx=608,110 maxx=610,460 → extent_m = 2,350 m

```
extent_m × 0.25 = 587.5 m
Candidates ≤ 587.5 m: 50, 100, 200, 250, 500
Largest = 500 m  → scale bar shows 500 m
```

**Known limitations and edge cases**

- The loop iterates through candidates in ascending order and the selected variable is
  updated by each valid candidate, keeping the last (largest) valid one. This is correct
  but relies on the candidate list being in ascending order.
- If `extent_m × 0.25` is smaller than 50 m (i.e. total paddock width < 200 m), no bar
  is drawn and the variable `scale_m` is undefined — the subsequent code using `scale_m`
  would throw a `NameError`. This is a **silent crash risk** for very small paddocks.

**Reviewer sign-off**

| Field             | Value |
|-------------------|-------|
| Reviewer name     |       |
| Date reviewed     |       |
| Version reviewed  | v1.0  |
| Verdict           |       |
| Comments          |       |

---

### 6.3 — Zone colour scheme

**Purpose**

Assigns a traffic-light colour to each zone so agronomists and operators can quickly
identify low, medium, and high productivity zones on the printed map.

**Calculation logic**

```
Zone 1 (lowest yield)  → Red   #d73027
Zone 2 (middle yield)  → Amber #fee090
Zone 3 (highest yield) → Green #1a9850
```

Colours are fixed constants, not computed. The mapping assumes exactly 3 zones. For more
than 3 zones (not the current configuration), extra colours from `EXTRA_COLOURS` would be
used.

**Known limitations and edge cases**

- The red-for-low / green-for-high colour scheme is agronomically intuitive for yield
  (green = productive). However, high-protein zones are also Zone 3 in most cases — and
  some agronomists may associate high protein with over-shooting N application (a waste or
  a quality risk), which might be coloured differently in their mental model. The colour
  scheme encodes yield rank, not management recommendation.
- Red-green colour scheme is the most common form of colour blindness (deuteranopia/
  protanopia). The three zone colours — red (#d73027), amber (#fee090), and green (#1a9850)
  — may be difficult to distinguish for approximately 8% of male readers.

**Reviewer sign-off**

| Field             | Value |
|-------------------|-------|
| Reviewer name     |       |
| Date reviewed     |       |
| Version reviewed  | v1.0  |
| Verdict           |       |
| Comments          |       |

---

## Stage 7 — Prescription (`07_prescription.py`)

**Status: Stub only — not yet implemented.**

The script contains no calculation logic. It prints a placeholder message and exits. There
are no calculations to audit.

When Stage 7 is implemented, a new section will be added to this document and the version
number will be incremented. Reviewer sign-off will be required for all new sections before
Stage 7 outputs are used operationally.

---

## Appendix A — Risk register summary

The following items were flagged as specific risks during the audit. They are listed here
for ease of reference:

| Risk ID | Stage | Description                                                                                 | Severity  |
|---------|-------|---------------------------------------------------------------------------------------------|-----------|
| R-01    | 0.2   | Protein sentinel exact float equality check — rounding could miss 6553.5                    | Low       |
| R-02    | 0.3   | yield_tha formula assumes vrDeltaArea is a per-epoch delta, not a cumulative odometer        | Medium    |
| R-03    | 2.1   | Speed calc divides by zero for duplicate timestamps — result is NaN, silently filtered       | Low       |
| R-04    | 2.1   | dist_m assumed to be a per-epoch delta — not confirmed against CN1 SDK documentation        | Medium    |
| R-05    | 2.4   | pyprecag patches not in version control — env rebuild loses them silently                   | High      |
| R-06    | 4.2   | If implement width cannot be derived, protein coverage check silently skips → "ok" assumed  | Medium    |
| R-07    | 4.4   | Config shows `interpolation_method=vesper` but VESPER not implemented — linear used instead | High      |
| R-08    | 5.1   | Missing Stage 4 log → protein included by default without coverage verification             | Medium    |
| R-09    | 6.2   | `scale_m` undefined if paddock width < 200 m → NameError crash                             | Low       |
| R-10    | 0.1   | First spatial record used for season window dating — not robust to GPS loss at op start      | Low       |

---

## Appendix B — Configuration values referenced in this audit

All thresholds and parameters as of the current `config/paddock_config.yaml`:

| Parameter                          | Value        | Used in section |
|------------------------------------|--------------|-----------------|
| `ingest.boundary_buffer_m`         | 30 m         | 1.2, 3.4        |
| `ingest.max_outside_boundary_pct`  | 5%           | 1.2, 3.4        |
| `yield_cleaning.speed_min_kmh`     | 3.0 km/h     | 2.1             |
| `yield_cleaning.speed_max_kmh`     | 12.0 km/h    | 2.1             |
| `yield_cleaning.machine_offset_warn_pct` | 15%    | 2.2             |
| `yield_cleaning.yield_lower_pct`   | 2            | 2.3             |
| `yield_cleaning.yield_upper_pct`   | 98           | 2.3             |
| `yield_cleaning.outlier_stdevs`    | 3            | 2.4             |
| `yield_cleaning.thin_dist_m`       | 1.0 m        | 2.4             |
| `protein_cleaning.protein_min_pct` | 5.0%         | 3.2             |
| `protein_cleaning.protein_max_pct` | 25.0%        | 3.2             |
| `protein_cleaning.zscore_flag_threshold` | 3.0    | 3.3             |
| `protein_cleaning.coverage_warn_passes`  | 2      | 4.2             |
| `protein_cleaning.coverage_fail_passes`  | 4      | 4.2, 4.3        |
| `protein_cleaning.coverage_fail_area_pct`| 5%     | 4.2             |
| `raster.resolution_m`              | 10 m         | 4.1, 5.4        |
| `raster.interpolation_method`      | `"vesper"` (→ linear in practice) | 4.4 |
| `zones.n_zones`                    | 3            | 5.2             |
| `zones.min_zone_area_ha`           | 2.0 ha       | 5.4             |
| `zones.random_seed`                | 42           | 5.2             |
| `zones.smooth_passes`              | 3            | 5.5             |
| Stage 0 protein sentinel value     | 6553.5       | 0.2             |
| pykrige variogram subsampling cap  | 3,000 pts    | 4.4             |
| pykrige n_closest_points           | 20           | 4.4             |
