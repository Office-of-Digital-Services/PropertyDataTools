"""
    Unit tests for the count parity checks.

    The important tests here are the ones that corrupt the fixture. A count check that
    cannot fail is worse than no check at all, because it produces a green report over
    a broken dataset - so every scenario below deliberately breaks something and
    asserts the check notices.

    No arcpy and no geodatabase: the fake accessor supplies all counts, and the source
    folder is a tree of empty .gdb directories built in tmp_path, because source
    discovery inspects folder names only.
"""

import os

import pytest

from unbox.validation.checks.counts import (
    check_base_county_reconciliation,
    check_county_coverage,
    check_per_county_theme,
)
from unbox.validation.expectations import MergeExpectations
from unbox.validation.models import CheckStatus, ValidationContext
from unbox.validation.source_discovery import discover_source_geodatabases, extract_fips_code
from tests.fakes.fake_gdb_accessor import FakeDataset, FakeGeodatabaseAccessor

OUTPUT_GDB = r"C:\fake\full_state.gdb"
BASE_FIPS = "06037"

# A small but realistic fixture: three source counties plus the merge base, and two
# themes. Keeping it small makes an assertion failure readable; the shape matches the
# real build exactly.
SOURCE_FIPS_CODES = ("06001", "06003", "06005")
THEMES_UNDER_TEST = ("Parcels", "Addresses")

# Per-county source counts, by theme.
SOURCE_COUNTS = {
    "Parcels": {"06001": 500, "06003": 120, "06005": 80},
    "Addresses": {"06001": 1_200, "06003": 300, "06005": 150},
}

# What the merge base county (LA) contributes, present only in the output.
BASE_COUNTY_COUNTS = {"Parcels": 7_000, "Addresses": 15_000}


def build_source_folder(tmp_path, fips_codes=SOURCE_FIPS_CODES) -> str:
    """
        Creates empty .gdb folders named the way the real delivery names them, e.g.
        SF_Professional_06001.gdb. Source discovery reads names only, so empty
        directories are sufficient and keep the test fast.
    """
    source_folder = tmp_path / "processing"
    source_folder.mkdir(exist_ok=True)

    for fips_code in fips_codes:
        (source_folder / f"SF_Professional_{fips_code}.gdb").mkdir()

    return str(source_folder)


def build_accessor(source_folder: str,
                   output_counts_override=None,
                   source_counts_override=None,
                   omit_theme_from_county=None) -> FakeGeodatabaseAccessor:
    """
        Builds a fake accessor describing a healthy merge, with hooks for corrupting it.

        :param output_counts_override: {theme: {fips: count}} replacing the derived output.
        :param source_counts_override: {theme: {fips: count}} replacing the source counts.
        :param omit_theme_from_county: (fips_code, theme) to leave out of a source gdb.
    """
    accessor = FakeGeodatabaseAccessor()
    source_counts = source_counts_override or SOURCE_COUNTS

    # The output: every source county's rows plus the merge base county's rows.
    for theme_name in THEMES_UNDER_TEST:
        if output_counts_override and theme_name in output_counts_override:
            grouped = dict(output_counts_override[theme_name])
        else:
            grouped = dict(source_counts[theme_name])
            grouped[BASE_FIPS] = BASE_COUNTY_COUNTS[theme_name]

        accessor.add_dataset(
            OUTPUT_GDB, theme_name,
            FakeDataset(data_type="FeatureClass", grouped_counts={"FIPS_CODE": grouped}),
        )

    # Each source county geodatabase, holding only its own rows.
    for fips_code in SOURCE_FIPS_CODES:
        source_gdb = os.path.join(source_folder, f"SF_Professional_{fips_code}.gdb")
        for theme_name in THEMES_UNDER_TEST:
            if omit_theme_from_county == (fips_code, theme_name):
                continue
            accessor.add_dataset(
                source_gdb, theme_name,
                FakeDataset(data_type="FeatureClass",
                            row_count=source_counts[theme_name][fips_code]),
            )

    return accessor


