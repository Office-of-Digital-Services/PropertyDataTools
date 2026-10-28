"""
    Referential integrity: do the relationship keys actually resolve?

    A relationship class can exist, be correctly shaped, and still be useless if the
    keys in the relation table point at rows that are not there. That is a plausible
    outcome of a partial merge, and it is invisible until someone clicks a parcel in
    ArcGIS Pro and gets an empty list of assessments.

    Three checks:

        integrity.relationship_orphans   sampled relation keys resolve to real rows
        integrity.primary_key_resolution Parcels.primary_*_lid resolve, nulls allowed
        integrity.fips_consistency       a relation row agrees with what it links

    All three sample rather than sweep. Resolving 500 keys against the 26.7M-row
    Addresses table was measured at 0.84 seconds using batched IN clauses; checking
    every one of 26 million keys would take hours and tell us very little more. The
    sample size is configurable with --sample_size.
"""

from __future__ import annotations

import logging
from typing import Any, Dict, Iterator, List, Optional, Sequence, Tuple

from unbox.validation.expectations import COUNTY_FIELD_NAME, ExpectedRelationship
from unbox.validation.models import CheckResult, CheckStatus, ValidationContext, make_result
from unbox.validation.registry import register_check

module_logger = logging.getLogger(__name__)

CATEGORY = "integrity"

# The two Parcels fields that point at a single "primary" related record. Both are
# nullable in the real schema - a parcel with no building is perfectly ordinary - so a
# null here is valid data, not an orphan.
PARCEL_PRIMARY_KEY_FIELDS = (
    ("PRIMARY_ASSESSMENT_LID", "Assessments", "ASSESSMENT_LID"),
    ("PRIMARY_BUILDING_LID", "Buildings", "BUILDING_LID"),
)

PARCELS_THEME_NAME = "Parcels"


# ----------------------------------------------------------------------------------
# Shared helpers
# ----------------------------------------------------------------------------------


def _orphan_rate(unresolved_count: int, sampled_count: int) -> float:
    """
        The fraction of sampled keys that did not resolve.

        Returns 0.0 for an empty sample rather than dividing by zero - an empty sample
        is handled separately as a WARN, since it means there was nothing to test.
    """
    if sampled_count <= 0:
        return 0.0
    return unresolved_count / sampled_count


def _sample_keys(context: ValidationContext,
                 dataset_name: str,
                 field_names: Sequence[str]) -> List[Tuple[Any, ...]]:
    """
        Reads up to --sample_size rows of the given key fields from an output dataset.
    """
    return context.accessor.sample_field_values(
        context.output_gdb,
        dataset_name,
        field_names,
        limit=context.sample_size,
    )


def _verdict_for_orphan_rate(context: ValidationContext,
                             unresolved_count: int,
                             sampled_count: int) -> str:
    """
        Maps an orphan count to a row status, honouring --orphan_tolerance.
    """
    if unresolved_count == 0:
        return "pass"
    return "fail" if _orphan_rate(unresolved_count, sampled_count) > context.orphan_tolerance \
        else "warn"


# ----------------------------------------------------------------------------------
# integrity.relationship_orphans
# ----------------------------------------------------------------------------------


@register_check(
    check_id="integrity.relationship_orphans",
    category=CATEGORY,
    title="Relationship keys resolve to real rows",
)
def check_relationship_orphans(context: ValidationContext) -> Iterator[CheckResult]:
    """
        For each many-to-many relation, samples keys from both sides and confirms each
        resolves to a row in the feature class it points at.

        One result per relation so a broken link is attributed precisely.
    """
    for expected in context.expectations.manytomany_relationships:
        yield _check_one_relation_for_orphans(context, expected)


