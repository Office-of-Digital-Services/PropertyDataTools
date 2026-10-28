"""
    Unit tests for the expectations layer.

    These confirm that what the validator believes it should find is derived from
    GDBMerge rather than restated, and that the derivation applies the same naming
    conventions the merge code itself applies (idx_<field>, rc_<name>).

    These read class attributes only - no geodatabase is opened, no file is read, and
    GDBMerge is never instantiated.
"""

from unbox.compile_gdbs import GDBMerge
from unbox.validation.expectations import (
    INDEX_NAME_PREFIX,
    MergeExpectations,
    normalize_name,
)


def test_expectation_counts_match_the_merge_configuration():
    """
        The number of expectations must equal the number of declarations in GDBMerge.
        Reading the lengths from GDBMerge rather than hard-coding 20 and 29 means this
        test keeps working when the build adds an index - it is asserting the
        derivation, not a snapshot.
    """
    expectations = MergeExpectations.from_merge_class(GDBMerge)

    expected_index_count = len(GDBMerge.KEY_INDEXES) + len(GDBMerge.ATTRIBUTE_INDEXES)
    assert len(expectations.expected_indexes) == expected_index_count
    assert len(expectations.spatially_indexed_tables) == len(GDBMerge.SPATIAL_INDEXES)
    assert len(expectations.manytomany_relationships) == len(GDBMerge.MANYTOMANY_RELATIONSHIPS)
    assert len(expectations.onetomany_relationships) == len(GDBMerge.ONETOMANY_RELATIONSHIPS)


def test_current_build_declares_the_expected_totals():
    """
        A snapshot of the current configuration, kept deliberately separate from the
        derivation test above. If someone changes the build config this test fails and
        prompts a conscious decision, rather than the change passing unnoticed.
    """
    expectations = MergeExpectations.from_merge_class(GDBMerge)

    assert len(expectations.expected_indexes) == 49       # 20 key + 29 attribute
    assert len(expectations.spatially_indexed_tables) == 4
    assert len(expectations.manytomany_relationships) == 5
    assert len(expectations.onetomany_relationships) == 3
    assert len(expectations.all_countable_theme_names) == 10


def test_index_names_follow_the_idx_prefix_convention():
    """
        GDBMerge.create_indexes() names every index f"idx_{field}". The expectations
        must use the identical convention or every index check would report a false
        failure.
    """
    expectations = MergeExpectations.from_merge_class(GDBMerge)

    for expected_index in expectations.expected_indexes:
        assert expected_index.index_name == f"{INDEX_NAME_PREFIX}{expected_index.field_name}"


def test_one_to_many_relationship_names_carry_the_rc_prefix():
    """
        create_relationship_classes() prepends ONETOMANY_RELATIONSHIPS_PREFIX before
        calling CreateRelationshipClass, so the datasets in the output are named
        rc_ParcelAssessments and so on.
    """
    expectations = MergeExpectations.from_merge_class(GDBMerge)

    relationship_names = [
        relationship.relationship_name
        for relationship in expectations.onetomany_relationships
    ]

    assert relationship_names == [
        "rc_ParcelAssessments",
        "rc_ParcelPrimaryAssessment",
        "rc_ParcelPrimaryBuilding",
    ]
    for name in relationship_names:
        assert name.startswith(GDBMerge.ONETOMANY_RELATIONSHIPS_PREFIX)


def test_many_to_many_relationship_names_have_no_prefix():
    """
        The many-to-many relationship classes take over the original table names, since
        _handle_manytomany_relationships() renames the table out of the way first.
    """
    expectations = MergeExpectations.from_merge_class(GDBMerge)

    relationship_names = {
        relationship.relationship_name
        for relationship in expectations.manytomany_relationships
    }

    assert relationship_names == {
        "BuildingParcelRelation",
        "BuildingAssessmentRelation",
        "AddressParcelRelation",
        "AddressBuildingRelation",
        "AddressAssessmentRelation",
    }


def test_countable_themes_include_the_four_cores_and_six_relations():
    """
        Ten datasets carry per-county rows: four core feature classes, the five
        many-to-many relations, and ParcelAssessmentRelation.
    """
    expectations = MergeExpectations.from_merge_class(GDBMerge)
    countable = expectations.all_countable_theme_names

    for core_theme in ("Parcels", "Addresses", "Buildings", "Assessments"):
        assert core_theme in countable

    assert "ParcelAssessmentRelation" in countable
    assert "AddressParcelRelation" in countable
    assert len(countable) == 10


