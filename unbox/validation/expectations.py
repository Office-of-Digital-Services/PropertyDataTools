"""
    Derives "what the merged geodatabase should look like" directly from the GDBMerge
    class, rather than restating it.

    GDBMerge already declares its intent as class-level data: KEY_INDEXES,
    ATTRIBUTE_INDEXES, SPATIAL_INDEXES, MANYTOMANY_RELATIONSHIPS and
    ONETOMANY_RELATIONSHIPS. Reading those attributes means there is exactly one source
    of truth. If someone adds an index to the build, the validation picks it up on the
    next run with no edit here.

    This module imports GDBMerge only to read class attributes - it never instantiates
    it and never touches the filesystem, so it remains importable without arcpy data.
"""

from __future__ import annotations

import dataclasses
from typing import Any, Dict, Iterable, List, Optional, Tuple

from unbox.compile_gdbs import GDBMerge

# The index naming convention applied by GDBMerge.create_indexes(): index_name = f"idx_{field}"
INDEX_NAME_PREFIX = "idx_"

# Esri's own spatial index on a feature class is always named FDO_SHAPE. GDBMerge
# recreates these via AddSpatialIndex() in recreate_spatial_indexes().
SPATIAL_INDEX_NAME = "FDO_SHAPE"

# The four primary themes. Every source county geodatabase carries these as feature
# classes and they survive the merge unchanged in type.
CORE_THEME_NAMES: Tuple[str, ...] = ("Parcels", "Addresses", "Buildings", "Assessments")

# This relation stays a plain table on both sides of the merge; the five many-to-many
# relations become relationship classes in the output while remaining tables in sources.
PLAIN_RELATION_TABLE_NAMES: Tuple[str, ...] = ("ParcelAssessmentRelation",)

# The field every theme carries identifying which county a row came from. This is the
# field the count checks group by to recover per-county totals from the merged output.
COUNTY_FIELD_NAME = "FIPS_CODE"


def normalize_name(name: Optional[str]) -> str:
    """
        Lowercases and trims a field, index, or table name for comparison.

        Field name casing is genuinely inconsistent across this build - ATTRIBUTE_INDEXES
        declares ("Parcels", "Parcel_LID") while arcpy.ListIndexes reports the field as
        PARCEL_LID - so every comparison in the validation module goes through here.
    """
    if name is None:
        return ""
    return str(name).strip().lower()


@dataclasses.dataclass(frozen=True)
class ExpectedIndex:
    """
        One attribute index that GDBMerge.create_indexes() should have created.
    """

    table_name: str
    field_name: str
    index_name: str  # "idx_" + field_name, matching GDBMerge's convention
    # Which GDBMerge list this came from, so the report can distinguish a missing
    # relationship key index from a missing convenience attribute index.
    source_list: str  # "KEY_INDEXES" or "ATTRIBUTE_INDEXES"

    @property
    def normalized_field_name(self) -> str:
        return normalize_name(self.field_name)

    @property
    def normalized_index_name(self) -> str:
        return normalize_name(self.index_name)

    def __str__(self) -> str:
        return f"{self.table_name}.{self.field_name}"


@dataclasses.dataclass(frozen=True)
class ExpectedRelationship:
    """
        One relationship class that should exist in the merged output.

        `origin_key` / `destination_key` are the keys as declared in GDBMerge. For the
        many-to-many relations the same key name is used as both the primary key on the
        origin and the foreign key in the relationship table, which is how
        _handle_manytomany_relationships() calls CreateRelationshipClass.
    """

    relationship_name: str
    origin_table: str
    destination_table: str
    origin_key: str
    destination_key: str
    cardinality: str          # "MANY_TO_MANY" or "ONE_TO_MANY"
    is_attributed: bool
    # True for the five relations that also need per-county row counting, because in the
    # source geodatabases they exist as ordinary tables holding real records.
    is_countable_theme: bool

    def __str__(self) -> str:
        return self.relationship_name


