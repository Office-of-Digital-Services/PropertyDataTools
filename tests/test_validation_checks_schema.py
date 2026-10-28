"""
    Unit tests for the schema, index, and relationship class checks.

    As with the count tests, the interesting cases are the corrupted ones. The fixtures
    mirror shapes confirmed against the real output geodatabase: idx_Parcel_LID indexing
    a field arcpy reports as PARCEL_LID, AddressParcelRelation holding RID and lowercase
    key names where the source table holds OBJECTID and uppercase ones.
"""

import os

import pytest

from unbox.validation.checks.schema import (
    check_attribute_indexes,
    check_field_conformance,
    check_relationship_classes,
    check_spatial_indexes,
)
from unbox.validation.expectations import (
    ExpectedIndex,
    ExpectedRelationship,
    MergeExpectations,
)
from unbox.validation.models import CheckStatus, ValidationContext
from tests.fakes.fake_gdb_accessor import (
    FakeDataset,
    FakeFieldDefinition,
    FakeGeodatabaseAccessor,
    FakeIndexDefinition,
    FakeRelationshipDefinition,
)

OUTPUT_GDB = r"C:\fake\full_state.gdb"


def build_expectations(expected_indexes=None,
                       spatially_indexed_tables=("Parcels",),
                       manytomany=None,
                       onetomany=(),
                       core_themes=("Parcels",),
                       relation_tables=()) -> MergeExpectations:
    """
        A small, explicit set of expectations. Using a purpose-built set rather than the
        real GDBMerge one keeps each test focused on the behaviour it is exercising.
    """
    if expected_indexes is None:
        expected_indexes = (
            # Declared as "Parcel_LID" but indexed on a field arcpy reports as
            # PARCEL_LID - exactly the casing mismatch present in the real build.
            ExpectedIndex("Parcels", "Parcel_LID", "idx_Parcel_LID", "KEY_INDEXES"),
            ExpectedIndex("Parcels", "FIPS_CODE", "idx_FIPS_CODE", "ATTRIBUTE_INDEXES"),
        )

    if manytomany is None:
        manytomany = (
            ExpectedRelationship(
                relationship_name="AddressParcelRelation",
                origin_table="Addresses",
                destination_table="Parcels",
                origin_key="address_lid",
                destination_key="parcel_lid",
                cardinality="MANY_TO_MANY",
                is_attributed=True,
                is_countable_theme=True,
            ),
        )

    return MergeExpectations(
        core_theme_names=core_themes,
        relation_table_names=relation_tables,
        expected_indexes=expected_indexes,
        spatially_indexed_tables=spatially_indexed_tables,
        manytomany_relationships=manytomany,
        onetomany_relationships=onetomany,
    )


def build_context(accessor, expectations=None, source_folder=None) -> ValidationContext:
    return ValidationContext(
        output_gdb=OUTPUT_GDB,
        accessor=accessor,
        expectations=expectations or build_expectations(),
        source_folder=source_folder,
    )


def healthy_parcels_indexes():
    """
        The index list the real Parcels feature class reports.
    """
    return [
        FakeIndexDefinition("FDO_OBJECTID", ("OBJECTID",), is_unique=True),
        FakeIndexDefinition("idx_Parcel_LID", ("PARCEL_LID",)),
        FakeIndexDefinition("idx_FIPS_CODE", ("FIPS_CODE",)),
        FakeIndexDefinition("FDO_SHAPE", ("SHAPE",)),
    ]


# ----------------------------------------------------------------------
# schema.attribute_indexes
# ----------------------------------------------------------------------


def test_attribute_indexes_pass_when_every_declared_index_exists():
    accessor = FakeGeodatabaseAccessor()
    accessor.add_dataset(OUTPUT_GDB, "Parcels",
                         FakeDataset(data_type="FeatureClass", indexes=healthy_parcels_indexes()))

    result = check_attribute_indexes(build_context(accessor))

    assert result.status == CheckStatus.PASS
    assert result.details["indexes_missing"] == 0
    assert len(result.rows) == 2


