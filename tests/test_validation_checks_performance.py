"""
    Unit tests for the query performance checks.

    These matter more than they might appear. A performance check has an unusually easy
    failure mode: it can report PASS because it never actually measured anything - no
    control field, a query matching zero rows, two result sets of wildly different
    sizes. Each of those must produce a SKIP carrying its reason.

    Timings are scripted through the fake accessor, so the tests are deterministic and
    run in milliseconds rather than depending on real disk speed.
"""

import pytest

from unbox.validation.checks.performance import (
    MAXIMUM_RESULT_COUNT_RATIO,
    SPATIAL_BOX_FRACTIONS,
    _centred_sub_extent,
    check_establish_scan_baseline,
    check_indexed_attribute_queries,
    check_spatial_queries,
)
from unbox.validation.expectations import ExpectedIndex, MergeExpectations
from unbox.validation.models import CheckStatus, ValidationContext
from tests.fakes.fake_gdb_accessor import (
    FakeDataset,
    FakeFieldDefinition,
    FakeGeodatabaseAccessor,
    FakeTimedQueryResult,
)

OUTPUT_GDB = r"C:\fake\full_state.gdb"
TABLE = "Addresses"

# The indexed field under test and the field used as the unindexed control. These
# mirror the real measurement: CITY is indexed, STREET_NAME is not.
INDEXED_FIELD = "CITY"
CONTROL_FIELD = "STREET_NAME"

# Timings taken from the real geodatabase. The 103x gap is what makes a threshold of
# 5.0 a conservative choice.
REAL_INDEXED_SECONDS = 0.11
REAL_CONTROL_SECONDS = 11.34
REAL_INDEXED_ROWS = 63_039
REAL_CONTROL_ROWS = 33_494

# The table is large enough that the default query volumes stay comfortably inside the
# selectivity limit, so the fixtures exercise the ratio logic rather than the guard.
TABLE_ROW_COUNT = 26_755_686


def build_expectations(expected_indexes=None,
                       spatially_indexed_tables=()) -> MergeExpectations:
    if expected_indexes is None:
        expected_indexes = (
            ExpectedIndex(TABLE, INDEXED_FIELD, f"idx_{INDEXED_FIELD}", "ATTRIBUTE_INDEXES"),
        )

    return MergeExpectations(
        core_theme_names=(TABLE,),
        relation_table_names=(),
        expected_indexes=expected_indexes,
        spatially_indexed_tables=spatially_indexed_tables,
        manytomany_relationships=(),
        onetomany_relationships=(),
    )


def build_context(accessor, expectations=None, perf_min_speedup=5.0) -> ValidationContext:
    return ValidationContext(
        output_gdb=OUTPUT_GDB,
        accessor=accessor,
        expectations=expectations or build_expectations(),
        perf_min_speedup=perf_min_speedup,
    )


def build_accessor(indexed_seconds=REAL_INDEXED_SECONDS,
                   control_seconds=REAL_CONTROL_SECONDS,
                   indexed_rows=REAL_INDEXED_ROWS,
                   control_rows=REAL_CONTROL_ROWS,
                   fields=None,
                   column_values=None) -> FakeGeodatabaseAccessor:
    """
        A fake holding one table with an indexed and an unindexed field, and scripted
        timings for a query against each.
    """
    accessor = FakeGeodatabaseAccessor()

    if fields is None:
        fields = [
            FakeFieldDefinition("OBJECTID", "OID", 4, is_nullable=False),
            FakeFieldDefinition(INDEXED_FIELD, "String", 50),
            FakeFieldDefinition(CONTROL_FIELD, "String", 50),
        ]

    if column_values is None:
        column_values = {
            INDEXED_FIELD: ["DAVIS"] * 10,
            CONTROL_FIELD: ["OAK"] * 10,
        }

    accessor.add_dataset(
        OUTPUT_GDB, TABLE,
        FakeDataset(data_type="FeatureClass", fields=fields,
                    row_count=TABLE_ROW_COUNT,
                    column_values=column_values,
                    extent=(-124.0, 32.0, -114.0, 42.0)),
    )

    accessor.set_timed_result(
        OUTPUT_GDB, TABLE, f"{CONTROL_FIELD} = 'OAK'",
        FakeTimedQueryResult(f"{CONTROL_FIELD} = 'OAK'", control_rows,
                             control_seconds * 1.2, control_seconds),
    )
    accessor.set_timed_result(
        OUTPUT_GDB, TABLE, f"{INDEXED_FIELD} = 'DAVIS'",
        FakeTimedQueryResult(f"{INDEXED_FIELD} = 'DAVIS'", indexed_rows,
                             indexed_seconds * 1.5, indexed_seconds),
    )

    return accessor


