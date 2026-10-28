"""
    Read-only validation for the merged statewide geodatabase produced by compile_gdbs.

    The module answers one question with evidence: did the merge produce a correct,
    well-indexed, queryable statewide geodatabase? It verifies four properties.

        counts       every county's records made it in, exactly once
        schema       the schema, every declared index, and every relationship exist
        integrity    the relationship keys actually resolve
        performance  indexed queries are fast because the indexes work

    Typical use:

        from unbox.validation import build_context, run_validation
        from unbox.validation.reporting import write_reports

        context = build_context(output_gdb=r"...\\full_state.gdb",
                                source_folder=r"...\\processing_20261020")
        run = run_validation(context)
        write_reports(run, report_dir=r"...\\reports")

    Everything here is read-only. See gdb_access.py, which is the only module in the
    package that imports arcpy, for the complete list of operations used.
"""

from __future__ import annotations

from typing import Any, Optional, Sequence

from unbox.validation.expectations import MergeExpectations
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
    default_registry,
    register_check,
)
from unbox.validation.runner import run_validation

# Importing the checks package is what populates the registry. Without this a caller
# would get an empty run, which is a confusing way to discover an import was missing.
from unbox.validation import checks  # noqa: F401  (imported for its registration side effect)


def build_context(output_gdb: str,
                  source_folder: Optional[str] = None,
                  base_county_gdb: Optional[str] = None,
                  base_fips: str = "06037",
                  counties: Sequence[str] = (),
                  perf_min_speedup: float = 5.0,
                  sample_size: int = 500,
                  orphan_tolerance: float = 0.0,
                  accessor: Optional[Any] = None) -> ValidationContext:
    """
        Assembles a ValidationContext with the real arcpy-backed accessor and
        expectations read from GDBMerge.

        This is the normal entry point for callers who are not writing unit tests. Tests
        construct ValidationContext directly and pass a fake accessor.

        :param output_gdb: path to the merged statewide file geodatabase.
        :param source_folder: folder of extracted per-county source geodatabases.
        :param base_county_gdb: optional copy of the merge-base county geodatabase.
        :param base_fips: FIPS code of the county that became the merge base.
        :param counties: optional subset of FIPS codes to restrict the count sweep to.
        :param perf_min_speedup: how many times faster an indexed query must be.
        :param sample_size: how many keys to sample per relation for integrity checks.
        :param orphan_tolerance: the fraction of unresolved keys tolerated before FAIL.
        :param accessor: override the accessor, chiefly for testing.
    """
    if accessor is None:
        # Imported lazily so that importing unbox.validation for its data models does
        # not pull in arcpy until an actual geodatabase is about to be read.
        from unbox.validation.gdb_access import GeodatabaseAccessor
        accessor = GeodatabaseAccessor()

    return ValidationContext(
        output_gdb=output_gdb,
        accessor=accessor,
        expectations=MergeExpectations.from_merge_class(),
        source_folder=source_folder,
        base_county_gdb=base_county_gdb,
        base_fips=base_fips,
        counties=tuple(counties),
        perf_min_speedup=perf_min_speedup,
        sample_size=sample_size,
        orphan_tolerance=orphan_tolerance,
    )


__all__ = [
    "CheckDescriptor",
    "CheckRegistry",
    "CheckResult",
    "CheckStatus",
    "MergeExpectations",
    "ValidationContext",
    "ValidationRun",
    "build_context",
    "default_registry",
    "make_result",
    "register_check",
    "run_validation",
]
