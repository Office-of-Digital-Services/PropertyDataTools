"""
    Unit tests for the three report writers.

    The reports are the product - a run nobody can read has accomplished nothing - so
    these assert on content rather than merely that a file appeared. The HTML test in
    particular checks that the file is genuinely self-contained, because it is opened
    by double-clicking from a delivery folder with no web server and often no internet.
"""

import datetime
import json
import os

import pytest

from unbox.validation.models import CheckStatus, ValidationRun, make_result
from unbox.validation.reporting import (
    ALL_REPORT_FORMATS,
    HTML_REPORT_FILENAME,
    JSON_REPORT_FILENAME,
    TEXT_REPORT_FILENAME,
    build_report_folder_name,
    read_json_report,
    render_html_report,
    render_text_report,
    write_reports,
)


def build_synthetic_run() -> ValidationRun:
    """
        A run covering every status, with rows and details, so one render exercises
        every branch of the template.
    """
    started = datetime.datetime(2026, 10, 26, 14, 30, 0)

    run = ValidationRun(
        output_gdb=r"C:\deliveries\full_state_smartfabric_20261020.gdb",
        started_at=started,
        finished_at=started + datetime.timedelta(minutes=12, seconds=30),
        configuration={
            "output_gdb": r"C:\deliveries\full_state_smartfabric_20261020.gdb",
            "source_folder": r"C:\deliveries\processing_20261020",
            "base_fips": "06037",
            "counties": [],
            "perf_min_speedup": 5.0,
        },
    )

    run.results = [
        make_result(
            check_id="counts.per_county_theme",
            category="counts",
            title="Per county record counts",
            status=CheckStatus.PASS,
            summary="All 58 counties matched across all 10 themes.",
            details={"counties_compared": 58, "themes_compared": 10},
            rows=[
                {"county": "06001", "theme": "Parcels", "source": 489_123,
                 "output": 489_123, "delta": 0, "status": "pass"},
                {"county": "06003", "theme": "Parcels", "source": 1_024,
                 "output": 1_024, "delta": 0, "status": "pass"},
            ],
            duration_seconds=187.4,
            started_at=started,
        ),
        make_result(
            check_id="schema.attribute_indexes",
            category="schema",
            title="Declared attribute indexes exist",
            status=CheckStatus.FAIL,
            summary="1 of 49 declared indexes is missing from the output.",
            details={"expected": 49, "found": 48, "missing": 1},
            rows=[
                {"table": "Addresses", "field": "CITY", "index": "idx_CITY",
                 "present": False, "status": "fail"},
            ],
            duration_seconds=3.2,
            started_at=started,
        ),
        make_result(
            check_id="integrity.relationship_orphans",
            category="integrity",
            title="Relationship keys resolve",
            status=CheckStatus.WARN,
            summary="AddressBuildingRelation exists but contains no rows.",
            details={"sampled": 0},
            duration_seconds=1.1,
            started_at=started,
        ),
        make_result(
            check_id="performance.spatial_queries",
            category="performance",
            title="Spatial query performance",
            status=CheckStatus.SKIP,
            summary="Skipped: no usable unindexed control field was found on Assessments.",
            details={"reason": "no_control_field"},
            duration_seconds=0.4,
            started_at=started,
        ),
        make_result(
            check_id="performance.indexed_attribute_queries",
            category="performance",
            title="Indexed attribute query performance",
            status=CheckStatus.ERROR,
            summary="The check raised an unhandled exception.",
            details={"traceback": "Traceback (most recent call last):\n  RuntimeError: boom"},
            duration_seconds=0.9,
            started_at=started,
        ),
    ]

    return run


# ----------------------------------------------------------------------
# HTML
# ----------------------------------------------------------------------


def test_html_contains_the_verdict_every_check_and_every_row():
    run = build_synthetic_run()
    html = render_html_report(run)

    # The overall verdict is FAIL, because one check failed.
    assert run.overall_status == CheckStatus.FAIL
    assert "FAIL" in html

    for result in run.results:
        assert result.check_id in html
        assert result.title in html

    # Evidence rows and their values must appear, not just the check names.
    assert "06001" in html
    assert "idx_CITY" in html
    assert "489123" in html or "489,123" in html


def test_html_is_self_contained_with_no_external_references():
    """
        The report is opened by double-clicking it from a delivery folder. A linked
        stylesheet, a CDN script, or a web font would simply fail to load.
    """
    html = render_html_report(build_synthetic_run())

    assert "<style>" in html
    assert "<link" not in html.lower()
    assert "<script" not in html.lower()
    assert "http://" not in html
    assert "https://" not in html


def test_html_escapes_values_that_look_like_markup():
    """
        Row values come straight out of the geodatabase. A value containing angle
        brackets must render as text rather than becoming markup.
    """
    run = build_synthetic_run()
    run.results = [
        make_result(
            check_id="counts.demo",
            category="counts",
            title="Escaping demonstration",
            status=CheckStatus.PASS,
            summary="",
            rows=[{"value": "<script>alert('x')</script>"}],
        )
    ]

    html = render_html_report(run)

    assert "<script>alert" not in html
    assert "&lt;script&gt;" in html


