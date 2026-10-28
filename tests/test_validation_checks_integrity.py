"""
    Unit tests for the referential integrity checks.

    The scenario that matters most here is the 3-of-500 orphan case. A validation suite
    that only ever sees clean data proves nothing; these fixtures deliberately break
    links and assert the orphan rate is both detected and reported accurately.
"""

import pytest

from unbox.validation.checks.integrity import (
    check_fips_consistency,
    check_primary_key_resolution,
    check_relationship_orphans,
)
from unbox.validation.expectations import ExpectedRelationship, MergeExpectations
from unbox.validation.models import CheckStatus, ValidationContext
from tests.fakes.fake_gdb_accessor import FakeDataset, FakeGeodatabaseAccessor

OUTPUT_GDB = r"C:\fake\full_state.gdb"

RELATION_NAME = "AddressParcelRelation"

ADDRESS_PARCEL_RELATION = ExpectedRelationship(
    relationship_name=RELATION_NAME,
    origin_table="Addresses",
    destination_table="Parcels",
    origin_key="address_lid",
    destination_key="parcel_lid",
    cardinality="MANY_TO_MANY",
    is_attributed=True,
    is_countable_theme=True,
)


def build_expectations(manytomany=(ADDRESS_PARCEL_RELATION,)) -> MergeExpectations:
    return MergeExpectations(
        core_theme_names=("Parcels", "Addresses"),
        relation_table_names=(RELATION_NAME,),
        expected_indexes=(),
        spatially_indexed_tables=(),
        manytomany_relationships=manytomany,
        onetomany_relationships=(),
    )


def build_context(accessor, sample_size=500, orphan_tolerance=0.0,
                  expectations=None) -> ValidationContext:
    return ValidationContext(
        output_gdb=OUTPUT_GDB,
        accessor=accessor,
        expectations=expectations or build_expectations(),
        sample_size=sample_size,
        orphan_tolerance=orphan_tolerance,
    )


def build_relation_accessor(address_keys, parcel_keys,
                            resolvable_address_keys=None,
                            resolvable_parcel_keys=None,
                            relation_fips=None,
                            address_fips_map=None) -> FakeGeodatabaseAccessor:
    """
        Builds a fake holding one relation table and the two feature classes it links.

        Keys absent from the resolvable sets are orphans, which is how the broken
        scenarios below are expressed.
    """
    accessor = FakeGeodatabaseAccessor()

    relation_columns = {
        "address_lid": list(address_keys),
        "parcel_lid": list(parcel_keys),
    }
    if relation_fips is not None:
        relation_columns["FIPS_CODE"] = list(relation_fips)

    accessor.add_dataset(
        OUTPUT_GDB, RELATION_NAME,
        FakeDataset(data_type="RelationshipClass", column_values=relation_columns),
    )
    accessor.add_dataset(OUTPUT_GDB, "Addresses", FakeDataset(data_type="FeatureClass"))
    accessor.add_dataset(OUTPUT_GDB, "Parcels", FakeDataset(data_type="FeatureClass"))

    accessor.set_resolvable_keys(
        OUTPUT_GDB, "Addresses", "address_lid",
        resolvable_address_keys if resolvable_address_keys is not None else address_keys,
    )
    accessor.set_resolvable_keys(
        OUTPUT_GDB, "Parcels", "parcel_lid",
        resolvable_parcel_keys if resolvable_parcel_keys is not None else parcel_keys,
    )

    if address_fips_map is not None:
        accessor.set_key_value_map(
            OUTPUT_GDB, "Addresses", "address_lid", "FIPS_CODE", address_fips_map
        )

    return accessor


# ----------------------------------------------------------------------
# integrity.relationship_orphans
# ----------------------------------------------------------------------


def test_orphans_pass_when_every_sampled_key_resolves():
    """
        The healthy case, matching the measured result on the real data: 500 of 500
        address keys resolved in 0.84 seconds.
    """
    address_keys = [f"A{index}" for index in range(500)]
    parcel_keys = [f"P{index}" for index in range(500)]
    accessor = build_relation_accessor(address_keys, parcel_keys)

    results = list(check_relationship_orphans(build_context(accessor)))

    assert len(results) == 1
    assert results[0].status == CheckStatus.PASS
    assert results[0].details["total_orphans"] == 0
    assert results[0].details["orphan_rate"] == 0.0
    assert results[0].details["distinct_keys_checked"] == 1_000  # both sides