def test_attribute_index_matching_is_case_insensitive():
    """
        The real build declares ("Parcels", "Parcel_LID") while arcpy reports the
        indexed field as PARCEL_LID. A case-sensitive comparison would report every
        such index as missing.
    """
    accessor = FakeGeodatabaseAccessor()
    accessor.add_dataset(OUTPUT_GDB, "Parcels",
                         FakeDataset(data_type="FeatureClass", indexes=healthy_parcels_indexes()))

    result = check_attribute_indexes(build_context(accessor))
    parcel_lid_row = next(row for row in result.rows if row["field"] == "Parcel_LID")

    assert result.status == CheckStatus.PASS
    assert parcel_lid_row["status"] == "pass"
    assert parcel_lid_row["actual_index"] == "idx_Parcel_LID"


def test_attribute_indexes_fail_when_an_index_is_removed():
    """
        Removing idx_CITY must be reported, naming the table and field.
    """
    expectations = build_expectations(
        expected_indexes=(
            ExpectedIndex("Addresses", "CITY", "idx_CITY", "ATTRIBUTE_INDEXES"),
            ExpectedIndex("Addresses", "ZIP", "idx_ZIP", "ATTRIBUTE_INDEXES"),
        )
    )

    accessor = FakeGeodatabaseAccessor()
    accessor.add_dataset(
        OUTPUT_GDB, "Addresses",
        FakeDataset(data_type="FeatureClass", indexes=[
            # idx_CITY is deliberately absent.
            FakeIndexDefinition("idx_ZIP", ("ZIP",)),
        ]),
    )

    result = check_attribute_indexes(build_context(accessor, expectations))

    assert result.status == CheckStatus.FAIL
    assert result.details["indexes_missing"] == 1
    assert "Addresses.CITY" in result.summary

    city_row = next(row for row in result.rows if row["field"] == "CITY")
    assert city_row["status"] == "fail"
    assert city_row["actual_index"] == ""


def test_attribute_indexes_warn_when_an_index_exists_under_another_name():
    """
        The field is indexed, so queries are fast - which is what matters - but the
        naming convention was not followed, so this is a WARN rather than a FAIL.
    """
    accessor = FakeGeodatabaseAccessor()
    accessor.add_dataset(
        OUTPUT_GDB, "Parcels",
        FakeDataset(data_type="FeatureClass", indexes=[
            FakeIndexDefinition("some_other_name", ("PARCEL_LID",)),
            FakeIndexDefinition("idx_FIPS_CODE", ("FIPS_CODE",)),
        ]),
    )

    result = check_attribute_indexes(build_context(accessor))

    assert result.status == CheckStatus.WARN
    assert result.details["indexes_missing"] == 0
    assert result.details["indexes_present_under_another_name"] == 1


def test_attribute_indexes_report_an_unreadable_table_without_aborting():
    """
        One table that cannot be read must not cost the other expectations.
    """
    expectations = build_expectations(
        expected_indexes=(
            ExpectedIndex("Missing", "SOME_FIELD", "idx_SOME_FIELD", "KEY_INDEXES"),
            ExpectedIndex("Parcels", "FIPS_CODE", "idx_FIPS_CODE", "ATTRIBUTE_INDEXES"),
        )
    )

    accessor = FakeGeodatabaseAccessor()
    accessor.add_dataset(OUTPUT_GDB, "Parcels",
                         FakeDataset(data_type="FeatureClass", indexes=healthy_parcels_indexes()))

    result = check_attribute_indexes(build_context(accessor, expectations))

    assert result.status == CheckStatus.FAIL
    assert "Missing" in result.details["unreadable_tables"]
    # The readable table was still checked.
    assert any(row["table"] == "Parcels" and row["status"] == "pass" for row in result.rows)


# ----------------------------------------------------------------------
# schema.spatial_indexes
# ----------------------------------------------------------------------


def test_spatial_indexes_pass_when_fdo_shape_is_present():
    accessor = FakeGeodatabaseAccessor()
    accessor.add_dataset(OUTPUT_GDB, "Parcels",
                         FakeDataset(data_type="FeatureClass", indexes=healthy_parcels_indexes()))

    result = check_spatial_indexes(build_context(accessor))

    assert result.status == CheckStatus.PASS
    assert result.details["spatial_indexes_missing"] == 0
    assert result.rows[0]["spatial_index"] == "FDO_SHAPE"


