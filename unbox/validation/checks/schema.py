"""
    Schema, index, and relationship class conformance.

    Where the count checks ask "did all the data arrive?", these ask "is the database
    shaped the way the build intended?" - the fields, the indexes that make it usable,
    and the relationship classes that make it navigable in ArcGIS Pro.

    Four checks:

        schema.field_conformance    output fields match a reference source county
        schema.attribute_indexes    every declared index exists
        schema.spatial_indexes      every spatially indexed feature class has FDO_SHAPE
        schema.relationship_classes endpoints, keys and cardinality are as declared

    Two deliberate allowances, both verified against the real output geodatabase:

    1. RID instead of OBJECTID. Converting a table into an attributed relationship
       class replaces the OID field. AddressParcelRelation holds RID where the source
       table holds OBJECTID. This is intentional, not a defect.
    2. Field name casing. The same relation is ADDRESS_LID in the source and
       address_lid in the output, and ATTRIBUTE_INDEXES declares "Parcel_LID" while
       ListIndexes reports PARCEL_LID. Every comparison here is case-insensitive.
"""

from __future__ import annotations

import logging
from typing import Any, Dict, Iterator, List, Optional, Sequence, Set, Tuple

from unbox.validation.expectations import (
    ExpectedRelationship,
    SPATIAL_INDEX_NAME,
    normalize_name,
)
from unbox.validation.models import CheckResult, CheckStatus, ValidationContext, make_result
from unbox.validation.registry import register_check
from unbox.validation.source_discovery import discover_source_geodatabases

module_logger = logging.getLogger(__name__)

CATEGORY = "schema"

# Object ID field names treated as equivalent. An attributed relationship class uses
# RID where the originating table used OBJECTID - a known, intentional difference.
EQUIVALENT_OID_FIELD_NAMES = frozenset({"objectid", "rid"})


# ----------------------------------------------------------------------------------
# Shared helpers
# ----------------------------------------------------------------------------------


def _reference_source_gdb(context: ValidationContext) -> Optional[str]:
    """
        Picks one source county geodatabase to compare the output schema against.

        Any county will do - they all ship the same schema - so the lowest FIPS code is
        used for determinism. Cached because both the field conformance check and its
        per-theme sweep need it.
    """
    cache_key = "schema.reference_source_gdb"

    if cache_key not in context.shared_state:
        if not context.source_folder:
            context.shared_state[cache_key] = None
        else:
            discovery = discover_source_geodatabases(
                context.source_folder, counties=context.counties
            )
            context.shared_state[cache_key] = (
                discovery.source_geodatabases[0].gdb_path
                if discovery.source_geodatabases else None
            )

    return context.shared_state[cache_key]


def _index_definitions(context: ValidationContext, dataset_name: str) -> List[Any]:
    """
        The indexes on one output dataset, cached so the attribute and spatial index
        checks share a single ListIndexes call per table.
    """
    cache_key = f"schema.indexes.{dataset_name}"

    if cache_key not in context.shared_state:
        context.shared_state[cache_key] = context.accessor.list_index_definitions(
            context.output_gdb, dataset_name
        )

    return context.shared_state[cache_key]


def _normalized_cardinality(cardinality: str) -> str:
    """
        Collapses "MANY_TO_MANY" and "ManyToMany" to one comparable form.

        GDBMerge declares the underscored form; arcpy reports the camel-cased one.
    """
    return cardinality.replace("_", "").strip().lower()


# ----------------------------------------------------------------------------------
# schema.field_conformance
# ----------------------------------------------------------------------------------


@register_check(
    check_id="schema.field_conformance",
    category=CATEGORY,
    title="Output fields match the source schema",
    requires_source_folder=True,
)
def check_field_conformance(context: ValidationContext) -> Iterator[CheckResult]:
    """
        Diffs each output theme's fields against the same theme in a reference source
        county, reporting missing, extra, and mismatched fields.

        One result per theme, so a schema drift in one dataset is named precisely.
    """
    reference_gdb = _reference_source_gdb(context)

    if reference_gdb is None:
        yield make_result(
            check_id="schema.field_conformance",
            category=CATEGORY,
            title="Output fields match the source schema",
            status=CheckStatus.SKIP,
            summary="Skipped: no source geodatabase was available to compare against.",
            details={"reason": "no_reference_source_gdb"},
        )
        return

    for theme_name in context.expectations.all_countable_theme_names:
        yield _compare_theme_fields(context, reference_gdb, theme_name)


