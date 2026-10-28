"""
    Count parity: did every county's records make it into the merge, exactly once?

    This is the most consequential check family. A dropped county means publishing a
    statewide dataset with a hole in it; a double-appended county means publishing
    duplicates. Both are silent failures in the output - nothing about the merged
    geodatabase looks wrong until someone counts.

    Three checks:

        counts.county_coverage            the right set of counties is present
        counts.per_county_theme           each county's rows match, theme by theme
        counts.base_county_reconciliation LA's contribution adds up

    Query strategy note. Counting the output is done with one grouped pass per theme
    rather than one filtered query per county. FIPS_CODE is indexed on the four core
    feature classes but not on the relation tables, where a single filtered count was
    measured at 3.5-13.4 seconds - roughly 13 minutes per theme across 58 counties. One
    grouped TableToNumPyArray pass covers all 58 counties of the 26.1M-row
    AddressParcelRelation in about 16 seconds.
"""

from __future__ import annotations

import logging
from typing import Any, Dict, Iterator, List, Optional, Tuple

from unbox.validation.expectations import COUNTY_FIELD_NAME
from unbox.validation.models import CheckResult, CheckStatus, ValidationContext, make_result
from unbox.validation.registry import register_check
from unbox.validation.source_discovery import SourceDiscovery, discover_source_geodatabases

module_logger = logging.getLogger(__name__)

CATEGORY = "counts"

# How a null or empty FIPS_CODE value is labelled in the report. Grouped counts collapse
# nulls to the empty string; surfacing them under an explicit label keeps rows with no
# county from disappearing into a blank table cell.
NULL_COUNTY_LABEL = "(null or empty)"


# ----------------------------------------------------------------------------------
# Shared helpers
# ----------------------------------------------------------------------------------


def _discover_sources(context: ValidationContext) -> SourceDiscovery:
    """
        Finds the per-county source geodatabases, honouring the --counties filter.

        The result is cached on the context, because all three count checks need it and
        re-scanning the folder for each would be wasteful and could, in principle, give
        inconsistent answers if the folder changed mid-run.
    """
    cache_key = "counts.source_discovery"

    if cache_key not in context.shared_state:
        context.shared_state[cache_key] = discover_source_geodatabases(
            context.source_folder, counties=context.counties
        )

    return context.shared_state[cache_key]


def _output_counts_by_county(context: ValidationContext, theme_name: str) -> Dict[str, int]:
    """
        One grouped pass over the output theme, returning {fips_code: row_count}.

        Cached per theme so county_coverage, per_county_theme and the reconciliation
        check share a single expensive pass rather than taking one each.
    """
    cache_key = f"counts.output_grouped.{theme_name}"

    if cache_key not in context.shared_state:
        module_logger.info(f"Counting output rows of {theme_name} grouped by {COUNTY_FIELD_NAME}")
        context.shared_state[cache_key] = context.accessor.count_rows_by_field(
            context.output_gdb, theme_name, COUNTY_FIELD_NAME
        )

    return context.shared_state[cache_key]


def _label_county(fips_code: str) -> str:
    """
        Renders a FIPS code for display, naming the null group explicitly.
    """
    return fips_code if fips_code else NULL_COUNTY_LABEL


def _theme_names(context: ValidationContext) -> Tuple[str, ...]:
    """
        The ten datasets that carry per-county rows.
    """
    return context.expectations.all_countable_theme_names


def _safe_source_count(context: ValidationContext,
                       source_gdb_path: str,
                       theme_name: str) -> Optional[int]:
    """
        Counts a theme in one source geodatabase, returning None if it is absent.

        A county delivery that omits a theme is a real condition worth reporting, but it
        must not abort the sweep across the other 56 counties.

        Results are cached on the context. Both per_county_theme and
        base_county_reconciliation need the same 570 counts (57 counties x 10 themes),
        and a full sweep of one theme across all source geodatabases was measured at
        about 12.7 seconds - so caching saves roughly two minutes per run.
    """
    cache: Dict[Tuple[str, str], Optional[int]] = context.shared_state.setdefault(
        "counts.source_counts", {}
    )
    cache_key = (source_gdb_path, theme_name)

    if cache_key not in cache:
        if context.accessor.dataset_exists(source_gdb_path, theme_name):
            cache[cache_key] = context.accessor.get_row_count(source_gdb_path, theme_name)
        else:
            cache[cache_key] = None

    return cache[cache_key]


# ----------------------------------------------------------------------------------
# counts.county_coverage
# ----------------------------------------------------------------------------------


