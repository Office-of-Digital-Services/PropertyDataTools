"""
    Data quality tests against the real geodatabases.

    Every other validation test module uses a fake accessor and proves the *engine*
    works. This one runs the engine against the actual delivery and asks whether the
    *data* is sound - so each check in the suite surfaces as an ordinary named pytest
    failure alongside the HTML report.

    The expensive work happens once. A session-scoped fixture performs a single
    run_validation and the tests then parametrise over its results, so adding checks
    costs no additional geodatabase access.

    Running these:

        # default: three counties, a few minutes
        pytest tests/test_validation_data_quality.py

        # skip them entirely for a fast unit-only run
        pytest -m "not data_quality"

        # the full statewide sweep, roughly 10-20 minutes
        set VALIDATION_FULL_SWEEP=1 && pytest tests/test_validation_data_quality.py

    Every operation is read-only; these tests cannot modify the geodatabase.
"""

import os

import pytest

from tests import config

# arcpy is required to reach the real data. Skipping the whole module cleanly is far
# better than a collection error on a machine without ArcGIS Pro.
arcpy = pytest.importorskip("arcpy", reason="The data quality tests require arcpy.")

from unbox.validation import build_context, run_validation
from unbox.validation.models import CheckStatus

# Lets the whole module be deselected with -m "not data_quality".
pytestmark = pytest.mark.data_quality

# Set VALIDATION_FULL_SWEEP=1 to validate all 58 counties instead of the sample.
FULL_SWEEP_ENVIRONMENT_VARIABLE = "VALIDATION_FULL_SWEEP"


def _should_run_full_sweep() -> bool:
    return os.environ.get(FULL_SWEEP_ENVIRONMENT_VARIABLE, "").strip().lower() in (
        "1", "true", "yes"
    )


def _skip_unless_delivery_is_present() -> None:
    """
        Skips rather than fails when the delivery is not on this machine.

        These tests depend on a 100+ GB delivery that will not exist on every developer
        machine or build agent. A missing delivery is an absent prerequisite, not a
        data defect, and conflating the two would make the suite untrustworthy.
    """
    if not os.path.isdir(config.DELIVERY_2026_10_OUTPUT_GDB):
        pytest.skip(
            f"The merged output geodatabase is not present at "
            f"{config.DELIVERY_2026_10_OUTPUT_GDB}; see tests/config.py."
        )

    if not os.path.isdir(config.DELIVERY_2026_10_SOURCE_FOLDER):
        pytest.skip(
            f"The per-county source folder is not present at "
            f"{config.DELIVERY_2026_10_SOURCE_FOLDER}; see tests/config.py."
        )


# The single cached run, shared by the collection hook and every fixture below.
# Without this the parametrising hook and the session fixture would each trigger their
# own sweep, doubling a run that already reads tens of millions of rows.
_cached_validation_run = None


def _get_validation_run():
    """
        Performs the validation once per process and returns the cached result.
    """
    global _cached_validation_run

    if _cached_validation_run is None:
        counties = () if _should_run_full_sweep() else config.DELIVERY_2026_10_SAMPLE_COUNTIES

        context = build_context(
            output_gdb=config.DELIVERY_2026_10_OUTPUT_GDB,
            source_folder=config.DELIVERY_2026_10_SOURCE_FOLDER,
            base_fips=config.DELIVERY_2026_10_BASE_FIPS,
            counties=counties,
        )
        _cached_validation_run = run_validation(context)

    return _cached_validation_run


@pytest.fixture(scope="session")
def validation_run():
    """
        The one validation run for this session.

        Session scope plus the module-level cache is what makes this practical: the
        sweep reads tens of millions of rows, and repeating it per test would take
        hours.
    """
    _skip_unless_delivery_is_present()
    return _get_validation_run()


@pytest.fixture(scope="session")
def results_by_check_id(validation_run):
    """
        The run's results keyed by check id, for tests that target one specific check.
    """
    return {result.check_id: result for result in validation_run.results}


def _result_identifier(result) -> str:
    """
        Gives each parametrised case the check id as its pytest node name, so a failure
        reads as `test_check_does_not_fail[counts.per_county_theme.Parcels]`.
    """
    return result.check_id


