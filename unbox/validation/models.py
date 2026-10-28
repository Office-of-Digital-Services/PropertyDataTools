"""
    Core data structures shared by every part of the validation module.

    Nothing in this file touches arcpy or the filesystem. These are plain, serializable
    containers so that check functions, the runner, and the report writers all agree on
    one vocabulary: a check produces a CheckResult, and a run collects CheckResults into
    a ValidationRun.
"""

from __future__ import annotations

import dataclasses
import datetime
import enum
from typing import Any, Dict, List, Optional, Sequence, Tuple


class CheckStatus(enum.Enum):
    """
        The verdict of a single check.

        The distinction between SKIP and FAIL matters a great deal here. A check that
        cannot gather the evidence it needs (no source folder supplied, a query that
        returned zero rows and so carries no timing information) must report SKIP rather
        than quietly passing. A silent false PASS is the most dangerous defect a
        validation suite can have.
    """

    PASS = "pass"
    FAIL = "fail"
    WARN = "warn"    # suspicious, worth a human look, but not disqualifying
    SKIP = "skip"    # a prerequisite was absent, so no verdict could be reached
    ERROR = "error"  # the check itself raised an exception

    def __str__(self) -> str:
        return self.value


# Ordering used to roll individual results up into one overall verdict. Anything that
# represents "we know something is wrong" or "we could not tell" outranks a pass.
_STATUS_SEVERITY: Dict[CheckStatus, int] = {
    CheckStatus.PASS: 0,
    CheckStatus.SKIP: 1,
    CheckStatus.WARN: 2,
    CheckStatus.ERROR: 3,
    CheckStatus.FAIL: 4,
}


@dataclasses.dataclass(frozen=True)
class CheckResult:
    """
        The uniform result produced by every check, whatever it inspected.

        `details` holds scalars rendered in the header of a check's report section
        (counts, thresholds, elapsed seconds). `rows` holds the tabular evidence that is
        rendered as an HTML table - one dictionary per row, keys become column headers.
    """

    check_id: str                                   # e.g. "counts.per_county_theme"
    category: str                                   # counts | schema | performance | integrity
    title: str                                      # short human-readable name
    status: CheckStatus
    summary: str                                    # one sentence a non-developer can read
    details: Dict[str, Any] = dataclasses.field(default_factory=dict)
    rows: List[Dict[str, Any]] = dataclasses.field(default_factory=list)
    duration_seconds: float = 0.0
    started_at: datetime.datetime = dataclasses.field(default_factory=datetime.datetime.now)

    @property
    def is_failure(self) -> bool:
        """
            True when this result should prevent the overall run from passing.
            An ERROR counts as a failure because an unevaluated check is not a safe check.
        """
        return self.status in (CheckStatus.FAIL, CheckStatus.ERROR)

    @property
    def row_column_names(self) -> List[str]:
        """
            The union of every key across `rows`, in first-seen order, so the report can
            build a stable table header even when individual rows omit a key.
        """
        column_names: List[str] = []
        for row in self.rows:
            for key in row.keys():
                if key not in column_names:
                    column_names.append(key)
        return column_names


