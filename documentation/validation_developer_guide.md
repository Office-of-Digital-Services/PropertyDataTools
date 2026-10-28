# Geodatabase Validation — Developer Guide

How `unbox/validation` is built, how to add a check, and the constraints you must work
within. For running the tool, see
[validation_user_guide.md](validation_user_guide.md).

---

## The shape of the module

Four concerns are kept deliberately separate, because in most geodatabase QA scripts
they are tangled together and the result cannot be tested.

| Concern | Lives in | Why separate |
|---|---|---|
| What *should* be true | `expectations.py` | Derived from `GDBMerge`, so it cannot drift from the build config |
| How to ask the geodatabase | `gdb_access.py` | The only module importing arcpy, so everything else is testable with a fake |
| What to verify | `checks/` | Independent, individually runnable, uniformly reportable |
| How to present it | `reporting/` | One run feeds a human report and a CI pass/fail |

```
unbox/validation/
    models.py           CheckStatus, CheckResult, ValidationRun, ValidationContext
    expectations.py     MergeExpectations.from_merge_class(GDBMerge)
    gdb_access.py       GeodatabaseAccessor — the ONLY arcpy importer
    source_discovery.py finds per-county source geodatabases by FIPS code
    registry.py         CheckDescriptor, CheckRegistry, @register_check
    runner.py           run_validation, exception isolation
    checks/             counts.py  schema.py  integrity.py  performance.py
    reporting/          html_report.py  json_report.py  text_report.py
```

---

## Two constraints you must not break

### 1. Read-only

The merged geodatabase takes 8–24 hours to build. Nothing in this package may modify
it. `tests/test_validation_read_only.py` enforces this by parsing every source file's
AST and failing if it finds `Append`, `Delete`, `AddIndex`, `RepairGeometry`,
`CalculateField`, `InsertCursor`, `UpdateCursor`, or any other write-capable name.

Permitted arcpy surface: `GetCount`, `MakeTableView`, `MakeFeatureLayer`,
`SelectLayerByAttribute`, `SelectLayerByLocation`, `da.SearchCursor`,
`da.TableToNumPyArray`, `ListFields`, `ListIndexes`, `Describe`, `Exists`,
`AddFieldDelimiters`.

### 2. arcpy lives in exactly one file

Only `gdb_access.py` may `import arcpy`. The same test enforces this. It is what lets
the entire engine — every check, the runner, the registry — be unit-tested against
`tests/fakes/fake_gdb_accessor.py` with no ArcGIS Pro installation and no data.

If you need a new geodatabase operation, add a read-only method to
`GeodatabaseAccessor`, then add the matching method to `FakeGeodatabaseAccessor`. Do
not reach for arcpy from inside a check.

---

## Writing a check

A check is a plain function taking a `ValidationContext` and returning a `CheckResult`,
an iterable of them, or `None`.

```python
from unbox.validation.models import CheckStatus, ValidationContext, make_result
from unbox.validation.registry import register_check


@register_check(
    check_id="counts.my_new_check",      # must be globally unique
    category="counts",                   # counts | schema | integrity | performance
    title="A sentence a non-developer can read",
    requires_source_folder=True,         # runner skips it if no --source_folder
)
def check_my_new_thing(context: ValidationContext):
    observed = context.accessor.get_row_count(context.output_gdb, "Parcels")
    expected = context.expectations.core_theme_names

    return make_result(
        check_id="counts.my_new_check",
        category="counts",
        title="A sentence a non-developer can read",
        status=CheckStatus.PASS if observed > 0 else CheckStatus.FAIL,
        summary=f"Parcels holds {observed:,} rows.",
        details={"row_count": observed},     # scalars, shown in the report header
        rows=[{"theme": "Parcels", "count": observed, "status": "pass"}],
    )
```

Then import your module in `checks/__init__.py` — registration happens as an import
side effect, so a module nobody imports contributes nothing.

### The `CheckResult` contract

| Field | Purpose |
|---|---|
| `check_id` | Unique. Appears in the report and as the pytest test name. |
| `category` | Determines the report section and the `--categories` filter. |
| `title` | Shown as the check's heading. |
| `status` | `PASS` / `FAIL` / `WARN` / `SKIP` / `ERROR`. |
| `summary` | One sentence, readable by someone who has never seen the codebase. |
| `details` | Scalars for the report header, and the natural place for thresholds. |
| `rows` | Tabular evidence. Each dict is a row; keys become columns. A `status` key tints the row. |
| `duration_seconds` | Leave at `0.0` and the runner fills it in. |

### Returning several results

Returning a generator gives each theme or table its own pass/fail, which reads far
better than one monolithic verdict:

```python
def check_each_theme(context):
    for theme_name in context.expectations.all_countable_theme_names:
        yield _check_one_theme(context, theme_name)
```

---

## Choosing a status — the most important decision

The most dangerous defect a validation suite can have is a check that silently always
passes. Two rules follow.

**Use SKIP whenever you could not actually verify something.** Never return PASS as a
fallback. A SKIP must carry its reason in `details` and in `summary`:

```python
return make_result(
    ..., status=CheckStatus.SKIP,
    summary="Skipped: no non-null value could be sampled, so the timing is meaningless.",
    details={"reason": "no_sampleable_value"},
)
```

