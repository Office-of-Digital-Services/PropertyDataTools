"""
    Report writers for a ValidationRun.

    One run produces up to three artifacts in one timestamped folder:

        validation_report.html   a self-contained human report
        validation_results.json  machine-readable, for diffing runs over time
        validation_summary.txt   a digest for pasting into an email or a ticket

    The timestamped folder means a nightly run never overwrites yesterday's evidence.
"""

from __future__ import annotations

import datetime
import logging
import os
from typing import Dict, Optional, Sequence

from unbox.validation.models import ValidationRun
from unbox.validation.reporting.html_report import (
    HTML_REPORT_FILENAME,
    render_html_report,
    write_html_report,
)
from unbox.validation.reporting.json_report import (
    JSON_REPORT_FILENAME,
    read_json_report,
    run_to_dictionary,
    write_json_report,
)
from unbox.validation.reporting.text_report import (
    TEXT_REPORT_FILENAME,
    render_text_report,
    write_text_report,
)

module_logger = logging.getLogger(__name__)

# The formats write_reports understands.
ALL_REPORT_FORMATS = ("html", "json", "text")

# Folder name pattern: validation_<yyyymmdd>_<hhmmss>.
REPORT_FOLDER_PREFIX = "validation"


def build_report_folder_name(timestamp: Optional[datetime.datetime] = None) -> str:
    """
        The timestamped folder name for one run's artifacts.
    """
    moment = timestamp or datetime.datetime.now()
    return f"{REPORT_FOLDER_PREFIX}_{moment:%Y%m%d_%H%M%S}"


def write_reports(run: ValidationRun,
                  report_dir: str,
                  formats: Sequence[str] = ALL_REPORT_FORMATS,
                  timestamp: Optional[datetime.datetime] = None,
                  logger: Optional[logging.Logger] = None) -> Dict[str, str]:
    """
        Writes the requested artifacts into a new timestamped folder under report_dir.

        :param run: the completed run to report on.
        :param report_dir: parent folder; the timestamped subfolder is created inside it.
        :param formats: any of "html", "json", "text".
        :param timestamp: overrides the folder timestamp, which makes tests deterministic.
        :param logger: where progress is reported.
        :returns: {format_name: written_file_path}, plus "folder" for the folder itself.
        :raises ValueError: if an unrecognised format name is requested.
    """
    active_logger = logger or module_logger

    unrecognised_formats = [
        format_name for format_name in formats
        if format_name.strip().lower() not in ALL_REPORT_FORMATS
    ]
    if unrecognised_formats:
        raise ValueError(
            f"Unrecognised report format(s) {unrecognised_formats}. "
            f"Supported formats are {list(ALL_REPORT_FORMATS)}."
        )

    report_folder = os.path.join(report_dir, build_report_folder_name(timestamp))
    os.makedirs(report_folder, exist_ok=True)

    written_paths: Dict[str, str] = {"folder": report_folder}
    requested = {format_name.strip().lower() for format_name in formats}

    # JSON is written first deliberately. It is the cheapest and least likely to fail,
    # so if HTML rendering hits an unexpected value the raw evidence still survives.
    if "json" in requested:
        written_paths["json"] = write_json_report(run, report_folder)
        active_logger.info(f"Wrote {written_paths['json']}")

    if "text" in requested:
        written_paths["text"] = write_text_report(run, report_folder)
        active_logger.info(f"Wrote {written_paths['text']}")

    if "html" in requested:
        written_paths["html"] = write_html_report(run, report_folder)
        active_logger.info(f"Wrote {written_paths['html']}")

    return written_paths


__all__ = [
    "ALL_REPORT_FORMATS",
    "HTML_REPORT_FILENAME",
    "JSON_REPORT_FILENAME",
    "TEXT_REPORT_FILENAME",
    "build_report_folder_name",
    "read_json_report",
    "render_html_report",
    "render_text_report",
    "run_to_dictionary",
    "write_html_report",
    "write_json_report",
    "write_reports",
    "write_text_report",
]
