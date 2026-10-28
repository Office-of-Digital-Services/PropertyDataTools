"""
    Query performance: are the indexes actually delivering?

    An index that exists is not the same as an index that works. These checks prove the
    difference empirically, by timing a query that should use an index against a
    control query on the same table that cannot.

    Why a ratio rather than a seconds budget. An absolute threshold ("CITY must return
    in under half a second") needs retuning for every machine, disk, and cache state.
    The ratio self-calibrates: both queries run on the same table, on the same hardware,
    moments apart, so everything except the index cancels out. Measured on the real
    data, the gap is about 100x:

        indexed   Addresses.CITY = 'DAVIS'        63,039 rows    0.11s
        unindexed Addresses.STREET_NAME = 'OAK'   33,494 rows   11.34s

    A default threshold of 5.0 is therefore deliberately conservative.

    Three checks:

        performance.establish_scan_baseline  time an unindexed control per table
        performance.indexed_attribute_queries  time each indexed field, compute ratio
        performance.spatial_queries            time small/medium/large bbox selections

    The discipline that makes this trustworthy is refusing to guess. Every query value
    is sampled from the data, because a guessed literal is easy to get wrong - on this
    dataset CITY = 'Davis' returns zero rows while 'DAVIS' returns 63,039 - and a
    zero-row query produces a timing that means nothing. Wherever the measurement
    cannot be made meaningful, the check reports SKIP with the reason rather than a
    false PASS.
"""

from __future__ import annotations

import logging
from typing import Any, Dict, Iterator, List, Optional, Tuple

from unbox.validation.expectations import normalize_name
from unbox.validation.models import CheckResult, CheckStatus, ValidationContext, make_result
from unbox.validation.registry import register_check

module_logger = logging.getLogger(__name__)

CATEGORY = "performance"

# Field types a control or probe value can be sampled from. Text comparisons are the
# realistic case for this dataset and avoid float equality awkwardness.
SAMPLEABLE_FIELD_TYPES = frozenset({"String", "Integer", "SmallInteger"})

# How many rows to read when looking for a usable query value. Large enough to skip
# past a run of nulls, small enough to stay cheap on a 26M-row table.
VALUE_SAMPLE_ROW_LIMIT = 200

# The indexed and control result counts must be within this factor of each other.
# Comparing a 60,000-row query against a 3-row query would measure result set size,
# not index effectiveness.
MAXIMUM_RESULT_COUNT_RATIO = 10.0

# A query returning more than this fraction of a table is not a selective lookup, and
# an index is not expected to help it.
#
# This is the central correction learned from running against the real data. The first
# version of this check failed Addresses.RESBUS_USPS for being "only 1.3x faster than a
# scan" - but that query returns 15,438,094 of 26,755,686 rows. At 58% of the table a
# full scan is the correct plan, and any competent query engine will choose it. Failing
# that would be penalising the database for behaving properly. Such queries are now
# timed and reported, but reported as SKIP rather than judged.
#
# The giveaway in that run: FIPS_CODE and COUNTY both returned exactly 7,414,167 rows,
# and one passed while the other failed. The measurement was tracking result volume,
# not index effectiveness.
MAXIMUM_SELECTIVE_FRACTION = 0.05

# How many unindexed candidate fields to assess when choosing a control. Cardinality is
# estimated from a cheap sample rather than by timing each one, because a timed query
# on a 26M-row table costs seconds.
MAXIMUM_CONTROL_CANDIDATES = 12

# Timings below this are dominated by fixed overhead rather than by the work itself,
# so a ratio computed from them is noise. Treated as "too fast to measure".
MINIMUM_MEANINGFUL_SECONDS = 0.001

# Fractions of a feature class's full extent used for the bounding box tests, as a
# fraction of the full width and height.
#
# These are deliberately small. A spatial index earns its keep on the queries a user
# actually makes - panning a map, clicking a neighbourhood - not on selecting a third
# of California. The original 0.33 "large" box intersected most of the dataset, which
# a scan handles correctly and an index cannot improve, so it tested nothing. On
# California's extent 0.02 is roughly county-sized and 0.08 roughly region-sized.
SPATIAL_BOX_FRACTIONS = (("small", 0.005), ("medium", 0.02), ("large", 0.08))

