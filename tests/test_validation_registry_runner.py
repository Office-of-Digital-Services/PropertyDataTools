"""
    Unit tests for the check registry and the runner.

    The runner's contract is the part of the engine most worth pinning down: a check
    that raises must not take the rest of a twenty-minute run down with it, a duplicate
    check id must be caught loudly, and the overall verdict must be the worst individual
    verdict and not an average.
"""

import datetime

import pytest

from unbox.validation.models import (
    CheckResult,
    CheckStatus,
    ValidationContext,
    ValidationRun,
    make_result,
)
from unbox.validation.registry import (
    CheckDescriptor,
    CheckRegistry,
    DuplicateCheckError,
    register_check,
)
from unbox.validation.runner import run_validation


def build_context(source_folder=None) -> ValidationContext:
    """
        A minimal context. These tests exercise orchestration, so the accessor and
        expectations are never actually consulted.
    """
    return ValidationContext(
        output_gdb=r"C:\fake\output.gdb",
        accessor=object(),
        expectations=object(),
        source_folder=source_folder,
    )


def passing_result(check_id: str = "demo.pass", category: str = "counts") -> CheckResult:
    return make_result(
        check_id=check_id,
        category=category,
        title="A passing demonstration check",
        status=CheckStatus.PASS,
        summary="Everything matched.",
    )


# ----------------------------------------------------------------------
# Registry
# ----------------------------------------------------------------------


def test_registering_a_duplicate_check_id_raises():
    """
        A shadowed check is a check that silently stops running, so this must fail loudly
        at import time rather than quietly overwriting.
    """
    registry = CheckRegistry()

    @register_check("demo.only_one", "counts", "First", registry=registry)
    def first_check(context):
        return passing_result()

    with pytest.raises(DuplicateCheckError):
        @register_check("demo.only_one", "counts", "Second", registry=registry)
        def second_check(context):
            return passing_result()


def test_register_check_returns_the_function_unchanged():
    """
        The decorator must not wrap the function, so a unit test can call a check
        directly with a hand-built context.
    """
    registry = CheckRegistry()

    def original_check(context):
        return passing_result()

    decorated = register_check("demo.unwrapped", "counts", "Unwrapped", registry=registry)(
        original_check
    )

    assert decorated is original_check


def test_select_filters_by_category():
    registry = CheckRegistry()

    @register_check("demo.counts_one", "counts", "Counts check", registry=registry)
    def counts_check(context):
        return passing_result()

    @register_check("demo.schema_one", "schema", "Schema check", registry=registry)
    def schema_check(context):
        return passing_result()

    selected = registry.select(categories=["counts"])

    assert [descriptor.check_id for descriptor in selected] == ["demo.counts_one"]


def test_select_filters_by_check_id():
    registry = CheckRegistry()

    @register_check("demo.first", "counts", "First", registry=registry)
    def first_check(context):
        return passing_result()

    @register_check("demo.second", "counts", "Second", registry=registry)
    def second_check(context):
        return passing_result()

    selected = registry.select(check_ids=["demo.second"])

    assert [descriptor.check_id for descriptor in selected] == ["demo.second"]


def test_descriptors_are_ordered_by_category_then_registration():
    """
        Counts run before schema, schema before integrity, integrity before performance,
        regardless of the order the modules happened to be imported in.
    """
    registry = CheckRegistry()

    @register_check("demo.perf", "performance", "Perf", registry=registry)
    def perf_check(context):
        return passing_result()

    @register_check("demo.counts_b", "counts", "Counts B", registry=registry)
    def counts_b_check(context):
        return passing_result()

    @register_check("demo.counts_a", "counts", "Counts A", registry=registry)
    def counts_a_check(context):
        return passing_result()

    ordered_ids = [descriptor.check_id for descriptor in registry.all_descriptors()]

    assert ordered_ids == ["demo.counts_b", "demo.counts_a", "demo.perf"]


def test_unregister_removes_a_check():
    registry = CheckRegistry()

    @register_check("demo.temporary", "counts", "Temporary", registry=registry)
    def temporary_check(context):
        return passing_result()

    assert "demo.temporary" in registry
    registry.unregister("demo.temporary")
    assert "demo.temporary" not in registry
    assert len(registry) == 0


# ----------------------------------------------------------------------
# Runner
# ----------------------------------------------------------------------


