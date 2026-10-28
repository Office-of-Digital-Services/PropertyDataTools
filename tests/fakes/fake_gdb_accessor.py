"""
    An in-memory stand-in for GeodatabaseAccessor.

    This exists so every check can be driven through both a healthy scenario and a
    deliberately corrupted one without touching arcpy, a geodatabase, or a disk. That
    matters more than it might sound: the most common defect in validation code is a
    check that silently always passes, and the only way to rule that out is to feed it
    broken data and confirm it says so.

    The fake deliberately defines its own small value classes rather than importing them
    from unbox.validation.gdb_access, because that module imports arcpy. Keeping the
    fake arcpy-free means the unit test suite runs anywhere, including on a machine with
    no ArcGIS Pro installation.
"""

from __future__ import annotations

import dataclasses
from typing import Any, Dict, List, Optional, Sequence, Tuple


@dataclasses.dataclass(frozen=True)
class FakeFieldDefinition:
    """
        Mirrors unbox.validation.gdb_access.FieldDefinition.
    """

    name: str
    type: str = "String"
    length: int = 50
    is_nullable: bool = True

    @property
    def normalized_name(self) -> str:
        return self.name.strip().lower()


@dataclasses.dataclass(frozen=True)
class FakeIndexDefinition:
    """
        Mirrors unbox.validation.gdb_access.IndexDefinition.
    """

    name: str
    field_names: Tuple[str, ...] = ()
    is_unique: bool = False
    is_ascending: bool = True

    @property
    def normalized_name(self) -> str:
        return self.name.strip().lower()

    @property
    def normalized_field_names(self) -> Tuple[str, ...]:
        return tuple(field_name.strip().lower() for field_name in self.field_names)


@dataclasses.dataclass(frozen=True)
class FakeRelationshipDefinition:
    """
        Mirrors unbox.validation.gdb_access.RelationshipDefinition.
    """

    name: str
    origin_table: str
    destination_table: str
    origin_primary_key: str
    origin_foreign_key: str
    destination_primary_key: str = ""
    destination_foreign_key: str = ""
    cardinality: str = "ManyToMany"
    is_attributed: bool = True

    @property
    def normalized_cardinality(self) -> str:
        return self.cardinality.replace("_", "").strip().lower()


@dataclasses.dataclass(frozen=True)
class FakeTimedQueryResult:
    """
        Mirrors unbox.validation.gdb_access.TimedQueryResult.
    """

    where_clause: Optional[str]
    matched_row_count: int
    cold_seconds: float
    warm_seconds: float


@dataclasses.dataclass
class FakeDataset:
    """
        One scripted dataset inside a scripted geodatabase.

        `grouped_counts` maps a field name to {field_value: row_count}, which is what
        count_rows_by_field returns. When a dataset declares grouped counts for
        FIPS_CODE but no explicit row_count, the total is derived by summing them, so a
        test fixture cannot accidentally describe a dataset whose parts do not add up.
    """

    data_type: str = "Table"
    row_count: Optional[int] = None
    grouped_counts: Dict[str, Dict[str, int]] = dataclasses.field(default_factory=dict)
    fields: List[FakeFieldDefinition] = dataclasses.field(default_factory=list)
    indexes: List[FakeIndexDefinition] = dataclasses.field(default_factory=list)
    # Rows available to sample_field_values, as {field_name: [values...]}.
    column_values: Dict[str, List[Any]] = dataclasses.field(default_factory=dict)
    extent: Tuple[float, float, float, float] = (0.0, 0.0, 100.0, 100.0)

    def total_row_count(self) -> int:
        """
            The dataset's row count, derived from the grouped counts when not given.
        """
        if self.row_count is not None:
            return self.row_count
        for grouped in self.grouped_counts.values():
            return sum(grouped.values())
        return 0

    def row_tuples(self, field_names: Sequence[str], limit: int) -> List[Tuple[Any, ...]]:
        """
            Zips the scripted column values back into row tuples for the requested
            fields. Columns of unequal length are truncated to the shortest, which keeps
            a sloppy fixture from producing phantom rows.
        """
        columns = [self.column_values.get(field_name, []) for field_name in field_names]
        if not columns or any(len(column) == 0 for column in columns):
            return []

        available_row_count = min(len(column) for column in columns)
        row_count = min(available_row_count, limit)
        return [
            tuple(column[row_index] for column in columns)
            for row_index in range(row_count)
        ]