**Never guess a value to query with.** Sample it from the data. On this dataset
`CITY = 'Davis'` returns zero rows while `CITY = 'DAVIS'` returns 63,039 — a guessed
literal silently produces a meaningless measurement. Use
`accessor.sample_field_values()`.

**Use WARN for suspicious-but-not-disqualifying.** An existing relationship class with
no rows is a WARN; a missing one is a FAIL.

`ValidationRun.overall_status` is the worst result, ordered
`PASS < SKIP < WARN < ERROR < FAIL`. An empty run reports SKIP, not PASS.

---

## Expectations come from `GDBMerge`, never from a copy

`MergeExpectations.from_merge_class(GDBMerge)` reads `KEY_INDEXES`,
`ATTRIBUTE_INDEXES`, `SPATIAL_INDEXES`, `MANYTOMANY_RELATIONSHIPS` and
`ONETOMANY_RELATIONSHIPS` as class attributes and projects them into validation shapes,
applying the build's own naming conventions (`idx_<field>`, the `rc_` prefix).

**Adding an index to the build automatically extends the validation.** Never hard-code
a list of indexes or relationships into a check; ask `context.expectations`.

Two things to know when comparing against reality:

- **Always compare names case-insensitively** via `expectations.normalize_name()`. The
  build declares `"Parcel_LID"`, arcpy reports `PARCEL_LID`, and the attributed
  relationship classes use lowercase `address_lid`.
- **Never discover datasets by listing.** In the output, five of the ten countable
  themes are relationship classes, so `arcpy.ListTables()` returns only
  `ParcelAssessmentRelation`. Resolve by path, which works for every dataset type.

---

## Two lessons the real data taught us

Both of these were found by running against the live geodatabase, not by unit testing,
and both are now locked in by regression tests.

### Query strategy is a design decision

`FIPS_CODE` is indexed on the four core feature classes but not on the relation tables,
where one filtered count costs 3.5–13.4 seconds. Counting 58 counties that way would
take ~13 minutes *per theme*. One grouped `TableToNumPyArray` pass does all 58 counties
of the 26.1M-row `AddressParcelRelation` in about 16 seconds. Use
`accessor.count_rows_by_field()`.

### A performance check can easily measure the wrong thing

Two defects surfaced on the first real performance run, both in the check rather than
in the database:

1. **The control field was chosen by field order**, which on `Addresses` picked `STATE`
   — a constant matching all 26,755,686 rows. That measured how long it takes to
   *return* 26 million rows, not how long it takes to *find* them. Control fields are
   now ranked by sampled cardinality, which picks `ADDRESS` (1 row) instead.

2. **Non-selective queries were being judged.** `RESBUS_USPS` is indexed but returns
   15,438,094 of 26,755,686 rows. At 58% of a table a full scan *is* the correct plan,
   so failing it penalised the database for behaving properly. Queries above
   `MAXIMUM_SELECTIVE_FRACTION` are now timed and reported but not judged.

The tell was that `FIPS_CODE` and `COUNTY` both returned exactly 7,414,167 rows and one
passed while the other failed — the measurement was tracking result volume, not
indexing. **If two queries returning identical row counts get different verdicts, your
measurement is wrong.**

---

## Testing

Two tiers. The module is itself code that must be trusted before its verdicts mean
anything.

**Tier 1 — unit tests, no arcpy, no data.** Drive each check through a healthy fixture
*and* deliberately corrupted ones. Proving a check can fail matters as much as proving
it can pass:

```python
def test_counts_fail_when_a_county_is_double_appended(tmp_path):
    accessor = build_accessor(output_counts_override={"Parcels": {"06003": 240, ...}})
    result = next(r for r in check_per_county_theme(build_context(accessor)) ...)

    assert result.status == CheckStatus.FAIL
    assert result.rows[0]["delta"] == 120
```

**Tier 2 — `tests/test_validation_data_quality.py`**, against the real delivery. One
cached run is parametrised so each check appears as its own named test. Marked
`data_quality` so it can be deselected with `-m "not data_quality"`.

Run everything with:

```
conda activate arcgispro-py3
pytest tests/ -q
```

---

## Extending `GeodatabaseAccessor`

1. Add a **read-only** method. If the name starts with `add`, `create`, `delete`,
   `update`, `insert`, `write`, `repair`, `rename`, `append`, `truncate` or
   `calculate`, the read-only test will reject it.
2. Add the matching method to `FakeGeodatabaseAccessor`, recording the call in
   `self.calls` so tests can assert on *how* a check did its work.
3. For anything timed, run a discarded warm-up pass and report cold and warm seconds
   separately — the first touch of a 26M-row table spends seconds on cache warming that
   says nothing about indexing.

---

## Shared state between checks

`context.shared_state` is a plain dict scoped to one run, for caching expensive work
across checks. Namespace your keys:

```python
cache_key = f"performance.baselines"
if cache_key not in context.shared_state:
    context.shared_state[cache_key] = expensive_work()
```

This is how the 570 source-geodatabase counts are shared between
`counts.per_county_theme` and `counts.base_county_reconciliation`, saving about two
minutes per run, and how `performance.establish_scan_baseline` publishes its baselines
to the two checks that consume them. Checks within a category run in registration
order, so a producer must be registered before its consumers.