@register_check(
    check_id="counts.county_coverage",
    category=CATEGORY,
    title="Every source county is present in the output",
    requires_source_folder=True,
)
def check_county_coverage(context: ValidationContext) -> CheckResult:
    """
        Compares the set of counties found in the sources (plus the merge base) against
        the set of counties actually present in the merged output.

        This is the cheapest and bluntest completeness check: before comparing any row
        counts, confirm no county vanished entirely and no unexpected county appeared.
    """
    discovery = _discover_sources(context)

    # Parcels is the reference theme for coverage. Every county has parcels, and it is
    # the smallest of the large themes, so this is the cheapest reliable census.
    reference_theme = "Parcels"
    output_counts = _output_counts_by_county(context, reference_theme)

    source_fips_codes = set(discovery.fips_codes)

    # Only counties in scope are considered. With a --counties filter active the output
    # still legitimately contains all 58 counties, so comparing the filtered source set
    # against the full output set would report 56 false "unexpected county" failures.
    output_fips_codes = {
        fips for fips in output_counts.keys()
        if fips and context.is_county_selected(fips)
    }

    # The merge-base county has no source geodatabase but must appear in the output.
    expected_fips_codes = set(source_fips_codes)
    if context.is_county_selected(context.base_fips):
        expected_fips_codes.add(context.base_fips)

    missing_from_output = sorted(expected_fips_codes - output_fips_codes)
    unexpected_in_output = sorted(output_fips_codes - expected_fips_codes)

    # A null FIPS_CODE is neither missing nor unexpected, but it does mean rows whose
    # county cannot be determined - which must be reported, not swallowed. These are
    # only meaningful on a full sweep, since a filter cannot select them.
    null_county_row_count = 0 if context.counties else output_counts.get("", 0)

    rows: List[Dict[str, Any]] = []

    for fips_code in sorted(expected_fips_codes | output_fips_codes):
        is_present_in_output = fips_code in output_fips_codes
        has_source = fips_code in source_fips_codes
        is_base_county = fips_code == context.base_fips

        if not is_present_in_output:
            row_status = "fail"
            note = "Present in the sources but absent from the output."
        elif fips_code in unexpected_in_output:
            row_status = "fail"
            note = "Present in the output but has no source geodatabase."
        elif is_base_county and not has_source:
            row_status = "pass"
            note = "Merge base county; no source geodatabase by design."
        else:
            row_status = "pass"
            note = ""

        rows.append({
            "county": _label_county(fips_code),
            "has_source_gdb": has_source,
            "in_output": is_present_in_output,
            f"output_{reference_theme}_rows": output_counts.get(fips_code, 0),
            "status": row_status,
            "note": note,
        })

    if null_county_row_count:
        rows.append({
            "county": NULL_COUNTY_LABEL,
            "has_source_gdb": False,
            "in_output": True,
            f"output_{reference_theme}_rows": null_county_row_count,
            "status": "fail",
            "note": f"{null_county_row_count:,} output rows carry no {COUNTY_FIELD_NAME}.",
        })

    # Folders that looked like county geodatabases but could not be read are reported,
    # because a county silently excluded from the comparison defeats the purpose.
    for skipped_folder_name in discovery.skipped_folder_names:
        rows.append({
            "county": "(unknown)",
            "has_source_gdb": True,
            "in_output": False,
            f"output_{reference_theme}_rows": 0,
            "status": "warn",
            "note": f"Source folder {skipped_folder_name} has no recognisable FIPS code; skipped.",
        })

    details = {
        "reference_theme": reference_theme,
        "source_counties_found": len(source_fips_codes),
        "counties_in_output": len(output_fips_codes),
        "base_fips": context.base_fips,
        "missing_from_output": missing_from_output,
        "unexpected_in_output": unexpected_in_output,
        "rows_with_null_county": null_county_row_count,
        "skipped_source_folders": list(discovery.skipped_folder_names),
    }

    if missing_from_output or unexpected_in_output or null_county_row_count:
        status = CheckStatus.FAIL
        problems = []
        if missing_from_output:
            problems.append(f"{len(missing_from_output)} county(ies) missing from the output "
                            f"({', '.join(missing_from_output)})")
        if unexpected_in_output:
            problems.append(f"{len(unexpected_in_output)} unexpected county(ies) in the output "
                            f"({', '.join(unexpected_in_output)})")
        if null_county_row_count:
            problems.append(f"{null_county_row_count:,} rows with no {COUNTY_FIELD_NAME}")
        summary = "County coverage is wrong: " + "; ".join(problems) + "."
    elif discovery.skipped_folder_names:
        status = CheckStatus.WARN
        summary = (
            f"All {len(output_fips_codes)} expected counties are present, but "
            f"{len(discovery.skipped_folder_names)} source folder(s) could not be identified."
        )
    else:
        status = CheckStatus.PASS
        summary = (
            f"All {len(output_fips_codes)} expected counties are present in the output "
            f"({len(source_fips_codes)} from source geodatabases plus the merge base "
            f"{context.base_fips})."
        )

    return make_result(
        check_id="counts.county_coverage",
        category=CATEGORY,
        title="Every source county is present in the output",
        status=status,
        summary=summary,
        details=details,
        rows=rows,
    )