def test_spatial_indexes_fail_when_fdo_shape_is_missing():
    """
        Without FDO_SHAPE, a bbox select on 11.6M parcels goes from 0.24s to a full
        scan - the difference between a usable dataset and an unusable one.
    """
    accessor = FakeGeodatabaseAccessor()
    accessor.add_dataset(
        OUTPUT_GDB, "Parcels",
        FakeDataset(data_type="FeatureClass", indexes=[
            FakeIndexDefinition("idx_Parcel_LID", ("PARCEL_LID",)),
        ]),
    )

    result = check_spatial_indexes(build_context(accessor))

    assert result.status == CheckStatus.FAIL
    assert result.details["spatial_indexes_missing"] == 1
    assert "Parcels" in result.summary
    assert "scan every row" in result.rows[0]["note"]


# ----------------------------------------------------------------------
# schema.relationship_classes
# ----------------------------------------------------------------------


def healthy_manytomany_relationship():
    """
        AddressParcelRelation exactly as the real geodatabase reports it.
    """
    return FakeRelationshipDefinition(
        name="AddressParcelRelation",
        origin_table="Addresses",
        destination_table="Parcels",
        origin_primary_key="address_lid",
        origin_foreign_key="address_lid",
        destination_primary_key="parcel_lid",
        destination_foreign_key="parcel_lid",
        cardinality="ManyToMany",
        is_attributed=True,
    )


def test_relationship_classes_pass_for_a_correctly_shaped_relationship():
    accessor = FakeGeodatabaseAccessor()
    accessor.add_relationship(OUTPUT_GDB, healthy_manytomany_relationship())

    result = check_relationship_classes(build_context(accessor))

    assert result.status == CheckStatus.PASS
    assert result.details["relationships_with_problems"] == 0


def test_cardinality_comparison_tolerates_the_two_spellings():
    """
        GDBMerge declares MANY_TO_MANY; arcpy reports ManyToMany. Both must compare
        equal or every relationship would report a false failure.
    """
    accessor = FakeGeodatabaseAccessor()
    accessor.add_relationship(OUTPUT_GDB, healthy_manytomany_relationship())

    result = check_relationship_classes(build_context(accessor))

    assert result.status == CheckStatus.PASS
    assert result.rows[0]["cardinality"] == "ManyToMany"


def test_relationship_classes_fail_on_a_wrong_origin_key():
    accessor = FakeGeodatabaseAccessor()
    accessor.add_relationship(
        OUTPUT_GDB,
        FakeRelationshipDefinition(
            name="AddressParcelRelation",
            origin_table="Addresses",
            destination_table="Parcels",
            origin_primary_key="wrong_key",        # should be address_lid
            origin_foreign_key="wrong_key",
            destination_primary_key="parcel_lid",
            destination_foreign_key="parcel_lid",
            cardinality="ManyToMany",
            is_attributed=True,
        ),
    )

    result = check_relationship_classes(build_context(accessor))

    assert result.status == CheckStatus.FAIL
    assert result.details["relationships_with_problems"] == 1
    assert "origin key is wrong_key, expected address_lid" in result.rows[0]["note"]


def test_relationship_classes_fail_on_a_wrong_destination_table():
    accessor = FakeGeodatabaseAccessor()
    accessor.add_relationship(
        OUTPUT_GDB,
        FakeRelationshipDefinition(
            name="AddressParcelRelation",
            origin_table="Addresses",
            destination_table="Buildings",         # should be Parcels
            origin_primary_key="address_lid",
            origin_foreign_key="address_lid",
            destination_primary_key="parcel_lid",
            destination_foreign_key="parcel_lid",
            cardinality="ManyToMany",
            is_attributed=True,
        ),
    )

    result = check_relationship_classes(build_context(accessor))

    assert result.status == CheckStatus.FAIL
    assert "destination is Buildings, expected Parcels" in result.rows[0]["note"]


