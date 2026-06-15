# CLAUDE.md

Operational notes for working in this repo. For project scope, data formats,
folder structure, and design decisions, see `constitution/CONSTITUTION.md` —
that is the source of truth; do not duplicate it here.

## Environment

- Conda env: `farmworkflow` (Python 3.11, Miniforge)
- Run scripts via: `conda run -n farmworkflow python scripts/<script>.py`
- All pipeline parameters live in `config/paddock_config.yaml` — never
  hardcode paddock-specific values in scripts.

## CN1 / ADAPT SDK (pythonnet)

Scripts that load the CN1 SDK (`sdk/cn1/*.dll` via `clr.AddReference`) need:

- `pythonnet` installed in the `farmworkflow` env (`pip install pythonnet`).
- `sdk/cn1/Resources/RepresentationSystem.xml` and `UnitSystem.xml` copied
  into `<conda env dir>/Resources/` (e.g.
  `C:\Users\kerri\miniforge3\envs\farmworkflow\Resources\`). The ADAPT
  Representation library loads these from the .NET host's base directory
  (the `python.exe` directory), not from the SDK folder. If this is missing
  you'll see `DirectoryNotFoundException` for `RepresentationSystem.xml`
  when any `Representation`/`UnitOfMeasure` property is first accessed.
  This is a one-time env setup step — redo it if the env is rebuilt.

### Decoding spatial record values

`sr.GetMeterValue(wd)` returns a `NumericRepresentationValue` /
`EnumeratedRepresentationValue`. Its `.Value` is itself a wrapper, not the
raw number:

- `NumericValue`: reading = `.Value.Value` (double), unit = `.Value.UnitOfMeasure.Code`
- `EnumerationMember`: label = `.Value.Value` (string), code = `.Value.Code` (int)

See `scripts/explore_spatial.py` for a worked example.

### SDK bootstrap helper

Use `scripts/cn1_sdk.py` (`get_plugin`, `import_adm`, `decode_meter_value`,
`resolve_machine_id`) rather than re-deriving the pythonnet bootstrap.

### Season window (multi-year harvest)

WA grain harvest runs through the southern-hemisphere summer (Oct-Jan), so a
single season's harvest can span into the following calendar year — and a
CN1 export for a field can contain `LoggedData` entries from multiple
seasons. Stage 0 only keeps Harvesting operations whose first spatial record
falls within `[Oct 1 of run.season, Jan 31 of run.season+1]`, derived purely
from `run.season` (no separate config field). To process a different season
for the same paddock, change `run.season` and re-run Stage 0 — it will
select the matching `LoggedData` entries from the same CN1 export.

### Process exit hang

Once `clr.AddReference` loads the CLR, normal Python interpreter shutdown can
hang for hours waiting on .NET runtime threads — the script's actual work
completes fine (check `logs/*.log` and `logs/run_log.csv` for a final entry),
but the process itself never returns. Any script that imports `cn1_sdk` must
call `os._exit(0)` after `main()` completes (all files/logs must be
flushed/closed by that point — `os._exit` skips normal cleanup). See
`scripts/00_cn1_to_points.py` for the pattern.

## pyprecag (yield/protein cleaning)

`pyprecag` 0.4.3 predates pandas 3.0 and the installed env has pandas 3.0.3.
Several spots in `pyprecag.processing.clean_trim_points` /
`pyprecag.vector_ops.thin_point_by_distance` / `pyprecag.describe` break
under pandas 3.0's stricter `.loc` setitem typing, removed Series positional
`[]` indexing, and new default string dtype. These have been patched directly
in `<conda env dir>/Lib/site-packages/pyprecag/` (one-time env setup step —
redo if the env/package is reinstalled):

- `processing.py` ~462 and `vector_ops.py` ~117: drop the redundant `axis=1`
  from `.drop(columns=..., axis=1, ...)` (pandas 3.0 rejects `axis` +
  `columns` together).
- `processing.py` ~468: `gdf_points['filter'] = np.nan` → initialise as
  `pd.Series(np.nan, index=gdf_points.index, dtype=object)` so later string
  assignments (`'null/missing data'`, `'<= zero'`, etc.) don't raise
  `LossySetitemError`.
- `processing.py` ~568: `x[0]`/`x[1]` on a `.apply(axis=1)` row Series →
  `x.iloc[0]`/`x.iloc[1]` (pandas 3.0 removed positional fallback for `[]`).
- `describe.py` `get_column_properties`: also treat pandas' new default
  string dtype as `object` (`pd.api.types.is_string_dtype(_type)`), not just
  `_type.name == 'object'`, to avoid `np.zeros(1, _type)` TypeError.

**`save_geopandas_tofile` path quirk**: if `out_keep_shapefile` /
`out_removed_shapefile` passed to `clean_trim_points` is a *relative* path,
pyprecag silently redirects it to a temp dir
(`%LOCALAPPDATA%\Temp\PrecisionAg\...`) instead of erroring. Always pass
absolute paths (`Path(...).resolve()`) for these arguments — see
`scripts/02_clean_yield.py`.

## Script conventions

- Stage scripts are numbered (`00_cn1_to_points.py` ... `07_prescription.py`)
  and run in sequence; see README.md for the stage table.
- Shared helpers (`load_config`, `setup_logger`, `log_run_entry`,
  `ensure_output_dirs`) live in `scripts/utils.py` — use these rather than
  reimplementing logging/config loading per script.
- Every run should append a structured entry via `log_run_entry` to
  `logs/run_log.csv`.
- Raw inputs in `data/raw/` are never modified by any script.