def test_orphans_fail_when_three_of_five_hundred_keys_do_not_resolve():
    """
        The headline broken case. Three unresolved keys out of 500 is a 0.6% orphan
        rate, which must fail at the default tolerance of zero and be reported
        accurately rather than rounded away.
    """
    address_keys = [f"A{index}" for index in range(500)]
    parcel_keys = [f"P{index}" for index in range(500)]

    # Three address keys point at rows that are not there.
    resolvable_addresses = address_keys[:-3]

    accessor = build_relation_accessor(
        address_keys, parcel_keys, resolvable_address_keys=resolvable_addresses
    )

    results = list(check_relationship_orphans(build_context(accessor)))
    result = results[0]

    assert result.status == CheckStatus.FAIL
    assert result.details["total_orphans"] == 3

    # The per-side rate is 3 of the 500 address keys: 0.6%.
    address_row = next(row for row in result.rows if row["key_field"] == "address_lid")
    assert address_row["keys_sampled"] == 500
    assert address_row["keys_resolved"] == 497
    assert address_row["orphans"] == 3
    assert address_row["orphan_rate"] == pytest.approx(0.006)

    # The summary reports the rate across both sides of the relation - 3 of the 1,000
    # keys checked in total - which is why it reads 0.30% rather than 0.60%.
    assert result.details["distinct_keys_checked"] == 1_000
    assert result.details["orphan_rate"] == pytest.approx(0.003)
    assert "3 of 1000 sampled keys do not resolve (0.30%)" in result.summary

    # The parcel side is clean, so it must not be tarred with the same brush.
    parcel_row = next(row for row in result.rows if row["key_field"] == "parcel_lid")
    assert parcel_row["orphans"] == 0
    assert parcel_row["status"] == "pass"


def test_orphans_within_tolerance_are_reported_as_warn_not_fail():
    """
        With a tolerance of 1%, a 0.6% orphan rate is tolerated - but still surfaced,
        because silently passing over broken links would defeat the purpose.
    """
    address_keys = [f"A{index}" for index in range(500)]
    parcel_keys = [f"P{index}" for index in range(500)]
    accessor = build_relation_accessor(
        address_keys, parcel_keys, resolvable_address_keys=address_keys[:-3]
    )

    results = list(check_relationship_orphans(
        build_context(accessor, orphan_tolerance=0.01)
    ))

    assert results[0].status == CheckStatus.WARN
    assert results[0].details["total_orphans"] == 3


def test_an_existing_but_empty_relation_is_warn_not_fail():
    """
        A relationship class with no rows is suspicious but not disqualifying - it
        might legitimately be empty - so it warns rather than failing the build.
    """
    accessor = build_relation_accessor([], [])

    results = list(check_relationship_orphans(build_context(accessor)))

    assert results[0].status == CheckStatus.WARN
    assert results[0].details["rows_sampled"] == 0
    assert "no rows to sample" in results[0].summary


def test_a_missing_relation_fails():
    accessor = FakeGeodatabaseAccessor()
    accessor.add_dataset(OUTPUT_GDB, "Addresses", FakeDataset(data_type="FeatureClass"))

    results = list(check_relationship_orphans(build_context(accessor)))

    assert results[0].status == CheckStatus.FAIL
    assert "does not exist" in results[0].summary


def test_the_sample_size_limit_is_honoured():
    """
        --sample_size must actually bound the work. Reading every one of 26 million
        keys would take hours and add nothing.
    """
    address_keys = [f"A{index}" for index in range(1_000)]
    parcel_keys = [f"P{index}" for index in range(1_000)]
    accessor = build_relation_accessor(address_keys, parcel_keys)

    results = list(check_relationship_orphans(build_context(accessor, sample_size=50)))

    assert results[0].details["rows_sampled"] == 50