class FakeGeodatabaseAccessor:
    """
        A scripted, read-only accessor backed by dictionaries.

        Build one by handing it {gdb_path: {dataset_name: FakeDataset}}. Every call is
        recorded in `self.calls`, so a test can also assert *how* a check went about its
        work - for instance that the count check used one grouped pass per theme rather
        than one query per county.
    """

    def __init__(self,
                 datasets_by_gdb: Optional[Dict[str, Dict[str, FakeDataset]]] = None,
                 relationships_by_gdb: Optional[Dict[str, Dict[str, FakeRelationshipDefinition]]] = None,
                 timed_results: Optional[Dict[Tuple[str, str, str], FakeTimedQueryResult]] = None,
                 default_timed_result: Optional[FakeTimedQueryResult] = None) -> None:
        self.datasets_by_gdb: Dict[str, Dict[str, FakeDataset]] = datasets_by_gdb or {}
        self.relationships_by_gdb: Dict[str, Dict[str, FakeRelationshipDefinition]] = (
            relationships_by_gdb or {}
        )
        # Keyed by (gdb_path, dataset_name, where_clause) so a test can script an exact
        # timing for an exact query.
        self.timed_results: Dict[Tuple[str, str, str], FakeTimedQueryResult] = timed_results or {}
        self.default_timed_result = default_timed_result

        # Scripted spatial timings, keyed by (gdb_path, feature_class_name).
        self.spatial_timed_results: Dict[Tuple[str, str], FakeTimedQueryResult] = {}

        # Keys considered resolvable, as {(gdb_path, dataset_name, key_field): {values}}.
        # A key absent from the set is treated as an orphan.
        self.resolvable_keys: Dict[Tuple[str, str, str], set] = {}

        # Companion field lookups for the FIPS consistency check, keyed by
        # (gdb_path, dataset_name, key_field, value_field) -> {key: value}.
        self.key_value_maps: Dict[Tuple[str, str, str, str], Dict[Any, Any]] = {}

        # Every method call, as (method_name, args_tuple). Used for behavioural asserts.
        self.calls: List[Tuple[str, Tuple[Any, ...]]] = []

    # ------------------------------------------------------------------
    # Fixture construction helpers
    # ------------------------------------------------------------------

    def add_dataset(self, gdb_path: str, dataset_name: str, dataset: FakeDataset) -> None:
        self.datasets_by_gdb.setdefault(gdb_path, {})[dataset_name] = dataset

    def add_relationship(self, gdb_path: str, relationship: FakeRelationshipDefinition) -> None:
        self.relationships_by_gdb.setdefault(gdb_path, {})[relationship.name] = relationship

    def set_resolvable_keys(self, gdb_path: str, dataset_name: str,
                            key_field: str, key_values: Sequence[Any]) -> None:
        self.resolvable_keys[(gdb_path, dataset_name, key_field.lower())] = set(key_values)

    def set_key_value_map(self, gdb_path: str, dataset_name: str, key_field: str,
                          value_field: str, mapping: Dict[Any, Any]) -> None:
        self.key_value_maps[
            (gdb_path, dataset_name, key_field.lower(), value_field.lower())
        ] = dict(mapping)

    def set_timed_result(self, gdb_path: str, dataset_name: str,
                         where_clause: str, result: FakeTimedQueryResult) -> None:
        self.timed_results[(gdb_path, dataset_name, where_clause)] = result

    def set_spatial_timed_result(self, gdb_path: str, feature_class_name: str,
                                 result: FakeTimedQueryResult) -> None:
        self.spatial_timed_results[(gdb_path, feature_class_name)] = result

    def _dataset(self, gdb_path: str, dataset_name: str) -> FakeDataset:
        try:
            return self.datasets_by_gdb[gdb_path][dataset_name]
        except KeyError:
            raise KeyError(
                f"The fake accessor has no dataset {dataset_name!r} in {gdb_path!r}. "
                f"Add it to the fixture if the check under test should see it."
            )

    # ------------------------------------------------------------------
    # The accessor interface
    # ------------------------------------------------------------------

    def dataset_path(self, gdb_path: str, dataset_name: str) -> str:
        return f"{gdb_path}\\{dataset_name}"

    def dataset_exists(self, gdb_path: str, dataset_name: str) -> bool:
        self.calls.append(("dataset_exists", (gdb_path, dataset_name)))
        return dataset_name in self.datasets_by_gdb.get(gdb_path, {}) or \
            dataset_name in self.relationships_by_gdb.get(gdb_path, {})

    def workspace_exists(self, gdb_path: str) -> bool:
        self.calls.append(("workspace_exists", (gdb_path,)))
        return gdb_path in self.datasets_by_gdb or gdb_path in self.relationships_by_gdb

    def describe_children(self, gdb_path: str) -> Dict[str, str]:
        self.calls.append(("describe_children", (gdb_path,)))
        children = {
            name: dataset.data_type
            for name, dataset in self.datasets_by_gdb.get(gdb_path, {}).items()
        }
        for name in self.relationships_by_gdb.get(gdb_path, {}):
            children.setdefault(name, "RelationshipClass")
        return children

    def get_row_count(self, gdb_path: str, dataset_name: str) -> int:
        self.calls.append(("get_row_count", (gdb_path, dataset_name)))
        return self._dataset(gdb_path, dataset_name).total_row_count()

    def count_rows_by_field(self, gdb_path: str, dataset_name: str,
                            field_name: str = "FIPS_CODE") -> Dict[str, int]:
        self.calls.append(("count_rows_by_field", (gdb_path, dataset_name, field_name)))
        dataset = self._dataset(gdb_path, dataset_name)
        return dict(dataset.grouped_counts.get(field_name, {}))

    def list_field_definitions(self, gdb_path: str, dataset_name: str) -> List[FakeFieldDefinition]:
        self.calls.append(("list_field_definitions", (gdb_path, dataset_name)))
        return list(self._dataset(gdb_path, dataset_name).fields)

    def list_index_definitions(self, gdb_path: str, dataset_name: str) -> List[FakeIndexDefinition]:
        self.calls.append(("list_index_definitions", (gdb_path, dataset_name)))
        return list(self._dataset(gdb_path, dataset_name).indexes)

    def describe_relationship_class(self, gdb_path: str,
                                    relationship_name: str) -> FakeRelationshipDefinition:
        self.calls.append(("describe_relationship_class", (gdb_path, relationship_name)))
        try:
            return self.relationships_by_gdb[gdb_path][relationship_name]
        except KeyError:
            raise KeyError(
                f"The fake accessor has no relationship class {relationship_name!r} "
                f"in {gdb_path!r}."
            )

    def get_extent(self, gdb_path: str, feature_class_name: str) -> Tuple[float, float, float, float]:
        self.calls.append(("get_extent", (gdb_path, feature_class_name)))
        return self._dataset(gdb_path, feature_class_name).extent

    def time_attribute_selection(self, gdb_path: str, dataset_name: str,
                                 where_clause: str) -> FakeTimedQueryResult:
        self.calls.append(("time_attribute_selection", (gdb_path, dataset_name, where_clause)))

        scripted = self.timed_results.get((gdb_path, dataset_name, where_clause))
        if scripted is not None:
            return scripted
        if self.default_timed_result is not None:
            return dataclasses.replace(self.default_timed_result, where_clause=where_clause)

        raise KeyError(
            f"The fake accessor has no scripted timing for {dataset_name!r} with "
            f"where clause {where_clause!r}, and no default was supplied."
        )

    def time_spatial_selection(self, gdb_path: str, feature_class_name: str,
                               extent: Tuple[float, float, float, float]) -> FakeTimedQueryResult:
        self.calls.append(("time_spatial_selection", (gdb_path, feature_class_name, extent)))

        scripted = self.spatial_timed_results.get((gdb_path, feature_class_name))
        if scripted is not None:
            return dataclasses.replace(scripted, where_clause=f"BBOX {extent}")
        if self.default_timed_result is not None:
            return dataclasses.replace(self.default_timed_result, where_clause=f"BBOX {extent}")

        raise KeyError(
            f"The fake accessor has no scripted spatial timing for {feature_class_name!r}."
        )

    def sample_field_values(self, gdb_path: str, dataset_name: str,
                            field_names: Sequence[str],
                            where_clause: Optional[str] = None,
                            limit: int = 500) -> List[Tuple[Any, ...]]:
        self.calls.append(
            ("sample_field_values", (gdb_path, dataset_name, tuple(field_names), where_clause, limit))
        )
        return self._dataset(gdb_path, dataset_name).row_tuples(field_names, limit)

    def count_matching_keys(self, gdb_path: str, dataset_name: str,
                            key_field: str, key_values: Sequence[Any],
                            batch_size: int = 250) -> int:
        self.calls.append(("count_matching_keys", (gdb_path, dataset_name, key_field, tuple(key_values))))

        resolvable = self.resolvable_keys.get((gdb_path, dataset_name, key_field.lower()))
        if resolvable is None:
            # With no script, fall back to whatever the dataset's own column holds, so a
            # simple fixture does not need to declare resolvable keys separately.
            dataset = self.datasets_by_gdb.get(gdb_path, {}).get(dataset_name)
            resolvable = set(dataset.column_values.get(key_field, [])) if dataset else set()

        distinct_supplied = {key for key in key_values if key is not None}
        return len(distinct_supplied & resolvable)

    def fetch_key_to_value_map(self, gdb_path: str, dataset_name: str,
                               key_field: str, value_field: str,
                               key_values: Sequence[Any],
                               batch_size: int = 250) -> Dict[Any, Any]:
        self.calls.append(
            ("fetch_key_to_value_map", (gdb_path, dataset_name, key_field, value_field, tuple(key_values)))
        )
        mapping = self.key_value_maps.get(
            (gdb_path, dataset_name, key_field.lower(), value_field.lower()), {}
        )
        wanted = {key for key in key_values if key is not None}
        return {key: value for key, value in mapping.items() if key in wanted}

    # ------------------------------------------------------------------
    # Assertion helpers for tests
    # ------------------------------------------------------------------

    def call_names(self) -> List[str]:
        """
            Just the method names, in call order.
        """
        return [name for name, _ in self.calls]

    def count_calls(self, method_name: str) -> int:
        """
            How many times a given method was called, for behavioural assertions such as
            "the sweep used one grouped pass per theme, not one query per county".
        """
        return sum(1 for name, _ in self.calls if name == method_name)
