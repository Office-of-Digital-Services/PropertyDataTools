"""
validate_gdb_cli.py

Command line wrapper for validating the merged statewide Esri File Geodatabase
produced by compile_gdbs.py.

Every operation this performs is read-only. The merged geodatabase takes 8-24 hours to
build and is never modified, repaired, reindexed, or rebuilt by this tool.

Goals:
    - output FGDB to validate (required)
    - optional folder of per-county source FGDBs to compare record counts against
    - which families of checks to run, and the thresholds they use
    - where to write the HTML / JSON / text report

Typical use:

    python validate_gdb_cli.py ^
        --output_gdb "...\\full_state_smartfabric_20261020.gdb" ^
        --source_folder "...\\processing_20261020" ^
        --report_dir "...\\validation_reports"

A quick iteration against two counties, schema and counts only:

    python validate_gdb_cli.py --output_gdb "...\\full_state.gdb" ^
        --source_folder "...\\processing_20261020" ^
        --counties 06001,06003 --categories counts,schema

Exits non-zero when any check fails, so the command can gate a pipeline.
"""

from __future__ import annotations

import json
import logging
import os
import sys
from typing import Optional, Tuple

import click

from unbox.validation import build_context, run_validation
from unbox.validation.models import CheckStatus
from unbox.validation.registry import CATEGORY_ORDER
from unbox.validation.reporting import ALL_REPORT_FORMATS, write_reports


# --------------------------------------------------------------------------------------
# Types / helpers
# --------------------------------------------------------------------------------------


def _split_comma_separated(value: Optional[str]) -> Tuple[str, ...]:
    """
        Turns "06001, 06003" into ("06001", "06003"), dropping blanks.
    """
    if not value:
        return ()
    return tuple(item.strip() for item in value.split(",") if item.strip())


def _validate_counties(ctx: click.Context, param: click.Parameter,
                       value: Optional[str]) -> Tuple[str, ...]:
    """
        Parses and sanity-checks the --counties filter.

        California FIPS codes are five digits ("06001"). Catching a malformed code here
        is much friendlier than silently sweeping zero counties and reporting a pass.
    """
    county_codes = _split_comma_separated(value)

    malformed = [code for code in county_codes if not (code.isdigit() and len(code) == 5)]
    if malformed:
        raise click.BadParameter(
            f"County codes must be five digits, e.g. 06001. Got: {malformed}"
        )

    return county_codes


def _validate_categories(ctx: click.Context, param: click.Parameter,
                         value: Optional[str]) -> Tuple[str, ...]:
    """
        Parses and checks the --categories filter against the known check families.
    """
    categories = tuple(name.lower() for name in _split_comma_separated(value))

    unknown = [name for name in categories if name not in CATEGORY_ORDER]
    if unknown:
        raise click.BadParameter(
            f"Unknown check categor{'y' if len(unknown) == 1 else 'ies'} {unknown}. "
            f"Valid categories are {list(CATEGORY_ORDER)}."
        )

    return categories


def _validate_report_formats(ctx: click.Context, param: click.Parameter,
                             value: Optional[str]) -> Tuple[str, ...]:
    """
        Parses --report_formats, defaulting to all three when unspecified.
    """
    formats = tuple(name.lower() for name in _split_comma_separated(value))
    if not formats:
        return ALL_REPORT_FORMATS

    unknown = [name for name in formats if name not in ALL_REPORT_FORMATS]
    if unknown:
        raise click.BadParameter(
            f"Unknown report format(s) {unknown}. "
            f"Valid formats are {list(ALL_REPORT_FORMATS)}."
        )

    return formats


def _validate_existing_gdb(ctx: click.Context, param: click.Parameter,
                           value: Optional[str]) -> Optional[str]:
    """
        Confirms an optional geodatabase path exists before any check runs.

        Failing here gives a clear message immediately rather than an obscure arcpy
        error several minutes into a sweep.
    """
    if value is None:
        return None

    trimmed = value.strip()
    if not trimmed:
        return None

    if not os.path.exists(trimmed):
        raise click.BadParameter(f"Path does not exist: {trimmed}")

    return trimmed


