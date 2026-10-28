# Geodatabase Validation — User Guide

After `compile_gdbs.py` merges the county deliveries into a statewide geodatabase, the
validation module answers one question with evidence: **did the merge produce a
correct, well-indexed, queryable statewide geodatabase?**

Every operation it performs is read-only. It cannot modify, repair, or rebuild the
geodatabase, so running it can never endanger an 8–24 hour build.

---

## Running it

### The simplest run

```
conda activate arcgispro-py3
python validate_gdb_cli.py --output_gdb "D:\deliveries\full_state_smartfabric_20261020.gdb"
```

This validates the schema, indexes, relationship classes, referential integrity, and
query performance. Record counts are skipped, because without the source geodatabases
there is nothing to compare against.

### The full run, including record counts

```
python validate_gdb_cli.py ^
    --output_gdb "D:\deliveries\full_state_smartfabric_20261020.gdb" ^
    --source_folder "D:\deliveries\processing_20261020"
```

This is the run you want after an overnight build. It takes roughly 10–20 minutes.

### A fast run while iterating

```
python validate_gdb_cli.py ^
    --output_gdb "D:\deliveries\full_state_smartfabric_20261020.gdb" ^
    --source_folder "D:\deliveries\processing_20261020" ^
    --counties 06001,06003,06005 ^
    --categories counts,schema
```

Restricting to a few counties and a couple of check families brings a run down to well
under a minute.

### Automatically, after a merge

`main.py` has a `RUN_VALIDATION` flag, set to `True` by default. When the merge
finishes, validation runs and writes its report into the processing folder. Set it to
`False` to skip.

### As part of the test suite

```
pytest tests/test_validation_data_quality.py      # three sample counties
pytest -m "not data_quality"                      # unit tests only, no real data
```

Set `VALIDATION_FULL_SWEEP=1` to make the data quality tests sweep all 58 counties.

---

## What you get

Each run creates a timestamped folder — `validation_20261026_164530` — containing three
files:

| File | Purpose |
|---|---|
| `validation_report.html` | The human report. Open it by double-clicking; it needs no web server and no internet. |
| `validation_results.json` | Every number from the run, for comparing one run against another. |
| `validation_summary.txt` | A plain-text digest for pasting into an email or a ticket. |

The command also prints the verdict and exits non-zero if anything failed, so it can
gate a pipeline.

---

## Reading the HTML report

The banner at the top gives the overall verdict and is colour-coded. Below it are the
run details, a tally of results, and then one section per check family. **Checks that
need your attention are expanded automatically**; passing checks are collapsed.

### What each status means

| Status | Meaning | Does it need action? |
|---|---|---|
| **PASS** | The check verified what it set out to verify, and the data was correct. | No. |
| **FAIL** | The check found a real problem. | **Yes.** |
| **WARN** | Something looks odd but is not disqualifying — for example a relationship class that exists but is empty. | Worth a look. |
| **SKIP** | The check could not gather the evidence it needed, so it reached no verdict. | Read the reason. |
| **ERROR** | The check itself hit an unexpected problem and could not run. | **Yes** — this is a bug in the validation module, not necessarily in your data. |

### Understanding a SKIP

A SKIP is **not** a quiet pass. It means the check deliberately declined to give a
verdict because any verdict would have been misleading. Every SKIP carries its reason.
The common ones:

- **"no source geodatabase folder was supplied"** — you did not pass `--source_folder`,
  so record counts could not be compared. Supply it if you want count parity.
- **"A scan is the correct plan at that volume"** — the query returned a large share of
  the table. For example `DELIVERY_FLAG` matches 62% of all addresses; when a query
  returns most of a table, reading the whole table *is* the fastest approach and an
  index genuinely cannot help. The timing is still reported. This is normal and healthy.
- **"too different in size for the comparison to be meaningful"** — the indexed query
  and the comparison query returned very different numbers of rows, so comparing their
  timings would measure result size rather than indexing.
- **"The sampled value matched no rows"** — no usable test value could be found, so the
  timing would mean nothing.
- **"a `--counties` filter is active"** — the statewide reconciliation only holds for a
  full sweep.