@dataclasses.dataclass
class ValidationRun:
    """
        One complete execution of the validation suite against one output geodatabase.
    """

    output_gdb: str
    started_at: datetime.datetime
    finished_at: Optional[datetime.datetime] = None
    results: List[CheckResult] = dataclasses.field(default_factory=list)
    # Free-form record of how the run was configured, echoed into the reports so a
    # reader can tell which thresholds and filters produced these numbers.
    configuration: Dict[str, Any] = dataclasses.field(default_factory=dict)

    @property
    def overall_status(self) -> CheckStatus:
        """
            The worst status in the run, by the severity ordering above. An empty run is
            reported as SKIP rather than PASS, because nothing was actually verified.
        """
        if not self.results:
            return CheckStatus.SKIP

        return max(
            (result.status for result in self.results),
            key=lambda status: _STATUS_SEVERITY[status],
        )

    @property
    def duration_seconds(self) -> float:
        """
            Wall clock seconds for the whole run, or 0.0 if it has not finished yet.
        """
        if self.finished_at is None:
            return 0.0
        return (self.finished_at - self.started_at).total_seconds()

    def by_category(self) -> Dict[str, List[CheckResult]]:
        """
            Results grouped by category, preserving the order in which categories were
            first encountered so the report sections stay in execution order.
        """
        grouped: Dict[str, List[CheckResult]] = {}
        for result in self.results:
            grouped.setdefault(result.category, []).append(result)
        return grouped

    def status_counts(self) -> Dict[CheckStatus, int]:
        """
            How many results landed in each status, used for the report's rollup banner.
        """
        counts = {status: 0 for status in CheckStatus}
        for result in self.results:
            counts[result.status] += 1
        return counts


@dataclasses.dataclass
class ValidationContext:
    """
        Everything a check needs to do its job, passed as a single argument.

        Checks never import arcpy and never read configuration from anywhere else. They
        ask `accessor` for facts about the data and `expectations` for what those facts
        ought to be. That is what makes every check unit-testable against a fake.
    """

    output_gdb: str
    # The accessor is deliberately untyped here to avoid importing gdb_access (and
    # therefore arcpy) into this module. Any object implementing the read-only accessor
    # interface - including the test fake - is acceptable.
    accessor: Any
    expectations: Any

    # Folder holding the extracted per-county source geodatabases. When None, the count
    # checks have nothing to compare against and will report SKIP.
    source_folder: Optional[str] = None
    # Optional direct copy of the merge-base county geodatabase. Normally absent, since
    # that geodatabase was consumed to become the output.
    base_county_gdb: Optional[str] = None
    base_fips: str = "06037"

    # Restricts the count sweep to these FIPS codes. Empty means "every county found".
    counties: Tuple[str, ...] = ()

    # Tuning knobs, all surfaced as CLI options.
    perf_min_speedup: float = 5.0
    sample_size: int = 500
    orphan_tolerance: float = 0.0

    # Scratch space shared between checks within a single run. `performance` uses this
    # to cache the expensive unindexed scan baseline so later checks can reuse it.
    shared_state: Dict[str, Any] = dataclasses.field(default_factory=dict)

    def is_county_selected(self, fips_code: str) -> bool:
        """
            Whether a county should be included, honouring the optional --counties filter.
        """
        if not self.counties:
            return True
        return fips_code in self.counties

    def configuration_summary(self) -> Dict[str, Any]:
        """
            A JSON-safe echo of the run configuration for the report header.
        """
        return {
            "output_gdb": self.output_gdb,
            "source_folder": self.source_folder,
            "base_county_gdb": self.base_county_gdb,
            "base_fips": self.base_fips,
            "counties": list(self.counties),
            "perf_min_speedup": self.perf_min_speedup,
            "sample_size": self.sample_size,
            "orphan_tolerance": self.orphan_tolerance,
        }


def make_result(check_id: str,
                category: str,
                title: str,
                status: CheckStatus,
                summary: str,
                details: Optional[Dict[str, Any]] = None,
                rows: Optional[Sequence[Dict[str, Any]]] = None,
                duration_seconds: float = 0.0,
                started_at: Optional[datetime.datetime] = None) -> CheckResult:
    """
        Small convenience constructor so check bodies stay readable.

        Checks build results in several branches (pass / fail / skip) and repeating the
        full dataclass constructor at each branch obscures the logic being expressed.
    """
    return CheckResult(
        check_id=check_id,
        category=category,
        title=title,
        status=status,
        summary=summary,
        details=dict(details or {}),
        rows=[dict(row) for row in (rows or [])],
        duration_seconds=duration_seconds,
        started_at=started_at or datetime.datetime.now(),
    )