# ----------------------------------------------------------------------------------
# counts.per_county_theme
# ----------------------------------------------------------------------------------


@register_check(
    check_id="counts.per_county_theme",
    category=CATEGORY,
    title="Per county record counts match the sources",
    requires_source_folder=True,
)
def check_per_county_theme(context: ValidationContext) -> Iterator[CheckResult]:
    """
        For each of the ten themes, compares every county's source row count against
        the output row count for that county.

        One result is produced per theme so that a problem in one theme is reported
        precisely rather than being buried in a single enormous pass/fail.

        The merge-base county is excluded here - it has no source to compare against -
        and is handled by counts.base_county_reconciliation instead.
    """
    discovery = _discover_sources(context)

    for theme_name in _theme_names(context):
        yield _compare_one_theme(context, discovery, theme_name)


def _compare_one_theme(context: ValidationContext,
                       discovery: SourceDiscovery,
                       theme_name: str) -> CheckResult:
    """
        Compares one theme across every discovered county.
    """
    output_counts = _output_counts_by_county(context, theme_name)

    rows: List[Dict[str, Any]] = []
    mismatch_count = 0
    skipped_count = 0
    total_source_rows = 0
    total_output_rows_for_sources = 0

    for source in discovery.source_geodatabases:
        source_row_count = _safe_source_count(context, source.gdb_path, theme_name)

        if source_row_count is None:
            # The source geodatabase does not carry this theme. Report the gap and keep
            # sweeping rather than aborting the whole theme.
            skipped_count += 1
            rows.append({
                "county": source.fips_code,
                "source_rows": None,
                "output_rows": output_counts.get(source.fips_code, 0),
                "delta": None,
                "status": "skip",
                "note": f"{theme_name} is not present in {source.folder_name}.",
            })
            continue

        output_row_count = output_counts.get(source.fips_code, 0)
        delta = output_row_count - source_row_count

        total_source_rows += source_row_count
        total_output_rows_for_sources += output_row_count

        if delta == 0:
            row_status = "pass"
            note = ""
        else:
            mismatch_count += 1
            row_status = "fail"
            if output_row_count == 0:
                note = "No rows for this county reached the output."
            elif delta > 0:
                note = f"Output has {delta:,} more rows than the source."
            else:
                note = f"Output is short by {abs(delta):,} rows."

        rows.append({
            "county": source.fips_code,
            "source_rows": source_row_count,
            "output_rows": output_row_count,
            "delta": delta,
            "status": row_status,
            "note": note,
        })

    details = {
        "theme": theme_name,
        "counties_compared": len(discovery.source_geodatabases) - skipped_count,
        "counties_skipped": skipped_count,
        "counties_mismatched": mismatch_count,
        "total_source_rows": total_source_rows,
        "total_output_rows_for_those_counties": total_output_rows_for_sources,
        "total_delta": total_output_rows_for_sources - total_source_rows,
    }

    if mismatch_count:
        status = CheckStatus.FAIL
        summary = (
            f"{theme_name}: {mismatch_count} of "
            f"{len(discovery.source_geodatabases) - skipped_count} counties have a row "
            f"count that does not match the source."
        )
    elif skipped_count == len(discovery.source_geodatabases):
        status = CheckStatus.SKIP
        summary = f"{theme_name}: no source geodatabase carries this theme, so nothing was compared."
    elif skipped_count:
        status = CheckStatus.WARN
        summary = (
            f"{theme_name}: all {len(discovery.source_geodatabases) - skipped_count} "
            f"comparable counties match, but {skipped_count} source geodatabase(s) do "
            f"not carry this theme."
        )
    else:
        status = CheckStatus.PASS
        summary = (
            f"{theme_name}: all {len(discovery.source_geodatabases)} counties match "
            f"exactly ({total_source_rows:,} rows)."
        )

    return make_result(
        check_id=f"counts.per_county_theme.{theme_name}",
        category=CATEGORY,
        title=f"Per county record counts - {theme_name}",
        status=status,
        summary=summary,
        details=details,
        rows=rows,
    )