def _configure_logging(verbose: bool) -> None:
    """
        Sends progress to stdout. A full-state run takes 10-20 minutes, so the operator
        needs to see which check is currently running.
    """
    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(logging.Formatter("%(asctime)s  %(levelname)-7s %(message)s"))

    root_logger = logging.getLogger()
    root_logger.setLevel(logging.DEBUG if verbose else logging.INFO)
    root_logger.addHandler(handler)


# --------------------------------------------------------------------------------------
# Core logic
# --------------------------------------------------------------------------------------
def main(cfg: dict) -> int:
    """
        Runs the validation described by cfg and writes the reports.

        :param cfg: the parsed CLI configuration.
        :returns: a process exit code - 0 when nothing failed, 1 when something did.
    """
    click.echo("Parsed configuration:")
    click.echo(json.dumps(cfg, indent=2, sort_keys=True, default=str))
    click.echo("")
    click.echo("All operations are read-only. The geodatabase will not be modified.")
    click.echo("")

    context = build_context(
        output_gdb=cfg["output_gdb"],
        source_folder=cfg["source_folder"],
        base_county_gdb=cfg["base_county_gdb"],
        base_fips=cfg["base_fips"],
        counties=cfg["counties"],
        perf_min_speedup=cfg["perf_min_speedup"],
        sample_size=cfg["sample_size"],
        orphan_tolerance=cfg["orphan_tolerance"],
    )

    # Confirm the geodatabase is readable before spending time on anything else.
    if not context.accessor.workspace_exists(context.output_gdb):
        raise click.ClickException(
            f"arcpy cannot open the output geodatabase at {context.output_gdb}. "
            f"Check the path and that it is a valid file geodatabase."
        )

    run = run_validation(context, categories=cfg["categories"] or None)

    written_paths = write_reports(
        run,
        report_dir=cfg["report_dir"],
        formats=cfg["report_formats"],
    )

    _echo_run_summary(run, written_paths)

    # ERROR counts as a failure: a check that could not reach a verdict has not
    # established that the data are sound, so the pipeline should not proceed.
    return 1 if any(result.is_failure for result in run.results) else 0


def _echo_run_summary(run, written_paths: dict) -> None:
    """
        Prints the verdict and where the artifacts landed.
    """
    status_counts = run.status_counts()

    click.echo("")
    click.echo("=" * 78)
    click.echo(f"OVERALL STATUS: {run.overall_status.value.upper()}")
    click.echo("=" * 78)
    click.echo(
        "  ".join(
            f"{status.value.upper()}={status_counts[status]}"
            for status in (CheckStatus.PASS, CheckStatus.FAIL, CheckStatus.WARN,
                           CheckStatus.SKIP, CheckStatus.ERROR)
        )
    )

    # Name the problems directly, so the operator does not have to open a file to learn
    # whether anything needs attention.
    for result in run.results:
        if result.is_failure:
            click.echo(f"  {result.status.value.upper():<6} {result.check_id}: {result.summary}")

    click.echo("")
    click.echo(f"Reports written to {written_paths['folder']}")
    for format_name in ("html", "json", "text"):
        if format_name in written_paths:
            click.echo(f"  {format_name:<5} {written_paths[format_name]}")


# --------------------------------------------------------------------------------------
# CLI
# --------------------------------------------------------------------------------------
CONTEXT_SETTINGS = {
    "help_option_names": ["-h", "--help"],
}


