"""
    Renders a ValidationRun as a single self-contained HTML file.

    "Self-contained" is a requirement rather than a nicety: the report is written to a
    delivery folder and opened by double-clicking it, so a linked stylesheet or a CDN
    script would simply fail to load. Everything - markup, CSS, data - ends up inside
    the one file, and the expand/collapse interaction uses native <details> elements
    rather than JavaScript.
"""

from __future__ import annotations

import datetime
import os
from typing import Any

import jinja2

from unbox.validation.models import CheckStatus, ValidationRun

HTML_REPORT_FILENAME = "validation_report.html"

TEMPLATE_FOLDER_NAME = "templates"
TEMPLATE_FILE_NAME = "report.html.j2"

# A per-county sweep across ten themes produces several hundred rows per check. The
# HTML shows a generous slice and points at the JSON for the remainder, which keeps the
# file openable in a browser rather than producing a multi-megabyte page.
DEFAULT_MAX_ROWS_PER_TABLE = 400

# The order status chips appear in the rollup. Fixed so the banner looks the same from
# run to run regardless of which statuses happened to occur.
STATUS_DISPLAY_ORDER = (
    CheckStatus.PASS,
    CheckStatus.FAIL,
    CheckStatus.WARN,
    CheckStatus.SKIP,
    CheckStatus.ERROR,
)


def build_template_environment() -> jinja2.Environment:
    """
        The jinja2 environment used to render the report.

        Autoescaping is on. Check rows carry values read straight out of the
        geodatabase - street names with ampersands, owner names with angle brackets -
        and those must render as text rather than as markup.
    """
    template_folder = os.path.join(os.path.dirname(os.path.abspath(__file__)), TEMPLATE_FOLDER_NAME)

    return jinja2.Environment(
        loader=jinja2.FileSystemLoader(template_folder),
        autoescape=jinja2.select_autoescape(["html", "htm", "xml", "j2"]),
        trim_blocks=True,
        lstrip_blocks=True,
    )


def render_html_report(run: ValidationRun,
                       max_rows_per_table: int = DEFAULT_MAX_ROWS_PER_TABLE) -> str:
    """
        Renders the report and returns it as a string.
    """
    environment = build_template_environment()
    template = environment.get_template(TEMPLATE_FILE_NAME)

    status_counts = run.status_counts()

    return template.render(
        run=run,
        grouped_results=run.by_category(),
        # Pre-ordered pairs, so the template does not need sorting logic.
        status_counts=[(status, status_counts[status]) for status in STATUS_DISPLAY_ORDER],
        max_rows_per_table=max_rows_per_table,
        generated_at=datetime.datetime.now(),
        format_duration=format_duration,
        is_numeric=is_numeric,
    )


def write_html_report(run: ValidationRun,
                      report_folder: str,
                      max_rows_per_table: int = DEFAULT_MAX_ROWS_PER_TABLE) -> str:
    """
        Writes validation_report.html into the folder and returns its full path.
    """
    output_path = os.path.join(report_folder, HTML_REPORT_FILENAME)

    with open(output_path, "w", encoding="utf-8") as html_file:
        html_file.write(render_html_report(run, max_rows_per_table=max_rows_per_table))

    return output_path


def format_duration(total_seconds: float) -> str:
    """
        Renders elapsed time readably: seconds under a minute, else minutes and seconds.
    """
    if total_seconds < 60:
        return f"{total_seconds:.1f} seconds"

    whole_minutes = int(total_seconds // 60)
    remaining_seconds = total_seconds - (whole_minutes * 60)
    return f"{whole_minutes}m {remaining_seconds:.0f}s"


def is_numeric(value: Any) -> bool:
    """
        Whether a table cell should be right-aligned.

        Row counts and timings read far better in a right-aligned column, and booleans
        are deliberately excluded because True/False read as labels, not quantities.
    """
    return isinstance(value, (int, float)) and not isinstance(value, bool)