@dataclasses.dataclass(frozen=True)
class MergeExpectations:
    """
        The complete set of expectations projected out of GDBMerge.
    """

    core_theme_names: Tuple[str, ...]
    relation_table_names: Tuple[str, ...]
    expected_indexes: Tuple[ExpectedIndex, ...]
    spatially_indexed_tables: Tuple[str, ...]
    manytomany_relationships: Tuple[ExpectedRelationship, ...]
    onetomany_relationships: Tuple[ExpectedRelationship, ...]

    @classmethod
    def from_merge_class(cls, merge_class: Any = GDBMerge) -> "MergeExpectations":
        """
            Reads the class-level configuration off GDBMerge (or any class shaped like
            it, which makes this straightforward to unit test with a stub).
        """
        expected_indexes = cls._build_expected_indexes(merge_class)
        manytomany = cls._build_manytomany_relationships(merge_class)
        onetomany = cls._build_onetomany_relationships(merge_class)

        # The relation datasets we count per county: the five many-to-many relations
        # (tables in the sources, relationship classes in the output) plus the plain
        # ParcelAssessmentRelation table that stays a table throughout.
        relation_table_names = tuple(
            [relationship.relationship_name for relationship in manytomany]
            + list(PLAIN_RELATION_TABLE_NAMES)
        )

        return cls(
            core_theme_names=CORE_THEME_NAMES,
            relation_table_names=relation_table_names,
            expected_indexes=expected_indexes,
            spatially_indexed_tables=tuple(merge_class.SPATIAL_INDEXES),
            manytomany_relationships=manytomany,
            onetomany_relationships=onetomany,
        )

    @staticmethod
    def _build_expected_indexes(merge_class: Any) -> Tuple[ExpectedIndex, ...]:
        """
            Projects KEY_INDEXES and ATTRIBUTE_INDEXES into ExpectedIndex records,
            applying the same idx_<field> naming that create_indexes() uses.
        """
        expected: List[ExpectedIndex] = []

        for source_list_name in ("KEY_INDEXES", "ATTRIBUTE_INDEXES"):
            for table_name, field_name in getattr(merge_class, source_list_name):
                expected.append(
                    ExpectedIndex(
                        table_name=table_name,
                        field_name=field_name,
                        index_name=f"{INDEX_NAME_PREFIX}{field_name}",
                        source_list=source_list_name,
                    )
                )

        return tuple(expected)

    @staticmethod
    def _build_manytomany_relationships(merge_class: Any) -> Tuple[ExpectedRelationship, ...]:
        """
            Projects MANYTOMANY_RELATIONSHIPS into ExpectedRelationship records.

            Note on `is_attributed`: GDBMerge passes
            `attributed="ATTRIBUTED" if config["Attributed"] else "NONE"` - and because
            every config supplies a non-empty string (including the literal "NONE"),
            that condition is always truthy. Every many-to-many relationship class in the
            output is therefore created as ATTRIBUTED, regardless of what the config
            field reads. We mirror the behaviour of the code rather than the apparent
            intent of the config, because the validator must describe the database that
            actually exists.
        """
        relationships: List[ExpectedRelationship] = []

        for config in merge_class.MANYTOMANY_RELATIONSHIPS:
            relationships.append(
                ExpectedRelationship(
                    relationship_name=config["RelationName"],
                    origin_table=config["Origin"],
                    destination_table=config["Destination"],
                    origin_key=config["OriginKey"],
                    destination_key=config["DestinationKey"],
                    cardinality=config["Cardinality"],
                    # See docstring: the build's truthiness test makes all of these attributed.
                    is_attributed=bool(config.get("Attributed")),
                    is_countable_theme=True,
                )
            )

        return tuple(relationships)

    @staticmethod
    def _build_onetomany_relationships(merge_class: Any) -> Tuple[ExpectedRelationship, ...]:
        """
            Projects ONETOMANY_RELATIONSHIPS into ExpectedRelationship records, applying
            the ONETOMANY_RELATIONSHIPS_PREFIX ("rc_") that create_relationship_classes()
            prepends to each name at creation time.
        """
        relationships: List[ExpectedRelationship] = []
        common = merge_class.ONETOMANY_RELATIONSHIP_COMMON
        prefix = merge_class.ONETOMANY_RELATIONSHIPS_PREFIX

        for config in merge_class.ONETOMANY_RELATIONSHIPS:
            relationships.append(
                ExpectedRelationship(
                    relationship_name=f"{prefix}{config['out_relationship_class']}",
                    origin_table=config["origin_table"],
                    destination_table=config["destination_table"],
                    origin_key=config["origin_primary_key"],
                    destination_key=config["origin_foreign_key"],
                    cardinality=common["cardinality"],
                    is_attributed=common["attributed"] != "NONE",
                    # These are derived views over existing data, not merged datasets of
                    # their own, so they carry no per-county row count of their own.
                    is_countable_theme=False,
                )
            )

        return tuple(relationships)

    # ------------------------------------------------------------------
    # Convenience accessors used by the checks
    # ------------------------------------------------------------------

    @property
    def all_countable_theme_names(self) -> Tuple[str, ...]:
        """
            Every dataset whose rows should be counted per county: the four core themes
            plus the six relation datasets. Ten in total for this build.
        """
        return tuple(self.core_theme_names) + tuple(self.relation_table_names)

    @property
    def all_relationships(self) -> Tuple[ExpectedRelationship, ...]:
        """
            Every relationship class expected in the output, both kinds together.
        """
        return self.manytomany_relationships + self.onetomany_relationships

    def indexes_for_table(self, table_name: str) -> Tuple[ExpectedIndex, ...]:
        """
            The expected indexes on one table, matched case-insensitively on table name.
        """
        wanted = normalize_name(table_name)
        return tuple(
            index for index in self.expected_indexes
            if normalize_name(index.table_name) == wanted
        )

    def indexed_field_names_for_table(self, table_name: str) -> frozenset:
        """
            The normalized set of field names that carry an index on a given table.

            The performance checks use this to pick a *control* field that is guaranteed
            not to be indexed, so the timed comparison is genuinely indexed-vs-unindexed.
        """
        return frozenset(
            index.normalized_field_name for index in self.indexes_for_table(table_name)
        )

    def tables_with_expected_indexes(self) -> Tuple[str, ...]:
        """
            Distinct table names appearing in the index expectations, in declaration
            order, so reports and sweeps have a stable, predictable ordering.
        """
        ordered_names: List[str] = []
        seen: set = set()
        for index in self.expected_indexes:
            key = normalize_name(index.table_name)
            if key not in seen:
                seen.add(key)
                ordered_names.append(index.table_name)
        return tuple(ordered_names)

    def relationship_by_name(self, relationship_name: str) -> Optional[ExpectedRelationship]:
        """
            Look up a single expected relationship by name, case-insensitively.
        """
        wanted = normalize_name(relationship_name)
        for relationship in self.all_relationships:
            if normalize_name(relationship.relationship_name) == wanted:
                return relationship
        return None

    def describe(self) -> Dict[str, Any]:
        """
            A compact dictionary summary, handy for the report header and for debugging
            what the validator believes it should be looking for.
        """
        return {
            "core_themes": list(self.core_theme_names),
            "relation_tables": list(self.relation_table_names),
            "expected_index_count": len(self.expected_indexes),
            "spatially_indexed_tables": list(self.spatially_indexed_tables),
            "manytomany_relationship_count": len(self.manytomany_relationships),
            "onetomany_relationship_count": len(self.onetomany_relationships),
        }


def iter_index_expectations(expectations: MergeExpectations) -> Iterable[ExpectedIndex]:
    """
        Small helper used by the schema checks so the iteration order is defined in one
        place: KEY_INDEXES first, then ATTRIBUTE_INDEXES, both in declaration order.
    """
    return expectations.expected_indexes
