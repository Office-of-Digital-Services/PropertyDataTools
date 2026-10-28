"""
    Executes selected checks and collects their results into a ValidationRun.

    The runner's one interesting responsibility is isolation. A full-state run takes
    ten to twenty minutes; if a single check raises on one awkward theme, losing the
    other thirty minutes of evidence would be a poor trade. So every exception is caught,
    converted into an ERROR result carrying its traceback, and the run continues.
"""

from __future__ import annotations

import dataclasses
import datetime
import logging
import time
import traceback
from typing import Any, Dict, Iterable, List, Optional, Sequence

from unbox.validation.models import (
    CheckResult,
    CheckStatus,
    ValidationContext,
    ValidationRun,
    make_result,
)
from unbox.validation.registry import CheckDescriptor, CheckRegistry, default_registry

module_logger = logging.getLogger(__name__)


def run_validation(context: ValidationContext,
                   categories: Optional[Sequence[str]] = None,
                   check_ids: Optional[Sequence[str]] = None,
                   registry: Optional[CheckRegistry] = None,
                   logger: Optional[logging.Logger] = None) -> ValidationRun:
    """
        Runs the selected checks against the context and returns the collected results.

        :param context: the geodatabase paths, expectations, accessor, and thresholds.
        :param categories: optional subset of "counts", "schema", "integrity", "performance".
        :param check_ids: optional subset of individual check ids.
        :param registry: the registry to pull checks from; defaults to the global one.
        :param logger: where progress is reported; defaults to this module's logger.
        :returns: a ValidationRun whose overall_status is the worst individual status.
    """
    active_registry = registry if registry is not None else default_registry
    active_logger = logger or module_logger

    run = ValidationRun(
        output_gdb=context.output_gdb,
        started_at=datetime.datetime.now(),
        configuration=context.configuration_summary(),
    )

    selected_descriptors = active_registry.select(categories=categories, check_ids=check_ids)
    active_logger.info(
        f"Running {len(selected_descriptors)} validation check(s) against {context.output_gdb}"
    )

    for descriptor in selected_descriptors:
        run.results.extend(_execute_descriptor(descriptor, context, active_logger))

    run.finished_at = datetime.datetime.now()
    active_logger.info(
        f"Validation finished in {run.duration_seconds:.1f}s with overall status "
        f"{run.overall_status}"
    )

    return run


def _execute_descriptor(descriptor: CheckDescriptor,
                        context: ValidationContext,
                        logger: logging.Logger) -> List[CheckResult]:
    """
        Runs one check, converting whatever it returns (or raises) into a flat list of
        CheckResults with their timings filled in.
    """
    # Checks that compare against the per-county source geodatabases cannot run at all
    # without them. Reporting SKIP here keeps that condition out of every check body.
    if descriptor.requires_source_folder and not context.source_folder:
        logger.info(f"Skipping {descriptor.check_id}: no source folder was supplied")
        return [
            make_result(
                check_id=descriptor.check_id,
                category=descriptor.category,
                title=descriptor.title,
                status=CheckStatus.SKIP,
                summary=(
                    "Skipped because no source geodatabase folder was supplied, so there "
                    "is nothing to compare the merged output against."
                ),
                details={"reason": "source_folder_not_supplied"},
            )
        ]

    logger.info(f"Running check {descriptor.check_id} - {descriptor.title}")
    started_at = datetime.datetime.now()
    started_counter = time.perf_counter()

    try:
        returned = descriptor.function(context)
        results = _flatten_results(returned)
    except Exception:
        elapsed_seconds = time.perf_counter() - started_counter
        formatted_traceback = traceback.format_exc()
        logger.error(f"Check {descriptor.check_id} raised an exception:\n{formatted_traceback}")
        return [
            make_result(
                check_id=descriptor.check_id,
                category=descriptor.category,
                title=descriptor.title,
                status=CheckStatus.ERROR,
                summary=(
                    f"The check raised an unhandled exception and could not reach a "
                    f"verdict. See the traceback in the details."
                ),
                details={"traceback": formatted_traceback},
                duration_seconds=elapsed_seconds,
                started_at=started_at,
            )
        ]

    elapsed_seconds = time.perf_counter() - started_counter

    # A check that produced nothing at all is itself a problem worth surfacing - it
    # means a sweep found no datasets to look at, which should never pass silently.
    if not results:
        return [
            make_result(
                check_id=descriptor.check_id,
                category=descriptor.category,
                title=descriptor.title,
                status=CheckStatus.SKIP,
                summary="The check ran but produced no results, so nothing was verified.",
                details={"reason": "no_results_produced"},
                duration_seconds=elapsed_seconds,
                started_at=started_at,
            )
        ]

    return [
        _apply_run_metadata(result, descriptor, started_at, elapsed_seconds, len(results))
        for result in results
    ]


def _flatten_results(returned: Any) -> List[CheckResult]:
    """
        Normalizes a check's return value into a list.

        A check may return one CheckResult or any iterable of them; both are common and
        the runner should not care which style a check author chose.
    """
    if returned is None:
        return []
    if isinstance(returned, CheckResult):
        return [returned]
    if isinstance(returned, Iterable):
        flattened = list(returned)
        for item in flattened:
            if not isinstance(item, CheckResult):
                raise TypeError(
                    f"A check returned {type(item).__name__}; checks must return "
                    f"CheckResult objects or an iterable of them."
                )
        return flattened

    raise TypeError(
        f"A check returned {type(returned).__name__}; checks must return a CheckResult, "
        f"an iterable of CheckResults, or None."
    )


def _apply_run_metadata(result: CheckResult,
                        descriptor: CheckDescriptor,
                        started_at: datetime.datetime,
                        elapsed_seconds: float,
                        result_count: int) -> CheckResult:
    """
        Fills in timing and identity fields a check did not set for itself.

        Checks are allowed to supply their own duration (a per-theme sweep knows how
        long each theme took); when they leave it at zero we apportion the measured
        wall clock time across the results the call produced.
    """
    replacements: Dict[str, Any] = {}

    if result.duration_seconds == 0.0:
        replacements["duration_seconds"] = elapsed_seconds / max(result_count, 1)

    # Allow a check to omit the boilerplate identity fields and inherit them.
    if not result.check_id:
        replacements["check_id"] = descriptor.check_id
    if not result.category:
        replacements["category"] = descriptor.category
    if not result.title:
        replacements["title"] = descriptor.title

    if not replacements:
        return result

    return dataclasses.replace(result, **replacements)