def _check_one_relation_for_orphans(context: ValidationContext,
                                    expected: ExpectedRelationship) -> CheckResult:
    """
        Samples one relation table and resolves both its origin and destination keys.
    """
    relation_name = expected.relationship_name
    check_id = f"integrity.relationship_orphans.{relation_name}"
    title = f"Relationship keys resolve - {relation_name}"

    if not context.accessor.dataset_exists(context.output_gdb, relation_name):
        return make_result(
            check_id=check_id, category=CATEGORY, title=title,
            status=CheckStatus.FAIL,
            summary=f"{relation_name} does not exist in the output geodatabase.",
            details={"relation": relation_name},
        )

    sampled_rows = _sample_keys(
        context, relation_name, [expected.origin_key, expected.destination_key]
    )

    if not sampled_rows:
        # An existing but empty relationship class is suspicious rather than wrong -
        # a county genuinely might have no address-to-building links - so WARN.
        return make_result(
            check_id=check_id, category=CATEGORY, title=title,
            status=CheckStatus.WARN,
            summary=(
                f"{relation_name} exists but returned no rows to sample, so its keys "
                f"could not be verified."
            ),
            details={"relation": relation_name, "rows_sampled": 0},
        )

    rows: List[Dict[str, Any]] = []
    total_unresolved = 0

    # Each side of the relation is resolved against the feature class it points at.
    sides = (
        (expected.origin_key, expected.origin_table, 0),
        (expected.destination_key, expected.destination_table, 1),
    )

    for key_field, target_table, tuple_position in sides:
        sampled_keys = [row[tuple_position] for row in sampled_rows]
        non_null_keys = [key for key in sampled_keys if key is not None]
        distinct_keys = sorted({key for key in non_null_keys})

        if not distinct_keys:
            rows.append({
                "key_field": key_field,
                "resolves_against": target_table,
                "keys_sampled": 0,
                "keys_resolved": 0,
                "orphans": 0,
                "orphan_rate": 0.0,
                "status": "warn",
                "note": "Every sampled key was null; nothing could be resolved.",
            })
            continue

        resolved_count = context.accessor.count_matching_keys(
            context.output_gdb, target_table,
            _target_key_field(expected, key_field, target_table), distinct_keys,
        )
        unresolved_count = len(distinct_keys) - resolved_count
        total_unresolved += unresolved_count

        rows.append({
            "key_field": key_field,
            "resolves_against": target_table,
            "keys_sampled": len(distinct_keys),
            "keys_resolved": resolved_count,
            "orphans": unresolved_count,
            "orphan_rate": round(_orphan_rate(unresolved_count, len(distinct_keys)), 6),
            "status": _verdict_for_orphan_rate(context, unresolved_count, len(distinct_keys)),
            "note": (
                "" if unresolved_count == 0
                else f"{unresolved_count} sampled key(s) point at rows that do not exist."
            ),
            "null_keys_in_sample": len(sampled_keys) - len(non_null_keys),
        })

    total_sampled = sum(row["keys_sampled"] for row in rows)
    overall_orphan_rate = _orphan_rate(total_unresolved, total_sampled)

    details = {
        "relation": relation_name,
        "rows_sampled": len(sampled_rows),
        "sample_size_requested": context.sample_size,
        "distinct_keys_checked": total_sampled,
        "total_orphans": total_unresolved,
        "orphan_rate": round(overall_orphan_rate, 6),
        "orphan_tolerance": context.orphan_tolerance,
    }

    if any(row["status"] == "fail" for row in rows):
        status = CheckStatus.FAIL
        summary = (
            f"{relation_name}: {total_unresolved} of {total_sampled} sampled keys do "
            f"not resolve ({overall_orphan_rate:.2%}), above the tolerance of "
            f"{context.orphan_tolerance:.2%}."
        )
    elif any(row["status"] == "warn" for row in rows):
        status = CheckStatus.WARN
        summary = (
            f"{relation_name}: {total_unresolved} of {total_sampled} sampled keys do "
            f"not resolve ({overall_orphan_rate:.2%}), within the configured tolerance."
        )
    else:
        status = CheckStatus.PASS
        summary = (
            f"{relation_name}: all {total_sampled} sampled keys resolve to real rows "
            f"in {expected.origin_table} and {expected.destination_table}."
        )

    return make_result(
        check_id=check_id, category=CATEGORY, title=title,
        status=status, summary=summary, details=details, rows=rows,
    )


def _target_key_field(expected: ExpectedRelationship,
                      relation_key_field: str,
                      target_table: str) -> str:
    """
        The field name to resolve a relation key against in the target feature class.

        The relation table and the feature class use the same logical key name - the
        AddressParcelRelation.address_lid resolves against Addresses.ADDRESS_LID - and
        the accessor matches case-insensitively, so the relation's own field name is
        the correct one to use. This indirection exists so that a future schema in
        which the names diverge has one obvious place to be taught about it.
    """
    return relation_key_field