def run_baseline_then_indexed(context):
    """
        The two checks in their real order: the baseline publishes to shared state, and
        the indexed check consumes it.
    """
    baseline_result = check_establish_scan_baseline(context)
    indexed_results = list(check_indexed_attribute_queries(context))
    return baseline_result, indexed_results


# ----------------------------------------------------------------------
# performance.establish_scan_baseline
# ----------------------------------------------------------------------


def test_baseline_selects_an_unindexed_field_and_times_it():
    context = build_context(build_accessor())

    result = check_establish_scan_baseline(context)

    assert result.status == CheckStatus.PASS
    assert result.details["baselines_established"] == 1
    assert result.rows[0]["control_field"] == CONTROL_FIELD
    assert result.rows[0]["rows_matched"] == REAL_CONTROL_ROWS


def test_baseline_prefers_a_high_cardinality_control_field():
    """
        Regression test for a defect found only against the real data.

        The first implementation took the first unindexed field in field order, which on
        Addresses was STATE - a constant matching all 26,755,686 rows. That control
        measured the cost of returning 26 million rows rather than of scanning for them,
        making every comparison against it meaningless.

        The control must therefore be the most selective unindexed field available.
    """
    fields = [
        FakeFieldDefinition("OBJECTID", "OID", 4, is_nullable=False),
        FakeFieldDefinition(INDEXED_FIELD, "String", 50),
        # Appears first among the unindexed fields, but holds a single repeated value.
        FakeFieldDefinition("STATE", "String", 2),
        FakeFieldDefinition(CONTROL_FIELD, "String", 50),
    ]
    column_values = {
        INDEXED_FIELD: ["DAVIS"] * 20,
        "STATE": ["CA"] * 20,                                  # one distinct value
        CONTROL_FIELD: [f"STREET {index}" for index in range(20)],  # twenty distinct
    }

    accessor = build_accessor(fields=fields, column_values=column_values)
    accessor.set_timed_result(
        OUTPUT_GDB, TABLE, "STATE = 'CA'",
        FakeTimedQueryResult("STATE = 'CA'", TABLE_ROW_COUNT, 14.0, 13.68),
    )
    accessor.set_timed_result(
        OUTPUT_GDB, TABLE, f"{CONTROL_FIELD} = 'STREET 0'",
        FakeTimedQueryResult(f"{CONTROL_FIELD} = 'STREET 0'", 1, 12.0, 11.34),
    )

    context = build_context(accessor)
    result = check_establish_scan_baseline(context)

    assert result.rows[0]["control_field"] == CONTROL_FIELD
    assert result.rows[0]["rows_matched"] == 1
    assert result.rows[0]["control_field"] != "STATE"


def test_baseline_never_selects_an_indexed_field_as_the_control():
    """
        A control that is itself indexed would make every ratio approximately 1.0 and
        fail the whole database for no reason.
    """
    context = build_context(build_accessor())

    check_establish_scan_baseline(context)
    baseline = context.shared_state["performance.baselines"]["addresses"]

    indexed_fields = context.expectations.indexed_field_names_for_table(TABLE)
    assert baseline["control_field"].lower() not in indexed_fields


def test_baseline_records_both_cold_and_warm_timings():
    """
        The first touch of a large table spends time on cache warming that says nothing
        about indexing, so both numbers are reported and the ratio uses the warm one.
    """
    context = build_context(build_accessor())

    result = check_establish_scan_baseline(context)

    assert result.rows[0]["cold_seconds"] > result.rows[0]["warm_seconds"]
    assert result.rows[0]["warm_seconds"] == pytest.approx(REAL_CONTROL_SECONDS)


def test_baseline_skips_when_every_sampleable_field_is_indexed():
    """
        With no unindexed field there is no control, and without a control no ratio can
        be computed. That must be a SKIP, never a PASS.
    """
    expectations = build_expectations(
        expected_indexes=(
            ExpectedIndex(TABLE, INDEXED_FIELD, f"idx_{INDEXED_FIELD}", "ATTRIBUTE_INDEXES"),
            ExpectedIndex(TABLE, CONTROL_FIELD, f"idx_{CONTROL_FIELD}", "ATTRIBUTE_INDEXES"),
        )
    )
    context = build_context(build_accessor(), expectations)

    result = check_establish_scan_baseline(context)

    assert result.status == CheckStatus.SKIP
    assert result.details["baselines_established"] == 0
    assert "Every sampleable field on this table is indexed" in result.rows[0]["note"]