def test_index_lookup_is_case_insensitive_on_table_and_field():
    """
        Casing is genuinely inconsistent in this build - ATTRIBUTE_INDEXES declares
        "Parcel_LID" while arcpy reports the field as PARCEL_LID - so every lookup has
        to normalize.
    """
    expectations = MergeExpectations.from_merge_class(GDBMerge)

    parcel_indexes_lowercase = expectations.indexes_for_table("parcels")
    parcel_indexes_uppercase = expectations.indexes_for_table("PARCELS")
    assert parcel_indexes_lowercase == parcel_indexes_uppercase
    assert len(parcel_indexes_lowercase) > 0

    indexed_fields = expectations.indexed_field_names_for_table("Parcels")
    # Declared as "Parcel_LID"; must be findable under any casing.
    assert "parcel_lid" in indexed_fields
    assert normalize_name("PARCEL_LID") in indexed_fields


def test_relationship_lookup_by_name_is_case_insensitive():
    expectations = MergeExpectations.from_merge_class(GDBMerge)

    found = expectations.relationship_by_name("addressparcelrelation")
    assert found is not None
    assert found.relationship_name == "AddressParcelRelation"
    assert found.origin_table == "Addresses"
    assert found.destination_table == "Parcels"
    assert found.origin_key == "address_lid"
    assert found.destination_key == "parcel_lid"


def test_unknown_relationship_lookup_returns_none():
    expectations = MergeExpectations.from_merge_class(GDBMerge)
    assert expectations.relationship_by_name("NotARelationship") is None


def test_expectations_can_be_derived_from_a_stub_class():
    """
        from_merge_class reads class attributes only. Feeding it a minimal stub proves
        it neither instantiates the class nor touches the filesystem, which is what
        keeps the whole engine testable without arcpy.
    """

    class StubMerge:
        KEY_INDEXES = [("Widgets", "widget_id")]
        ATTRIBUTE_INDEXES = [("Widgets", "colour")]
        SPATIAL_INDEXES = ["Widgets"]
        MANYTOMANY_RELATIONSHIPS = [
            {
                "RelationName": "WidgetGadgetRelation",
                "Origin": "Widgets",
                "Destination": "Gadgets",
                "OriginKey": "widget_id",
                "DestinationKey": "gadget_id",
                "Cardinality": "MANY_TO_MANY",
                "Attributed": "NONE",
            }
        ]
        ONETOMANY_RELATIONSHIPS_PREFIX = "rc_"
        ONETOMANY_RELATIONSHIP_COMMON = {
            "message_direction": "NONE",
            "cardinality": "ONE_TO_MANY",
            "attributed": "NONE",
            "relationship_type": "SIMPLE",
        }
        ONETOMANY_RELATIONSHIPS = [
            {
                "out_relationship_class": "WidgetGadgets",
                "origin_table": "Widgets",
                "destination_table": "Gadgets",
                "origin_primary_key": "widget_id",
                "origin_foreign_key": "WIDGET_ID",
                "forward_label": "Gadgets",
                "backward_label": "Widgets",
            }
        ]

    expectations = MergeExpectations.from_merge_class(StubMerge)

    assert len(expectations.expected_indexes) == 2
    assert expectations.expected_indexes[0].index_name == "idx_widget_id"
    assert expectations.expected_indexes[0].source_list == "KEY_INDEXES"
    assert expectations.expected_indexes[1].source_list == "ATTRIBUTE_INDEXES"
    assert expectations.onetomany_relationships[0].relationship_name == "rc_WidgetGadgets"
    assert expectations.onetomany_relationships[0].cardinality == "ONE_TO_MANY"
    assert expectations.onetomany_relationships[0].is_attributed is False


def test_describe_summarizes_the_expectations():
    expectations = MergeExpectations.from_merge_class(GDBMerge)
    summary = expectations.describe()

    assert summary["expected_index_count"] == 49
    assert summary["manytomany_relationship_count"] == 5
    assert summary["onetomany_relationship_count"] == 3
    assert "Parcels" in summary["core_themes"]


def test_normalize_name_handles_none_and_whitespace():
    assert normalize_name(None) == ""
    assert normalize_name("  FIPS_CODE  ") == "fips_code"
    assert normalize_name("Parcel_LID") == "parcel_lid"