# ----------------------------------------------------------------------------------
# integrity.primary_key_resolution
# ----------------------------------------------------------------------------------


@register_check(
    check_id="integrity.primary_key_resolution",
    category=CATEGORY,
    title="Parcel primary assessment and building keys resolve",
)
def check_primary_key_resolution(context: ValidationContext) -> CheckResult:
    """
        Confirms Parcels.PRIMARY_ASSESSMENT_LID and PRIMARY_BUILDING_LID point at rows
        that exist.

        Nulls are valid and are counted separately rather than treated as orphans. Both
        fields are nullable in the real schema: a vacant parcel has no primary building,
        and that is correct data, not a broken link.
    """
    if not context.accessor.dataset_exists(context.output_gdb, PARCELS_THEME_NAME):
        return make_result(
            check_id="integrity.primary_key_resolution",
            category=CATEGORY,
            title="Parcel primary assessment and building keys resolve",
            status=CheckStatus.FAIL,
            summary=f"{PARCELS_THEME_NAME} does not exist in the output geodatabase.",
            details={"theme": PARCELS_THEME_NAME},
        )

    rows: List[Dict[str, Any]] = []
    total_unresolved = 0
    total_checked = 0

    for parcel_field, target_table, target_field in PARCEL_PRIMARY_KEY_FIELDS:
        sampled_rows = _sample_keys(context, PARCELS_THEME_NAME, [parcel_field])
        sampled_values = [row[0] for row in sampled_rows]

        # A null primary key is ordinary data. Only non-null values are resolvable, and
        # only those can be orphans.
        non_null_values = [value for value in sampled_values if value not in (None, "")]
        null_count = len(sampled_values) - len(non_null_values)
        distinct_values = sorted(set(non_null_values))

        if not distinct_values:
            rows.append({
                "parcel_field": parcel_field,
                "resolves_against": f"{target_table}.{target_field}",
                "rows_sampled": len(sampled_values),
                "null_or_empty": null_count,
                "keys_checked": 0,
                "keys_resolved": 0,
                "orphans": 0,
                "status": "warn",
                "note": "Every sampled value was null or empty; nothing to resolve.",
            })
            continue

        resolved_count = context.accessor.count_matching_keys(
            context.output_gdb, target_table, target_field, distinct_values
        )
        unresolved_count = len(distinct_values) - resolved_count

        total_unresolved += unresolved_count
        total_checked += len(distinct_values)

        rows.append({
            "parcel_field": parcel_field,
            "resolves_against": f"{target_table}.{target_field}",
            "rows_sampled": len(sampled_values),
            "null_or_empty": null_count,
            "keys_checked": len(distinct_values),
            "keys_resolved": resolved_count,
            "orphans": unresolved_count,
            "status": _verdict_for_orphan_rate(context, unresolved_count, len(distinct_values)),
            "note": (
                f"{null_count} sampled parcel(s) have no {parcel_field}, which is valid."
                if unresolved_count == 0
                else f"{unresolved_count} key(s) point at a {target_table} row that does not exist."
            ),
        })

    details = {
        "theme": PARCELS_THEME_NAME,
        "sample_size_requested": context.sample_size,
        "keys_checked": total_checked,
        "total_orphans": total_unresolved,
        "orphan_rate": round(_orphan_rate(total_unresolved, total_checked), 6),
        "orphan_tolerance": context.orphan_tolerance,
        "nulls_treated_as": "valid - a parcel may legitimately have no primary record",
    }

    if any(row["status"] == "fail" for row in rows):
        status = CheckStatus.FAIL
        summary = (
            f"{total_unresolved} of {total_checked} sampled parcel primary keys point "
            f"at records that do not exist."
        )
    elif any(row["status"] == "warn" for row in rows):
        status = CheckStatus.WARN
        summary = (
            f"Parcel primary keys resolved with {total_unresolved} orphan(s) within "
            f"tolerance, or a sample consisting entirely of nulls."
        )
    else:
        status = CheckStatus.PASS
        summary = (
            f"All {total_checked} sampled parcel primary assessment and building keys "
            f"resolve to real records; nulls were tolerated as valid."
        )

    return make_result(
        check_id="integrity.primary_key_resolution",
        category=CATEGORY,
        title="Parcel primary assessment and building keys resolve",
        status=status,
        summary=summary,
        details=details,
        rows=rows,
    )


