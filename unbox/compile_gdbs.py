import os
import tempfile
import zipfile  # this is probably slower than extracting beforehand
import shutil

import arcpy

import logging

class GDBMerge(object):
    """
        Given a folder of zipped LightBox FGDB delivery files split by FIPS codes, merges them all into a single FGDB
    """

    input_folder = None
    temp_folder = None
    output_gdb_path = None

    extract_zips = True  # when True, input folder must be full of zips, which will be extracted to temp_folder. When False, input_folder should be the extracted zip folder
    delete_zips = False
    delete_temp = False
    zips = list()
    zips_by_size = list()

    table_names = list()

    ATTRIBUTED_RELATIONSHIPS = [
        {
            "RelationName": "BuildingParcelRelation",
            "TempName": "BPR",
            "Origin": "Parcels",
            "Destination": "Buildings",
            "OriginKey": "PARCEL_LID",
            "DestinationKey": "building_lid",
            "Cardinality": "MANY_TO_MANY",
        }
    ]

    ATTRIBUTE_INDEXES = [
        # Table, Field
        ("Parcels", "Parcel_LID"),
        ("Parcels", "FIPS_CODE"),
        ("Parcels", "PRIMARY_ASSESSMENT_LID"),
        ("Parcels", "PRIMARY_BUILDING_LID"),
        ("Buildings", "building_lid"),
        ("Buildings", "FIPS_CODE"),
        ("Buildings", "county"),
        ("Buildings", "area_sqft"),
        ("Buildings", "primary_address_lid"),
        ("Buildings", "primary_parcel_lid"),
        ("Addresses", "address_lid"),
        ("Addresses", "FIPS_CODE"),
        ("Addresses", "county"),
        ("Addresses", "city"),
        ("Addresses", "zip"),
        ("Addresses", "resbus_usps"),
        ("Addresses", "precision_code"),
        ("Addresses", "address_confidence_score"),
        ("Addresses", "is_primary_address"),
        ("Addresses", "primary_address_lid"),
        ("Addresses", "parent_address_lid"),
        ("BuildingParcelRelation", "PARCEL_LID"),
        ("BuildingParcelRelation", "building_lid"),
        ("BuildingParcelRelation", "building_overlap_ratio"),
        ("BuildingParcelRelation", "parcel_overlap_ratio")
    ]

    SPATIAL_INDEXES = ["Parcels", "Buildings", "Addresses", "Assessments"]

    def __init__(self, input_folder, output_gdb_path, temp_folder=None, setup_logging=False, extract_zips=True):
        """

        :param input_folder: The folder that contains the SmartFabric zip files for each FIPS code
        :param temp_folder: Optional path to folder to use for extracting data. If not provided, one will be created automatically in your system temp folder
        :param output_gdb_path: The full path to where you want the output geodatabase to be created, including the geodatabase name and extension. Must not exist yet.
        """
        self.input_folder = input_folder
        self.output_gdb_path = output_gdb_path
        self.extract_zips = extract_zips

        if temp_folder is not None:
            if not os.path.exists(temp_folder):
                os.makedirs(temp_folder)
            self.temp_folder = temp_folder
        else:
            self.temp_folder = tempfile.mkdtemp(prefix="lightbox_merge_")

        if setup_logging:
            logging.basicConfig()

        logging.info(f"Using temp folder {self.temp_folder}")

    def cleanup(self):
        if self.delete_zips:
            logging.warning(f"Deleting zip files is not yet implemented. Zip files will remain")

        if self.delete_temp:
            logging.info(f"Deleting temp folder {self.temp_folder}")
            shutil.rmtree(self.temp_folder)

    def run_merge(self):
        if self.extract_zips:
            self.process_zips()
            self.move_largest_to_output()
        self.get_source_tables()

        self.create_indexes()  # we want this before we merge everything - it can help with the relationship classes
        self._handle_attributed_relationships()
        self.append_all_gdbs()

        self.recreate_spatial_indexes() # we want this after the append so that the optimal grid size gets recalculated and the index is rebuilt
        self.create_relationship_classes()  # this may end up going away or moving because we might build them differently

        self.cleanup()

    def _get_zip_sizes(self):
        self.zips = [os.path.join(self.input_folder, z) for z in os.listdir(str(self.input_folder)) if z.endswith(".zip")]
        self.zips_by_size = sorted(self.zips, key=lambda x: os.path.getsize(x), reverse=True)  # we want to work in size order for some things

    def process_zips(self):
        self._get_zip_sizes()

        logging.info(f"Unzipping {len(self.zips)} files")
        for full_path in self.zips:
            if not zipfile.is_zipfile(full_path):
                logging.warning(f"Skipping {full_path} - is not a zip file")
                continue
            logging.info(f"Unzipping {os.path.split(full_path)[1]}")
            shutil.unpack_archive(full_path, str(self.temp_folder))

    def move_largest_to_output(self):

        initial_source_gdb = self._zip_to_gdb_name(self.zips_by_size[0])

        logging.info(f"Moving largest zip to become the output GDB {self.output_gdb_path}")
        shutil.move(initial_source_gdb, self.output_gdb_path)

        self.zips_by_size.pop(0) # remove it since it's now the base gdb

    def _zip_to_gdb_name(self, zip_name) -> str:
        gdb_zipname = os.path.split(zip_name)[1]
        gdb_basename = f"{os.path.splitext(gdb_zipname)[0][:-9]}.gdb"  # the negative nine here strips off the datestamp from the filenames that isn't in zips. THis isn't robust, but works for now
        gdb = os.path.join(self.temp_folder, gdb_basename)
        return gdb

    @property
    def gdbs_by_size(self):
        return [self._zip_to_gdb_name(z) for z in self.zips_by_size]

    def get_source_tables(self):
        with arcpy.EnvManager(workspace=self.output_gdb_path):
            logging.info(f"Getting list of tables in {self.output_gdb_path}")
            features = arcpy.ListFeatureClasses()
            tables = arcpy.ListTables()
            self.table_names = features + tables

    def create_indexes(self):
        with arcpy.EnvManager(workspace=self.output_gdb_path):
            for table, field in self.ATTRIBUTE_INDEXES:
                logging.info(f"Creating index on {table}.{field}")
                arcpy.management.AddIndex(table, field, f"idx_{field}")

    def recreate_spatial_indexes(self):
            for table in self.SPATIAL_INDEXES:
                logging.info(f"Recreating spatial index on {table}")
                arcpy.management.AddSpatialIndex(table, 0, 0, 0)  # the three zeros force it to recalculate the optimal grid size and ensure the index will be rebuilt

    def append_all_gdbs(self):
        with arcpy.EnvManager(workspace=self.output_gdb_path):
            for dataset in self.table_names:
                logging.info(f"Appending contents of all GDBs for theme {dataset}")
                input_data = [os.path.join(self._zip_to_gdb_name(z), dataset) for z in self.zips_by_size] # get a list with all the inputs and we can run them at once!
                arcpy.management.Append(input_data, os.path.join(self.output_gdb_path, dataset))

    def create_relationship_classes(self):
        with arcpy.EnvManager(workspace=self.output_gdb_path):
            arcpy.management.CreateRelationshipClass(
                origin_table=r"Parcels",
                destination_table=r"Assessments",
                out_relationship_class=r"RC_Parcels_Assessments",
                relationship_type="SIMPLE",
                forward_label="Assessments",
                backward_label="Parcels",
                message_direction="NONE",
                cardinality="ONE_TO_MANY",
                attributed="NONE",
                origin_primary_key="PARCEL_LID",
                origin_foreign_key="PARCEL_LID",
            )

    def generate_overlaps(self, skip=0):
        with arcpy.EnvManager(workspace=self.output_gdb_path):
            pass
            # parcel_layers = [os.path.join(db, "parcels") for db in self.gdbs_by_size]
            #arcpy.analysis.Intersect(parcel_layers[skip:], "county_overlaps")

    def _handle_attributed_relationships(self):
        return
        arcpy.management.TableToRelationshipClass(self.output_gdb_path,
                    origin_table=config["Origin"],
                    destination_table=config["Destination"],
                    out_relationship_class=config["RelationName"],
                    relationship_type="SIMPLE",
                    forward_label=config["Destination"],
                    backward_label=config["Origin"],
                    message_direction="NONE",
                    cardinality=config["Cardinality"],
                    attributed="ATTRIBUTED",
                    origin_primary_key=config["OriginKey"],
                    origin_foreign_key=config["OriginKey"],
                    destination_primary_key=config["DestinationKey"],
                    destination_foreign_key=config["DestinationKey"],)

    def _original_handle_attributed_relationships(self):
        """
            These need to be handled first rather than created at the end, so that the information from other
            geodatabases gets appended into the relationship class


            Blerg, this might just need to be Table To Relationship Class!
        :return:
        """
        with arcpy.EnvManager(workspace=self.output_gdb_path):
            logging.info("Preparing Many to Many relationships by renaming tables")
            for config in self.ATTRIBUTED_RELATIONSHIPS:
                logging.info(f"Renaming {config['RelationName']} to {config['TempName']}")
                arcpy.management.Rename(config['RelationName'], config['TempName'])

                logging.info("Creating Many to Many Relationship Class")
                arcpy.management.CreateRelationshipClass(
                    origin_table=config["Origin"],
                    destination_table=config["Destination"],
                    out_relationship_class=config["RelationName"],
                    relationship_type="SIMPLE",
                    forward_label=config["Destination"],
                    backward_label=config["Origin"],
                    message_direction="NONE",
                    cardinality=config["Cardinality"],
                    attributed="ATTRIBUTED",
                    origin_primary_key=config["OriginKey"],
                    origin_foreign_key=config["OriginKey"],
                    destination_primary_key=config["DestinationKey"],
                    destination_foreign_key=config["DestinationKey"],
                )

                # Get all attributes in the temp table
                all_fields = arcpy.ListFields(config["TempName"])
                existing_fields = arcpy.ListFields(config["RelationName"])
                existing_field_names = [field.name.lower() for field in existing_fields]
                fields = [field for field in all_fields if (field.name.lower() not in existing_field_names) and (field.name != "OBJECTID")]  # just use the fields that don't already exist on the relation

                # Add them to the relationship class
                additions = [[field.name, field.type, field.aliasName, field.length, field.defaultValue, field.precision, field.scale, field.isNullable, field.required] for field in fields]
                #print(additions)
                #arcpy.management.AddFields(config["RelationName"], additions)
                for addition in additions:
                #    print(addition[0])
                    arcpy.management.AddField(config["RelationName"], addition[0], addition[1], field_alias=addition[2], field_length=addition[3], field_is_nullable=addition[7], field_is_required=addition[8])

                logging.info("Filling Many to Many Relationship Class with initial data")
                # Append the records - after we migrate the ones here in, everything else should append normally, but they'll have to skip the RID attribute
                arcpy.management.Append([config["TempName"]], config["RelationName"])

                # Delete the temp table
                arcpy.management.Delete(config["TempName"])

# Add indexes
# Do we need to rebuild the attribute indexes at the end?
# Or maybe it makes the most sense to add the attribute indexes for the various keys up front, then build the indexes for everything else at the end?
# We might be able to do it all at once after appends if we can move the work related to the attributed relationship class to the end too (I think we can)
## -- on what fields? And on the input data as well? Should we add it to the county-level datasets, then re-zip them?
# Recalculate spatial indexes
# Add remaining relationship classes - check naming scheme though - also check cardinality of each one

# request Git Repo
# Add timing code for each step
# Make logs print out
# Check output records
# See if other parameters would help
# produce layer of overlapping - that tool may take a while - we'll want it to be a separate step/call, I think.