# Fields never used as a performance control, regardless of whether they are indexed.
# An OID or geometry field does not behave like an ordinary attribute.
NON_CONTROL_FIELD_TYPES = frozenset({"OID", "Geometry", "Blob", "Raster", "GlobalID", "GUID"})


# ----------------------------------------------------------------------------------
# Value sampling
# ----------------------------------------------------------------------------------


def _sample_query_value(context: ValidationContext,
                        table_name: str,
                        field_name: str) -> Optional[Any]:
    """
        Picks a real, non-null value from a field to use in a timed query.

        Returns None when the field holds nothing usable, which the caller turns into a
        SKIP. Guessing a value instead would risk timing a query that matches no rows.
    """
    try:
        sampled_rows = context.accessor.sample_field_values(
            context.output_gdb, table_name, [field_name], limit=VALUE_SAMPLE_ROW_LIMIT
        )
    except Exception as error:
        module_logger.debug(f"Could not sample {table_name}.{field_name}: {error}")
        return None

    for row in sampled_rows:
        value = row[0]
        if value is not None and value != "":
            return value

    return None


def _build_where_clause(field_name: str, value: Any) -> str:
    """
        An equality predicate, quoting strings and escaping embedded single quotes.
    """
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        return f"{field_name} = {value}"

    escaped_value = str(value).replace("'", "''")
    return f"{field_name} = '{escaped_value}'"


def _field_definitions(context: ValidationContext, table_name: str) -> List[Any]:
    """
        The fields on an output table, cached across checks.
    """
    cache_key = f"performance.fields.{table_name}"

    if cache_key not in context.shared_state:
        context.shared_state[cache_key] = context.accessor.list_field_definitions(
            context.output_gdb, table_name
        )

    return context.shared_state[cache_key]


def _total_row_count(context: ValidationContext, table_name: str) -> Optional[int]:
    """
        The total rows in an output table, cached, used to judge selectivity.
    """
    cache_key = f"performance.total_rows.{table_name}"

    if cache_key not in context.shared_state:
        try:
            context.shared_state[cache_key] = context.accessor.get_row_count(
                context.output_gdb, table_name
            )
        except Exception as error:
            module_logger.debug(f"Could not count {table_name}: {error}")
            context.shared_state[cache_key] = None

    return context.shared_state[cache_key]


def _estimate_field_selectivity(context: ValidationContext,
                                table_name: str,
                                field_name: str) -> Tuple[int, Optional[Any]]:
    """
        Cheaply estimates how selective a field is, and returns a usable value from it.

        Returns (distinct_values_seen, sampled_value). A field showing 200 distinct
        values in a 200 row sample is high-cardinality, so an equality query on it
        returns very few rows; a field showing 1 distinct value is a constant and an
        equality query returns the whole table.

        Estimating from a sample rather than by timing each candidate matters for
        runtime: a timed query on a 26M-row table costs seconds, and there can be a
        dozen candidates per table.
    """
    try:
        sampled_rows = context.accessor.sample_field_values(
            context.output_gdb, table_name, [field_name], limit=VALUE_SAMPLE_ROW_LIMIT
        )
    except Exception as error:
        module_logger.debug(f"Could not sample {table_name}.{field_name}: {error}")
        return 0, None

    non_null_values = [row[0] for row in sampled_rows if row[0] is not None and row[0] != ""]
    if not non_null_values:
        return 0, None

    return len(set(non_null_values)), non_null_values[0]


# ----------------------------------------------------------------------------------
# performance.establish_scan_baseline
# ----------------------------------------------------------------------------------


