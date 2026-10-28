"""
    Prints the full per-row detail of the latest performance run.

    The question: are the five failures real defects, or artefacts of how the check
    measures? Specifically -

      - the four "slow" indexed Addresses fields (COUNTY, RESBUS_USPS, PRECISION_CODE,
        DELIVERY_FLAG) are all low-cardinality. A query on DELIVERY_FLAG = 'Y' may match
        twenty million rows, in which case the time is spent returning rows, not finding
        them, and the comparison says nothing about the index.
      - the "large" bounding box covers 33% of California. If it intersects most of the
        dataset, the same objection applies.

    If the row counts confirm that, the checks are measuring result volume rather than
    index effectiveness, and the guard needs to apply to these cases too.
"""

import glob
import json
import os

report_folders = sorted(
    glob.glob(os.path.join(os.environ["TEMP"], "junie_validation_reports", "validation_*"))
)
latest_folder = report_folders[-1]
print(f"Reading {latest_folder}\n")

with open(os.path.join(latest_folder, "validation_results.json"), encoding="utf-8") as results_file:
    run = json.load(results_file)

print("=" * 100)
print("BASELINES - the unindexed control query chosen for each table")
print("=" * 100)

baseline_result = next(
    item for item in run["results"] if item["check_id"] == "performance.establish_scan_baseline"
)
print(f"status: {baseline_result['status']}   {baseline_result['summary']}\n")
for row in baseline_result["rows"]:
    print(
        f"  {row['table']:28} control={str(row.get('control_field')):26} "
        f"rows={str(row.get('rows_matched')):>12} "
        f"warm={str(row.get('warm_seconds')):>8}  {row.get('note', '')}"
    )

print("\n" + "=" * 100)
print("INDEXED ATTRIBUTE QUERIES - every field, with row counts")
print("=" * 100)

for result in run["results"]:
    if not result["check_id"].startswith("performance.indexed_attribute_queries"):
        continue

    details = result["details"]
    print(f"\n--- {details.get('table')} [{result['status']}] ---")
    print(
        f"    control: {details.get('control_field')} "
        f"matched {details.get('control_rows_matched'):,} rows in "
        f"{details.get('control_warm_seconds')}s"
    )

    for row in result["rows"]:
        matched = row.get("rows_matched")
        matched_text = f"{matched:,}" if isinstance(matched, int) else str(matched)
        print(
            f"    {row['status']:5} {row['field']:28} rows={matched_text:>12} "
            f"warm={str(row.get('warm_seconds')):>8} "
            f"speedup={str(row.get('speedup')):>7}"
        )
        if row.get("note"):
            print(f"          -> {row['note']}")

print("\n" + "=" * 100)
print("SPATIAL QUERIES - how much of each dataset does each box actually intersect?")
print("=" * 100)

for result in run["results"]:
    if not result["check_id"].startswith("performance.spatial_queries"):
        continue

    details = result["details"]
    print(f"\n--- {details.get('feature_class')} [{result['status']}] ---")
    print(f"    control warm: {details.get('control_warm_seconds')}s "
          f"on {details.get('control_field')}")

    for row in result["rows"]:
        matched = row.get("rows_matched")
        matched_text = f"{matched:,}" if isinstance(matched, int) else str(matched)
        print(
            f"    {row['status']:5} {row['box_size']:8} "
            f"fraction={row.get('extent_fraction')} rows={matched_text:>12} "
            f"warm={str(row.get('warm_seconds')):>8} "
            f"speedup={str(row.get('speedup')):>7}"
        )
        if row.get("note"):
            print(f"          -> {row['note']}")
