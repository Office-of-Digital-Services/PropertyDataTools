import os
import tempfile

import arcpy
import arcgis

def prepare_locator_data(parcels, assessments, output_folder, parcel_gdb_name="temp_parcels.gdb"):
    """
    Copies parcels to a temporary geodatabase and joins address information for use in building a locator.

    Args:
        parcels: Path to input parcels feature class
        assessments: Path to assessments table containing primary address information

    Returns:
        Path to the output parcels feature class with joined address fields
    """
    # Create temporary geodatabase
    print("Preparing statewide parcel data for locator by attaching address information")
    temp_folder=tempfile.mkdtemp(prefix="parcels_with_addresses_", dir=output_folder)
    temp_gdb = arcpy.management.CreateFileGDB(temp_folder, parcel_gdb_name)[0]

    # Copy parcels to temp gdb
    output_parcels = os.path.join(temp_gdb, "parcels_with_addresses")
    arcpy.management.CopyFeatures(parcels, output_parcels)

    # Join address fields
    arcpy.management.JoinField(
        in_data=output_parcels,
        in_field="PRIMARY_ASSESSMENT_LID",
        join_table=assessments,
        join_field="ASSESSMENT_LID",
        fields=["COUNTY", "SITE_ADDR", "SITE_HOUSE_NUMBER", "SITE_DIRECTION", "SITE_STREET_NAME", "SITE_MODE", "SITE_QUADRANT", "SITE_UNIT_PREFIX", "SITE_UNIT_NUMBER", "SITE_CITY", "SITE_STATE", "SITE_ZIP", "SITE_PLUS_4"],
        index_join_fields="NEW_INDEXES"
    )

    return output_parcels, temp_gdb

def copy_remote_to_local(cities, counties, temp_gdb):
    if not (cities.startswith("http") or counties.startswith("http")):
        return

    print("Downloading remote data for locator")

    portal = arcgis.GIS("https://arcgis.com")

    if cities.startswith("http"):
        cities_layer = arcgis.features.FeatureLayer(cities)
        features = cities_layer.query()
        features.save(temp_gdb, "cities")
        cities = os.path.join(temp_gdb, "cities")

    if counties.startswith("http"):
        counties_layer = arcgis.features.FeatureLayer(counties)
        features = counties_layer.query()
        features.save(temp_gdb, "counties")
        counties = os.path.join(temp_gdb, "counties")

    return cities, counties