def test_an_exception_becomes_an_error_result_and_the_run_continues():
    """
        The central resilience guarantee. One bad check must cost one result, not the
        whole report.
    """
    registry = CheckRegistry()
    executed_check_ids = []

    @register_check("demo.explodes", "counts", "Explodes", registry=registry)
    def exploding_check(context):
        executed_check_ids.append("demo.explodes")
        raise RuntimeError("the geodatabase answered with nonsense")

    @register_check("demo.after", "counts", "Runs afterwards", registry=registry)
    def later_check(context):
        executed_check_ids.append("demo.after")
        return passing_result("demo.after")

    run = run_validation(build_context(), registry=registry)

    assert executed_check_ids == ["demo.explodes", "demo.after"]

    error_result = next(result for result in run.results if result.check_id == "demo.explodes")
    assert error_result.status == CheckStatus.ERROR
    assert "RuntimeError" in error_result.details["traceback"]
    assert "the geodatabase answered with nonsense" in error_result.details["traceback"]

    later_result = next(result for result in run.results if result.check_id == "demo.after")
    assert later_result.status == CheckStatus.PASS


def test_a_check_returning_multiple_results_is_flattened():
    registry = CheckRegistry()

    @register_check("demo.multi", "schema", "Multiple results", registry=registry)
    def multi_result_check(context):
        return [
            passing_result("demo.multi.one", "schema"),
            passing_result("demo.multi.two", "schema"),
        ]

    run = run_validation(build_context(), registry=registry)

    assert [result.check_id for result in run.results] == ["demo.multi.one", "demo.multi.two"]


def test_a_generator_of_results_is_accepted():
    """
        Sweeps read most naturally as generators, so the runner must consume one.
    """
    registry = CheckRegistry()

    @register_check("demo.generator", "schema", "Generator", registry=registry)
    def generator_check(context):
        for index in range(3):
            yield passing_result(f"demo.generator.{index}", "schema")

    run = run_validation(build_context(), registry=registry)

    assert len(run.results) == 3


def test_a_check_returning_nothing_is_reported_as_skip():
    """
        A sweep that found nothing to look at verified nothing, and must not pass.
    """
    registry = CheckRegistry()

    @register_check("demo.empty", "counts", "Produces nothing", registry=registry)
    def empty_check(context):
        return []

    run = run_validation(build_context(), registry=registry)

    assert len(run.results) == 1
    assert run.results[0].status == CheckStatus.SKIP
    assert run.results[0].details["reason"] == "no_results_produced"


def test_a_check_returning_a_non_result_becomes_an_error():
    registry = CheckRegistry()

    @register_check("demo.wrong_type", "counts", "Wrong type", registry=registry)
    def wrong_type_check(context):
        return "not a CheckResult"

    run = run_validation(build_context(), registry=registry)

    assert run.results[0].status == CheckStatus.ERROR
    assert "TypeError" in run.results[0].details["traceback"]


def test_checks_requiring_a_source_folder_skip_without_one():
    """
        Count parity cannot run with nothing to compare against, and the runner handles
        that uniformly so no check body has to.
    """
    registry = CheckRegistry()
    was_called = []

    @register_check("demo.needs_sources", "counts", "Needs sources",
                    requires_source_folder=True, registry=registry)
    def needs_sources_check(context):
        was_called.append(True)
        return passing_result("demo.needs_sources")

    run = run_validation(build_context(source_folder=None), registry=registry)

    assert was_called == []
    assert run.results[0].status == CheckStatus.SKIP
    assert run.results[0].details["reason"] == "source_folder_not_supplied"


def test_checks_requiring_a_source_folder_run_when_one_is_supplied():
    registry = CheckRegistry()

    @register_check("demo.needs_sources", "counts", "Needs sources",
                    requires_source_folder=True, registry=registry)
    def needs_sources_check(context):
        return passing_result("demo.needs_sources")

    run = run_validation(build_context(source_folder=r"C:\fake\sources"), registry=registry)

    assert run.results[0].status == CheckStatus.PASS


def test_category_filter_limits_which_checks_run():
    registry = CheckRegistry()
    executed = []

    @register_check("demo.counts", "counts", "Counts", registry=registry)
    def counts_check(context):
        executed.append("counts")
        return passing_result("demo.counts", "counts")

    @register_check("demo.schema", "schema", "Schema", registry=registry)
    def schema_check(context):
        executed.append("schema")
        return passing_result("demo.schema", "schema")

    run_validation(build_context(), categories=["counts"], registry=registry)

    assert executed == ["counts"]


def test_the_runner_fills_in_a_duration_the_check_left_unset():
    registry = CheckRegistry()

    @register_check("demo.untimed", "counts", "Untimed", registry=registry)
    def untimed_check(context):
        return passing_result("demo.untimed")

    run = run_validation(build_context(), registry=registry)

    assert run.results[0].duration_seconds >= 0.0
    assert run.finished_at is not None
    assert run.duration_seconds >= 0.0


