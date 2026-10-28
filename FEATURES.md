# PropertyDataTools — Feature List

A running catalogue of what this project does. **Consult this file before making
changes**, to understand which behaviours must be preserved. When adding a feature, add
a line here and confirm no earlier feature has been changed by accident.

This file was created alongside the validation module (2026-10-26). The entries for
features that predate it were written by reading the code, and are marked accordingly.

---

## 1. Geodatabase merge — `unbox/compile_gdbs.py`

The core pipeline. Merges per-county LightBox SmartFabric deliveries into one statewide
Esri File Geodatabase. Driven by `main.py`; takes 8–24 hours for the full state.

| # | Feature | Where | Notes |
|---|---|---|---|
| 1.1 | Extract county delivery zips into a processing folder | `GDBMerge.unzip_all` | *(pre-existing)* |
| 1.2 | Promote the largest county geodatabase to become the merge output | `GDBMerge.move_largest_to_output` | *(pre-existing)* Los Angeles (`06037`) in the 2026-10 delivery; this is why LA has no source geodatabase. |
| 1.3 | Repair geometry on the themes listed in `REPAIR_GEOMETRY_TABLES` | `GDBMerge.repair_geometries` | *(pre-existing)* |
| 1.4 | Convert the five many-to-many relation tables into attributed relationship classes | `GDBMerge._handle_manytomany_relationships` | *(pre-existing)* Driven by `MANYTOMANY_RELATIONSHIPS`. Changes the OID field from `OBJECTID` to `RID`. |
| 1.5 | Drop indexes before appending, for speed | `GDBMerge.drop_indexes` | *(pre-existing)* |
| 1.6 | Append every county into the output | `GDBMerge.append_all` | *(pre-existing)* |
| 1.7 | Recreate attribute indexes as `idx_<field>` | `GDBMerge.create_indexes` | *(pre-existing)* Driven by `KEY_INDEXES` (20) + `ATTRIBUTE_INDEXES` (29). |
| 1.8 | Recreate spatial indexes (`FDO_SHAPE`) | `GDBMerge.recreate_spatial_indexes` | *(pre-existing)* Driven by `SPATIAL_INDEXES` — `Parcels`, `Buildings`, `Addresses`, `Assessments`. |
| 1.9 | Create the three `rc_`-prefixed one-to-many relationship classes | `GDBMerge.create_relationship_classes` | *(pre-existing)* Driven by `ONETOMANY_RELATIONSHIPS`. |
| 1.10 | Declarative build configuration as class attributes | `GDBMerge` class body | *(pre-existing)* **Load-bearing for validation** — the validator reads these as its expectations, so they must stay class-level and readable without instantiation. |

## 2. Geocoding locator — `unbox/build_locator.py`, `build_locator_cli.py`

| # | Feature | Where | Notes |
|---|---|---|---|
| 2.1 | Build an ArcGIS address locator from the merged geodatabase | `build_locator.make_locator` | *(pre-existing)* |
| 2.2 | Join parcels to addresses for locator input | `unbox/build_locator.py` | *(pre-existing)* |
| 2.3 | Incorporate city, county and TIGER reference layers | `unbox/build_locator.py` | *(pre-existing)* |
| 2.4 | Command line entry point | `build_locator_cli.py` | *(pre-existing)* click-based; the model `validate_gdb_cli.py` follows. |

## 3. Quality assessment — `unbox/quality_assessment.py`

| # | Feature | Where | Notes |
|---|---|---|---|
| 3.1 | Assess geocoding result quality | `unbox/quality_assessment.py` | *(pre-existing)* Separate from the validation module in §4, which verifies the merge rather than the geocoder. |

---

## 4. Geodatabase validation — `unbox/validation/`, `validate_gdb_cli.py`

*Added 2026-10-26.* A read-only module that verifies the merged geodatabase after a
build and writes a report. **Every operation is read-only** and this must stay true —
see the safety guarantees at 4.31–4.33.

### Engine

| # | Feature | Where | Notes |
|---|---|---|---|
| 4.1 | Expectations derived from `GDBMerge` class attributes, never duplicated | `validation/expectations.py` | Adding an index to the build automatically extends the validation. Applies `idx_<field>` and `rc_` conventions. |
| 4.2 | Single read-only arcpy facade | `validation/gdb_access.py` | The only module importing arcpy. Enables testing the whole engine with no ArcGIS Pro. |
| 4.3 | Check registry with duplicate-id protection | `validation/registry.py` | `@register_check`; a duplicate `check_id` raises at import time. |
| 4.4 | Runner with per-check exception isolation | `validation/runner.py` | A raising check becomes an `ERROR` result with its traceback; the run continues. |
| 4.5 | Five-state verdict: PASS / FAIL / WARN / SKIP / ERROR | `validation/models.py` | `SKIP` means "could not verify", never a quiet pass. Overall status is the worst result; an empty run is `SKIP`. |
| 4.6 | Per-county source geodatabase discovery by FIPS code | `validation/source_discovery.py` | Guards against the output geodatabase's own name parsing as a county. |
| 4.7 | Shared state cache across checks within a run | `ValidationContext.shared_state` | Saves ~2 minutes per full run by sharing 570 source counts. |

### Count parity checks