def test_duplicate_keys_are_counted_once():
    """
        A relation legitimately repeats the same address across several rows. Counting
        distinct keys keeps a single popular key from dominating the orphan rate.
    """
    address_keys = ["A1"] * 100
    parcel_keys = [f"P{index}" for index in range(100)]
    accessor = build_relation_accessor(address_keys, parcel_keys)

    results = list(check_relationship_orphans(build_context(accessor)))
    address_row = next(row for row in results[0].rows if row["key_field"] == "address_lid")

    assert address_row["keys_sampled"] == 1
    assert results[0].status == CheckStatus.PASS


# ----------------------------------------------------------------------
# integrity.primary_key_resolution
# ----------------------------------------------------------------------


def build_parcels_accessor(primary_assessment_values,
                           primary_building_values,
                           resolvable_assessments=None,
                           resolvable_buildings=None) -> FakeGeodatabaseAccessor:
    accessor = FakeGeodatabaseAccessor()

    accessor.add_dataset(
        OUTPUT_GDB, "Parcels",
        FakeDataset(data_type="FeatureClass", column_values={
            "PRIMARY_ASSESSMENT_LID": list(primary_assessment_values),
            "PRIMARY_BUILDING_LID": list(primary_building_values),
        }),
    )
    accessor.add_dataset(OUTPUT_GDB, "Assessments", FakeDataset(data_type="FeatureClass"))
    accessor.add_dataset(OUTPUT_GDB, "Buildings", FakeDataset(data_type="FeatureClass"))

    non_null_assessments = [value for value in primary_assessment_values if value]
    non_null_buildings = [value for value in primary_building_values if value]

    accessor.set_resolvable_keys(
        OUTPUT_GDB, "Assessments", "ASSESSMENT_LID",
        resolvable_assessments if resolvable_assessments is not None else non_null_assessments,
    )
    accessor.set_resolvable_keys(
        OUTPUT_GDB, "Buildings", "BUILDING_LID",
        resolvable_buildings if resolvable_buildings is not None else non_null_buildings,
    )

    return accessor


def test_primary_keys_pass_when_everything_resolves():
    accessor = build_parcels_accessor(
        primary_assessment_values=[f"ASM{index}" for index in range(100)],
        primary_building_values=[f"BLD{index}" for index in range(100)],
    )

    result = check_primary_key_resolution(build_context(accessor))

    assert result.status == CheckStatus.PASS
    assert result.details["total_orphans"] == 0
    assert result.details["keys_checked"] == 200


def test_null_primary_building_values_are_tolerated_not_counted_as_orphans():
    """
        A vacant parcel legitimately has no primary building. Both fields are nullable
        in the real schema, and treating a null as an orphan would produce a report
        full of false failures.
    """
    # Half the parcels have no primary building at all.
    building_values = [f"BLD{index}" if index % 2 == 0 else None for index in range(100)]

    accessor = build_parcels_accessor(
        primary_assessment_values=[f"ASM{index}" for index in range(100)],
        primary_building_values=building_values,
    )

    result = check_primary_key_resolution(build_context(accessor))
    building_row = next(
        row for row in result.rows if row["parcel_field"] == "PRIMARY_BUILDING_LID"
    )

    assert result.status == CheckStatus.PASS
    assert building_row["null_or_empty"] == 50
    assert building_row["keys_checked"] == 50
    assert building_row["orphans"] == 0
    assert "which is valid" in building_row["note"]


def test_empty_string_primary_keys_are_treated_as_null():
    building_values = ["" for _ in range(20)]

    accessor = build_parcels_accessor(
        primary_assessment_values=[f"ASM{index}" for index in range(20)],
        primary_building_values=building_values,
    )

    result = check_primary_key_resolution(build_context(accessor))
    building_row = next(
        row for row in result.rows if row["parcel_field"] == "PRIMARY_BUILDING_LID"
    )

    assert building_row["null_or_empty"] == 20
    assert building_row["keys_checked"] == 0
    assert result.status == CheckStatus.WARN