@register_check(
    check_id="performance.establish_scan_baseline",
    category=CATEGORY,
    title="Full scan baseline on an unindexed field",
)
def check_establish_scan_baseline(context: ValidationContext) -> CheckResult:
    """
        Times one query per table on a field that carries no index, and caches the
        result for the other performance checks to compare against.

        The control field is chosen deterministically: the first sampleable field, in
        the order arcpy reports them, that appears in no index for that table. Picking
        the first match rather than an arbitrary one keeps consecutive runs comparable.
    """
    baselines: Dict[str, Dict[str, Any]] = {}
    rows: List[Dict[str, Any]] = []

    for table_name in context.expectations.tables_with_expected_indexes():
        baseline, row = _measure_baseline_for_table(context, table_name)
        rows.append(row)
        if baseline is not None:
            baselines[normalize_name(table_name)] = baseline

    # Published for the other two performance checks, which run after this one.
    context.shared_state["performance.baselines"] = baselines

    details = {
        "tables_considered": len(rows),
        "baselines_established": len(baselines),
        "control_field_rule": "first sampleable field carrying no index, in field order",
    }

    if not baselines:
        status = CheckStatus.SKIP
        summary = (
            "No unindexed control field could be found on any table, so index "
            "performance cannot be measured by comparison."
        )
    elif len(baselines) < len(rows):
        status = CheckStatus.WARN
        summary = (
            f"Established a full scan baseline for {len(baselines)} of {len(rows)} "
            f"tables; the rest have no usable unindexed control field."
        )
    else:
        status = CheckStatus.PASS
        summary = (
            f"Established a full scan baseline on an unindexed field for all "
            f"{len(baselines)} tables."
        )

    return make_result(
        check_id="performance.establish_scan_baseline",
        category=CATEGORY,
        title="Full scan baseline on an unindexed field",
        status=status,
        summary=summary,
        details=details,
        rows=rows,
    )


def _measure_baseline_for_table(context: ValidationContext,
                                table_name: str) -> Tuple[Optional[Dict[str, Any]], Dict[str, Any]]:
    """
        Finds and times an unindexed control query for one table.

        Returns (baseline_or_None, report_row).
    """
    indexed_field_names = context.expectations.indexed_field_names_for_table(table_name)

    try:
        field_definitions = _field_definitions(context, table_name)
    except Exception as error:
        return None, {
            "table": table_name,
            "control_field": "",
            "rows_matched": None,
            "warm_seconds": None,
            "status": "skip",
            "note": f"Could not read the fields of {table_name}: {error}",
        }

    unindexed_fields = [
        field for field in field_definitions
        if field.type in SAMPLEABLE_FIELD_TYPES
        and field.type not in NON_CONTROL_FIELD_TYPES
        and normalize_name(field.name) not in indexed_field_names
    ]

    if not unindexed_fields:
        return None, {
            "table": table_name,
            "control_field": "",
            "rows_matched": None,
            "warm_seconds": None,
            "status": "skip",
            "note": (
                "Every sampleable field on this table is indexed, so no unindexed "
                "control query is possible."
            ),
        }

    # Order the candidates by estimated cardinality, highest first.
    #
    # This ordering is the fix for a real defect. Without it the first unindexed field
    # in field order was used, which on Addresses was STATE - a constant, matching all
    # 26,755,686 rows. That control measured how long it takes to return 26 million
    # rows, not how long it takes to find them, and it made every comparison against it
    # meaningless. A high-cardinality control such as Parcels.PARCEL_APN matches a
    # single row, so its elapsed time is almost entirely the cost of scanning.
    control_candidates = _rank_control_candidates(context, table_name, unindexed_fields)

    for control_field, sampled_value in control_candidates:
        where_clause = _build_where_clause(control_field.name, sampled_value)

        try:
            timing = context.accessor.time_attribute_selection(
                context.output_gdb, table_name, where_clause
            )
        except Exception as error:
            module_logger.debug(f"Control query failed on {table_name}: {error}")
            continue

        if timing.matched_row_count == 0:
            continue

        baseline = {
            "table": table_name,
            "control_field": control_field.name,
            "where_clause": where_clause,
            "matched_row_count": timing.matched_row_count,
            "cold_seconds": timing.cold_seconds,
            "warm_seconds": timing.warm_seconds,
        }

        return baseline, {
            "table": table_name,
            "control_field": control_field.name,
            "rows_matched": timing.matched_row_count,
            "cold_seconds": round(timing.cold_seconds, 4),
            "warm_seconds": round(timing.warm_seconds, 4),
            "status": "pass",
            "note": "",
        }

    return None, {
        "table": table_name,
        "control_field": "",
        "rows_matched": None,
        "warm_seconds": None,
        "status": "skip",
        "note": (
            "No unindexed field yielded a sampled value that matched any rows, so no "
            "control query could be timed."
        ),
    }


