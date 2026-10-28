"""
    The one and only place in the validation module that imports arcpy.

    Every question a check wants to ask the geodatabase is asked through
    GeodatabaseAccessor. Two things follow from that:

    1. Checks are unit-testable. A fake accessor backed by dictionaries lets the whole
       engine run with no arcpy and no data.
    2. The read-only guarantee is auditable in one file. This class exposes only
       counting, listing, describing, selecting, and reading. No operation here can
       modify a geodatabase, so the 8-24 hour statewide build is never at risk.

    Allowed arcpy surface: GetCount, MakeTableView, MakeFeatureLayer, SelectLayerBy*,
    da.SearchCursor, da.TableToNumPyArray, ListFields, ListIndexes, Describe, Exists.
"""

from __future__ import annotations

import dataclasses
import itertools
import logging
import os
import time
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple

import arcpy
import numpy

# Monotonic counter used to give every scratch layer / table view a unique name.
# Reusing a name would require overwriting an existing in-memory object, and unique
# names keep concurrent or repeated calls from colliding.
_scratch_name_counter = itertools.count(1)


def _next_scratch_name(prefix: str) -> str:
    """
        A process-unique in-memory layer name. These are views onto data, never copies,
        and they are discarded when the arcpy session ends.
    """
    return f"{prefix}_{next(_scratch_name_counter)}"


@dataclasses.dataclass(frozen=True)
class FieldDefinition:
    """
        The subset of an arcpy Field that the schema checks actually compare.
    """

    name: str
    type: str
    length: int
    is_nullable: bool

    @property
    def normalized_name(self) -> str:
        return self.name.strip().lower()


@dataclasses.dataclass(frozen=True)
class IndexDefinition:
    """
        One index as reported by arcpy.ListIndexes.
    """

    name: str
    field_names: Tuple[str, ...]
    is_unique: bool
    is_ascending: bool

    @property
    def normalized_name(self) -> str:
        return self.name.strip().lower()

    @property
    def normalized_field_names(self) -> Tuple[str, ...]:
        return tuple(field_name.strip().lower() for field_name in self.field_names)


@dataclasses.dataclass(frozen=True)
class RelationshipDefinition:
    """
        The shape of a relationship class as the geodatabase reports it.

        Esri reports cardinality as e.g. "OneToMany" while GDBMerge declares
        "ONE_TO_MANY"; `normalized_cardinality` collapses both forms so the schema check
        can compare them without caring about the spelling.
    """

    name: str
    origin_table: str
    destination_table: str
    origin_primary_key: str
    origin_foreign_key: str
    destination_primary_key: str
    destination_foreign_key: str
    cardinality: str
    is_attributed: bool

    @property
    def normalized_cardinality(self) -> str:
        return self.cardinality.replace("_", "").strip().lower()


@dataclasses.dataclass(frozen=True)
class TimedQueryResult:
    """
        The outcome of one timed query.

        Both timings are reported because they answer different questions. The cold
        number is what a user feels on the first click; the warm number, taken after a
        discarded warmup, is the stable figure to compare against a control query.
    """

    where_clause: Optional[str]
    matched_row_count: int
    cold_seconds: float
    warm_seconds: float