def pytest_generate_tests(metafunc):
    """
        Parametrises over the real CheckResult list.

        The run happens here, at collection time, so every check becomes its own named
        test rather than being hidden inside one monolithic assertion.
    """
    if "check_result" not in metafunc.fixturenames:
        return

    # Collection cannot be skipped the way a test can, so when the delivery is absent
    # we parametrise with nothing and let the other tests report the skip.
    if not (os.path.isdir(config.DELIVERY_2026_10_OUTPUT_GDB)
            and os.path.isdir(config.DELIVERY_2026_10_SOURCE_FOLDER)):
        metafunc.parametrize("check_result", [])
        return

    run = _get_validation_run()
    metafunc.parametrize("check_result", run.results, ids=_result_identifier)


# ----------------------------------------------------------------------
# One test per check
# ----------------------------------------------------------------------


def test_check_does_not_fail(check_result):
    """
        Every individual check must not FAIL or ERROR.

        SKIP and WARN pass here deliberately. A SKIP means the check could not gather
        evidence - no source folder, a query too non-selective to judge - and reporting
        that as a test failure would train people to ignore the suite.
    """
    assert check_result.status not in (CheckStatus.FAIL, CheckStatus.ERROR), (
        f"{check_result.check_id} returned {check_result.status.value.upper()}: "
        f"{check_result.summary}\n"
        f"details: {check_result.details}"
    )


# ----------------------------------------------------------------------
# Whole-run assertions
# ----------------------------------------------------------------------


def test_the_run_executed_a_meaningful_number_of_checks(validation_run):
    """
        Guards against a silently empty run. A suite that checks nothing passes
        everything, which is the most dangerous state this module could be in.
    """
    assert len(validation_run.results) >= 20, (
        f"Only {len(validation_run.results)} checks ran, which suggests the registry "
        f"was not fully populated."
    )


def test_all_four_check_categories_ran(validation_run):
    """
        Counts, schema, integrity and performance must each contribute results.
    """
    categories_present = set(validation_run.by_category().keys())

    assert categories_present == {"counts", "schema", "integrity", "performance"}, (
        f"Expected all four check categories to run, got {sorted(categories_present)}."
    )


def test_no_check_errored(validation_run):
    """
        An ERROR means a check raised and reached no verdict, which is a defect in the
        validation module itself rather than in the data.
    """
    errored = [
        result for result in validation_run.results
        if result.status == CheckStatus.ERROR
    ]

    assert not errored, (
        "These checks raised an exception:\n"
        + "\n".join(
            f"  {result.check_id}: {result.details.get('traceback', '')[:500]}"
            for result in errored
        )
    )


def test_the_overall_run_is_not_a_failure(validation_run):
    """
        The headline assertion: the merged geodatabase is sound.
    """
    assert validation_run.overall_status != CheckStatus.FAIL, (
        "The validation run failed overall. Failing checks:\n"
        + "\n".join(
            f"  {result.check_id}: {result.summary}"
            for result in validation_run.results if result.is_failure
        )
    )


# ----------------------------------------------------------------------
# Specific data quality facts worth asserting by name
# ----------------------------------------------------------------------


def test_every_county_is_present_in_the_output(results_by_check_id):
    """
        No county was dropped from the merge.
    """
    result = results_by_check_id.get("counts.county_coverage")
    assert result is not None, "The county coverage check did not run."

    assert result.status in (CheckStatus.PASS, CheckStatus.WARN), result.summary
    assert result.details.get("missing_from_output") == [], (
        f"Counties missing from the output: {result.details.get('missing_from_output')}"
    )
    assert result.details.get("unexpected_in_output") == [], (
        f"Unexpected counties in the output: {result.details.get('unexpected_in_output')}"
    )


def test_no_output_rows_lack_a_county_code(results_by_check_id):
    """
        Every row must be attributable to a county.
    """
    result = results_by_check_id.get("counts.county_coverage")
    assert result is not None

    assert result.details.get("rows_with_null_county", 0) == 0, (
        f"{result.details.get('rows_with_null_county')} output rows have no FIPS_CODE."
    )