def test_relationship_classes_fail_when_one_is_missing_entirely():
    accessor = FakeGeodatabaseAccessor()
    # No relationship registered at all.

    result = check_relationship_classes(build_context(accessor))

    assert result.status == CheckStatus.FAIL
    assert "does not exist" in result.rows[0]["note"]


def test_one_to_many_destination_key_is_read_from_the_origin_foreign_key():
    """
        A one-to-many relationship class reports no destination keys; the foreign key
        lives on the origin side. rc_ParcelPrimaryBuilding really does report
        [('primary_building_lid', 'OriginPrimary'), ('building_lid', 'OriginForeign')].
    """
    expectations = build_expectations(
        manytomany=(),
        onetomany=(
            ExpectedRelationship(
                relationship_name="rc_ParcelPrimaryBuilding",
                origin_table="Parcels",
                destination_table="Buildings",
                origin_key="primary_building_lid",
                destination_key="building_lid",
                cardinality="ONE_TO_MANY",
                is_attributed=False,
                is_countable_theme=False,
            ),
        ),
    )

    accessor = FakeGeodatabaseAccessor()
    accessor.add_relationship(
        OUTPUT_GDB,
        FakeRelationshipDefinition(
            name="rc_ParcelPrimaryBuilding",
            origin_table="Parcels",
            destination_table="Buildings",
            origin_primary_key="primary_building_lid",
            origin_foreign_key="building_lid",
            destination_primary_key="",            # genuinely empty for one-to-many
            destination_foreign_key="",
            cardinality="OneToMany",
            is_attributed=False,
        ),
    )

    result = check_relationship_classes(build_context(accessor, expectations))

    assert result.status == CheckStatus.PASS, result.rows[0]["note"]


# ----------------------------------------------------------------------
# schema.field_conformance
# ----------------------------------------------------------------------


def build_source_folder(tmp_path) -> str:
    source_folder = tmp_path / "processing"
    source_folder.mkdir(exist_ok=True)
    (source_folder / "SF_Professional_06001.gdb").mkdir()
    return str(source_folder)


def source_gdb_path(source_folder: str) -> str:
    return os.path.join(source_folder, "SF_Professional_06001.gdb")


def parcels_fields():
    return [
        FakeFieldDefinition("OBJECTID", "OID", 4, is_nullable=False),
        FakeFieldDefinition("PARCEL_LID", "String", 50),
        FakeFieldDefinition("FIPS_CODE", "String", 5),
    ]


def test_field_conformance_passes_for_identical_schemas(tmp_path):
    source_folder = build_source_folder(tmp_path)
    accessor = FakeGeodatabaseAccessor()

    accessor.add_dataset(OUTPUT_GDB, "Parcels",
                         FakeDataset(data_type="FeatureClass", fields=parcels_fields()))
    accessor.add_dataset(source_gdb_path(source_folder), "Parcels",
                         FakeDataset(data_type="FeatureClass", fields=parcels_fields()))

    results = list(check_field_conformance(
        build_context(accessor, source_folder=source_folder)
    ))

    assert len(results) == 1
    assert results[0].status == CheckStatus.PASS
    assert results[0].details["output_field_count"] == 3


def test_field_conformance_allows_rid_instead_of_objectid(tmp_path):
    """
        The known allowance. Converting a table into an attributed relationship class
        replaces OBJECTID with RID - verified on the real AddressParcelRelation - and
        that must not be reported as a missing field plus an extra field.
    """
    source_folder = build_source_folder(tmp_path)
    expectations = build_expectations(core_themes=("AddressParcelRelation",))
    accessor = FakeGeodatabaseAccessor()

    accessor.add_dataset(
        OUTPUT_GDB, "AddressParcelRelation",
        FakeDataset(data_type="RelationshipClass", fields=[
            FakeFieldDefinition("RID", "OID", 4, is_nullable=False),
            FakeFieldDefinition("address_lid", "String", 50),
            FakeFieldDefinition("parcel_lid", "String", 50),
            FakeFieldDefinition("FIPS_CODE", "String", 5),
        ]),
    )
    accessor.add_dataset(
        source_gdb_path(source_folder), "AddressParcelRelation",
        FakeDataset(data_type="Table", fields=[
            FakeFieldDefinition("OBJECTID", "OID", 4, is_nullable=False),
            FakeFieldDefinition("ADDRESS_LID", "String", 50),
            FakeFieldDefinition("PARCEL_LID", "String", 50),
            FakeFieldDefinition("FIPS_CODE", "String", 5),
        ]),
    )

    results = list(check_field_conformance(
        build_context(accessor, expectations, source_folder=source_folder)
    ))

    assert results[0].status == CheckStatus.PASS
    assert results[0].details["object_id_substitution_allowed"] is True
    assert results[0].details["fields_missing_from_output"] == []