def build_context(source_folder: str,
                  accessor: FakeGeodatabaseAccessor,
                  counties=()) -> ValidationContext:
    """
        A context whose expectations are narrowed to the two themes under test, so the
        fixture stays small while the check code under test is entirely unmodified.
    """
    full_expectations = MergeExpectations.from_merge_class()
    narrowed_expectations = type(full_expectations)(
        core_theme_names=THEMES_UNDER_TEST,
        relation_table_names=(),
        expected_indexes=full_expectations.expected_indexes,
        spatially_indexed_tables=full_expectations.spatially_indexed_tables,
        manytomany_relationships=full_expectations.manytomany_relationships,
        onetomany_relationships=full_expectations.onetomany_relationships,
    )

    return ValidationContext(
        output_gdb=OUTPUT_GDB,
        accessor=accessor,
        expectations=narrowed_expectations,
        source_folder=source_folder,
        base_fips=BASE_FIPS,
        counties=tuple(counties),
    )


# ----------------------------------------------------------------------
# Source discovery
# ----------------------------------------------------------------------


def test_fips_code_is_extracted_from_the_delivery_naming_convention():
    assert extract_fips_code("SF_Professional_06001.gdb") == "06001"
    assert extract_fips_code("LBX_PARCEL_PROFESSIONAL_FGDB_06037.gdb") == "06037"
    assert extract_fips_code("full_state_smartfabric_20261020.gdb") is None
    assert extract_fips_code("not_a_geodatabase") is None


def test_discovery_finds_every_county_gdb_sorted_by_fips(tmp_path):
    source_folder = build_source_folder(tmp_path)

    discovery = discover_source_geodatabases(source_folder)

    assert discovery.fips_codes == SOURCE_FIPS_CODES
    assert discovery.skipped_folder_names == ()


def test_discovery_honours_the_counties_filter(tmp_path):
    source_folder = build_source_folder(tmp_path)

    discovery = discover_source_geodatabases(source_folder, counties=["06001", "06005"])

    assert discovery.fips_codes == ("06001", "06005")


def test_discovery_reports_a_gdb_with_no_recognisable_fips_code(tmp_path):
    """
        A partially extracted or oddly named geodatabase must be surfaced, not silently
        dropped - a county quietly excluded from the comparison defeats the purpose.
    """
    source_folder = build_source_folder(tmp_path)
    os.mkdir(os.path.join(source_folder, "partial_extract_working.gdb"))

    discovery = discover_source_geodatabases(source_folder)

    assert discovery.fips_codes == SOURCE_FIPS_CODES
    assert discovery.skipped_folder_names == ("partial_extract_working.gdb",)


def test_discovery_ignores_non_geodatabase_folders(tmp_path):
    source_folder = build_source_folder(tmp_path)
    os.mkdir(os.path.join(source_folder, "logs"))

    discovery = discover_source_geodatabases(source_folder)

    assert discovery.fips_codes == SOURCE_FIPS_CODES
    assert discovery.skipped_folder_names == ()


def test_discovery_raises_for_a_missing_source_folder():
    with pytest.raises(FileNotFoundError):
        discover_source_geodatabases(r"C:\does\not\exist")


# ----------------------------------------------------------------------
# counts.county_coverage
# ----------------------------------------------------------------------


def test_county_coverage_passes_when_every_county_is_present(tmp_path):
    source_folder = build_source_folder(tmp_path)
    context = build_context(source_folder, build_accessor(source_folder))

    result = check_county_coverage(context)

    assert result.status == CheckStatus.PASS
    assert result.details["source_counties_found"] == 3
    assert result.details["counties_in_output"] == 4  # three sources plus the merge base
    assert result.details["missing_from_output"] == []


def test_county_coverage_fails_when_a_county_is_missing_from_the_output(tmp_path):
    """
        The headline failure mode: a county was dropped during the merge.
    """
    source_folder = build_source_folder(tmp_path)

    output_without_06003 = {
        "Parcels": {"06001": 500, "06005": 80, BASE_FIPS: 7_000},
    }
    accessor = build_accessor(source_folder, output_counts_override=output_without_06003)
    context = build_context(source_folder, accessor)

    result = check_county_coverage(context)

    assert result.status == CheckStatus.FAIL
    assert result.details["missing_from_output"] == ["06003"]
    assert "06003" in result.summary

    missing_row = next(row for row in result.rows if row["county"] == "06003")
    assert missing_row["status"] == "fail"
    assert missing_row["in_output"] is False


