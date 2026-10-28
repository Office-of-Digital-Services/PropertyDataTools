"""
    Renders a ValidationRun as a fixed-width plain text digest.

    This is the artifact that gets pasted into an email or a ticket. It is deliberately
    narrow and column-aligned so it survives being quoted in a mail client, and it
    carries one line per check plus the overall verdict.
"""

from __future__ import annotations

import os
from typing import List

from unbox.validation.models import CheckStatus, ValidationRun

TEXT_REPORT_FILENAME = "validation_summary.txt"

# Total line width. 100 characters fits comfortably in a terminal and in most mail
# clients without wrapping.
LINE_WIDTH = 100

# Column widths for the per-check lines.
STATUS_COLUMN_WIDTH = 7
CHECK_ID_COLUMN_WIDTH = 38
DURATION_COLUMN_WIDTH = 9


def render_text_report(run: ValidationRun) -> str:
    """
        Builds the complete digest as a single string.
    """
    lines: List[str] = []

    lines.extend(_render_header(run))
    lines.append("")
    lines.extend(_render_status_rollup(run))
    lines.append("")
    lines.extend(_render_check_lines(run))
    lines.append("")
    lines.extend(_render_failure_detail(run))

    return "\n".join(lines).rstrip() + "\n"


def write_text_report(run: ValidationRun, report_folder: str) -> str:
    """
        Writes validation_summary.txt into the folder and returns its full path.
    """
    output_path = os.path.join(report_folder, TEXT_REPORT_FILENAME)

    with open(output_path, "w", encoding="utf-8") as text_file:
        text_file.write(render_text_report(run))

    return output_path


def _render_header(run: ValidationRun) -> List[str]:
    """
        Title block: what was validated, when, and the overall verdict.
    """
    # A run that is still in progress has no finish time, which the digest should state
    # plainly rather than printing "None".
    if run.finished_at is None:
        finished_text = "(still running)"
    else:
        finished_text = f"{run.finished_at:%Y-%m-%d %H:%M:%S}"

    return [
        "=" * LINE_WIDTH,
        "GEODATABASE VALIDATION SUMMARY",
        "=" * LINE_WIDTH,
        f"Output geodatabase : {run.output_gdb}",
        f"Started            : {run.started_at:%Y-%m-%d %H:%M:%S}",
        f"Finished           : {finished_text}",
        f"Elapsed            : {_format_duration(run.duration_seconds)}",
        f"OVERALL STATUS     : {run.overall_status.value.upper()}",
    ]


def _render_status_rollup(run: ValidationRun) -> List[str]:
    """
        A one-line tally of how many checks landed in each status.
    """
    counts = run.status_counts()
    tally = "  ".join(
        f"{status.value.upper()}={counts[status]}"
        for status in (CheckStatus.PASS, CheckStatus.FAIL, CheckStatus.WARN,
                       CheckStatus.SKIP, CheckStatus.ERROR)
    )
    return [
        f"Checks run: {len(run.results)}",
        f"Results   : {tally}",
    ]


def _render_check_lines(run: ValidationRun) -> List[str]:
    """
        One aligned line per check, grouped under its category heading.
    """
    lines: List[str] = [
        "-" * LINE_WIDTH,
        f"{'STATUS':<{STATUS_COLUMN_WIDTH}} "
        f"{'CHECK':<{CHECK_ID_COLUMN_WIDTH}} "
        f"{'SECONDS':>{DURATION_COLUMN_WIDTH}}  SUMMARY",
        "-" * LINE_WIDTH,
    ]

    grouped_results = run.by_category()
    if not grouped_results:
        lines.append("(no checks were run)")
        return lines

    for category_name, category_results in grouped_results.items():
        lines.append(f"[{category_name.upper()}]")
        for result in category_results:
            lines.append(
                f"{result.status.value.upper():<{STATUS_COLUMN_WIDTH}} "
                f"{_truncate(result.check_id, CHECK_ID_COLUMN_WIDTH):<{CHECK_ID_COLUMN_WIDTH}} "
                f"{result.duration_seconds:>{DURATION_COLUMN_WIDTH}.2f}  "
                f"{result.summary}"
            )
        lines.append("")

    # Drop the trailing blank line left by the last category.
    if lines and lines[-1] == "":
        lines.pop()

    return lines


def _render_failure_detail(run: ValidationRun) -> List[str]:
    """
        Expands the problems.

        Someone reading the digest wants the failures without opening the HTML, so each
        failing or erroring check gets its summary plus the first few evidence rows.
    """
    problem_results = [
        result for result in run.results
        if result.status in (CheckStatus.FAIL, CheckStatus.ERROR)
    ]

    if not problem_results:
        return ["-" * LINE_WIDTH, "No failures or errors were recorded."]

    lines: List[str] = ["-" * LINE_WIDTH, "FAILURES AND ERRORS", "-" * LINE_WIDTH]

    for result in problem_results:
        lines.append(f"{result.status.value.upper()}  {result.check_id} - {result.title}")
        lines.append(f"    {result.summary}")

        # Show a handful of evidence rows; the full set lives in the HTML and JSON.
        for row in result.rows[:10]:
            rendered_cells = ", ".join(f"{key}={value}" for key, value in row.items())
            lines.append(f"      {_truncate(rendered_cells, LINE_WIDTH - 6)}")

        if len(result.rows) > 10:
            lines.append(f"      ... and {len(result.rows) - 10} more row(s); see the HTML report.")

        lines.append("")

    return lines


def _truncate(text: str, maximum_length: int) -> str:
    """
        Shortens an over-long value with an ellipsis so columns stay aligned.
    """
    text = str(text)
    if len(text) <= maximum_length:
        return text
    return text[: maximum_length - 3] + "..."


def _format_duration(total_seconds: float) -> str:
    """
        Renders elapsed time readably: seconds under a minute, else minutes and seconds.
    """
    if total_seconds < 60:
        return f"{total_seconds:.1f}s"

    whole_minutes = int(total_seconds // 60)
    remaining_seconds = total_seconds - (whole_minutes * 60)
    return f"{whole_minutes}m {remaining_seconds:.1f}s"