def _compare_theme_fields(context: ValidationContext,
                          reference_gdb: str,
                          theme_name: str) -> CheckResult:
    """
        Compares the field list of one theme between the output and a source county.
    """
    if not context.accessor.dataset_exists(context.output_gdb, theme_name):
        return make_result(
            check_id=f"schema.field_conformance.{theme_name}",
            category=CATEGORY,
            title=f"Field conformance - {theme_name}",
            status=CheckStatus.FAIL,
            summary=f"{theme_name} is missing from the output geodatabase entirely.",
            details={"theme": theme_name, "present_in_output": False},
        )

    if not context.accessor.dataset_exists(reference_gdb, theme_name):
        return make_result(
            check_id=f"schema.field_conformance.{theme_name}",
            category=CATEGORY,
            title=f"Field conformance - {theme_name}",
            status=CheckStatus.SKIP,
            summary=(
                f"Skipped: {theme_name} is not present in the reference source county, "
                f"so there is no schema to compare against."
            ),
            details={"theme": theme_name, "reference_gdb": reference_gdb},
        )

    output_fields = context.accessor.list_field_definitions(context.output_gdb, theme_name)
    source_fields = context.accessor.list_field_definitions(reference_gdb, theme_name)

    output_by_name = {normalize_name(field.name): field for field in output_fields}
    source_by_name = {normalize_name(field.name): field for field in source_fields}

    missing_field_names = set(source_by_name) - set(output_by_name)
    extra_field_names = set(output_by_name) - set(source_by_name)

    # Allow the known OID substitution: an attributed relationship class carries RID
    # where the source table carried OBJECTID. Only forgive it when it is genuinely a
    # swap - one OID name missing and a different OID name appearing.
    oid_substitution = _detect_oid_substitution(missing_field_names, extra_field_names)
    if oid_substitution is not None:
        missing_field_names.discard(oid_substitution[0])
        extra_field_names.discard(oid_substitution[1])

    rows: List[Dict[str, Any]] = []
    mismatch_count = 0

    # Fields present in both: compare type and length.
    for normalized_name in sorted(set(output_by_name) & set(source_by_name)):
        output_field = output_by_name[normalized_name]
        source_field = source_by_name[normalized_name]

        type_matches = output_field.type == source_field.type
        length_matches = output_field.length == source_field.length

        if type_matches and length_matches:
            continue

        mismatch_count += 1
        rows.append({
            "field": output_field.name,
            "issue": "type or length differs",
            "source": f"{source_field.type}({source_field.length})",
            "output": f"{output_field.type}({output_field.length})",
            "status": "fail",
        })

    for normalized_name in sorted(missing_field_names):
        source_field = source_by_name[normalized_name]
        rows.append({
            "field": source_field.name,
            "issue": "missing from the output",
            "source": f"{source_field.type}({source_field.length})",
            "output": "",
            "status": "fail",
        })

    for normalized_name in sorted(extra_field_names):
        output_field = output_by_name[normalized_name]
        rows.append({
            "field": output_field.name,
            "issue": "present in the output but not in the source",
            "source": "",
            "output": f"{output_field.type}({output_field.length})",
            "status": "fail",
        })

    if oid_substitution is not None:
        source_oid_name, output_oid_name = oid_substitution
        rows.append({
            "field": output_by_name[output_oid_name].name,
            "issue": "object ID field renamed",
            "source": source_by_name[source_oid_name].name,
            "output": output_by_name[output_oid_name].name,
            "status": "pass",
            "note": (
                "Expected: converting a table to an attributed relationship class "
                "replaces the object ID field."
            ),
        })

    details = {
        "theme": theme_name,
        "reference_source_gdb": reference_gdb,
        "output_field_count": len(output_fields),
        "source_field_count": len(source_fields),
        "fields_missing_from_output": sorted(
            source_by_name[name].name for name in missing_field_names
        ),
        "fields_only_in_output": sorted(
            output_by_name[name].name for name in extra_field_names
        ),
        "fields_with_type_or_length_mismatch": mismatch_count,
        "object_id_substitution_allowed": oid_substitution is not None,
    }

    problem_count = len(missing_field_names) + len(extra_field_names) + mismatch_count

    if problem_count:
        status = CheckStatus.FAIL
        summary = (
            f"{theme_name}: {problem_count} field difference(s) between the output and "
            f"the source schema."
        )
    else:
        status = CheckStatus.PASS
        summary = (
            f"{theme_name}: all {len(output_fields)} fields match the source schema"
            + (" (allowing the expected object ID rename)." if oid_substitution else ".")
        )

    return make_result(
        check_id=f"schema.field_conformance.{theme_name}",
        category=CATEGORY,
        title=f"Field conformance - {theme_name}",
        status=status,
        summary=summary,
        details=details,
        rows=rows,
    )