def _rank_control_candidates(context: ValidationContext,
                             table_name: str,
                             unindexed_fields: List[Any]) -> List[Tuple[Any, Any]]:
    """
        Orders unindexed fields by estimated cardinality, most selective first.

        Returns (field_definition, sampled_value) pairs, omitting fields from which no
        value could be sampled. Ties keep the order arcpy reported the fields in, so
        repeated runs pick the same control and stay comparable.
    """
    assessed_candidates: List[Tuple[int, int, Any, Any]] = []

    for field_position, field in enumerate(unindexed_fields[:MAXIMUM_CONTROL_CANDIDATES]):
        distinct_count, sampled_value = _estimate_field_selectivity(
            context, table_name, field.name
        )
        if sampled_value is None:
            continue
        # Negated so that a higher distinct count sorts first, with field order as the
        # deterministic tie-break.
        assessed_candidates.append((-distinct_count, field_position, field, sampled_value))

    assessed_candidates.sort(key=lambda candidate: (candidate[0], candidate[1]))

    return [(field, value) for _negated, _position, field, value in assessed_candidates]


def _baseline_for_table(context: ValidationContext, table_name: str) -> Optional[Dict[str, Any]]:
    """
        The cached baseline for a table, or None if none could be established.
    """
    baselines = context.shared_state.get("performance.baselines", {})
    return baselines.get(normalize_name(table_name))


# ----------------------------------------------------------------------------------
# performance.indexed_attribute_queries
# ----------------------------------------------------------------------------------


@register_check(
    check_id="performance.indexed_attribute_queries",
    category=CATEGORY,
    title="Indexed attribute queries are faster than a full scan",
)
def check_indexed_attribute_queries(context: ValidationContext) -> Iterator[CheckResult]:
    """
        Times a selection on each indexed field and compares it against that table's
        unindexed control.

        One result per table, so the report reads as a performance profile of the
        database rather than as one undifferentiated pass or fail.
    """
    for table_name in context.expectations.tables_with_expected_indexes():
        yield _time_indexed_fields_on_table(context, table_name)


def _time_indexed_fields_on_table(context: ValidationContext, table_name: str) -> CheckResult:
    """
        Times every indexed field on one table against its cached baseline.
    """
    check_id = f"performance.indexed_attribute_queries.{table_name}"
    title = f"Indexed query performance - {table_name}"

    baseline = _baseline_for_table(context, table_name)

    if baseline is None:
        return make_result(
            check_id=check_id, category=CATEGORY, title=title,
            status=CheckStatus.SKIP,
            summary=(
                f"Skipped: no unindexed control query could be established on "
                f"{table_name}, so there is nothing to compare indexed queries against."
            ),
            details={"table": table_name, "reason": "no_baseline"},
        )

    rows: List[Dict[str, Any]] = []
    slow_field_count = 0
    measured_count = 0

    for expected_index in context.expectations.indexes_for_table(table_name):
        row = _time_one_indexed_field(context, table_name, expected_index, baseline)
        rows.append(row)

        if row["status"] == "fail":
            slow_field_count += 1
        if row["status"] in ("pass", "fail"):
            measured_count += 1

    details = {
        "table": table_name,
        "control_field": baseline["control_field"],
        "control_rows_matched": baseline["matched_row_count"],
        "control_cold_seconds": round(baseline["cold_seconds"], 4),
        "control_warm_seconds": round(baseline["warm_seconds"], 4),
        "required_speedup": context.perf_min_speedup,
        "fields_measured": measured_count,
        "fields_too_slow": slow_field_count,
        "fields_skipped": len(rows) - measured_count,
    }

    if slow_field_count:
        status = CheckStatus.FAIL
        slow_names = [row["field"] for row in rows if row["status"] == "fail"]
        summary = (
            f"{table_name}: {slow_field_count} indexed field(s) were not at least "
            f"{context.perf_min_speedup:g}x faster than the unindexed control "
            f"({', '.join(slow_names)})."
        )
    elif measured_count == 0:
        status = CheckStatus.SKIP
        summary = (
            f"{table_name}: no indexed field produced a comparable, measurable query, "
            f"so index performance was not established."
        )
    else:
        status = CheckStatus.PASS
        summary = (
            f"{table_name}: all {measured_count} measured indexed field(s) were at "
            f"least {context.perf_min_speedup:g}x faster than the "
            f"{baseline['warm_seconds']:.2f}s unindexed control scan."
        )

    return make_result(
        check_id=check_id, category=CATEGORY, title=title,
        status=status, summary=summary, details=details, rows=rows,
    )