def test_the_runner_preserves_a_duration_the_check_measured_itself():
    registry = CheckRegistry()

    @register_check("demo.self_timed", "counts", "Self timed", registry=registry)
    def self_timed_check(context):
        return make_result(
            check_id="demo.self_timed",
            category="counts",
            title="Self timed",
            status=CheckStatus.PASS,
            summary="Measured its own elapsed time.",
            duration_seconds=12.5,
        )

    run = run_validation(build_context(), registry=registry)

    assert run.results[0].duration_seconds == 12.5


def test_the_run_records_its_configuration():
    registry = CheckRegistry()
    context = build_context(source_folder=r"C:\fake\sources")

    run = run_validation(context, registry=registry)

    assert run.configuration["output_gdb"] == context.output_gdb
    assert run.configuration["source_folder"] == r"C:\fake\sources"
    assert run.configuration["base_fips"] == "06037"


# ----------------------------------------------------------------------
# Overall status precedence
# ----------------------------------------------------------------------


def build_run(statuses) -> ValidationRun:
    run = ValidationRun(output_gdb=r"C:\fake\output.gdb", started_at=datetime.datetime.now())
    for index, status in enumerate(statuses):
        run.results.append(
            make_result(
                check_id=f"demo.{index}",
                category="counts",
                title=f"Check {index}",
                status=status,
                summary="",
            )
        )
    return run


@pytest.mark.parametrize(
    "statuses, expected_overall",
    [
        ([CheckStatus.PASS, CheckStatus.PASS], CheckStatus.PASS),
        ([CheckStatus.PASS, CheckStatus.SKIP], CheckStatus.SKIP),
        ([CheckStatus.PASS, CheckStatus.WARN, CheckStatus.SKIP], CheckStatus.WARN),
        ([CheckStatus.PASS, CheckStatus.WARN, CheckStatus.ERROR], CheckStatus.ERROR),
        ([CheckStatus.ERROR, CheckStatus.FAIL], CheckStatus.FAIL),
        ([CheckStatus.FAIL, CheckStatus.PASS], CheckStatus.FAIL),
    ],
)
def test_overall_status_is_the_worst_individual_status(statuses, expected_overall):
    assert build_run(statuses).overall_status == expected_overall


def test_an_empty_run_is_skip_not_pass():
    """
        Verifying nothing is not the same as verifying everything successfully.
    """
    assert build_run([]).overall_status == CheckStatus.SKIP


def test_error_counts_as_a_failure_for_gating_purposes():
    error_result = make_result("demo.err", "counts", "Errored", CheckStatus.ERROR, "")
    pass_result = make_result("demo.ok", "counts", "Passed", CheckStatus.PASS, "")

    assert error_result.is_failure is True
    assert pass_result.is_failure is False


def test_results_group_by_category_in_encounter_order():
    run = ValidationRun(output_gdb=r"C:\fake\output.gdb", started_at=datetime.datetime.now())
    run.results.append(make_result("a", "counts", "A", CheckStatus.PASS, ""))
    run.results.append(make_result("b", "schema", "B", CheckStatus.PASS, ""))
    run.results.append(make_result("c", "counts", "C", CheckStatus.PASS, ""))

    grouped = run.by_category()

    assert list(grouped.keys()) == ["counts", "schema"]
    assert [result.check_id for result in grouped["counts"]] == ["a", "c"]


def test_status_counts_tally_each_status():
    run = build_run([CheckStatus.PASS, CheckStatus.PASS, CheckStatus.FAIL])
    counts = run.status_counts()

    assert counts[CheckStatus.PASS] == 2
    assert counts[CheckStatus.FAIL] == 1
    assert counts[CheckStatus.WARN] == 0


def test_row_column_names_are_the_union_in_first_seen_order():
    """
        The report builds one table header per check, so a row that omits a key must not
        shift the remaining columns.
    """
    result = make_result(
        check_id="demo.rows",
        category="counts",
        title="Rows",
        status=CheckStatus.PASS,
        summary="",
        rows=[
            {"county": "06001", "source": 10},
            {"county": "06003", "output": 20},
        ],
    )

    assert result.row_column_names == ["county", "source", "output"]


def test_context_county_filter_selects_everything_when_empty():
    context = build_context()
    assert context.is_county_selected("06001") is True


def test_context_county_filter_restricts_when_populated():
    context = ValidationContext(
        output_gdb=r"C:\fake\output.gdb",
        accessor=object(),
        expectations=object(),
        counties=("06001", "06003"),
    )

    assert context.is_county_selected("06001") is True
    assert context.is_county_selected("06037") is False