# ----------------------------------------------------------------------------------
# counts.base_county_reconciliation
# ----------------------------------------------------------------------------------


@register_check(
    check_id="counts.base_county_reconciliation",
    category=CATEGORY,
    title="Merge base county reconciles against the output total",
    requires_source_folder=True,
)
def check_base_county_reconciliation(context: ValidationContext) -> CheckResult:
    """
        Confirms the arithmetic identity that accounts for the merge-base county.

            sum(source counts) + output count for the base county == output total

        Los Angeles has no source geodatabase, because move_largest_to_output() promoted
        it to become the output. It does not need one: its contribution is recovered
        from the output's own FIPS_CODE grouping, and this identity is what proves the
        recovery is sound. Verified exact on Addresses before this module was written:

            19,341,519 (57 sources) + 7,414,167 (output 06037) == 26,755,686 (output total)

        If a --counties filter is active the identity does not hold - only part of the
        state is in scope - so the check reports SKIP rather than a misleading failure.
    """
    if context.counties:
        return make_result(
            check_id="counts.base_county_reconciliation",
            category=CATEGORY,
            title="Merge base county reconciles against the output total",
            status=CheckStatus.SKIP,
            summary=(
                "Skipped because a --counties filter is active; the statewide total "
                "identity only holds for a full sweep."
            ),
            details={"counties_filter": list(context.counties)},
        )

    discovery = _discover_sources(context)
    rows: List[Dict[str, Any]] = []
    discrepancy_count = 0
    skipped_count = 0

    for theme_name in _theme_names(context):
        output_counts = _output_counts_by_county(context, theme_name)
        output_total = context.accessor.get_row_count(context.output_gdb, theme_name)

        source_total = 0
        themes_missing_from_sources = 0

        for source in discovery.source_geodatabases:
            source_row_count = _safe_source_count(context, source.gdb_path, theme_name)
            if source_row_count is None:
                themes_missing_from_sources += 1
                continue
            source_total += source_row_count

        if themes_missing_from_sources == len(discovery.source_geodatabases):
            skipped_count += 1
            rows.append({
                "theme": theme_name,
                "source_total": None,
                "base_county_in_output": None,
                "expected_total": None,
                "actual_output_total": output_total,
                "delta": None,
                "status": "skip",
                "note": "No source geodatabase carries this theme.",
            })
            continue

        base_county_rows = output_counts.get(context.base_fips, 0)
        expected_total = source_total + base_county_rows
        delta = output_total - expected_total

        if delta == 0:
            row_status = "pass"
            note = ""
        else:
            discrepancy_count += 1
            row_status = "fail"
            note = (
                f"The output holds {abs(delta):,} "
                f"{'more' if delta > 0 else 'fewer'} rows than the sources plus the "
                f"merge base account for."
            )

        rows.append({
            "theme": theme_name,
            "source_total": source_total,
            "base_county_in_output": base_county_rows,
            "expected_total": expected_total,
            "actual_output_total": output_total,
            "delta": delta,
            "status": row_status,
            "note": note,
        })

    details = {
        "base_fips": context.base_fips,
        "source_geodatabases": len(discovery.source_geodatabases),
        "themes_reconciled": len(rows) - skipped_count,
        "themes_skipped": skipped_count,
        "themes_with_discrepancy": discrepancy_count,
        "identity": "sum(source counts) + output[base_fips] == output total",
    }

    if discrepancy_count:
        status = CheckStatus.FAIL
        summary = (
            f"{discrepancy_count} theme(s) do not reconcile: the source totals plus the "
            f"merge base county ({context.base_fips}) do not add up to the output total."
        )
    elif skipped_count == len(rows):
        status = CheckStatus.SKIP
        summary = "No theme could be reconciled, because no source geodatabase carried any of them."
    else:
        status = CheckStatus.PASS
        summary = (
            f"All {len(rows) - skipped_count} themes reconcile exactly: the "
            f"{len(discovery.source_geodatabases)} source counties plus the merge base "
            f"county {context.base_fips} account for every row in the output."
        )

    return make_result(
        check_id="counts.base_county_reconciliation",
        category=CATEGORY,
        title="Merge base county reconciles against the output total",
        status=status,
        summary=summary,
        details=details,
        rows=rows,
    )