def _time_one_indexed_field(context: ValidationContext,
                            table_name: str,
                            expected_index: Any,
                            baseline: Dict[str, Any]) -> Dict[str, Any]:
    """
        Times one indexed field and judges it against the baseline.

        Every path that cannot produce a meaningful number returns a skip carrying its
        reason, never a pass.
    """
    field_name = expected_index.field_name
    base_row = {
        "field": field_name,
        "index": expected_index.index_name,
        "rows_matched": None,
        "cold_seconds": None,
        "warm_seconds": None,
        "control_seconds": round(baseline["warm_seconds"], 4),
        "speedup": None,
    }

    sampled_value = _sample_query_value(context, table_name, field_name)
    if sampled_value is None:
        return {**base_row, "status": "skip",
                "note": "No non-null value could be sampled from this field."}

    where_clause = _build_where_clause(field_name, sampled_value)

    try:
        timing = context.accessor.time_attribute_selection(
            context.output_gdb, table_name, where_clause
        )
    except Exception as error:
        return {**base_row, "status": "skip",
                "note": f"The timed query failed: {error}"}

    row = {
        **base_row,
        "rows_matched": timing.matched_row_count,
        "cold_seconds": round(timing.cold_seconds, 4),
        "warm_seconds": round(timing.warm_seconds, 4),
    }

    # A query matching no rows tells us nothing about the index.
    if timing.matched_row_count == 0:
        return {**row, "status": "skip",
                "note": "The sampled value matched no rows, so the timing is meaningless."}

    # A query returning a large share of the table is not a selective lookup, and a
    # full scan is the correct plan for it. Judging it would penalise the database for
    # behaving properly - see MAXIMUM_SELECTIVE_FRACTION.
    total_rows = _total_row_count(context, table_name)
    if total_rows:
        matched_fraction = timing.matched_row_count / total_rows
        row["fraction_of_table"] = round(matched_fraction, 4)

        if matched_fraction > MAXIMUM_SELECTIVE_FRACTION:
            return {
                **row, "status": "skip",
                "note": (
                    f"Matched {timing.matched_row_count:,} rows, "
                    f"{matched_fraction:.0%} of the table. A scan is the correct plan "
                    f"at that volume, so this does not test the index."
                ),
            }

    # Comparing wildly different result set sizes would measure volume, not indexing.
    count_ratio = _ratio_between(timing.matched_row_count, baseline["matched_row_count"])
    if count_ratio > MAXIMUM_RESULT_COUNT_RATIO:
        return {
            **row, "status": "skip",
            "note": (
                f"Returned {timing.matched_row_count:,} rows against the control's "
                f"{baseline['matched_row_count']:,}; too different in size for the "
                f"comparison to be meaningful."
            ),
        }

    # Below the resolution of the measurement, a ratio is noise - but an indexed query
    # that is too fast to time is self-evidently not the problem, so this passes.
    if timing.warm_seconds < MINIMUM_MEANINGFUL_SECONDS:
        return {
            **row, "status": "pass", "speedup": None,
            "note": (
                f"Returned in under {MINIMUM_MEANINGFUL_SECONDS}s, which is too fast "
                f"to measure a ratio against - comfortably indexed."
            ),
        }

    speedup = baseline["warm_seconds"] / timing.warm_seconds
    meets_threshold = speedup >= context.perf_min_speedup

    return {
        **row,
        "speedup": round(speedup, 1),
        "status": "pass" if meets_threshold else "fail",
        "note": (
            "" if meets_threshold
            else (
                f"Only {speedup:.1f}x faster than an unindexed scan, below the "
                f"required {context.perf_min_speedup:g}x. The index may not be in use."
            )
        ),
    }


