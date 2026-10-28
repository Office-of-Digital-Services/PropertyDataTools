"""
    The read-only guard.

    The merged statewide geodatabase takes 8 to 24 hours to build. The validation module
    must never be able to damage it, and "we were careful" is not a guarantee - this is.

    These tests scan the package source directly. A new check that reached for
    arcpy.management.Delete, or a helper that imported arcpy outside the single
    designated seam, fails the suite immediately rather than at 3am against real data.
"""

import ast
import os

import pytest

import unbox.validation

# The directory holding the validation package, discovered from the package itself so
# this keeps working if the package is ever moved.
VALIDATION_PACKAGE_DIR = os.path.dirname(os.path.abspath(unbox.validation.__file__))

# The only module permitted to import arcpy.
ARCPY_SEAM_MODULE = "gdb_access.py"

# arcpy operations that create, modify, or destroy data. None of these may appear
# anywhere in the package, in any form.
FORBIDDEN_WRITE_OPERATIONS = (
    "Append",
    "Delete",
    "DeleteFeatures",
    "DeleteRows",
    "DeleteField",
    "AddIndex",
    "RemoveIndex",
    "AddSpatialIndex",
    "RemoveSpatialIndex",
    "AddField",
    "RepairGeometry",
    "CalculateField",
    "Rename",
    "CopyFeatures",
    "CopyRows",
    "CreateRelationshipClass",
    "TableToRelationshipClass",
    "CreateFileGDB",
    "CreateTable",
    "CreateFeatureclass",
    "Compact",
    "TruncateTable",
    "InsertCursor",
    "UpdateCursor",
    "Merge",
    "Dissolve",
)

# The read-only operations the accessor is allowed to use. Listed so the test can report
# something useful when it fails, and so the permitted surface is written down once.
PERMITTED_ARCPY_OPERATIONS = (
    "GetCount",
    "MakeTableView",
    "MakeFeatureLayer",
    "SelectLayerByAttribute",
    "SelectLayerByLocation",
    "SearchCursor",
    "TableToNumPyArray",
    "ListFields",
    "ListIndexes",
    "Describe",
    "Exists",
    "AddFieldDelimiters",
)


def iter_validation_source_files():
    """
        Every .py file in the validation package, as (relative_path, absolute_path).
    """
    for directory_path, _subdirectories, file_names in os.walk(VALIDATION_PACKAGE_DIR):
        for file_name in sorted(file_names):
            if not file_name.endswith(".py"):
                continue
            absolute_path = os.path.join(directory_path, file_name)
            relative_path = os.path.relpath(absolute_path, VALIDATION_PACKAGE_DIR)
            yield relative_path, absolute_path


def read_source(absolute_path: str) -> str:
    with open(absolute_path, "r", encoding="utf-8") as source_file:
        return source_file.read()


def test_the_package_has_source_files_to_scan():
    """
        Guards the guard. If the walk found nothing, every test below would pass
        vacuously, which would be considerably worse than a plain failure.
    """
    source_files = list(iter_validation_source_files())
    assert len(source_files) > 0
    relative_paths = [relative for relative, _absolute in source_files]
    assert ARCPY_SEAM_MODULE in relative_paths


@pytest.mark.parametrize(
    "relative_path, absolute_path", list(iter_validation_source_files())
)
def test_no_write_capable_arcpy_operation_appears_in_the_source(relative_path, absolute_path):
    """
        Scans for the names of destructive arcpy operations as attribute accesses or
        calls.

        The scan is AST-based rather than a plain substring search so that the word
        "Delete" inside a docstring or a comment does not trip it, while a genuine
        arcpy.management.Delete(...) call cannot hide behind formatting.
    """
    source = read_source(absolute_path)
    syntax_tree = ast.parse(source, filename=absolute_path)

    offending_operations = []

    for node in ast.walk(syntax_tree):
        # arcpy.management.Delete / arcpy.Delete_management style attribute access
        if isinstance(node, ast.Attribute) and node.attr in FORBIDDEN_WRITE_OPERATIONS:
            offending_operations.append((node.attr, getattr(node, "lineno", "?")))
        # A bare imported name, e.g. "from arcpy.management import Delete"
        elif isinstance(node, ast.Name) and node.id in FORBIDDEN_WRITE_OPERATIONS:
            offending_operations.append((node.id, getattr(node, "lineno", "?")))

    assert not offending_operations, (
        f"{relative_path} references write-capable operation(s) "
        f"{offending_operations}. The validation module must be strictly read-only; "
        f"permitted operations are {PERMITTED_ARCPY_OPERATIONS}."
    )


@pytest.mark.parametrize(
    "relative_path, absolute_path", list(iter_validation_source_files())
)
def test_arcpy_is_imported_only_in_the_designated_seam(relative_path, absolute_path):
    """
        Every arcpy call must go through GeodatabaseAccessor.

        Confining the import to one module is what makes the read-only promise auditable
        and what lets every check be unit-tested against a fake with no ArcGIS Pro
        installed.
    """
    source = read_source(absolute_path)
    syntax_tree = ast.parse(source, filename=absolute_path)

    imports_arcpy = False
    for node in ast.walk(syntax_tree):
        if isinstance(node, ast.Import):
            if any(alias.name.split(".")[0] == "arcpy" for alias in node.names):
                imports_arcpy = True
        elif isinstance(node, ast.ImportFrom):
            if node.module and node.module.split(".")[0] == "arcpy":
                imports_arcpy = True

    if relative_path == ARCPY_SEAM_MODULE:
        assert imports_arcpy, (
            f"{ARCPY_SEAM_MODULE} is the designated arcpy seam and is expected to "
            f"import arcpy."
        )
    else:
        assert not imports_arcpy, (
            f"{relative_path} imports arcpy. Only {ARCPY_SEAM_MODULE} may do so; "
            f"everything else must go through GeodatabaseAccessor."
        )


def test_the_accessor_exposes_no_write_methods():
    """
        A behavioural counterpart to the source scan: inspect the public surface of the
        accessor and confirm no method name suggests mutation.

        Skipped when arcpy is unavailable, since importing the accessor requires it.
    """
    pytest.importorskip("arcpy", reason="GeodatabaseAccessor cannot be imported without arcpy")

    from unbox.validation.gdb_access import GeodatabaseAccessor

    mutation_verbs = ("add", "create", "delete", "remove", "update", "insert",
                      "write", "repair", "rename", "append", "truncate", "calculate")

    public_method_names = [
        name for name in dir(GeodatabaseAccessor)
        if not name.startswith("_") and callable(getattr(GeodatabaseAccessor, name))
    ]

    for method_name in public_method_names:
        for verb in mutation_verbs:
            assert not method_name.lower().startswith(verb), (
                f"GeodatabaseAccessor.{method_name} looks like a mutating operation. "
                f"The accessor must expose read operations only."
            )
