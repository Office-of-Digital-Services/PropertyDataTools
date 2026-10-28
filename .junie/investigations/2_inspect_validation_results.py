"""
    Reads back the most recent validation_results.json and prints the details that
    matter for confirming the schema and integrity checks did real work.

    The specific question being answered: did the RID-instead-of-OBJECTID allowance
    actually fire on the five attributed relationship classes, or did those checks pass
    for some less interesting reason? A check that passes without exercising its logic
    is indistinguishable from a check that does not work.
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

print(f"overall status: {run['overall_status']}   checks: {len(run['results'])}\n")

print("--- field conformance: did the OBJECTID -> RID allowance fire? ---")
for result in run["results"]:
    if not result["check_id"].startswith("schema.field_conformance"):
        continue
    details = result["details"]
    theme = details.get("theme", "?")
    print(
        f"  {result['status']:5} {theme:28} "
        f"fields={details.get('output_field_count')} "
        f"oid_swap_allowed={details.get('object_id_substitution_allowed')}"
    )

print("\n--- indexes and relationships ---")
for check_id in ("schema.attribute_indexes", "schema.spatial_indexes",
                 "schema.relationship_classes", "integrity.primary_key_resolution"):
    result = next((item for item in run["results"] if item["check_id"] == check_id), None)
    if result is None:
        continue
    print(f"  {result['status']:5} {check_id}")
    print(f"        {result['summary']}")

print("\n--- orphan sampling: how many keys were actually resolved? ---")
for result in run["results"]:
    if not result["check_id"].startswith("integrity.relationship_orphans"):
        continue
    details = result["details"]
    print(
        f"  {result['status']:5} {details.get('relation', '?'):28} "
        f"rows_sampled={details.get('rows_sampled')} "
        f"keys_checked={details.get('distinct_keys_checked')} "
        f"orphans={details.get('total_orphans')}"
    )

print("\n--- county agreement ---")
for result in run["results"]:
    if not result["check_id"].startswith("integrity.fips_consistency"):
        continue
    details = result["details"]
    print(
        f"  {result['status']:5} {details.get('relation', '?'):28} "
        f"comparable={details.get('rows_comparable')} "
        f"disagreeing={details.get('rows_disagreeing')}"
    )