def _ratio_between(first_count: int, second_count: int) -> float:
    """
        The ratio of two counts, larger over smaller, so order does not matter.
    """
    larger = max(first_count, second_count)
    smaller = min(first_count, second_count)

    if smaller <= 0:
        return float("inf")

    return larger / smaller


# ----------------------------------------------------------------------------------
# performance.spatial_queries
# ----------------------------------------------------------------------------------


@register_check(
    check_id="performance.spatial_queries",
    category=CATEGORY,
    title="Spatial queries are faster than a full scan",
)
def check_spatial_queries(context: ValidationContext) -> Iterator[CheckResult]:
    """
        Times small, medium, and large bounding box selections against each spatially
        indexed feature class.

        The boxes are derived from each feature class's own extent rather than
        hard-coded to California coordinates, so the check remains valid against a
        subset or a differently projected dataset. Measured on the real data, a
        city-sized box against 11.6M parcels returned in 0.24 seconds.
    """
    for feature_class_name in context.expectations.spatially_indexed_tables:
        yield _time_spatial_queries_on(context, feature_class_name)


def _time_spatial_queries_on(context: ValidationContext,
                             feature_class_name: str) -> CheckResult:
    """
        Times the three bounding box sizes against one feature class.
    """
    check_id = f"performance.spatial_queries.{feature_class_name}"
    title = f"Spatial query performance - {feature_class_name}"

    baseline = _baseline_for_table(context, feature_class_name)

    try:
        full_extent = context.accessor.get_extent(context.output_gdb, feature_class_name)
    except Exception as error:
        return make_result(
            check_id=check_id, category=CATEGORY, title=title,
            status=CheckStatus.SKIP,
            summary=f"Skipped: could not read the extent of {feature_class_name}: {error}",
            details={"feature_class": feature_class_name, "reason": "no_extent"},
        )

    rows: List[Dict[str, Any]] = []
    slow_count = 0
    measured_count = 0

    for box_label, extent_fraction in SPATIAL_BOX_FRACTIONS:
        row = _time_one_bounding_box(
            context, feature_class_name, full_extent, box_label, extent_fraction, baseline
        )
        rows.append(row)

        if row["status"] == "fail":
            slow_count += 1
        if row["status"] in ("pass", "fail"):
            measured_count += 1

    details = {
        "feature_class": feature_class_name,
        "full_extent": list(full_extent),
        "box_sizes_tested": [label for label, _fraction in SPATIAL_BOX_FRACTIONS],
        "required_speedup": context.perf_min_speedup,
        "boxes_measured": measured_count,
        "boxes_too_slow": slow_count,
        "control_available": baseline is not None,
    }
    if baseline is not None:
        details["control_field"] = baseline["control_field"]
        details["control_warm_seconds"] = round(baseline["warm_seconds"], 4)

    if slow_count:
        status = CheckStatus.FAIL
        slow_labels = [row["box_size"] for row in rows if row["status"] == "fail"]
        summary = (
            f"{feature_class_name}: {slow_count} bounding box selection(s) "
            f"({', '.join(slow_labels)}) were not at least "
            f"{context.perf_min_speedup:g}x faster than a full scan."
        )
    elif measured_count == 0:
        status = CheckStatus.SKIP
        summary = (
            f"{feature_class_name}: no bounding box produced a measurable, comparable "
            f"result, so spatial index performance was not established."
        )
    else:
        status = CheckStatus.PASS
        summary = (
            f"{feature_class_name}: all {measured_count} measured bounding box "
            f"selection(s) met the {context.perf_min_speedup:g}x speed requirement."
        )

    return make_result(
        check_id=check_id, category=CATEGORY, title=title,
        status=status, summary=summary, details=details, rows=rows,
    )