A run whose worst result is SKIP reports SKIP overall. That is intentional: it tells you
some things were not verified, rather than implying everything was.

---

## What the four check families verify

### Counts — did every county's records arrive, exactly once?

Compares each county's row count in its source geodatabase against the merged output,
across all ten themes. This catches the two failures that are otherwise invisible: a
county silently dropped, and a county appended twice.

Los Angeles has no source geodatabase, because it was promoted to become the merged
output. It is reconciled arithmetically instead:

```
sum of all source counties  +  LA's rows in the output  ==  the output total
      19,341,519            +        7,414,167          ==    26,755,686     ✓
```

### Schema — is the database shaped the way the build intended?

Compares output fields against a source county, and confirms that all 49 declared
indexes, all four spatial indexes, and all eight relationship classes exist with the
right keys and cardinality.

One difference is expected and allowed: the five attributed relationship classes carry
`RID` where the source tables carried `OBJECTID`. That is a normal consequence of
converting a table into a relationship class.

### Integrity — do the relationship keys actually resolve?

Samples keys from each relation and confirms they point at rows that exist. A
relationship class can be perfectly shaped and still be useless if its keys dangle —
you would only discover it by clicking a parcel and getting nothing back.

Nulls are treated as valid. A vacant parcel legitimately has no primary building.

### Performance — are the indexes actually delivering?

Times a query on each indexed field against a control query on an unindexed field of
the same table. Because both run on the same table on the same machine moments apart,
everything except the index cancels out, so the result does not need retuning per
machine.

Measured on the 2026-10-20 delivery:

| Query | Result |
|---|---|
| `Assessments.PARCEL_LID` | **122× faster** than an unindexed scan |
| `Assessments.ASSESSMENT_LID` | **120× faster** |
| `Addresses.address_lid` | **69× faster** |
| `Buildings.building_lid` | **30× faster** |

The default pass threshold is 5×, which is deliberately conservative against those
numbers.

---

## Common situations

**"The overall status is SKIP but nothing failed."**
Some checks could not gather evidence. Open the report and read their reasons — most
often you did not supply `--source_folder`, or some queries were too non-selective to
judge. Nothing is wrong.

**"A county shows a count mismatch."**
This is a real problem. The report names the county, the theme, and the exact
difference. A positive delta usually means a county was appended twice; a negative one
means records did not make it in.

**"A performance check failed."**
Look at the `speedup` column for the field named in the summary. If it is close to 1,
the index is not being used. Check that the index actually exists — the
`schema.attribute_indexes` check will tell you.

**"It says a relationship class is empty."**
Reported as WARN, not FAIL, because it might legitimately be empty. Worth confirming
against what you expect for that theme.

---

## All command line options

| Option | Default | What it does |
|---|---|---|
| `--output_gdb` | *required* | The merged geodatabase to validate. |
| `--source_folder` | none | The per-county source geodatabases. Without it, count checks skip. |
| `--base_county_gdb` | none | A copy of the merge-base county, if one ever exists. |
| `--base_fips` | `06037` | The county promoted to become the merge base. |
| `--counties` | all | Restrict to specific counties, e.g. `06001,06003`. |
| `--categories` | all | Restrict to `counts`, `schema`, `integrity`, `performance`. |
| `--report_dir` | beside the geodatabase | Where to write the report folder. |
| `--report_formats` | all three | Any of `html`, `json`, `text`. |
| `--perf_min_speedup` | `5.0` | How much faster an indexed query must be to pass. |
| `--sample_size` | `500` | Keys sampled per relation for integrity checks. |
| `--orphan_tolerance` | `0.0` | Fraction of unresolved keys tolerated, e.g. `0.001` for 0.1%. |
| `--verbose` | off | Debug-level logging. |

Run `python validate_gdb_cli.py --help` for the same list at the command line.

---

## Exit codes

| Code | Meaning |
|---|---|
| `0` | No check failed. SKIP and WARN results still exit `0`. |
| `1` | At least one check returned FAIL or ERROR. |

---

For how the module is built and how to add your own checks, see
[validation_developer_guide.md](validation_developer_guide.md).