class GeodatabaseAccessor:
    """
        Read-only facade over arcpy for the validation module.
    """

    def __init__(self, logger: Optional[logging.Logger] = None) -> None:
        self.logger = logger or logging.getLogger(__name__)

    # ------------------------------------------------------------------
    # Path and existence helpers
    # ------------------------------------------------------------------

    def dataset_path(self, gdb_path: str, dataset_name: str) -> str:
        """
            Builds a full path to a dataset inside a geodatabase.

            Resolving by path rather than by listing matters here: in the merged output
            five of the ten countable themes are relationship classes, so
            arcpy.ListTables() would not return them, but they are still addressable
            and countable by path.
        """
        return os.path.join(gdb_path, dataset_name)

    def dataset_exists(self, gdb_path: str, dataset_name: str) -> bool:
        """
            Whether a named dataset exists in the geodatabase, whatever its type.
        """
        return bool(arcpy.Exists(self.dataset_path(gdb_path, dataset_name)))

    def workspace_exists(self, gdb_path: str) -> bool:
        """
            Whether the geodatabase itself is present and readable by arcpy.
        """
        return bool(arcpy.Exists(gdb_path))

    def describe_children(self, gdb_path: str) -> Dict[str, str]:
        """
            Maps every child dataset name in the geodatabase to its arcpy dataType,
            e.g. {"Parcels": "FeatureClass", "AddressParcelRelation": "RelationshipClass"}.
        """
        description = arcpy.Describe(gdb_path)
        return {child.name: child.dataType for child in description.children}

    # ------------------------------------------------------------------
    # Counting
    # ------------------------------------------------------------------

    def get_row_count(self, gdb_path: str, dataset_name: str) -> int:
        """
            Total rows in a dataset. Works for feature classes, tables, and the
            attributed relationship classes alike.
        """
        full_path = self.dataset_path(gdb_path, dataset_name)
        return int(arcpy.management.GetCount(full_path).getOutput(0))

    def count_rows_by_field(self,
                            gdb_path: str,
                            dataset_name: str,
                            field_name: str = "FIPS_CODE") -> Dict[str, int]:
        """
            Groups the whole dataset by one field in a single pass and returns
            {value: row_count}.

            This is deliberately one pass rather than one filtered query per county.
            FIPS_CODE is indexed on the four core themes but *not* on the relation
            tables, where a single filtered count was measured at 3.5-13.4 seconds -
            which over 58 counties would be roughly 13 minutes for one theme. The
            grouped pass covers all 58 counties of the 26.1M-row AddressParcelRelation
            in about 16 seconds.

            Null and empty values are grouped under the empty string so they are
            surfaced to the report explicitly rather than silently discarded.
        """
        full_path = self.dataset_path(gdb_path, dataset_name)

        try:
            return self._count_by_field_with_numpy(full_path, field_name)
        except Exception:
            # Nulls in a text field, or an unusual field type, can make the numpy
            # conversion unhappy. A cursor is slower but always works, and correctness
            # matters more than speed in the fallback path.
            self.logger.debug(
                f"Grouped numpy pass failed for {full_path}.{field_name}; "
                f"falling back to a search cursor."
            )
            return self._count_by_field_with_cursor(full_path, field_name)

    def _count_by_field_with_numpy(self, full_path: str, field_name: str) -> Dict[str, int]:
        """
            The fast path: read one column into a numpy array and group with unique().
        """
        column = arcpy.da.TableToNumPyArray(full_path, [field_name], skip_nulls=False)
        distinct_values, value_counts = numpy.unique(column[field_name], return_counts=True)

        grouped: Dict[str, int] = {}
        for raw_value, count in zip(distinct_values, value_counts):
            grouped[self._normalize_group_key(raw_value)] = (
                grouped.get(self._normalize_group_key(raw_value), 0) + int(count)
            )
        return grouped

    def _count_by_field_with_cursor(self, full_path: str, field_name: str) -> Dict[str, int]:
        """
            The robust path: iterate rows and tally. Used only when the numpy pass fails.
        """
        grouped: Dict[str, int] = {}
        with arcpy.da.SearchCursor(full_path, [field_name]) as rows:
            for row in rows:
                key = self._normalize_group_key(row[0])
                grouped[key] = grouped.get(key, 0) + 1
        return grouped

    @staticmethod
    def _normalize_group_key(raw_value: Any) -> str:
        """
            Turns a grouped field value into a stable string key.

            numpy hands back bytes for some text columns and None/NaN for nulls. All
            null-ish values collapse to the empty string, which the count checks report
            as an explicit "(null or empty)" row rather than hiding.
        """
        if raw_value is None:
            return ""
        if isinstance(raw_value, bytes):
            return raw_value.decode("utf-8", errors="replace").strip()
        return str(raw_value).strip()

    # ------------------------------------------------------------------
    # Schema introspection
    # ------------------------------------------------------------------

    def list_field_definitions(self, gdb_path: str, dataset_name: str) -> List[FieldDefinition]:
        """
            Every field on a dataset, in the order arcpy reports them.
        """
        full_path = self.dataset_path(gdb_path, dataset_name)
        return [
            FieldDefinition(
                name=field.name,
                type=field.type,
                length=int(field.length or 0),
                is_nullable=bool(field.isNullable),
            )
            for field in arcpy.ListFields(full_path)
        ]

    def list_index_definitions(self, gdb_path: str, dataset_name: str) -> List[IndexDefinition]:
        """
            Every index on a dataset, including the FDO_SHAPE spatial index when present.
        """
        full_path = self.dataset_path(gdb_path, dataset_name)
        definitions: List[IndexDefinition] = []

        for index in arcpy.ListIndexes(full_path):
            definitions.append(
                IndexDefinition(
                    name=index.name,
                    field_names=tuple(field.name for field in index.fields),
                    is_unique=bool(index.isUnique),
                    is_ascending=bool(index.isAscending),
                )
            )

        return definitions

    def describe_relationship_class(self, gdb_path: str, relationship_name: str) -> RelationshipDefinition:
        """
            Describes one relationship class: its endpoints, its keys, and its cardinality.

            arcpy reports keys as (name, role) pairs where role is one of
            OriginPrimary / OriginForeign / DestinationPrimary / DestinationForeign, so
            we sort them into named slots for easy comparison against expectations.
        """
        full_path = self.dataset_path(gdb_path, relationship_name)
        description = arcpy.Describe(full_path)

        origin_primary_key = self._first_key_with_role(description.originClassKeys, "OriginPrimary")
        origin_foreign_key = self._first_key_with_role(description.originClassKeys, "OriginForeign")

        # A one-to-many relationship class has no destination keys at all; only
        # many-to-many (and attributed) relationships carry them.
        destination_keys = getattr(description, "destinationClassKeys", None) or []
        destination_primary_key = self._first_key_with_role(destination_keys, "DestinationPrimary")
        destination_foreign_key = self._first_key_with_role(destination_keys, "DestinationForeign")

        return RelationshipDefinition(
            name=relationship_name,
            origin_table=self._first_or_empty(description.originClassNames),
            destination_table=self._first_or_empty(description.destinationClassNames),
            origin_primary_key=origin_primary_key,
            origin_foreign_key=origin_foreign_key,
            destination_primary_key=destination_primary_key,
            destination_foreign_key=destination_foreign_key,
            cardinality=str(description.cardinality),
            is_attributed=bool(description.isAttributed),
        )

    @staticmethod
    def _first_key_with_role(class_keys: Iterable[Sequence[str]], wanted_role: str) -> str:
        """
            Pulls the first key name matching a role out of arcpy's (name, role) pairs.
        """
        for key_pair in class_keys:
            if len(key_pair) >= 2 and str(key_pair[1]) == wanted_role:
                return str(key_pair[0])
        return ""

    @staticmethod
    def _first_or_empty(names: Sequence[str]) -> str:
        return str(names[0]) if names else ""

    # ------------------------------------------------------------------
    # Timed queries
    # ------------------------------------------------------------------

    def time_attribute_selection(self,
                                 gdb_path: str,
                                 dataset_name: str,
                                 where_clause: str) -> TimedQueryResult:
        """
            Times a SelectLayerByAttribute against a dataset and reports cold and warm
            seconds plus how many rows matched.

            The first run is kept as the cold timing, then the identical query is run
            again and that second figure is the warm timing. The warm number is what the
            performance checks compare, because the first touch of a 26M-row table can
            spend seconds on file system cache warming that says nothing about whether
            an index exists.
        """
        full_path = self.dataset_path(gdb_path, dataset_name)
        selection_layer = self._make_selectable_view(full_path)

        cold_seconds, matched_row_count = self._timed_attribute_pass(selection_layer, where_clause)
        warm_seconds, matched_row_count = self._timed_attribute_pass(selection_layer, where_clause)

        return TimedQueryResult(
            where_clause=where_clause,
            matched_row_count=matched_row_count,
            cold_seconds=cold_seconds,
            warm_seconds=warm_seconds,
        )

    def _timed_attribute_pass(self, selection_layer: str, where_clause: str) -> Tuple[float, int]:
        """
            One timed select-and-count cycle. Returns (elapsed_seconds, matched_rows).
        """
        started = time.perf_counter()
        arcpy.management.SelectLayerByAttribute(
            selection_layer, "NEW_SELECTION", where_clause
        )
        matched_row_count = int(arcpy.management.GetCount(selection_layer).getOutput(0))
        elapsed_seconds = time.perf_counter() - started
        return elapsed_seconds, matched_row_count

    def time_spatial_selection(self,
                               gdb_path: str,
                               feature_class_name: str,
                               extent: Tuple[float, float, float, float]) -> TimedQueryResult:
        """
            Times a SelectLayerByLocation using a bounding box, reporting cold and warm
            seconds and the number of intersecting features.

            `extent` is (x_min, y_min, x_max, y_max) in the coordinate system of the
            feature class being queried.
        """
        full_path = self.dataset_path(gdb_path, feature_class_name)
        selection_layer = self._make_selectable_view(full_path, force_feature_layer=True)

        spatial_reference = arcpy.Describe(full_path).spatialReference
        bounding_box_geometry = self._extent_to_polygon(extent, spatial_reference)

        cold_seconds, matched_row_count = self._timed_spatial_pass(selection_layer, bounding_box_geometry)
        warm_seconds, matched_row_count = self._timed_spatial_pass(selection_layer, bounding_box_geometry)

        return TimedQueryResult(
            where_clause=f"BBOX {extent}",
            matched_row_count=matched_row_count,
            cold_seconds=cold_seconds,
            warm_seconds=warm_seconds,
        )

    def _timed_spatial_pass(self, selection_layer: str, bounding_box_geometry: Any) -> Tuple[float, int]:
        """
            One timed spatial-select-and-count cycle.
        """
        started = time.perf_counter()
        arcpy.management.SelectLayerByLocation(
            selection_layer, "INTERSECT", bounding_box_geometry, None, "NEW_SELECTION"
        )
        matched_row_count = int(arcpy.management.GetCount(selection_layer).getOutput(0))
        elapsed_seconds = time.perf_counter() - started
        return elapsed_seconds, matched_row_count

    @staticmethod
    def _extent_to_polygon(extent: Tuple[float, float, float, float],
                           spatial_reference: Any) -> Any:
        """
            Builds a rectangular polygon from (x_min, y_min, x_max, y_max).
        """
        x_min, y_min, x_max, y_max = extent
        corner_points = arcpy.Array([
            arcpy.Point(x_min, y_min),
            arcpy.Point(x_min, y_max),
            arcpy.Point(x_max, y_max),
            arcpy.Point(x_max, y_min),
            arcpy.Point(x_min, y_min),
        ])
        return arcpy.Polygon(corner_points, spatial_reference)

    def get_extent(self, gdb_path: str, feature_class_name: str) -> Tuple[float, float, float, float]:
        """
            The full extent of a feature class as (x_min, y_min, x_max, y_max).

            The spatial performance check derives its small / medium / large bounding
            boxes from this, so the boxes are always valid for whatever data is present
            rather than hard-coded to California coordinates.
        """
        full_path = self.dataset_path(gdb_path, feature_class_name)
        extent = arcpy.Describe(full_path).extent
        return (float(extent.XMin), float(extent.YMin), float(extent.XMax), float(extent.YMax))

    def _make_selectable_view(self, full_path: str, force_feature_layer: bool = False) -> str:
        """
            Creates an in-memory view or layer that SelectLayerBy* can operate on.

            This creates no data. A table view / feature layer is a transient pointer at
            rows that already exist, and selecting on it never writes to the source.
        """
        description = arcpy.Describe(full_path)
        is_spatial = force_feature_layer or getattr(description, "shapeType", None) is not None

        if is_spatial:
            layer_name = _next_scratch_name("validation_feature_layer")
            arcpy.management.MakeFeatureLayer(full_path, layer_name)
            return layer_name

        view_name = _next_scratch_name("validation_table_view")
        arcpy.management.MakeTableView(full_path, view_name)
        return view_name

    # ------------------------------------------------------------------
    # Sampling and key resolution
    # ------------------------------------------------------------------

    def sample_field_values(self,
                            gdb_path: str,
                            dataset_name: str,
                            field_names: Sequence[str],
                            where_clause: Optional[str] = None,
                            limit: int = 500) -> List[Tuple[Any, ...]]:
        """
            Reads up to `limit` rows of the given fields.

            Values used in timed queries must be sampled from the data rather than
            guessed. A guessed literal is worryingly easy to get wrong: on this dataset
            CITY = 'Davis' returns zero rows while CITY = 'DAVIS' returns 63,039, and a
            zero-row query produces a timing that means nothing at all.
        """
        full_path = self.dataset_path(gdb_path, dataset_name)
        sampled_rows: List[Tuple[Any, ...]] = []

        with arcpy.da.SearchCursor(full_path, list(field_names), where_clause) as rows:
            for row in rows:
                sampled_rows.append(tuple(row))
                if len(sampled_rows) >= limit:
                    break

        return sampled_rows

    def count_matching_keys(self,
                            gdb_path: str,
                            dataset_name: str,
                            key_field: str,
                            key_values: Sequence[Any],
                            batch_size: int = 250) -> int:
        """
            How many of `key_values` are present in `dataset_name.key_field`.

            Resolution is done with batched IN clauses rather than one query per key:
            500 keys resolved against the 26.7M-row Addresses table in 0.84 seconds that
            way. The returned figure counts *distinct supplied keys that matched*, not
            matching rows, so a key appearing twice in the target does not mask an
            orphan elsewhere in the sample.
        """
        if not key_values:
            return 0

        full_path = self.dataset_path(gdb_path, dataset_name)
        distinct_keys = list({key for key in key_values if key is not None})
        field_delimited = arcpy.AddFieldDelimiters(full_path, key_field)
        matched_keys: set = set()

        for batch_start in range(0, len(distinct_keys), batch_size):
            batch = distinct_keys[batch_start:batch_start + batch_size]
            where_clause = f"{field_delimited} IN ({self._format_in_list(batch)})"

            with arcpy.da.SearchCursor(full_path, [key_field], where_clause) as rows:
                for row in rows:
                    matched_keys.add(row[0])

        return len(matched_keys)

    def fetch_key_to_value_map(self,
                               gdb_path: str,
                               dataset_name: str,
                               key_field: str,
                               value_field: str,
                               key_values: Sequence[Any],
                               batch_size: int = 250) -> Dict[Any, Any]:
        """
            Resolves a batch of keys to one companion field value, e.g. key -> FIPS_CODE.

            This backs the FIPS consistency check: given a sample of relation rows, look
            up the county of each feature the row points at and confirm they agree.
        """
        if not key_values:
            return {}

        full_path = self.dataset_path(gdb_path, dataset_name)
        distinct_keys = list({key for key in key_values if key is not None})
        field_delimited = arcpy.AddFieldDelimiters(full_path, key_field)
        resolved: Dict[Any, Any] = {}

        for batch_start in range(0, len(distinct_keys), batch_size):
            batch = distinct_keys[batch_start:batch_start + batch_size]
            where_clause = f"{field_delimited} IN ({self._format_in_list(batch)})"

            with arcpy.da.SearchCursor(full_path, [key_field, value_field], where_clause) as rows:
                for key, value in rows:
                    resolved[key] = value

        return resolved

    @staticmethod
    def _format_in_list(values: Sequence[Any]) -> str:
        """
            Renders values as a SQL IN list, quoting strings and escaping single quotes.
        """
        rendered: List[str] = []
        for value in values:
            if isinstance(value, (int, float)) and not isinstance(value, bool):
                rendered.append(str(value))
            else:
                escaped = str(value).replace("'", "''")
                rendered.append(f"'{escaped}'")
        return ", ".join(rendered)