def test_county_coverage_fails_on_an_unexpected_county_in_the_output(tmp_path):
    """
        A FIPS code with no corresponding source geodatabase means rows arrived from
        somewhere unaccounted for.
    """
    source_folder = build_source_folder(tmp_path)

    output_with_intruder = {
        "Parcels": {"06001": 500, "06003": 120, "06005": 80, BASE_FIPS: 7_000, "06099": 42},
    }
    accessor = build_accessor(source_folder, output_counts_override=output_with_intruder)
    context = build_context(source_folder, accessor)

    result = check_county_coverage(context)

    assert result.status == CheckStatus.FAIL
    assert result.details["unexpected_in_output"] == ["06099"]

    intruder_row = next(row for row in result.rows if row["county"] == "06099")
    assert intruder_row["status"] == "fail"
    assert intruder_row["has_source_gdb"] is False


def test_county_coverage_fails_on_rows_with_a_null_county(tmp_path):
    """
        Rows whose FIPS_CODE is null or empty cannot be attributed to any county and
        must be surfaced explicitly rather than grouped away.
    """
    source_folder = build_source_folder(tmp_path)

    output_with_nulls = {
        "Parcels": {"06001": 500, "06003": 120, "06005": 80, BASE_FIPS: 7_000, "": 17},
    }
    accessor = build_accessor(source_folder, output_counts_override=output_with_nulls)
    context = build_context(source_folder, accessor)

    result = check_county_coverage(context)

    assert result.status == CheckStatus.FAIL
    assert result.details["rows_with_null_county"] == 17
    assert any(row["county"] == "(null or empty)" for row in result.rows)


def test_county_coverage_accepts_the_base_county_having_no_source(tmp_path):
    """
        LA has no source geodatabase by design, and that must not be reported as a
        problem.
    """
    source_folder = build_source_folder(tmp_path)
    context = build_context(source_folder, build_accessor(source_folder))

    result = check_county_coverage(context)
    base_row = next(row for row in result.rows if row["county"] == BASE_FIPS)

    assert result.status == CheckStatus.PASS
    assert base_row["status"] == "pass"
    assert base_row["has_source_gdb"] is False
    assert "Merge base county" in base_row["note"]


def test_county_coverage_respects_the_counties_filter(tmp_path):
    """
        Regression test for a bug found only against the real data.

        With --counties active, source discovery is narrowed but the output still holds
        every county. Comparing the narrowed source set against the full output set
        reported all the other counties as "unexpected" - 56 false failures on the real
        statewide geodatabase. Counties outside the filter must simply be out of scope.
    """
    source_folder = build_source_folder(tmp_path)

    # The output holds all three counties plus the merge base, as the real one does.
    accessor = build_accessor(source_folder)
    context = build_context(source_folder, accessor, counties=["06001", "06003"])

    result = check_county_coverage(context)

    assert result.status == CheckStatus.PASS, result.summary
    assert result.details["unexpected_in_output"] == []
    assert result.details["missing_from_output"] == []

    # Only the two selected counties are reported on.
    assert {row["county"] for row in result.rows} == {"06001", "06003"}


def test_county_coverage_warns_about_an_unidentifiable_source_folder(tmp_path):
    source_folder = build_source_folder(tmp_path)
    os.mkdir(os.path.join(source_folder, "partial_extract.gdb"))
    context = build_context(source_folder, build_accessor(source_folder))

    result = check_county_coverage(context)

    assert result.status == CheckStatus.WARN
    assert "partial_extract.gdb" in result.details["skipped_source_folders"]


# ----------------------------------------------------------------------
# counts.per_county_theme
# ----------------------------------------------------------------------