def test_baseline_skips_a_control_field_holding_only_nulls():
    """
        An entirely null field is unindexed but useless - no value can be sampled from
        it, so no query can be timed.
    """
    accessor = build_accessor(column_values={
        INDEXED_FIELD: ["DAVIS"] * 10,
        CONTROL_FIELD: [None] * 10,
    })
    context = build_context(accessor)

    result = check_establish_scan_baseline(context)

    assert result.status == CheckStatus.SKIP
    assert "no control query could be timed" in result.rows[0]["note"]


def test_baseline_skips_when_the_control_query_matches_nothing():
    """
        A zero-row control carries no timing information. Observed for real:
        CITY = 'Davis' returns 0 rows while 'DAVIS' returns 63,039.
    """
    accessor = build_accessor(control_rows=0)
    context = build_context(accessor)

    result = check_establish_scan_baseline(context)

    assert result.status == CheckStatus.SKIP
    assert result.details["baselines_established"] == 0


# ----------------------------------------------------------------------
# performance.indexed_attribute_queries
# ----------------------------------------------------------------------


def test_a_fast_indexed_query_passes():
    """
        The real measurement: 0.11s indexed against 11.34s unindexed is a 103x speedup,
        comfortably above the 5.0 threshold.
    """
    context = build_context(build_accessor())

    _baseline, results = run_baseline_then_indexed(context)
    result = results[0]

    assert result.status == CheckStatus.PASS
    assert result.details["fields_measured"] == 1
    assert result.rows[0]["speedup"] == pytest.approx(103.1, abs=0.2)


def test_a_slow_indexed_query_fails():
    """
        9.0s against an 11.0s control is a ratio of 1.2 - the index is not doing its
        job, and the check must say so.
    """
    accessor = build_accessor(
        indexed_seconds=9.0, control_seconds=11.0,
        indexed_rows=50_000, control_rows=55_000,
    )
    context = build_context(accessor)

    _baseline, results = run_baseline_then_indexed(context)
    result = results[0]

    assert result.status == CheckStatus.FAIL
    assert result.details["fields_too_slow"] == 1
    assert result.rows[0]["speedup"] == pytest.approx(1.2, abs=0.05)
    assert "may not be in use" in result.rows[0]["note"]


def test_the_threshold_is_configurable():
    """
        A 3x speedup passes at a threshold of 2.0 and fails at 5.0.
    """
    accessor = build_accessor(
        indexed_seconds=1.0, control_seconds=3.0,
        indexed_rows=50_000, control_rows=55_000,
    )

    lenient_context = build_context(accessor, perf_min_speedup=2.0)
    _baseline, lenient_results = run_baseline_then_indexed(lenient_context)
    assert lenient_results[0].status == CheckStatus.PASS

    strict_context = build_context(build_accessor(
        indexed_seconds=1.0, control_seconds=3.0,
        indexed_rows=50_000, control_rows=55_000,
    ), perf_min_speedup=5.0)
    _baseline, strict_results = run_baseline_then_indexed(strict_context)
    assert strict_results[0].status == CheckStatus.FAIL


def test_indexed_queries_skip_without_a_baseline():
    """
        No control means no comparison. Reporting PASS here would be the exact false
        positive this design exists to avoid.
    """
    context = build_context(build_accessor())
    # The baseline check is deliberately not run, so shared state holds no baseline.

    results = list(check_indexed_attribute_queries(context))

    assert results[0].status == CheckStatus.SKIP
    assert results[0].details["reason"] == "no_baseline"


def test_a_zero_row_indexed_query_skips_rather_than_passing():
    """
        A query matching nothing returns almost instantly, which would look like a
        spectacular speedup. It means nothing and must not pass.
    """
    accessor = build_accessor(indexed_rows=0, indexed_seconds=0.002)
    context = build_context(accessor)

    _baseline, results = run_baseline_then_indexed(context)

    assert results[0].status == CheckStatus.SKIP
    assert results[0].rows[0]["status"] == "skip"
    assert "matched no rows" in results[0].rows[0]["note"]