def _detect_oid_substitution(missing_field_names: Set[str],
                             extra_field_names: Set[str]) -> Optional[Tuple[str, str]]:
    """
        Recognises the OBJECTID -> RID swap on an attributed relationship class.

        Returns (source_oid_name, output_oid_name) when exactly one recognised OID name
        is missing and a different recognised OID name has appeared. Returning None in
        any other case keeps this allowance narrow: a genuinely missing field must not
        be excused just because an unrelated field happens to be named RID.
    """
    missing_oid_names = missing_field_names & EQUIVALENT_OID_FIELD_NAMES
    extra_oid_names = extra_field_names & EQUIVALENT_OID_FIELD_NAMES

    if len(missing_oid_names) == 1 and len(extra_oid_names) == 1:
        return (next(iter(missing_oid_names)), next(iter(extra_oid_names)))

    return None


# ----------------------------------------------------------------------------------
# schema.attribute_indexes
# ----------------------------------------------------------------------------------


@register_check(
    check_id="schema.attribute_indexes",
    category=CATEGORY,
    title="Every declared attribute index exists",
)
def check_attribute_indexes(context: ValidationContext) -> CheckResult:
    """
        Confirms every index declared in KEY_INDEXES and ATTRIBUTE_INDEXES exists.

        The test is "is this field indexed?" rather than "does an index with this exact
        name exist". That is the question that actually matters for query performance,
        and it is robust to the naming inconsistency already present in the build -
        ATTRIBUTE_INDEXES declares "Parcel_LID", producing idx_Parcel_LID, while
        ListIndexes reports the indexed field as PARCEL_LID.

        A differently named index on the right field is reported as a note, not a
        failure; a field with no index at all is a failure.
    """
    rows: List[Dict[str, Any]] = []
    missing_count = 0
    renamed_count = 0
    unreadable_tables: List[str] = []

    for expected_index in context.expectations.expected_indexes:
        table_name = expected_index.table_name

        try:
            existing_indexes = _index_definitions(context, table_name)
        except Exception as error:
            # One unreadable table must not cost the other 48 expectations.
            if table_name not in unreadable_tables:
                unreadable_tables.append(table_name)
            missing_count += 1
            rows.append({
                "table": table_name,
                "field": expected_index.field_name,
                "expected_index": expected_index.index_name,
                "actual_index": "",
                "declared_in": expected_index.source_list,
                "status": "fail",
                "note": f"Could not read indexes on {table_name}: {error}",
            })
            continue

        matching_index = _find_index_on_field(existing_indexes, expected_index.field_name)

        if matching_index is None:
            missing_count += 1
            rows.append({
                "table": table_name,
                "field": expected_index.field_name,
                "expected_index": expected_index.index_name,
                "actual_index": "",
                "declared_in": expected_index.source_list,
                "status": "fail",
                "note": "No index exists on this field; queries against it will scan the table.",
            })
            continue

        name_matches = (
            normalize_name(matching_index.name) == expected_index.normalized_index_name
        )
        if not name_matches:
            renamed_count += 1

        rows.append({
            "table": table_name,
            "field": expected_index.field_name,
            "expected_index": expected_index.index_name,
            "actual_index": matching_index.name,
            "declared_in": expected_index.source_list,
            "status": "pass" if name_matches else "warn",
            "note": "" if name_matches else "Indexed, but under a different index name.",
        })

    details = {
        "indexes_expected": len(context.expectations.expected_indexes),
        "indexes_missing": missing_count,
        "indexes_present_under_another_name": renamed_count,
        "tables_checked": len(context.expectations.tables_with_expected_indexes()),
        "unreadable_tables": unreadable_tables,
    }

    if missing_count:
        status = CheckStatus.FAIL
        missing_descriptions = [
            f"{row['table']}.{row['field']}" for row in rows if row["status"] == "fail"
        ]
        summary = (
            f"{missing_count} of {len(context.expectations.expected_indexes)} declared "
            f"indexes are missing: {', '.join(missing_descriptions[:5])}"
            + (", ..." if len(missing_descriptions) > 5 else "") + "."
        )
    elif renamed_count:
        status = CheckStatus.WARN
        summary = (
            f"All {len(context.expectations.expected_indexes)} declared fields are "
            f"indexed, but {renamed_count} index(es) do not follow the idx_<field> "
            f"naming convention."
        )
    else:
        status = CheckStatus.PASS
        summary = (
            f"All {len(context.expectations.expected_indexes)} declared attribute "
            f"indexes exist with the expected names."
        )

    return make_result(
        check_id="schema.attribute_indexes",
        category=CATEGORY,
        title="Every declared attribute index exists",
        status=status,
        summary=summary,
        details=details,
        rows=rows,
    )


