"""
    Serializes a ValidationRun to JSON.

    This artifact is the machine-readable one. It carries every field of every
    CheckResult so that two runs can be diffed against each other, and so a future
    baseline-comparison feature can be layered on without re-running anything.
"""

from __future__ import annotations

import datetime
import json
import os
from typing import Any, Dict

from unbox.validation.models import CheckResult, CheckStatus, ValidationRun

JSON_REPORT_FILENAME = "validation_results.json"

# Bumped if the serialized shape ever changes, so an old results file can still be read.
REPORT_SCHEMA_VERSION = 1


def run_to_dictionary(run: ValidationRun) -> Dict[str, Any]:
    """
        Converts a ValidationRun into plain JSON-safe types.
    """
    return {
        "schema_version": REPORT_SCHEMA_VERSION,
        "output_gdb": run.output_gdb,
        "started_at": _format_timestamp(run.started_at),
        "finished_at": _format_timestamp(run.finished_at),
        "duration_seconds": run.duration_seconds,
        "overall_status": run.overall_status.value,
        "configuration": run.configuration,
        "status_counts": {
            status.value: count for status, count in run.status_counts().items()
        },
        "results": [result_to_dictionary(result) for result in run.results],
    }


def result_to_dictionary(result: CheckResult) -> Dict[str, Any]:
    """
        Converts one CheckResult into plain JSON-safe types.
    """
    return {
        "check_id": result.check_id,
        "category": result.category,
        "title": result.title,
        "status": result.status.value,
        "summary": result.summary,
        "details": _make_json_safe(result.details),
        "rows": _make_json_safe(result.rows),
        "duration_seconds": result.duration_seconds,
        "started_at": _format_timestamp(result.started_at),
    }


def write_json_report(run: ValidationRun, report_folder: str) -> str:
    """
        Writes validation_results.json into the folder and returns its full path.
    """
    output_path = os.path.join(report_folder, JSON_REPORT_FILENAME)

    with open(output_path, "w", encoding="utf-8") as json_file:
        json.dump(run_to_dictionary(run), json_file, indent=2, sort_keys=False)

    return output_path


def read_json_report(json_path: str) -> Dict[str, Any]:
    """
        Reads a previously written results file back.

        Provided so the round trip is testable and so a future baseline comparison has
        a supported way in, rather than every caller reaching for json.load directly.
    """
    with open(json_path, "r", encoding="utf-8") as json_file:
        return json.load(json_file)


def _format_timestamp(value) -> Any:
    """
        ISO 8601 for a datetime, None passed through untouched.
    """
    if value is None:
        return None
    if isinstance(value, datetime.datetime):
        return value.isoformat()
    return str(value)


def _make_json_safe(value: Any) -> Any:
    """
        Recursively converts values json.dump cannot handle on its own.

        Check details legitimately contain things like CheckStatus enums, numpy scalars
        from a grouped count, and datetimes. Losing a report to a serialization error
        after a twenty-minute run would be a poor outcome, so anything unrecognised is
        rendered with str() rather than raising.
    """
    if value is None or isinstance(value, (bool, int, float, str)):
        return value
    if isinstance(value, CheckStatus):
        return value.value
    if isinstance(value, datetime.datetime):
        return value.isoformat()
    if isinstance(value, dict):
        return {str(key): _make_json_safe(item) for key, item in value.items()}
    if isinstance(value, (list, tuple, set)):
        return [_make_json_safe(item) for item in value]

    # numpy integers and floats expose .item(); use it to get a native Python scalar.
    item_method = getattr(value, "item", None)
    if callable(item_method):
        try:
            return _make_json_safe(item_method())
        except Exception:
            pass

    return str(value)