def test_mismatched_result_set_sizes_skip_rather_than_comparing():
    """
        Timing a 3-row query against a 33,000-row control measures result set size, not
        index effectiveness.
    """
    accessor = build_accessor(
        indexed_rows=3,                       # vastly smaller than the control
        control_rows=REAL_CONTROL_ROWS,
        indexed_seconds=0.05,
    )
    context = build_context(accessor)

    _baseline, results = run_baseline_then_indexed(context)
    row = results[0].rows[0]

    assert results[0].status == CheckStatus.SKIP
    assert row["status"] == "skip"
    assert "too different in size" in row["note"]

    # The guard is the documented order-of-magnitude rule.
    assert REAL_CONTROL_ROWS / 3 > MAXIMUM_RESULT_COUNT_RATIO


def test_a_non_selective_query_is_reported_but_not_judged():
    """
        Regression test for the second defect found against the real data.

        Addresses.RESBUS_USPS is indexed, but an equality query on it returns
        15,438,094 of 26,755,686 rows - 58% of the table. At that volume a full scan is
        the correct plan and an index cannot improve on it, so the original check failed
        the database for behaving properly.

        The timing is still recorded, because it is useful; it simply is not judged.
    """
    non_selective_rows = int(TABLE_ROW_COUNT * 0.58)

    accessor = build_accessor(
        indexed_rows=non_selective_rows,
        indexed_seconds=10.49,
        control_rows=non_selective_rows,   # keeps the size-ratio guard out of the way
        control_seconds=13.68,
    )
    context = build_context(accessor)

    _baseline, results = run_baseline_then_indexed(context)
    row = results[0].rows[0]

    assert row["status"] == "skip"
    assert "A scan is the correct plan" in row["note"]
    assert row["fraction_of_table"] == pytest.approx(0.58, abs=0.01)

    # The measurement is still published even though no verdict was reached.
    assert row["warm_seconds"] == pytest.approx(10.49)
    assert row["rows_matched"] == non_selective_rows

    # And it must not be counted as a failure.
    assert results[0].status != CheckStatus.FAIL


def test_a_selective_query_is_still_judged_normally():
    """
        The selectivity guard must stay narrow. A query returning a small share of the
        table is exactly what an index is for, and it is still held to the threshold.
    """
    selective_rows = int(TABLE_ROW_COUNT * 0.001)

    accessor = build_accessor(
        indexed_rows=selective_rows,
        indexed_seconds=9.0,
        control_rows=selective_rows,
        control_seconds=11.0,
    )
    context = build_context(accessor)

    _baseline, results = run_baseline_then_indexed(context)
    row = results[0].rows[0]

    assert row["status"] == "fail"
    assert row["speedup"] == pytest.approx(1.2, abs=0.05)


def test_both_cold_and_warm_timings_appear_in_every_row():
    """
        The report doubles as a performance log, so the absolute numbers are recorded
        whatever the verdict.
    """
    context = build_context(build_accessor())

    _baseline, results = run_baseline_then_indexed(context)
    row = results[0].rows[0]

    assert row["cold_seconds"] == pytest.approx(REAL_INDEXED_SECONDS * 1.5)
    assert row["warm_seconds"] == pytest.approx(REAL_INDEXED_SECONDS)
    assert row["rows_matched"] == REAL_INDEXED_ROWS
    assert row["control_seconds"] == pytest.approx(REAL_CONTROL_SECONDS)


def test_an_unsampleable_indexed_field_skips():
    accessor = build_accessor(column_values={
        INDEXED_FIELD: [None] * 10,
        CONTROL_FIELD: ["OAK"] * 10,
    })
    context = build_context(accessor)

    _baseline, results = run_baseline_then_indexed(context)

    assert results[0].status == CheckStatus.SKIP
    assert "No non-null value could be sampled" in results[0].rows[0]["note"]


def test_an_immeasurably_fast_query_passes_without_a_ratio():
    """
        Below a millisecond the measurement is dominated by overhead, so no ratio is
        reported - but a query that fast is self-evidently not a performance problem.
    """
    accessor = build_accessor(indexed_seconds=0.0002, indexed_rows=40_000)
    context = build_context(accessor)

    _baseline, results = run_baseline_then_indexed(context)
    row = results[0].rows[0]

    assert results[0].status == CheckStatus.PASS
    assert row["status"] == "pass"
    assert row["speedup"] is None
    assert "too fast to measure" in row["note"]


# ----------------------------------------------------------------------
# performance.spatial_queries
# ----------------------------------------------------------------------