def test_html_renders_a_traceback_for_an_errored_check():
    html = render_html_report(build_synthetic_run())

    assert "traceback" in html.lower()
    assert "RuntimeError: boom" in html


def test_html_truncates_very_large_tables_and_says_so():
    """
        A full per-county sweep produces hundreds of rows. The HTML shows a slice and
        points at the JSON, which keeps the file openable.
    """
    run = build_synthetic_run()
    run.results = [
        make_result(
            check_id="counts.many_rows",
            category="counts",
            title="Many rows",
            status=CheckStatus.PASS,
            summary="",
            rows=[{"index": row_index} for row_index in range(50)],
        )
    ]

    html = render_html_report(run, max_rows_per_table=10)

    assert "Showing the first 10 of" in html
    assert "validation_results.json" in html


def test_html_renders_an_empty_run_without_raising():
    """
        An empty run must still produce a readable page saying so, rather than a crash
        or a blank file that looks like a pass.
    """
    empty_run = ValidationRun(
        output_gdb=r"C:\deliveries\empty.gdb",
        started_at=datetime.datetime(2026, 10, 26, 9, 0, 0),
        finished_at=datetime.datetime(2026, 10, 26, 9, 0, 1),
    )

    html = render_html_report(empty_run)

    assert "No checks were run" in html
    assert "SKIP" in html


def test_html_renders_an_all_error_run_without_raising():
    run = ValidationRun(
        output_gdb=r"C:\deliveries\broken.gdb",
        started_at=datetime.datetime(2026, 10, 26, 9, 0, 0),
        finished_at=datetime.datetime(2026, 10, 26, 9, 5, 0),
    )
    run.results = [
        make_result(
            check_id=f"counts.broken_{index}",
            category="counts",
            title=f"Broken {index}",
            status=CheckStatus.ERROR,
            summary="It raised.",
            details={"traceback": "Traceback...\nValueError: nope"},
        )
        for index in range(3)
    ]

    html = render_html_report(run)

    assert run.overall_status == CheckStatus.ERROR
    assert "ERROR" in html
    assert "ValueError: nope" in html


# ----------------------------------------------------------------------
# JSON
# ----------------------------------------------------------------------


def test_json_round_trips_every_status_and_duration(tmp_path):
    run = build_synthetic_run()

    written = write_reports(run, report_dir=str(tmp_path), formats=["json"])
    reloaded = read_json_report(written["json"])

    assert reloaded["overall_status"] == "fail"
    assert reloaded["output_gdb"] == run.output_gdb
    assert len(reloaded["results"]) == len(run.results)

    for original, restored in zip(run.results, reloaded["results"]):
        assert restored["check_id"] == original.check_id
        assert restored["category"] == original.category
        assert restored["status"] == original.status.value
        assert restored["duration_seconds"] == pytest.approx(original.duration_seconds)
        assert restored["summary"] == original.summary
        assert len(restored["rows"]) == len(original.rows)


def test_json_preserves_details_and_rows_exactly(tmp_path):
    run = build_synthetic_run()

    written = write_reports(run, report_dir=str(tmp_path), formats=["json"])
    reloaded = read_json_report(written["json"])

    counts_result = next(
        result for result in reloaded["results"] if result["check_id"] == "counts.per_county_theme"
    )

    assert counts_result["details"]["counties_compared"] == 58
    assert counts_result["rows"][0]["county"] == "06001"
    assert counts_result["rows"][0]["source"] == 489_123


def test_json_records_the_configuration_and_status_counts(tmp_path):
    run = build_synthetic_run()

    written = write_reports(run, report_dir=str(tmp_path), formats=["json"])
    reloaded = read_json_report(written["json"])

    assert reloaded["configuration"]["base_fips"] == "06037"
    assert reloaded["status_counts"]["pass"] == 1
    assert reloaded["status_counts"]["fail"] == 1
    assert reloaded["status_counts"]["error"] == 1


def test_json_serializes_awkward_values_rather_than_raising(tmp_path):
    """
        Check details legitimately contain enums, sets, datetimes and numpy scalars.
        Losing a report to a serialization error after a long run would be a poor
        outcome, so unknown types are stringified instead.
    """
    run = ValidationRun(
        output_gdb=r"C:\deliveries\awkward.gdb",
        started_at=datetime.datetime(2026, 10, 26, 9, 0, 0),
        finished_at=datetime.datetime(2026, 10, 26, 9, 1, 0),
    )
    run.results = [
        make_result(
            check_id="counts.awkward",
            category="counts",
            title="Awkward values",
            status=CheckStatus.PASS,
            summary="",
            details={
                "a_status": CheckStatus.WARN,
                "a_datetime": datetime.datetime(2026, 1, 1, 12, 0, 0),
                "a_set": {3, 1, 2},
                "an_object": object(),
            },
        )
    ]

    written = write_reports(run, report_dir=str(tmp_path), formats=["json"])
    reloaded = read_json_report(written["json"])
    details = reloaded["results"][0]["details"]

    assert details["a_status"] == "warn"
    assert details["a_datetime"].startswith("2026-01-01")
    assert sorted(details["a_set"]) == [1, 2, 3]
    assert isinstance(details["an_object"], str)