# ----------------------------------------------------------------------------------
# integrity.fips_consistency
# ----------------------------------------------------------------------------------


@register_check(
    check_id="integrity.fips_consistency",
    category=CATEGORY,
    title="Relation rows agree with the counties of what they link",
)
def check_fips_consistency(context: ValidationContext) -> Iterator[CheckResult]:
    """
        Confirms a relation row's FIPS_CODE matches the county of the features it links.

        This catches a subtler failure than an orphan: a key that resolves, but to a
        row in the wrong county. That would mean the merge crossed county boundaries
        while appending, producing links that are structurally valid and factually
        wrong.
    """
    for expected in context.expectations.manytomany_relationships:
        yield _check_one_relation_fips(context, expected)


def _check_one_relation_fips(context: ValidationContext,
                             expected: ExpectedRelationship) -> CheckResult:
    """
        Samples one relation and compares its FIPS_CODE against the origin feature's.
    """
    relation_name = expected.relationship_name
    check_id = f"integrity.fips_consistency.{relation_name}"
    title = f"County agreement - {relation_name}"

    if not context.accessor.dataset_exists(context.output_gdb, relation_name):
        return make_result(
            check_id=check_id, category=CATEGORY, title=title,
            status=CheckStatus.FAIL,
            summary=f"{relation_name} does not exist in the output geodatabase.",
            details={"relation": relation_name},
        )

    sampled_rows = _sample_keys(
        context, relation_name, [expected.origin_key, COUNTY_FIELD_NAME]
    )

    if not sampled_rows:
        return make_result(
            check_id=check_id, category=CATEGORY, title=title,
            status=CheckStatus.WARN,
            summary=f"{relation_name} returned no rows to sample.",
            details={"relation": relation_name, "rows_sampled": 0},
        )

    # Resolve each sampled origin key to the county of the feature it points at.
    origin_keys = [row[0] for row in sampled_rows if row[0] is not None]
    key_to_origin_county = context.accessor.fetch_key_to_value_map(
        context.output_gdb, expected.origin_table,
        expected.origin_key, COUNTY_FIELD_NAME, origin_keys,
    )

    rows: List[Dict[str, Any]] = []
    disagreement_count = 0
    comparable_count = 0

    for origin_key, relation_county in sampled_rows:
        if origin_key is None:
            continue

        origin_county = key_to_origin_county.get(origin_key)
        if origin_county is None:
            # Unresolvable keys are the orphan check's business, not this one's.
            continue

        comparable_count += 1
        if str(relation_county) == str(origin_county):
            continue

        disagreement_count += 1
        # Report a bounded number of examples; the pattern matters more than the volume.
        if len(rows) < 20:
            rows.append({
                "origin_key": origin_key,
                "relation_fips": relation_county,
                f"{expected.origin_table}_fips": origin_county,
                "status": "fail",
                "note": "The relation row is attributed to a different county than the feature.",
            })

    details = {
        "relation": relation_name,
        "origin_table": expected.origin_table,
        "rows_sampled": len(sampled_rows),
        "rows_comparable": comparable_count,
        "rows_disagreeing": disagreement_count,
        "examples_shown": len(rows),
    }

    if comparable_count == 0:
        status = CheckStatus.WARN
        summary = (
            f"{relation_name}: none of the {len(sampled_rows)} sampled rows could be "
            f"resolved to an {expected.origin_table} row, so county agreement was not tested."
        )
    elif disagreement_count:
        status = CheckStatus.FAIL
        summary = (
            f"{relation_name}: {disagreement_count} of {comparable_count} sampled rows "
            f"carry a different {COUNTY_FIELD_NAME} than the {expected.origin_table} "
            f"row they link to."
        )
    else:
        status = CheckStatus.PASS
        summary = (
            f"{relation_name}: all {comparable_count} comparable sampled rows agree "
            f"with the county of the {expected.origin_table} row they link to."
        )

    return make_result(
        check_id=check_id, category=CATEGORY, title=title,
        status=status, summary=summary, details=details, rows=rows,
    )