def test_spatial_queries_time_three_box_sizes():
    accessor = build_accessor()
    accessor.set_spatial_timed_result(
        OUTPUT_GDB, TABLE, FakeTimedQueryResult(None, 14_894, 0.4, 0.24)
    )
    context = build_context(accessor, build_expectations(spatially_indexed_tables=(TABLE,)))

    check_establish_scan_baseline(context)
    results = list(check_spatial_queries(context))

    assert len(results) == 1
    assert len(results[0].rows) == len(SPATIAL_BOX_FRACTIONS)
    assert [row["box_size"] for row in results[0].rows] == ["small", "medium", "large"]


def test_a_fast_spatial_query_passes():
    """
        The real measurement: a city-sized box on 11.6M parcels returned 14,894 rows in
        0.24 seconds.
    """
    accessor = build_accessor()
    accessor.set_spatial_timed_result(
        OUTPUT_GDB, TABLE, FakeTimedQueryResult(None, 14_894, 0.4, 0.24)
    )
    context = build_context(accessor, build_expectations(spatially_indexed_tables=(TABLE,)))

    check_establish_scan_baseline(context)
    results = list(check_spatial_queries(context))

    assert results[0].status == CheckStatus.PASS
    assert results[0].details["boxes_measured"] == 3
    assert results[0].rows[0]["speedup"] == pytest.approx(47.25, abs=0.1)


def test_a_slow_spatial_query_fails():
    accessor = build_accessor()
    accessor.set_spatial_timed_result(
        OUTPUT_GDB, TABLE, FakeTimedQueryResult(None, 14_894, 10.0, 9.5)
    )
    context = build_context(accessor, build_expectations(spatially_indexed_tables=(TABLE,)))

    check_establish_scan_baseline(context)
    results = list(check_spatial_queries(context))

    assert results[0].status == CheckStatus.FAIL
    assert results[0].details["boxes_too_slow"] == 3
    assert "spatial index may not be in use" in results[0].rows[0]["note"]


def test_a_spatial_query_matching_nothing_skips():
    accessor = build_accessor()
    accessor.set_spatial_timed_result(
        OUTPUT_GDB, TABLE, FakeTimedQueryResult(None, 0, 0.1, 0.05)
    )
    context = build_context(accessor, build_expectations(spatially_indexed_tables=(TABLE,)))

    check_establish_scan_baseline(context)
    results = list(check_spatial_queries(context))

    assert results[0].status == CheckStatus.SKIP
    assert "intersected no features" in results[0].rows[0]["note"]


def test_spatial_timings_are_still_reported_without_a_control():
    """
        Without a baseline the timing is still worth publishing, but no verdict can be
        reached - so the number appears and the status is SKIP.
    """
    accessor = build_accessor()
    accessor.set_spatial_timed_result(
        OUTPUT_GDB, TABLE, FakeTimedQueryResult(None, 14_894, 0.4, 0.24)
    )
    context = build_context(accessor, build_expectations(spatially_indexed_tables=(TABLE,)))
    # No baseline check is run.

    results = list(check_spatial_queries(context))
    row = results[0].rows[0]

    assert results[0].status == CheckStatus.SKIP
    assert row["warm_seconds"] == pytest.approx(0.24)
    assert row["rows_matched"] == 14_894
    assert "no unindexed control exists" in row["note"]


# ----------------------------------------------------------------------
# Bounding box geometry
# ----------------------------------------------------------------------


def test_sub_extent_is_centred_on_the_full_extent():
    """
        A box anchored at a corner of California's extent would frequently land in the
        Pacific and intersect nothing, so the boxes are centred.
    """
    full_extent = (-124.0, 32.0, -114.0, 42.0)

    small_box = _centred_sub_extent(full_extent, 0.1)
    x_min, y_min, x_max, y_max = small_box

    assert (x_min + x_max) / 2 == pytest.approx(-119.0)   # centre of the full extent
    assert (y_min + y_max) / 2 == pytest.approx(37.0)
    assert (x_max - x_min) == pytest.approx(1.0)          # 10% of a 10 degree width
    assert (y_max - y_min) == pytest.approx(1.0)


def test_larger_fractions_produce_strictly_larger_boxes():
    full_extent = (-124.0, 32.0, -114.0, 42.0)

    widths = [
        _centred_sub_extent(full_extent, fraction)[2] - _centred_sub_extent(full_extent, fraction)[0]
        for _label, fraction in SPATIAL_BOX_FRACTIONS
    ]

    assert widths == sorted(widths)
    assert len(set(widths)) == len(widths)
