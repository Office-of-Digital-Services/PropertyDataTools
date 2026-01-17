import os
import tempfile
import zipfile  # this is probably slower than extracting beforehand
import shutil

import arcgisscripting
import arcpy

import logging

# Remove indexes before appending.
# Run Check/Repair Geometry on counties before merge
# Copy zoning data into GDB too!
# Note that Nate indicated he'd be interested in a pipeline for the stripped down version that can be used publicly so they don't process it
#  -- sounds like Nate will just use a view to strip it down
# What do they need in terms of indexing?
# Add some metadata - make sure to include a statement of appropriate use - also do this for city/county (or label the existing info that way.
# probably want to run a compact at the end of things? Might not shrink anything though.
# Will's feedback on a SmartParcels compatible items
# Can we add code to create the view programatically, including joining on the primary assessment and the primary building?
# We could also make a view of the buildings with the parcel/assessment details?

class GDBMerge(object):
    """
        Given a folder of zipped LightBox FGDB delivery files split by FIPS codes, merges them all into a single FGDB
    """

    """
        Create a test package in the root of the project and add a test for instantiation of this class
    """


    input_folder = None
    temp_folder = None
    output_gdb_path = None

    extract_zips = True  # when True, input folder must be full of zips, which will be extracted to temp_folder. When False, input_folder should be the extracted zip folder
    delete_zips = False
    delete_temp = False
    zips = list()
    zips_by_size = list()

    repair_geometry = True
    REPAIR_GEOMETRY_TABLES = ["Parcels", "Buildings"]

    table_names = list()

    _create_indexes = True

    MANYTOMANY_RELATIONSHIPS = [
        {
            "RelationName": "BuildingParcelRelation",
            "TempName": "BPR",
            "Origin": "Parcels",
            "Destination": "Buildings",
            "OriginKey": "PARCEL_LID",
            "Attributed": "ATTRIBUTED",
            "DestinationKey": "building_lid",
            "Cardinality": "MANY_TO_MANY",
        },
        {
            "RelationName": "BuildingAssessmentRelation",
            "TempName": "BAR",
            "Origin": "Buildings",
            "Destination": "Assessments",
            "OriginKey": "building_lid",
            "Attributed": "NONE",
            "DestinationKey": "ASSESSMENT_LID",
            "Cardinality": "MANY_TO_MANY",
        },
        {  # this one isn't actually a many to many, but the addresses table doesn't have a parcel_lid field and the parcels don't have an address_lid, so it's all stored in here
            "RelationName": "AddressParcelRelation",
            "TempName": "APR",
            "Origin": "Addresses",
            "Destination": "Parcels",
            "OriginKey": "address_lid",
            "Attributed": "NONE",
            "DestinationKey": "parcel_lid",
            "Cardinality": "MANY_TO_MANY",  # this ensures that it actually uses the through table
        },
        {
            # this one isn't actually a many to many, but the addresses table doesn't have a building_lid field and while the buildings table has an address_lid, it's many addresses to one building, so that doesn't get us what we need. Need to use the intermediate table.
            "RelationName": "AddressBuildingRelation",
            "TempName": "ABR",
            "Origin": "Addresses",
            "Destination": "Buildings",
            "OriginKey": "address_lid",
            "Attributed": "NONE",
            "DestinationKey": "building_lid",
            "Cardinality": "MANY_TO_MANY",  # this ensures that it actually uses the through table
        },
        {
            # Same as prior
            "RelationName": "AddressAssessmentRelation",
            "TempName": "AAR",
            "Origin": "Addresses",
            "Destination": "Assessments",
            "OriginKey": "address_lid",
            "Attributed": "NONE",
            "DestinationKey": "assessment_lid",
            "Cardinality": "MANY_TO_MANY",  # this ensures that it actually uses the through table
        }
    ]

    ONETOMANY_RELATIONSHIPS_PREFIX = "rc_"
    ONETOMANY_RELATIONSHIP_COMMON = {
            "message_direction": "NONE",
            "cardinality": "ONE_TO_MANY",
            "attributed": "NONE",
            "relationship_type": "SIMPLE"
    }
    ONETOMANY_RELATIONSHIPS = [
        {
            "out_relationship_class": "ParcelAssessments",
            "origin_table": "Parcels",
            "destination_table": "Assessments",
            "origin_primary_key": "parcel_lid",
            "origin_foreign_key": "PARCEL_LID",
            "forward_label": "Assessments",
            "backward_label": "Parcels"
        },
        {
            "out_relationship_class": "PrimaryParcelAssessment",
            "origin_table": "Parcels",
            "destination_table": "Assessments",
            "origin_primary_key": "primary_assessment_lid",
            "origin_foreign_key": "assessment_lid",
            "forward_label": "Primary Assessment",
            "backward_label": "Parcel"
        },
    ]

    """
        #    Turns out ArcGIS won't let us make a relationship class with the same origin/destination. We'll need to likely
        #    copy these records out to another table if we want to traverse them with a relationship class
             
        {
            "out_relationship_class": "AddressParent",
            "origin_table": "Addresses",
            "destination_table": "Addresses",
            "origin_primary_key": "address_lid",
            "origin_foreign_key": "parent_address_lid",
            "forward_label": "child_addresses",
            "backward_label": "parent_address"
        },
        {
            "out_relationship_class": "AddressPrimarySecondary",
            "origin_table": "Addresses",
            "destination_table": "Addresses",
            "origin_primary_key": "address_lid",
            "origin_foreign_key": "primary_address_lid",
            "forward_label": "secondary_addresses",
            "backward_label": "primary_address"
        },
    ]
    """

    KEY_INDEXES = [
        ("Parcels", "Parcel_LID"),
        ("Parcels", "PRIMARY_ASSESSMENT_LID"),
        ("Buildings", "building_lid"),
        ("Buildings", "primary_address_lid"),
        ("Buildings", "primary_parcel_lid"),
        ("Addresses", "address_lid"),
        ("Assessments", "ASSESSMENT_LID"),
        ("BuildingParcelRelation", "PARCEL_LID"),
        ("BuildingParcelRelation", "building_lid"),

        # The following relations aren't attributed, so we can create relationship classes without moving anything around and only need to index the keys
        ("AddressAssessmentRelation", "address_lid"),
        ("AddressAssessmentRelation", "assessment_lid"),
        ("AddressBuildingRelation", "address_lid"),
        ("AddressBuildingRelation", "building_lid"),
        ("AddressParcelRelation", "address_lid"),
        ("AddressParcelRelation", "parcel_lid"),
        ("BuildingAssessmentRelation", "building_lid"),
        ("BuildingAssessmentRelation", "assessment_lid"),
        ("ParcelAssessmentRelation", "ASSESSMENT_LID"),
        ("ParcelAssessmentRelation", "PARCEL_LID"),
    ]

    ATTRIBUTE_INDEXES = [
        # Table, Field
        ("Parcels", "FIPS_CODE"),
        ("Parcels", "PRIMARY_BUILDING_LID"),
        ('Parcels', "AGGR_ACREAGE"),
        ('Parcels', "AGGR_GROUP"),
        ("Buildings", "FIPS_CODE"),
        ("Buildings", "county"),
        ("Buildings", "area_sqft"),
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
        # ("Addresses", "building_name_usps"), It's possible this field is empty in CA?
        ("Assessments", "FIPS_CODE"),
        ("Assessments", "PARCEL_LID"),
        ("Assessments", "PARCEL_APN"),
        ("Assessments", "ACREAGE"),
        ("Assessments", "TAXAPN"),
        ("Assessments", "SITE_ZIP"),
        ("Assessments", "MAIL_ZIP"),
        ("Assessments", "SITE_CITY"),
        ("Assessments", "CENSUS_BLOCK"),
        ("Assessments", "CENSUS_BLOCK_GROUP"),
        ("BuildingParcelRelation", "building_overlap_ratio"),
        ("BuildingParcelRelation", "parcel_overlap_ratio"),
    ]

    # FGDB doesn't have fulltext search index, so these aren't helpful
    #FULLTEXT_INDEXES = [
    #    ("Addresses", "address"),
    #    ("Assessments", "SITE_ADDR")
    #]

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

        if self.repair_geometry:
            self.handle_repair_geometry()

        # leaving the next line as a flag - it's unnecessary and just slows things down. Creating the relationship classes automatically creates the indexes.
        #self.create_indexes(self.KEY_INDEXES)  # add indexes to just the key attributes now to support creating the relationship classes better. We'll index everything else at the end
        self._handle_manytomany_relationships()  # when we use our method, this should happen first. If we use Esri's builtin, it should be last.
        self._drop_indexes(indexes=self.KEY_INDEXES)  # we'll drop them now so that when we go to insert records it's not slow. We'd need to recreate the index later anyway.

        self.append_all_gdbs()

        if self._create_indexes:
            self.create_indexes(drop_first=False)  # Our ideal is for this to happen after the appends and before the relationship classes. Since we're building relationship classes manually, this now happens last, except for non-attributed relationships
            self.recreate_spatial_indexes() # we want this after the append so that the optimal grid size gets recalculated and the index is rebuilt

        self.create_relationship_classes()  # Make the simpler relationship classes that we can build at the end now.

        self.cleanup()

    def _bypass_merge(self):
        self._get_zip_sizes()
        self.get_source_tables()

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

    def _size_sum(self, size_list):
        size_mb = round(sum(size_list) / 1024 / 1024)
        return size_mb / 1000  # 1000 here instead of 1024 to ensure only three decimal places. Could use number formatting, but used this.

    def _size_report(self):
        directory = self.output_gdb_path
        file_list = os.listdir(directory)
        sizes_in_bytes = {f: os.path.getsize(os.path.join(directory, f)) for f in file_list if os.path.isfile(os.path.join(directory, f))}
        size_gb = self._size_sum(sizes_in_bytes.values())

        index_sizes = [sizes_in_bytes[f] for f in sizes_in_bytes.keys() if "lid" in f.lower()]
        indexes_gb = self._size_sum(index_sizes)

        logging.info(f"Current Size: {size_gb} GB")
        logging.info(f"Index Size: {indexes_gb} GB - {round((indexes_gb/size_gb)*100)}%")

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

    def create_indexes(self, indexes=None, drop_first=False):
        if indexes is None:
            indexes = self.KEY_INDEXES + self.ATTRIBUTE_INDEXES

        with arcpy.EnvManager(workspace=self.output_gdb_path):
            if drop_first:
                self._drop_indexes(indexes)

            for table, field in indexes:  # it's a tuple, not a dict - 0 is table, 1 is field
                logging.info(f"Creating index on {table}.{field}")
                arcpy.management.AddIndex(table, field, f"idx_{field}")

    def _drop_indexes(self, indexes: list[tuple[str, str]]):
        tables = {table: 1 for table, field in indexes}  # get the set of unique tables  - could also do this as list(set(list)))

        with arcpy.EnvManager(workspace=self.output_gdb_path):
            for table in tables.keys():
                indexes = arcpy.ListIndexes(table)
                drop_indexes = [idx.name for idx in indexes if not idx.name.startswith("FDO")]
                try:
                    arcpy.management.RemoveIndex(table, drop_indexes)
                except arcpy.ExecuteError:
                    pass  # it's OK to not remove it

    def recreate_spatial_indexes(self):
        with arcpy.EnvManager(workspace=self.output_gdb_path):
            for table in self.SPATIAL_INDEXES:
                logging.info(f"Recreating spatial index on {table}")
                arcpy.management.AddSpatialIndex(table, 0, 0, 0)  # the three zeros force it to recalculate the optimal grid size and ensure the index will be rebuilt

    def handle_repair_geometry(self):
        for gdb in self.gdbs_by_size:
            with arcpy.EnvManager(workspace=gdb):
                for table in self.REPAIR_GEOMETRY_TABLES:
                    logging.info(f"Repairing geometry on {gdb}.{table}")
                    arcpy.management.RepairGeometry(table)

    def append_all_gdbs(self):
        with arcpy.EnvManager(workspace=self.output_gdb_path):
            for dataset in self.table_names:
                logging.info(f"Appending contents of all GDBs for theme {dataset}")
                input_data = [os.path.join(self._zip_to_gdb_name(z), dataset) for z in self.zips_by_size] # get a list with all the inputs and we can run them at once!
                arcpy.management.Append(input_data, os.path.join(self.output_gdb_path, dataset))

    def create_relationship_classes(self):
        """
            Warning - this isn't likely the best way to go about this.
        """
        with arcpy.EnvManager(workspace=self.output_gdb_path):
            for rel in self.ONETOMANY_RELATIONSHIPS:
                rel = {**rel, **self.ONETOMANY_RELATIONSHIP_COMMON}  # merge in the common items
                rel["out_relationship_class"] = self.ONETOMANY_RELATIONSHIPS_PREFIX + rel["out_relationship_class"]
                #rel["origin_table"] = os.path.join(self.output_gdb_path, rel["origin_table"])
                #rel["destination_table"] = os.path.join(self.output_gdb_path, rel["destination_table"])
                print(rel)
                arcpy.management.CreateRelationshipClass(**rel)

    def generate_overlaps(self, skip=0):
        """
            This function stub is meant to determine which features along county lines overlap each other
            We'll need to process the incoming data to determine potential overlaps
        """
        with arcpy.EnvManager(workspace=self.output_gdb_path):
            pass
            # parcel_layers = [os.path.join(db, "parcels") for db in self.gdbs_by_size]
            #arcpy.analysis.Intersect(parcel_layers[skip:], "county_overlaps")

    def _handle_attributed_relationships_alternative(self):
        """
            Wrote this method to see if using a built-in tool to handle this would make the intermediate attributes
            appear in Esri tools. Never got the chance to find out because it just *won't* finish running. We'll use the
            longer code we made in _handle_attributed_relationships instead, I think.
        """
        with arcpy.EnvManager(workspace=self.output_gdb_path):
            for config in self.MANYTOMANY_RELATIONSHIPS:

                att_field_names = [field.name for field in arcpy.ListFields(config["RelationName"]) if field.name.lower() not in ("objectid", "parcel_lid", "address_lid", "assessment_lid", "building_lid", "fips_code")]

                arcpy.management.TableToRelationshipClass(
                    origin_table=config["Origin"],
                    destination_table=config["Destination"],
                    out_relationship_class=f"rc_{config['RelationName']}",
                    relationship_type="SIMPLE",
                    forward_label=config["Destination"],
                    backward_label=config["Origin"],
                    message_direction="NONE",
                    cardinality=config["Cardinality"],
                    relationship_table=config["RelationName"],
                    attribute_fields=att_field_names,
                    origin_primary_key=config["OriginKey"],
                    origin_foreign_key=config["OriginKey"],
                    destination_primary_key=config["DestinationKey"],
                    destination_foreign_key=config["DestinationKey"],)

    def _handle_manytomany_relationships(self):
        """
            These need to be handled first rather than created at the end, so that the information from other
            geodatabases gets appended into the relationship class


            Blerg, this might just need to be Table To Relationship Class!
        :return:
        """
        with arcpy.EnvManager(workspace=self.output_gdb_path):
            logging.info("Preparing Many to Many relationships by renaming tables")
            for config in self.MANYTOMANY_RELATIONSHIPS:
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
                    attributed="ATTRIBUTED" if config["Attributed"] else "NONE",
                    origin_primary_key=config["OriginKey"],
                    origin_foreign_key=config["OriginKey"],
                    destination_primary_key=config["DestinationKey"],
                    destination_foreign_key=config["DestinationKey"],
                )

                logging.info("Adding attributes to Many to Many Relationship Class")
                # Get all attributes in the temp table
                all_fields = arcpy.ListFields(config["TempName"])
                existing_fields = arcpy.ListFields(config["RelationName"])
                existing_field_names = [field.name.lower() for field in existing_fields]
                fields = [field for field in all_fields if (field.name.lower() not in existing_field_names) and (field.name != "OBJECTID")]  # just use the fields that don't already exist on the relation

                # Add them to the relationship class
                additions = [[field.name, field.type, field.aliasName, field.length, field.defaultValue, field.precision, field.scale, field.isNullable, field.required] for field in fields]

                for addition in additions:
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