def test_primary_keys_fail_when_one_points_at_a_missing_record():
    assessment_values = [f"ASM{index}" for index in range(100)]

    accessor = build_parcels_accessor(
        primary_assessment_values=assessment_values,
        primary_building_values=[f"BLD{index}" for index in range(100)],
        resolvable_assessments=assessment_values[:-2],   # two dangle
    )

    result = check_primary_key_resolution(build_context(accessor))

    assert result.status == CheckStatus.FAIL
    assert result.details["total_orphans"] == 2

    assessment_row = next(
        row for row in result.rows if row["parcel_field"] == "PRIMARY_ASSESSMENT_LID"
    )
    assert assessment_row["orphans"] == 2
    assert "do not exist" in assessment_row["note"] or "does not exist" in assessment_row["note"]


def test_primary_key_check_fails_when_parcels_is_absent():
    accessor = FakeGeodatabaseAccessor()

    result = check_primary_key_resolution(build_context(accessor))

    assert result.status == CheckStatus.FAIL
    assert "does not exist" in result.summary


# ----------------------------------------------------------------------
# integrity.fips_consistency
# ----------------------------------------------------------------------


def test_fips_consistency_passes_when_relation_and_feature_agree():
    address_keys = [f"A{index}" for index in range(10)]
    parcel_keys = [f"P{index}" for index in range(10)]
    relation_fips = ["06001"] * 10
    address_fips = {key: "06001" for key in address_keys}

    accessor = build_relation_accessor(
        address_keys, parcel_keys,
        relation_fips=relation_fips, address_fips_map=address_fips,
    )

    results = list(check_fips_consistency(build_context(accessor)))

    assert results[0].status == CheckStatus.PASS
    assert results[0].details["rows_disagreeing"] == 0
    assert results[0].details["rows_comparable"] == 10


def test_fips_consistency_fails_when_a_relation_row_crosses_counties():
    """
        A key that resolves but points at a row in a different county means the merge
        crossed county boundaries - structurally valid links that are factually wrong.
    """
    address_keys = [f"A{index}" for index in range(10)]
    parcel_keys = [f"P{index}" for index in range(10)]
    relation_fips = ["06001"] * 10

    # Two addresses actually belong to a different county than the relation claims.
    address_fips = {key: "06001" for key in address_keys}
    address_fips["A3"] = "06003"
    address_fips["A7"] = "06005"

    accessor = build_relation_accessor(
        address_keys, parcel_keys,
        relation_fips=relation_fips, address_fips_map=address_fips,
    )

    results = list(check_fips_consistency(build_context(accessor)))
    result = results[0]

    assert result.status == CheckStatus.FAIL
    assert result.details["rows_disagreeing"] == 2

    disagreeing_keys = {row["origin_key"] for row in result.rows}
    assert disagreeing_keys == {"A3", "A7"}


def test_fips_consistency_warns_when_nothing_could_be_resolved():
    """
        Unresolvable keys are the orphan check's business. Here there is simply nothing
        to compare, which must be a WARN rather than a vacuous PASS.
    """
    address_keys = [f"A{index}" for index in range(10)]
    parcel_keys = [f"P{index}" for index in range(10)]

    accessor = build_relation_accessor(
        address_keys, parcel_keys,
        relation_fips=["06001"] * 10,
        address_fips_map={},          # nothing resolves
    )

    results = list(check_fips_consistency(build_context(accessor)))

    assert results[0].status == CheckStatus.WARN
    assert results[0].details["rows_comparable"] == 0


def test_fips_consistency_bounds_the_number_of_examples_reported():
    """
        A systematically broken relation would otherwise produce a row per sampled
        record. The pattern matters more than the volume, so examples are capped.
    """
    address_keys = [f"A{index}" for index in range(100)]
    parcel_keys = [f"P{index}" for index in range(100)]

    accessor = build_relation_accessor(
        address_keys, parcel_keys,
        relation_fips=["06001"] * 100,
        address_fips_map={key: "06099" for key in address_keys},   # all disagree
    )

    results = list(check_fips_consistency(build_context(accessor)))

    assert results[0].status == CheckStatus.FAIL
    assert results[0].details["rows_disagreeing"] == 100
    assert len(results[0].rows) == 20