def make_locator(input_smartfabric_gdb, output_locator_path, cities=None, counties=None, tiger=None, parcels_with_addresses=None, temp_gdb=None):
    if cities is None or counties is None or tiger is None:  # TODO: this is temporary and will be removed later
        raise ValueError("cities, counties, and TIGER must be specified")

    if parcels_with_addresses and not temp_gdb:
        raise ValueError("If parcels_with_addresses is specified, temp_gdb must also be specified")

    if not parcels_with_addresses:
        # Prepare parcel data with address information
        parcels_with_addresses, temp_gdb = prepare_locator_data(
            parcels=os.path.join(input_smartfabric_gdb, "Parcels"),
            assessments=os.path.join(input_smartfabric_gdb, "Assessments"),
            output_folder=os.path.dirname(output_locator_path),
        )

    cities, counties = copy_remote_to_local(cities, counties, temp_gdb)

    addresses_table = "Addresses"
    parcels_table = os.path.split(parcels_with_addresses)[1]  # in the field mapping, we just need the table name - we use the full path in the TABLE_MAPPING

    TABLE_MAPPING = [
        ("Addresses", "PointAddress"),  # this will be properly referenced below because we'll make its source database the workspace
        (parcels_with_addresses, "Parcel"),
        (cities, "City"),
        (tiger, "StreetAddress"),
        (counties, "Subregion")
    ]
    table_mapping = ";".join([" ".join(item) for item in TABLE_MAPPING])  # this is easier than constructing a valuetable input to the geoprocessing tool

    cities_table = os.path.split(cities)[1]
    counties_table = os.path.split(counties)[1]
    tiger_table = os.path.split(tiger)[1]

    # See https://pro.arcgis.com/en/pro-app/latest/help/data/geocoding/locator-role-fields.htm for mapping here
    VALUES_MAPPING = [
                      f'PointAddress.HOUSE_NUMBER {addresses_table}.house_number',
                      f'PointAddress.STREET_NAME {addresses_table}.street_name',
                      f'PointAddress.STREET_PREFIX_TYPE {addresses_table}.prefix_type',
                      f'PointAddress.STREET_SUFFIX_TYPE {addresses_table}.suffix_type',
                      f'PointAddress.STREET_SUFFIX_DIR {addresses_table}.suffix_direction',
                      f'PointAddress.FULL_STREET_NAME {addresses_table}.address',
                      f'PointAddress.SUB_ADDRESS_UNIT {addresses_table}.unit',
                      f'PointAddress.CITY {addresses_table}.city',
                      f'PointAddress.SUBREGION_JOIN_ID {addresses_table}.fips_code',
                      f'PointAddress.SUBREGION {addresses_table}.county',
                      f'PointAddress.REGION {addresses_table}.state',
                      f'PointAddress.POSTAL {addresses_table}.zip',
                      # f'PointAddress.COUNTRY_CODE {addresses_table}.country_code', # Apparently "US" is an invalid country code
                      # 'PointAddress.BUILDING_NAME {addresses_table}.building_name_usps'  # 100% null values as of October 2025 release

                      # Parcels layer
                      f'Parcel.PARCEL_NAME {parcels_table}.PARCEL_LID',
                      f'Parcel.PARCEL_JOIN_ID {parcels_table}.PARCEL_LID',
                      f'Parcel.SUBREGION_JOIN_ID {parcels_table}.FIPS_CODE',

                        # TODO: Check that this has the full county name
                      f'Parcel.SUBREGION {parcels_table}.COUNTY',  # Matches County in UI
                      f'Parcel.SUBREGION_JOIN_ID {parcels_table}.COUNTY_FIPS', # Matches County Join ID in UI
                      f'Parcel.HOUSE_NUMBER {parcels_table}.SITE_HOUSE_NUMBER',

                        # TODO: This may ultimately need to be prefix direction with SITE_QUADRANT being suffix direction
                      f'Parcel.STREET_PREFIX_DIR {parcels_table}.SITE_DIRECTION',
                      f'Parcel.STREET_NAME {parcels_table}.SITE_STREET_NAME',  # Matches Street Name in UI - not Full Street Name
                      f'Parcel.FULL_STREET_NAME {parcels_table}.SITE_ADDR',
                      f'Parcel.STREET_SUFFIX_TYPE {parcels_table}.SITE_MODE',
                      f'Parcel.STREET_SUFFIX_DIR {parcels_table}.SITE_QUADRANT',
                      f'Parcel.SUB_ADDRESS_UNIT_TYPE {parcels_table}.SITE_UNIT_PREFIX', # Matches Unit Type in UI
                      f'Parcel.SUB_ADDRESS_UNIT {parcels_table}.SITE_UNIT_NUMBER',
                      f'Parcel.CITY {parcels_table}.SITE_CITY',
                      f'Parcel.REGION {parcels_table}.SITE_STATE',
                      f'Parcel.POSTAL {parcels_table}.SITE_ZIP',
                      f'Parcel.POSTAL_EXT {parcels_table}.SITE_PLUS_4',

                      # Cities layer - supports locations with simple partial addresses
                      f'City.CITY_JOIN_ID {cities_table}.CENSUS_GEOID',
                      f'City.CITY {cities_table}.CDTFA_CITY',

                      # CENSUS TIGER layer - for address interpolation and geocoding
                      f'StreetAddress.HOUSE_NUMBER_FROM_LEFT {tiger_table}.LFROMHN',
                      f'StreetAddress.HOUSE_NUMBER_TO_LEFT {tiger_table}.LTOHN',
                      f'StreetAddress.HOUSE_NUMBER_FROM_RIGHT {tiger_table}.RFROMHN',
                      f'StreetAddress.HOUSE_NUMBER_TO_RIGHT {tiger_table}.RTOHN',
                      f'StreetAddress.PARITY_LEFT {tiger_table}.PARITYL',
                      f'StreetAddress.PARITY_RIGHT {tiger_table}.PARITYR',
                      f'StreetAddress.STREET_NAME {tiger_table}.FULLNAME',
                      f'StreetAddress.POSTAL_LEFT {tiger_table}.ZIPL',
                      f'StreetAddress.POSTAL_EXT_LEFT {tiger_table}.PLUS4L',
                      f'StreetAddress.POSTAL_RIGHT {tiger_table}.ZIPR',
                      f'StreetAddress.POSTAL_EXT_RIGHT {tiger_table}.PLUS4R',

                      # Counties layer
                      f'Subregion.SUBREGION_JOIN_ID {counties_table}.CENSUS_GEOID',
                      f'Subregion.SUBREGION {counties_table}.CDTFA_COUNTY'  # TODO: CHECK IF THIS HAS THE FULL COUNTY NAME - IT IS RECOMMENDED THAT IT INCLUDES "County"
    ]

    # TODO: Need to specify the actual table names in the field map for the items not in the workspace -
    #  this just captures the creation of the test locator

    print("Building locator")
    with arcpy.EnvManager(workspace=input_smartfabric_gdb):
        arcpy.geocoding.CreateLocator(
            country_code="USA",
            primary_reference_data=table_mapping,
            field_mapping=VALUES_MAPPING,  # we can use this one directly as a list
            out_locator=output_locator_path,
            language_code="ENG",
            alternatename_tables=None,
            alternate_field_mapping=None,
            custom_output_fields=None,
            precision_type="GLOBAL_EXTRA_HIGH",
            version_compatibility="CURRENT_VERSION"
        )

