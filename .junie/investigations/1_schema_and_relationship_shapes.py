"""
    Read-only probe of the real output geodatabase, to pin down three things the
    schema and integrity checks must compare against correctly:

    1. How arcpy reports relationship class endpoints, keys, and cardinality, for both
       the attributed many-to-many relations and the rc_ one-to-many ones.
    2. Whether attributed relationship classes really do use RID instead of OBJECTID,
       and how their field lists differ from the matching source table.
    3. Which index names ListIndexes actually returns, including the FDO_SHAPE spatial
       index, and whether field name casing differs from the GDBMerge declarations.

    Nothing here writes. Describe, ListFields and ListIndexes only.
"""

import os

import arcpy

OUTPUT_GDB = (
    r"C:\Users\nick.santos\Downloads\LightBox Deliveries 2026-10"
    r"\LBX_DELIVERY_20261020\PROFESSIONAL_FGDB\full_state_smartfabric_20261020.gdb"
)
SOURCE_GDB = (
    r"C:\Users\nick.santos\Downloads\LightBox Deliveries 2026-10"
    r"\LBX_DELIVERY_20261020\PROFESSIONAL_FGDB\processing_20261020"
    r"\SF_Professional_06003.gdb"
)


def show_relationship(relationship_name):
    """
        Prints everything the schema check wants to compare for one relationship class.
    """
    description = arcpy.Describe(os.path.join(OUTPUT_GDB, relationship_name))

    print(f"\n--- {relationship_name} ---")
    print(f"  dataType           : {description.dataType}")
    print(f"  cardinality        : {description.cardinality}")
    print(f"  isAttributed       : {description.isAttributed}")
    print(f"  originClassNames   : {description.originClassNames}")
    print(f"  destinationClassNames: {description.destinationClassNames}")
    print(f"  originClassKeys    : {description.originClassKeys}")
    print(f"  destinationClassKeys: {getattr(description, 'destinationClassKeys', None)}")
    print(f"  OIDFieldName       : {getattr(description, 'OIDFieldName', '(none)')}")


def show_fields(gdb_path, dataset_name, label):
    """
        Prints the field list so output and source can be diffed by eye.
    """
    full_path = os.path.join(gdb_path, dataset_name)
    if not arcpy.Exists(full_path):
        print(f"\n--- {label} {dataset_name}: DOES NOT EXIST ---")
        return

    fields = arcpy.ListFields(full_path)
    print(f"\n--- {label} {dataset_name}: {len(fields)} fields ---")
    print("  " + ", ".join(f"{field.name}({field.type},{field.length})" for field in fields))


def show_indexes(dataset_name):
    """
        Prints index names and their fields, to confirm the idx_<field> convention and
        the FDO_SHAPE spatial index.
    """
    full_path = os.path.join(OUTPUT_GDB, dataset_name)
    indexes = arcpy.ListIndexes(full_path)

    print(f"\n--- indexes on {dataset_name} ({len(indexes)}) ---")
    for index in indexes:
        field_names = ", ".join(field.name for field in index.fields)
        print(f"  {index.name:<28} fields=[{field_names}] unique={index.isUnique}")


print("=" * 78)
print("RELATIONSHIP CLASSES")
print("=" * 78)

# One attributed many-to-many, and one rc_ one-to-many, to see both shapes.
show_relationship("AddressParcelRelation")
show_relationship("rc_ParcelPrimaryBuilding")
show_relationship("rc_ParcelAssessments")

print("\n" + "=" * 78)
print("FIELD LISTS: output relationship class vs the same table in a source county")
print("=" * 78)

show_fields(OUTPUT_GDB, "AddressParcelRelation", "OUTPUT")
show_fields(SOURCE_GDB, "AddressParcelRelation", "SOURCE")

show_fields(OUTPUT_GDB, "ParcelAssessmentRelation", "OUTPUT")
show_fields(SOURCE_GDB, "ParcelAssessmentRelation", "SOURCE")

print("\n" + "=" * 78)
print("FIELD LISTS: a core feature class, output vs source")
print("=" * 78)

show_fields(OUTPUT_GDB, "Parcels", "OUTPUT")
show_fields(SOURCE_GDB, "Parcels", "SOURCE")

print("\n" + "=" * 78)
print("INDEXES")
print("=" * 78)

show_indexes("Parcels")
show_indexes("Addresses")
show_indexes("AddressParcelRelation")

print("\n" + "=" * 78)
print("PARCELS PRIMARY KEY FIELDS - nullability, for the integrity check")
print("=" * 78)

for field in arcpy.ListFields(os.path.join(OUTPUT_GDB, "Parcels")):
    if field.name.lower() in ("parcel_lid", "primary_assessment_lid", "primary_building_lid"):
        print(f"  {field.name:<26} type={field.type:<10} nullable={field.isNullable}")