@click.command(context_settings=CONTEXT_SETTINGS)
@click.option(
    "--output_gdb",
    "output_gdb",
    required=True,
    type=click.Path(exists=True, file_okay=False, dir_okay=True, readable=True, path_type=str),
    help="Path to the merged output Esri File Geodatabase to validate (folder ending in .gdb).",
)
@click.option(
    "--source_folder",
    default=None,
    callback=_validate_existing_gdb,
    type=click.Path(exists=False, file_okay=False, dir_okay=True, path_type=str),
    help=(
        "Folder containing the extracted per-county source geodatabases. "
        "Without it the record count checks are skipped."
    ),
)
@click.option(
    "--base_county_gdb",
    default=None,
    callback=_validate_existing_gdb,
    help=(
        "Optional copy of the merge-base county geodatabase (normally LA). "
        "Usually absent, because that geodatabase became the output."
    ),
)
@click.option(
    "--base_fips",
    default="06037",
    show_default=True,
    help="FIPS code of the county that was promoted to become the merge base.",
)
@click.option(
    "--counties",
    default=None,
    callback=_validate_counties,
    help=(
        "Comma separated FIPS codes to restrict the count sweep to, e.g. 06001,06003. "
        "Defaults to every county found."
    ),
)
@click.option(
    "--categories",
    default=None,
    callback=_validate_categories,
    help=(
        f"Comma separated check families to run. "
        f"Valid values: {', '.join(CATEGORY_ORDER)}. Defaults to all."
    ),
)
@click.option(
    "--report_dir",
    default=None,
    type=click.Path(exists=False, file_okay=False, dir_okay=True, path_type=str),
    help=(
        "Parent folder for the report. A timestamped subfolder is created inside it. "
        "Defaults to a 'validation_reports' folder beside the output geodatabase."
    ),
)
@click.option(
    "--report_formats",
    default=None,
    callback=_validate_report_formats,
    help=f"Comma separated artifacts to write. Valid values: {', '.join(ALL_REPORT_FORMATS)}.",
)
@click.option(
    "--perf_min_speedup",
    default=5.0,
    show_default=True,
    type=float,
    help=(
        "How many times faster an indexed query must be than the unindexed control "
        "scan to pass. The observed gap on this data is roughly 100x."
    ),
)
@click.option(
    "--sample_size",
    default=500,
    show_default=True,
    type=int,
    help="How many keys to sample per relation when checking referential integrity.",
)
@click.option(
    "--orphan_tolerance",
    default=0.0,
    show_default=True,
    type=float,
    help="Fraction of unresolved keys tolerated before a relation fails, e.g. 0.001 for 0.1%.",
)
@click.option(
    "--verbose",
    is_flag=True,
    default=False,
    help="Emit debug level logging.",
)
@click.option(
    "--usage",
    is_flag=True,
    default=False,
    help="Print usage information and exit (alias for --help).",
)
def cli(
    output_gdb: str,
    source_folder: Optional[str],
    base_county_gdb: Optional[str],
    base_fips: str,
    counties: Tuple[str, ...],
    categories: Tuple[str, ...],
    report_dir: Optional[str],
    report_formats: Tuple[str, ...],
    perf_min_speedup: float,
    sample_size: int,
    orphan_tolerance: float,
    verbose: bool,
    usage: bool,
) -> None:
    """
    Validate a merged statewide FGDB, read-only, and write a report.

    Run `python validate_gdb_cli.py --help` to see full usage.
    """
    if usage:
        # Print click's normal help text and exit 0.
        click.echo(click.get_current_context().get_help())
        raise SystemExit(0)

    if sample_size < 1:
        raise click.BadParameter("--sample_size must be at least 1.")
    if perf_min_speedup <= 0:
        raise click.BadParameter("--perf_min_speedup must be greater than zero.")
    if not (0.0 <= orphan_tolerance <= 1.0):
        raise click.BadParameter("--orphan_tolerance must be a fraction between 0 and 1.")

    # Default the report location to sit beside the geodatabase being validated, which
    # keeps the evidence with the delivery it describes.
    if not report_dir:
        report_dir = os.path.join(os.path.dirname(os.path.abspath(output_gdb)), "validation_reports")
    os.makedirs(report_dir, exist_ok=True)

    _configure_logging(verbose)

    cfg = {
        "output_gdb": output_gdb,
        "source_folder": source_folder,
        "base_county_gdb": base_county_gdb,
        "base_fips": base_fips,
        "counties": list(counties),
        "categories": list(categories),
        "report_dir": report_dir,
        "report_formats": list(report_formats),
        "perf_min_speedup": perf_min_speedup,
        "sample_size": sample_size,
        "orphan_tolerance": orphan_tolerance,
    }

    exit_code = main(cfg)
    raise SystemExit(exit_code)


if __name__ == "__main__":
    cli()