def test_json_is_valid_json_on_disk(tmp_path):
    written = write_reports(build_synthetic_run(), report_dir=str(tmp_path), formats=["json"])

    with open(written["json"], "r", encoding="utf-8") as json_file:
        json.load(json_file)  # raises if the file is malformed


# ----------------------------------------------------------------------
# Text
# ----------------------------------------------------------------------


def test_text_digest_has_one_line_per_check_with_its_status():
    run = build_synthetic_run()
    digest = render_text_report(run)

    for result in run.results:
        matching_lines = [
            line for line in digest.splitlines()
            if result.check_id in line and result.status.value.upper() in line
        ]
        assert matching_lines, f"No status line found for {result.check_id}"


def test_text_digest_states_the_overall_verdict_and_tally():
    digest = render_text_report(build_synthetic_run())

    assert "OVERALL STATUS     : FAIL" in digest
    assert "PASS=1" in digest
    assert "FAIL=1" in digest
    assert "ERROR=1" in digest


def test_text_digest_expands_failures_with_their_evidence():
    """
        Someone reading the digest should learn what went wrong without opening the
        HTML.
    """
    digest = render_text_report(build_synthetic_run())

    assert "FAILURES AND ERRORS" in digest
    assert "schema.attribute_indexes" in digest
    assert "idx_CITY" in digest


def test_text_digest_says_so_when_nothing_failed():
    run = build_synthetic_run()
    run.results = [result for result in run.results if not result.is_failure]

    digest = render_text_report(run)

    assert "No failures or errors were recorded." in digest


def test_text_digest_handles_an_empty_run():
    empty_run = ValidationRun(
        output_gdb=r"C:\deliveries\empty.gdb",
        started_at=datetime.datetime(2026, 10, 26, 9, 0, 0),
        finished_at=datetime.datetime(2026, 10, 26, 9, 0, 1),
    )

    digest = render_text_report(empty_run)

    assert "(no checks were run)" in digest
    assert "OVERALL STATUS     : SKIP" in digest


def test_text_digest_handles_an_unfinished_run():
    unfinished_run = ValidationRun(
        output_gdb=r"C:\deliveries\running.gdb",
        started_at=datetime.datetime(2026, 10, 26, 9, 0, 0),
        finished_at=None,
    )

    digest = render_text_report(unfinished_run)

    assert "(still running)" in digest


# ----------------------------------------------------------------------
# write_reports orchestration
# ----------------------------------------------------------------------


def test_write_reports_creates_a_timestamped_folder_with_all_three_artifacts(tmp_path):
    timestamp = datetime.datetime(2026, 10, 26, 16, 45, 30)

    written = write_reports(
        build_synthetic_run(), report_dir=str(tmp_path),
        formats=ALL_REPORT_FORMATS, timestamp=timestamp,
    )

    assert os.path.basename(written["folder"]) == "validation_20261026_164530"
    assert os.path.isfile(written["html"])
    assert os.path.isfile(written["json"])
    assert os.path.isfile(written["text"])

    folder_contents = sorted(os.listdir(written["folder"]))
    assert folder_contents == sorted(
        [HTML_REPORT_FILENAME, JSON_REPORT_FILENAME, TEXT_REPORT_FILENAME]
    )


def test_write_reports_honours_a_format_subset(tmp_path):
    written = write_reports(build_synthetic_run(), report_dir=str(tmp_path), formats=["json"])

    assert "json" in written
    assert "html" not in written
    assert "text" not in written
    assert os.listdir(written["folder"]) == [JSON_REPORT_FILENAME]


def test_write_reports_rejects_an_unknown_format(tmp_path):
    with pytest.raises(ValueError, match="Unrecognised report format"):
        write_reports(build_synthetic_run(), report_dir=str(tmp_path), formats=["pdf"])


def test_write_reports_creates_the_parent_folder_if_absent(tmp_path):
    missing_parent = os.path.join(str(tmp_path), "does", "not", "exist", "yet")

    written = write_reports(build_synthetic_run(), report_dir=missing_parent, formats=["text"])

    assert os.path.isfile(written["text"])


def test_report_folder_name_follows_the_documented_pattern():
    name = build_report_folder_name(datetime.datetime(2026, 1, 2, 3, 4, 5))
    assert name == "validation_20260102_030405"


def test_two_runs_do_not_overwrite_each_other(tmp_path):
    """
        A nightly run must never destroy the previous night's evidence.
    """
    first = write_reports(
        build_synthetic_run(), report_dir=str(tmp_path), formats=["text"],
        timestamp=datetime.datetime(2026, 10, 26, 1, 0, 0),
    )
    second = write_reports(
        build_synthetic_run(), report_dir=str(tmp_path), formats=["text"],
        timestamp=datetime.datetime(2026, 10, 27, 1, 0, 0),
    )

    assert first["folder"] != second["folder"]
    assert os.path.isfile(first["text"])
    assert os.path.isfile(second["text"])