def test_per_county_theme_passes_on_a_healthy_merge(tmp_path):
    source_folder = build_source_folder(tmp_path)
    context = build_context(source_folder, build_accessor(source_folder))

    results = list(check_per_county_theme(context))

    assert len(results) == len(THEMES_UNDER_TEST)
    for result in results:
        assert result.status == CheckStatus.PASS, result.summary
        assert result.details["counties_mismatched"] == 0
        assert len(result.rows) == len(SOURCE_FIPS_CODES)


def test_per_county_theme_fails_when_a_county_is_double_appended(tmp_path):
    """
        Simulates the merge appending one county twice. The output count is double the
        source count, which must be reported as a positive delta.
    """
    source_folder = build_source_folder(tmp_path)

    doubled = {
        "Parcels": {"06001": 500, "06003": 240, "06005": 80, BASE_FIPS: 7_000},
        "Addresses": {"06001": 1_200, "06003": 300, "06005": 150, BASE_FIPS: 15_000},
    }
    accessor = build_accessor(source_folder, output_counts_override=doubled)
    context = build_context(source_folder, accessor)

    parcels_result = next(
        result for result in check_per_county_theme(context)
        if result.details["theme"] == "Parcels"
    )

    assert parcels_result.status == CheckStatus.FAIL
    assert parcels_result.details["counties_mismatched"] == 1

    doubled_row = next(row for row in parcels_result.rows if row["county"] == "06003")
    assert doubled_row["source_rows"] == 120
    assert doubled_row["output_rows"] == 240
    assert doubled_row["delta"] == 120
    assert "more rows" in doubled_row["note"]


def test_per_county_theme_fails_when_a_county_contributed_nothing(tmp_path):
    source_folder = build_source_folder(tmp_path)

    missing_county_rows = {
        "Parcels": {"06001": 500, "06005": 80, BASE_FIPS: 7_000},
        "Addresses": {"06001": 1_200, "06003": 300, "06005": 150, BASE_FIPS: 15_000},
    }
    accessor = build_accessor(source_folder, output_counts_override=missing_county_rows)
    context = build_context(source_folder, accessor)

    parcels_result = next(
        result for result in check_per_county_theme(context)
        if result.details["theme"] == "Parcels"
    )

    assert parcels_result.status == CheckStatus.FAIL

    empty_row = next(row for row in parcels_result.rows if row["county"] == "06003")
    assert empty_row["output_rows"] == 0
    assert empty_row["delta"] == -120
    assert "No rows for this county reached the output" in empty_row["note"]


def test_per_county_theme_skips_a_county_missing_the_theme_and_keeps_sweeping(tmp_path):
    """
        A source delivery that omits a theme is a real condition. It is reported for
        that one county and the other counties are still compared.
    """
    source_folder = build_source_folder(tmp_path)

    source_counts_without_one = {
        "Parcels": {"06001": 500, "06003": 120, "06005": 80},
        "Addresses": {"06001": 1_200, "06003": 300, "06005": 150},
    }
    output_counts = {
        "Parcels": {"06001": 500, "06003": 120, "06005": 80, BASE_FIPS: 7_000},
        "Addresses": {"06001": 1_200, "06005": 150, BASE_FIPS: 15_000},
    }
    accessor = build_accessor(
        source_folder,
        output_counts_override=output_counts,
        source_counts_override=source_counts_without_one,
        omit_theme_from_county=("06003", "Addresses"),
    )
    context = build_context(source_folder, accessor)

    addresses_result = next(
        result for result in check_per_county_theme(context)
        if result.details["theme"] == "Addresses"
    )

    assert addresses_result.status == CheckStatus.WARN
    assert addresses_result.details["counties_skipped"] == 1
    assert addresses_result.details["counties_compared"] == 2

    skipped_row = next(row for row in addresses_result.rows if row["county"] == "06003")
    assert skipped_row["status"] == "skip"
    assert "not present in" in skipped_row["note"]


def test_counties_filter_restricts_the_rows_emitted(tmp_path):
    source_folder = build_source_folder(tmp_path)
    accessor = build_accessor(source_folder)
    context = build_context(source_folder, accessor, counties=["06001", "06003"])

    parcels_result = next(
        result for result in check_per_county_theme(context)
        if result.details["theme"] == "Parcels"
    )

    emitted_counties = {row["county"] for row in parcels_result.rows}
    assert emitted_counties == {"06001", "06003"}