def _time_one_bounding_box(context: ValidationContext,
                           feature_class_name: str,
                           full_extent: Tuple[float, float, float, float],
                           box_label: str,
                           extent_fraction: float,
                           baseline: Optional[Dict[str, Any]]) -> Dict[str, Any]:
    """
        Times one centred bounding box covering a fraction of the full extent.
    """
    box_extent = _centred_sub_extent(full_extent, extent_fraction)

    base_row = {
        "box_size": box_label,
        "extent_fraction": extent_fraction,
        "rows_matched": None,
        "cold_seconds": None,
        "warm_seconds": None,
        "control_seconds": round(baseline["warm_seconds"], 4) if baseline else None,
        "speedup": None,
    }

    try:
        timing = context.accessor.time_spatial_selection(
            context.output_gdb, feature_class_name, box_extent
        )
    except Exception as error:
        return {**base_row, "status": "skip", "note": f"The spatial query failed: {error}"}

    row = {
        **base_row,
        "rows_matched": timing.matched_row_count,
        "cold_seconds": round(timing.cold_seconds, 4),
        "warm_seconds": round(timing.warm_seconds, 4),
    }

    if timing.matched_row_count == 0:
        return {**row, "status": "skip",
                "note": "The bounding box intersected no features, so the timing is meaningless."}

    # The same selectivity rule as for attribute queries. The "large" box covers a third
    # of California and intersects most of the dataset; at that point the spatial index
    # cannot help and a scan is correct, so the timing is recorded but not judged.
    total_rows = _total_row_count(context, feature_class_name)
    if total_rows:
        matched_fraction = timing.matched_row_count / total_rows
        row["fraction_of_table"] = round(matched_fraction, 4)

        if matched_fraction > MAXIMUM_SELECTIVE_FRACTION:
            return {
                **row, "status": "skip",
                "note": (
                    f"The box intersected {timing.matched_row_count:,} features, "
                    f"{matched_fraction:.0%} of the feature class. A scan is the "
                    f"correct plan at that volume, so this does not test the index."
                ),
            }

    # With no control there is still a useful timing to publish, but no verdict to
    # reach. Reporting the number without a pass is more honest than inventing one.
    if baseline is None:
        return {**row, "status": "skip",
                "note": "Timing recorded, but no unindexed control exists to compare against."}

    if timing.warm_seconds < MINIMUM_MEANINGFUL_SECONDS:
        return {**row, "status": "pass",
                "note": (
                    f"Returned in under {MINIMUM_MEANINGFUL_SECONDS}s, too fast to "
                    f"measure a ratio against - comfortably indexed."
                )}

    speedup = baseline["warm_seconds"] / timing.warm_seconds
    meets_threshold = speedup >= context.perf_min_speedup

    return {
        **row,
        "speedup": round(speedup, 1),
        "status": "pass" if meets_threshold else "fail",
        "note": (
            "" if meets_threshold
            else (
                f"Only {speedup:.1f}x faster than a full scan, below the required "
                f"{context.perf_min_speedup:g}x. The spatial index may not be in use."
            )
        ),
    }


def _centred_sub_extent(full_extent: Tuple[float, float, float, float],
                        extent_fraction: float) -> Tuple[float, float, float, float]:
    """
        A box covering `extent_fraction` of the full width and height, centred on it.

        Centring matters: a box anchored at a corner of a statewide extent would often
        land in the ocean and intersect nothing.
    """
    x_min, y_min, x_max, y_max = full_extent

    centre_x = (x_min + x_max) / 2.0
    centre_y = (y_min + y_max) / 2.0
    half_width = (x_max - x_min) * extent_fraction / 2.0
    half_height = (y_max - y_min) * extent_fraction / 2.0

    return (
        centre_x - half_width,
        centre_y - half_height,
        centre_x + half_width,
        centre_y + half_height,
    )