def _find_index_on_field(existing_indexes: Sequence[Any], field_name: str) -> Optional[Any]:
    """
        The first index covering a given field, matched case-insensitively.
    """
    wanted_field = normalize_name(field_name)

    for index in existing_indexes:
        if wanted_field in tuple(normalize_name(name) for name in index.field_names):
            return index

    return None


# ----------------------------------------------------------------------------------
# schema.spatial_indexes
# ----------------------------------------------------------------------------------


@register_check(
    check_id="schema.spatial_indexes",
    category=CATEGORY,
    title="Spatially indexed feature classes carry FDO_SHAPE",
)
def check_spatial_indexes(context: ValidationContext) -> CheckResult:
    """
        Confirms each feature class in SPATIAL_INDEXES carries its FDO_SHAPE index.

        Without it, a map pan or a SelectLayerByLocation against an 11.6M-row feature
        class degrades from a quarter of a second to a full scan, which is the
        difference between a usable dataset and an unusable one.
    """
    rows: List[Dict[str, Any]] = []
    missing_count = 0

    for feature_class_name in context.expectations.spatially_indexed_tables:
        try:
            existing_indexes = _index_definitions(context, feature_class_name)
        except Exception as error:
            missing_count += 1
            rows.append({
                "feature_class": feature_class_name,
                "spatial_index": "",
                "status": "fail",
                "note": f"Could not read indexes: {error}",
            })
            continue

        spatial_index = next(
            (index for index in existing_indexes
             if normalize_name(index.name) == normalize_name(SPATIAL_INDEX_NAME)),
            None,
        )

        if spatial_index is None:
            missing_count += 1
            rows.append({
                "feature_class": feature_class_name,
                "spatial_index": "",
                "status": "fail",
                "note": (
                    f"No {SPATIAL_INDEX_NAME} index. Spatial queries against this "
                    f"feature class will scan every row."
                ),
            })
            continue

        rows.append({
            "feature_class": feature_class_name,
            "spatial_index": spatial_index.name,
            "status": "pass",
            "note": "",
        })

    details = {
        "feature_classes_expected": len(context.expectations.spatially_indexed_tables),
        "spatial_indexes_missing": missing_count,
        "spatial_index_name": SPATIAL_INDEX_NAME,
    }

    if missing_count:
        status = CheckStatus.FAIL
        missing_names = [row["feature_class"] for row in rows if row["status"] == "fail"]
        summary = (
            f"{missing_count} feature class(es) have no spatial index: "
            f"{', '.join(missing_names)}."
        )
    else:
        status = CheckStatus.PASS
        summary = (
            f"All {len(context.expectations.spatially_indexed_tables)} feature classes "
            f"carry a {SPATIAL_INDEX_NAME} spatial index."
        )

    return make_result(
        check_id="schema.spatial_indexes",
        category=CATEGORY,
        title="Spatially indexed feature classes carry FDO_SHAPE",
        status=status,
        summary=summary,
        details=details,
        rows=rows,
    )


# ----------------------------------------------------------------------------------
# schema.relationship_classes
# ----------------------------------------------------------------------------------