def test_per_county_theme_uses_one_grouped_pass_per_theme(tmp_path):
    """
        Behavioural assertion, not a cosmetic one. Filtering the output per county would
        cost 3.5-13.4 seconds per county on the relation tables - roughly 13 minutes for
        one theme across 58 counties - so the implementation must group once per theme.
    """
    source_folder = build_source_folder(tmp_path)
    accessor = build_accessor(source_folder)
    context = build_context(source_folder, accessor)

    list(check_per_county_theme(context))

    assert accessor.count_calls("count_rows_by_field") == len(THEMES_UNDER_TEST)


def test_source_counts_are_cached_across_checks(tmp_path):
    """
        per_county_theme and base_county_reconciliation need the same 570 source counts
        on a real run. Counting them twice would add roughly two minutes for nothing.
    """
    source_folder = build_source_folder(tmp_path)
    accessor = build_accessor(source_folder)
    context = build_context(source_folder, accessor)

    list(check_per_county_theme(context))
    calls_after_first_check = accessor.count_calls("get_row_count")

    check_base_county_reconciliation(context)
    calls_after_second_check = accessor.count_calls("get_row_count")

    # The reconciliation check needs one extra total per theme, and nothing more.
    assert calls_after_second_check - calls_after_first_check == len(THEMES_UNDER_TEST)


# ----------------------------------------------------------------------
# counts.base_county_reconciliation
# ----------------------------------------------------------------------


def test_base_county_reconciliation_passes_when_the_identity_holds(tmp_path):
    """
        sum(sources) + output[06037] == output total.

        This is the identity verified exact on the real data before the module was
        written: 19,341,519 + 7,414,167 == 26,755,686 Addresses.
    """
    source_folder = build_source_folder(tmp_path)
    context = build_context(source_folder, build_accessor(source_folder))

    result = check_base_county_reconciliation(context)

    assert result.status == CheckStatus.PASS
    assert result.details["themes_with_discrepancy"] == 0

    parcels_row = next(row for row in result.rows if row["theme"] == "Parcels")
    assert parcels_row["source_total"] == 700           # 500 + 120 + 80
    assert parcels_row["base_county_in_output"] == 7_000
    assert parcels_row["expected_total"] == 7_700
    assert parcels_row["actual_output_total"] == 7_700
    assert parcels_row["delta"] == 0


def test_base_county_reconciliation_fails_when_the_base_total_is_perturbed(tmp_path):
    """
        Moving the base county count by one must break the identity. If it does not,
        the check is not actually testing anything.
    """
    source_folder = build_source_folder(tmp_path)

    perturbed = {
        "Parcels": {"06001": 500, "06003": 120, "06005": 80, BASE_FIPS: 6_999},
        "Addresses": {"06001": 1_200, "06003": 300, "06005": 150, BASE_FIPS: 15_000},
    }
    accessor = build_accessor(source_folder, output_counts_override=perturbed)

    # The grouped counts now sum to 7,699 but the stated total stays at 7,700, which is
    # exactly the shape of a merge that lost a single row.
    accessor.datasets_by_gdb[OUTPUT_GDB]["Parcels"].row_count = 7_700

    context = build_context(source_folder, accessor)
    result = check_base_county_reconciliation(context)

    assert result.status == CheckStatus.FAIL
    assert result.details["themes_with_discrepancy"] == 1

    parcels_row = next(row for row in result.rows if row["theme"] == "Parcels")
    assert parcels_row["delta"] == 1


def test_base_county_reconciliation_skips_when_a_counties_filter_is_active(tmp_path):
    """
        The statewide identity cannot hold for a partial sweep, so the check must say
        it was skipped rather than report a meaningless failure.
    """
    source_folder = build_source_folder(tmp_path)
    accessor = build_accessor(source_folder)
    context = build_context(source_folder, accessor, counties=["06001"])

    result = check_base_county_reconciliation(context)

    assert result.status == CheckStatus.SKIP
    assert result.details["counties_filter"] == ["06001"]