def test_every_declared_index_exists(results_by_check_id):
    """
        All 49 declared attribute indexes survived the merge's drop-and-recreate cycle.
    """
    result = results_by_check_id.get("schema.attribute_indexes")
    assert result is not None, "The attribute index check did not run."

    assert result.details.get("indexes_missing") == 0, result.summary
    assert result.details.get("indexes_expected") >= 49, (
        f"Expected at least 49 declared indexes, found "
        f"{result.details.get('indexes_expected')}."
    )


def test_every_spatially_indexed_feature_class_has_its_spatial_index(results_by_check_id):
    """
        Without FDO_SHAPE, map panning and spatial selection degrade to a full scan.
    """
    result = results_by_check_id.get("schema.spatial_indexes")
    assert result is not None, "The spatial index check did not run."

    assert result.status == CheckStatus.PASS, result.summary
    assert result.details.get("spatial_indexes_missing") == 0


def test_all_eight_relationship_classes_are_correctly_shaped(results_by_check_id):
    """
        Clicking a parcel in ArcGIS Pro and getting its assessments depends on these.
    """
    result = results_by_check_id.get("schema.relationship_classes")
    assert result is not None, "The relationship class check did not run."

    assert result.status == CheckStatus.PASS, result.summary
    assert result.details.get("relationships_expected") == 8
    assert result.details.get("relationships_with_problems") == 0


def test_relationship_keys_resolve_without_orphans(results_by_check_id):
    """
        Sampled keys in every many-to-many relation point at rows that exist.
    """
    orphan_results = [
        result for check_id, result in results_by_check_id.items()
        if check_id.startswith("integrity.relationship_orphans.")
    ]

    assert orphan_results, "No relationship orphan checks ran."

    for result in orphan_results:
        assert result.details.get("total_orphans", 0) == 0, (
            f"{result.check_id}: {result.summary}"
        )


def test_parcel_primary_keys_resolve(results_by_check_id):
    """
        Parcels.PRIMARY_ASSESSMENT_LID and PRIMARY_BUILDING_LID resolve where set.
    """
    result = results_by_check_id.get("integrity.primary_key_resolution")
    assert result is not None, "The primary key resolution check did not run."

    assert result.details.get("total_orphans", 0) == 0, result.summary


def test_indexed_queries_are_measurably_faster_than_a_full_scan(results_by_check_id):
    """
        At least one table must demonstrate a real, measured speedup.

        This is deliberately a floor rather than a per-field assertion. Many indexed
        fields are legitimately non-selective - DELIVERY_FLAG matches 62% of Addresses,
        where a scan is the correct plan - and those are reported as SKIP. What must
        not happen is the performance category passing without measuring anything at
        all.
    """
    performance_results = [
        result for check_id, result in results_by_check_id.items()
        if check_id.startswith("performance.indexed_attribute_queries.")
    ]

    assert performance_results, "No indexed attribute query checks ran."

    total_fields_measured = sum(
        result.details.get("fields_measured", 0) for result in performance_results
    )
    assert total_fields_measured > 0, (
        "No indexed field produced a measurable, comparable timing, so index "
        "performance was never actually demonstrated."
    )

    for result in performance_results:
        assert result.details.get("fields_too_slow", 0) == 0, result.summary


def test_a_measured_index_delivers_a_substantial_speedup(results_by_check_id):
    """
        Asserts the headline performance claim with a real number.

        Measured on this delivery: ASSESSMENT_LID 120x, PARCEL_LID 122x, address_lid
        69x. Requiring 10x of the best observed speedup leaves ample headroom for a
        slower machine while still failing if indexing genuinely regressed.
    """
    observed_speedups = []

    for check_id, result in results_by_check_id.items():
        if not check_id.startswith("performance.indexed_attribute_queries."):
            continue
        for row in result.rows:
            if row.get("speedup") is not None:
                observed_speedups.append(row["speedup"])

    assert observed_speedups, "No speedup ratio was computed anywhere in the run."

    best_speedup = max(observed_speedups)
    assert best_speedup >= 10.0, (
        f"The best observed indexed query speedup was only {best_speedup:.1f}x. "
        f"Indexed lookups previously measured over 100x faster than a full scan."
    )