def test_field_conformance_fails_on_a_genuinely_missing_field(tmp_path):
    """
        The RID allowance must stay narrow. A real missing field is still a failure.
    """
    source_folder = build_source_folder(tmp_path)
    accessor = FakeGeodatabaseAccessor()

    accessor.add_dataset(
        OUTPUT_GDB, "Parcels",
        FakeDataset(data_type="FeatureClass", fields=[
            FakeFieldDefinition("OBJECTID", "OID", 4, is_nullable=False),
            FakeFieldDefinition("PARCEL_LID", "String", 50),
            # FIPS_CODE is gone.
        ]),
    )
    accessor.add_dataset(source_gdb_path(source_folder), "Parcels",
                         FakeDataset(data_type="FeatureClass", fields=parcels_fields()))

    results = list(check_field_conformance(
        build_context(accessor, source_folder=source_folder)
    ))

    assert results[0].status == CheckStatus.FAIL
    assert results[0].details["fields_missing_from_output"] == ["FIPS_CODE"]


def test_field_conformance_fails_on_a_type_or_length_change(tmp_path):
    source_folder = build_source_folder(tmp_path)
    accessor = FakeGeodatabaseAccessor()

    accessor.add_dataset(
        OUTPUT_GDB, "Parcels",
        FakeDataset(data_type="FeatureClass", fields=[
            FakeFieldDefinition("OBJECTID", "OID", 4, is_nullable=False),
            FakeFieldDefinition("PARCEL_LID", "String", 25),   # was 50
            FakeFieldDefinition("FIPS_CODE", "String", 5),
        ]),
    )
    accessor.add_dataset(source_gdb_path(source_folder), "Parcels",
                         FakeDataset(data_type="FeatureClass", fields=parcels_fields()))

    results = list(check_field_conformance(
        build_context(accessor, source_folder=source_folder)
    ))

    assert results[0].status == CheckStatus.FAIL
    assert results[0].details["fields_with_type_or_length_mismatch"] == 1

    mismatch_row = next(row for row in results[0].rows if row["field"] == "PARCEL_LID")
    assert mismatch_row["source"] == "String(50)"
    assert mismatch_row["output"] == "String(25)"


def test_field_conformance_fails_when_the_theme_is_absent_from_the_output(tmp_path):
    source_folder = build_source_folder(tmp_path)
    accessor = FakeGeodatabaseAccessor()
    accessor.add_dataset(source_gdb_path(source_folder), "Parcels",
                         FakeDataset(data_type="FeatureClass", fields=parcels_fields()))

    results = list(check_field_conformance(
        build_context(accessor, source_folder=source_folder)
    ))

    assert results[0].status == CheckStatus.FAIL
    assert "missing from the output geodatabase" in results[0].summary


def test_field_conformance_skips_when_the_source_lacks_the_theme(tmp_path):
    source_folder = build_source_folder(tmp_path)
    accessor = FakeGeodatabaseAccessor()
    accessor.add_dataset(OUTPUT_GDB, "Parcels",
                         FakeDataset(data_type="FeatureClass", fields=parcels_fields()))
    # The source gdb exists as a folder but holds no Parcels dataset.
    accessor.datasets_by_gdb.setdefault(source_gdb_path(source_folder), {})

    results = list(check_field_conformance(
        build_context(accessor, source_folder=source_folder)
    ))

    assert results[0].status == CheckStatus.SKIP
    assert "not present in the reference source county" in results[0].summary