| # | Feature | Where | Notes |
|---|---|---|---|
| 4.8 | Every source county present in the output, no unexpected counties | `checks/counts.py` — `counts.county_coverage` | Also surfaces rows with a null `FIPS_CODE`. |
| 4.9 | Per-county, per-theme row count comparison across all ten themes | `counts.per_county_theme` | Catches dropped and double-appended counties. One result per theme. |
| 4.10 | Merge-base county reconciled arithmetically | `counts.base_county_reconciliation` | `sum(sources) + output[06037] == output total`; verified exact at 19,341,519 + 7,414,167 = 26,755,686 on Addresses. |
| 4.11 | One grouped pass per theme rather than per-county queries | `accessor.count_rows_by_field` | 16s for all 58 counties of a 26.1M-row relation, versus ~13 minutes naively. |

### Schema and integrity checks

| # | Feature | Where | Notes |
|---|---|---|---|
| 4.12 | Output field names, types and lengths match a source county | `checks/schema.py` — `schema.field_conformance` | Allows the known `OBJECTID` → `RID` substitution on attributed relationship classes only. |
| 4.13 | All 49 declared attribute indexes exist | `schema.attribute_indexes` | Case-insensitive; a differently named index on the right field is a WARN, a missing index is a FAIL. |
| 4.14 | All four spatial indexes exist | `schema.spatial_indexes` | Checks for `FDO_SHAPE`. |
| 4.15 | All eight relationship classes exist with the right endpoints, keys and cardinality | `schema.relationship_classes` | Handles both many-to-many and `rc_` one-to-many key layouts. |
| 4.16 | Sampled relationship keys resolve to real rows | `checks/integrity.py` — `integrity.relationship_orphans` | Batched `IN` clauses; 500 keys in 0.84s. Empty relationship class is WARN, not FAIL. |
| 4.17 | `Parcels` primary assessment and building keys resolve | `integrity.primary_key_resolution` | Nulls are valid — a vacant parcel has no primary building. |
| 4.18 | Relation rows agree with the county of what they link | `integrity.fips_consistency` | Catches keys that resolve to the wrong county. |

### Performance checks

| # | Feature | Where | Notes |
|---|---|---|---|
| 4.19 | Indexed-versus-unindexed timing ratio, self-calibrating across machines | `checks/performance.py` | Measured: `PARCEL_LID` 122×, `ASSESSMENT_LID` 120×, `address_lid` 69×. Default threshold 5×. |
| 4.20 | Control field chosen by sampled cardinality, not field order | `performance.establish_scan_baseline` | **Do not revert to field order** — it picked `STATE` on Addresses, a constant matching all 26.7M rows, making every comparison meaningless. |
| 4.21 | Non-selective queries reported but not judged | `MAXIMUM_SELECTIVE_FRACTION` | A query returning >5% of a table is correctly served by a scan; judging it penalises the database for behaving properly. |
| 4.22 | Spatial bounding box timing at three sizes | `performance.spatial_queries` | Boxes derived from each feature class's own extent and centred, so they are valid for any subset or projection. |
| 4.23 | Cold and warm timings reported separately | `accessor.time_attribute_selection` | A discarded warm-up precedes each timing; the ratio uses the warm figure. |

### Reporting and invocation

| # | Feature | Where | Notes |
|---|---|---|---|
| 4.24 | Self-contained HTML report | `reporting/html_report.py` | Inline CSS, native `<details>`, no JavaScript, no external references — opens by double-clicking with no internet. Autoescaped. |
| 4.25 | JSON results for diffing runs | `reporting/json_report.py` | Survives enums, sets, datetimes and numpy scalars rather than failing to serialise. |
| 4.26 | Plain text digest | `reporting/text_report.py` | One line per check plus expanded failures. |
| 4.27 | Timestamped report folder per run | `reporting/write_reports` | `validation_<yyyymmdd>_<hhmmss>/`; a nightly run never overwrites yesterday's evidence. |
| 4.28 | Command line entry point with county, category and threshold filters | `validate_gdb_cli.py` | Exits non-zero on failure so it can gate a pipeline. |
| 4.29 | Opt-in hook after the merge | `main.py` — `RUN_VALIDATION` | Defaults to `True`; writes the report into the processing folder. |
| 4.30 | Validation results surface as named pytest tests | `tests/test_validation_data_quality.py` | One cached run parametrised per check. Deselect with `-m "not data_quality"`. |

### Safety guarantees — these must not be broken

| # | Guarantee | Enforced by |
|---|---|---|
| 4.31 | No write-capable arcpy operation appears anywhere in the package | `tests/test_validation_read_only.py` — AST scan of every source file |
| 4.32 | `arcpy` is imported only in `gdb_access.py` | same test |
| 4.33 | `GeodatabaseAccessor` exposes no method whose name implies mutation | same test |

---

## Changing things safely

- **`GDBMerge`'s class-level configuration lists** (1.10) are read directly by the
  validator. Keep them class attributes, readable without instantiating the class.
- **The read-only guarantees** (4.31–4.33) exist because the geodatabase takes 8–24
  hours to rebuild. Do not add a write operation to the validation package.
- **The performance control-field rule** (4.20) and **selectivity guard** (4.21) were
  both the result of real measurements producing wrong verdicts. Both have regression
  tests; read the comments before changing either.
- **`main.py` previously shadowed the `datetime` module** with
  `from datetime import datetime`, which made `datetime.datetime.now()` raise. Fixed
  2026-10-26 when the validation hook was added.