@register_check(
    check_id="schema.relationship_classes",
    category=CATEGORY,
    title="Relationship classes exist with the declared shape",
)
def check_relationship_classes(context: ValidationContext) -> CheckResult:
    """
        Confirms all eight relationship classes exist with the right endpoints, keys,
        and cardinality.

        These are what make the data navigable: clicking a parcel in ArcGIS Pro and
        getting its assessments and buildings depends entirely on these being correct.
    """
    rows: List[Dict[str, Any]] = []
    problem_count = 0

    for expected in context.expectations.all_relationships:
        row, has_problem = _compare_one_relationship(context, expected)
        rows.append(row)
        if has_problem:
            problem_count += 1

    details = {
        "relationships_expected": len(context.expectations.all_relationships),
        "manytomany_expected": len(context.expectations.manytomany_relationships),
        "onetomany_expected": len(context.expectations.onetomany_relationships),
        "relationships_with_problems": problem_count,
    }

    if problem_count:
        status = CheckStatus.FAIL
        problem_names = [row["relationship"] for row in rows if row["status"] == "fail"]
        summary = (
            f"{problem_count} of {len(context.expectations.all_relationships)} "
            f"relationship classes are missing or wrongly shaped: "
            f"{', '.join(problem_names)}."
        )
    else:
        status = CheckStatus.PASS
        summary = (
            f"All {len(context.expectations.all_relationships)} relationship classes "
            f"exist with the expected origin, destination, keys, and cardinality."
        )

    return make_result(
        check_id="schema.relationship_classes",
        category=CATEGORY,
        title="Relationship classes exist with the declared shape",
        status=status,
        summary=summary,
        details=details,
        rows=rows,
    )


def _compare_one_relationship(context: ValidationContext,
                              expected: ExpectedRelationship) -> Tuple[Dict[str, Any], bool]:
    """
        Compares one relationship class against its expectation.

        Returns the report row and whether a problem was found.
    """
    relationship_name = expected.relationship_name

    if not context.accessor.dataset_exists(context.output_gdb, relationship_name):
        return (
            {
                "relationship": relationship_name,
                "expected": _describe_expectation(expected),
                "actual": "",
                "cardinality": "",
                "status": "fail",
                "note": "The relationship class does not exist in the output.",
            },
            True,
        )

    try:
        actual = context.accessor.describe_relationship_class(
            context.output_gdb, relationship_name
        )
    except Exception as error:
        return (
            {
                "relationship": relationship_name,
                "expected": _describe_expectation(expected),
                "actual": "",
                "cardinality": "",
                "status": "fail",
                "note": f"Could not describe the relationship class: {error}",
            },
            True,
        )

    differences: List[str] = []

    if normalize_name(actual.origin_table) != normalize_name(expected.origin_table):
        differences.append(
            f"origin is {actual.origin_table}, expected {expected.origin_table}"
        )

    if normalize_name(actual.destination_table) != normalize_name(expected.destination_table):
        differences.append(
            f"destination is {actual.destination_table}, expected {expected.destination_table}"
        )

    if normalize_name(actual.origin_primary_key) != normalize_name(expected.origin_key):
        differences.append(
            f"origin key is {actual.origin_primary_key}, expected {expected.origin_key}"
        )

    # Where the destination key lives depends on the cardinality. A many-to-many
    # relationship records it as the destination primary key; a one-to-many records the
    # foreign key on the origin side, with no destination keys at all.
    actual_destination_key = (
        actual.destination_primary_key
        if actual.destination_primary_key
        else actual.origin_foreign_key
    )
    if normalize_name(actual_destination_key) != normalize_name(expected.destination_key):
        differences.append(
            f"destination key is {actual_destination_key}, expected {expected.destination_key}"
        )

    if _normalized_cardinality(actual.cardinality) != _normalized_cardinality(expected.cardinality):
        differences.append(
            f"cardinality is {actual.cardinality}, expected {expected.cardinality}"
        )

    row = {
        "relationship": relationship_name,
        "expected": _describe_expectation(expected),
        "actual": (
            f"{actual.origin_table}.{actual.origin_primary_key} -> "
            f"{actual.destination_table}.{actual_destination_key}"
        ),
        "cardinality": actual.cardinality,
        "status": "fail" if differences else "pass",
        "note": "; ".join(differences),
    }

    return (row, bool(differences))


def _describe_expectation(expected: ExpectedRelationship) -> str:
    """
        A compact one-line rendering of what a relationship class should look like.
    """
    return (
        f"{expected.origin_table}.{expected.origin_key} -> "
        f"{expected.destination_table}.{expected.destination_key}"
    )
