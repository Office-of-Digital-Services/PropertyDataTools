from __future__ import annotations

import os
import tempfile

from dataclasses import dataclass
from typing import Dict, Iterable, List, Optional, Tuple

import arcpy
import arcgis

@dataclass
class BuildConfig:
    input_gdb: str
    output_locator_path: str

    # Common configuration flags with paths/URLs attached
    cities: Optional[str] = None
    counties: Optional[str] = None
    tiger: Optional[str] = None
    portal_auth: Optional[str] = "pro"
    portal: Optional[str] = "https://www.arcgis.com"

    # Feature flags / toggles
    include_address_points: bool = True
    include_parcels: bool = True

    # Optional advanced inputs
    parcels_with_addresses: Optional[str] = None
    temp_gdb: Optional[str] = None

    output_folder: str = None
    parcel_gdb_name: str = "temp_parcels.gdb"

    # Free-form config values: --config KEY=VALUE (repeatable)
    extra: Dict[str, str] | None = None

    def run_build(self):
        if not self.output_folder:
            self.output_folder = os.path.dirname(self.output_locator_path)

        if not self.temp_gdb:
            # Create the temporary geodatabase before running the rest of the code
            self.temp_gdb = make_temp_gdb(self.output_folder, self.parcel_gdb_name)

        make_locator(input_smartfabric_gdb=self.input_gdb,
                     output_locator_path=self.output_locator_path,
                     include_address_points=self.include_address_points,
                     include_parcels=self.include_parcels,
                     cities=self.cities,
                     counties=self.counties,
                     tiger=self.tiger,
                     parcels_with_addresses=self.parcels_with_addresses,
                     temp_gdb=self.temp_gdb
        )


def make_temp_gdb(output_folder: str, gdb_name: str = "temp_parcels.gdb"):
    temp_folder = tempfile.mkdtemp(prefix="parcels_with_addresses_", dir=output_folder)
    temp_gdb = arcpy.management.CreateFileGDB(temp_folder, gdb_name)[0]
    print(f"Temp GDB: {temp_gdb}")
    return temp_gdb

def prepare_parcel_data(parcels, assessments, temp_gdb):
    """
    Copies parcels to a temporary geodatabase and joins address information for use in building a locator.

    Args:
        parcels: Path to input parcels feature class
        assessments: Path to assessments table containing primary address information

    Returns:
        Path to the output parcels feature class with joined address fields
    """

    print("Preparing statewide parcel data for locator by attaching address information")
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

    return output_parcels

def prepare_address_data(addresses, temp_gdb):
    # copy it out to temp, make zip5 and zip4 fields
    print("Preparing statewide address data for locator by splitting ZIP codes into separate fields for 5 digit and extension")
    output_addresses = os.path.join(temp_gdb, "address_points")
    arcpy.management.CopyFeatures(addresses, output_addresses)

    arcpy.management.AddField(output_addresses, "ZIP5", field_type="TEXT", field_length=5, field_is_nullable=True)
    arcpy.management.AddField(output_addresses, "ZIPEXT", field_type="TEXT", field_length=4, field_is_nullable=True)

    arcpy.management.CalculateField(output_addresses, "ZIP5", "!ZIP![:5]")
    arcpy.management.CalculateField(output_addresses, "ZIPEXT", "!ZIP!.split('-')[1] if '-' in !ZIP! else None")

    return output_addresses

def copy_remote_to_local(cities=None, counties=None, temp_gdb=None, portal_auth="pro", portal=None):
    if not (cities.startswith("http") or counties.startswith("http")):
        return

    print("Downloading remote data for locator")

    if portal_auth != "pro" and portal is None:
        raise ValueError(f"Portal authentication set to something other than 'pro' but no portal URL provided.")
    if portal_auth == "pro":
        portal = arcgis.GIS(portal_auth)
    else:
        portal = arcgis.GIS(portal)

    if cities and cities.startswith("http"):
        cities_layer = arcgis.features.FeatureLayer(cities)
        features = cities_layer.query()
        features.save(temp_gdb, "cities")
        cities = os.path.join(temp_gdb, "cities")

    if counties and counties.startswith("http"):
        counties_layer = arcgis.features.FeatureLayer(counties)
        features = counties_layer.query()
        features.save(temp_gdb, "counties")
        counties = os.path.join(temp_gdb, "counties")

    return cities, counties



def make_locator(input_smartfabric_gdb, output_locator_path, include_address_points=True, processed_address_points=None, include_parcels=True, cities=None, counties=None, tiger=None, parcels_with_addresses=None, temp_gdb=None):

    if not temp_gdb:
        make_temp_gdb(os.path.dirname(output_locator_path), "temp_parcels.gdb")

    # do this first because we've had multiple failures in the download process and better to fail before doing
    # the other setup work that takes time.
    if cities or counties:
        cities, counties = copy_remote_to_local(cities, counties, temp_gdb)

    # prepare and validate parcel inputs
    if include_parcels:
        if not parcels_with_addresses:
            # Prepare parcel data with address information
            parcels_with_addresses = prepare_parcel_data(
                parcels=os.path.join(input_smartfabric_gdb, "Parcels"),
                assessments=os.path.join(input_smartfabric_gdb, "Assessments"),
                temp_gdb=temp_gdb
            )
        else:
            if not arcpy.Exists(parcels_with_addresses):
                raise ValueError(f"Parcels with addresses path provided ({parcels_with_addresses}) does not exist as a valid ArcGIS-readable dataset.")

    # prepare the data for the table mapping input
    table_mapping = []
    if include_parcels:
        parcels_table = os.path.split(parcels_with_addresses)[1]  # in the field mapping, we just need the table name - we use the full path in the TABLE_MAPPING
        table_mapping.append((parcels_with_addresses, "Parcel"))
    else:
        parcels_table = None

    if cities:
        table_mapping.append((cities, "City"))
        cities_table = os.path.split(cities)[1]
    else:
        cities_table = None

    if counties:
        table_mapping.append((counties, "Subregion"))
        counties_table = os.path.split(counties)[1]
    else:
        counties_table = None

    if include_address_points:
        if not processed_address_points:
            initial_addresses_table = os.path.join(input_smartfabric_gdb, "Addresses")
            addresses = prepare_address_data(initial_addresses_table, temp_gdb)
        else:
            addresses = processed_address_points
        table_mapping.insert(0, (addresses, "PointAddress"))
        addresses_table = os.path.split(addresses)[1]
    else:
        addresses_table = None

    if tiger:
        table_mapping.append((tiger, "StreetAddress"))
        tiger_table = os.path.split(tiger)[1]
    else:
        tiger_table = None

    if len(table_mapping) == 0:
        raise ValueError("No input tables provided for locator - likely misconfiguration of input flags")

    table_mapping = ";".join([" ".join(item) for item in table_mapping])  # this is easier than constructing a valuetable input to the geoprocessing tool
    print(table_mapping)

    # See https://pro.arcgis.com/en/pro-app/latest/help/data/geocoding/locator-role-fields.htm for mapping here
    values_mapping = _get_locator_fields(addresses_table=addresses_table,
                                         cities_table=cities_table,
                                         counties_table=counties_table,
                                         parcels_table=parcels_table,
                                         tiger_table=tiger_table)

    print(values_mapping)
    # TODO: Need to specify the actual table names in the field map for the items not in the workspace -
    #  this just captures the creation of the test locator

    print("Building locator")
    with arcpy.EnvManager(workspace=input_smartfabric_gdb):
        arcpy.geocoding.CreateLocator(
            country_code="USA",
            primary_reference_data=table_mapping,
            field_mapping=values_mapping,  # we can use this one directly as a list
            out_locator=output_locator_path,
            language_code="ENG",
            alternatename_tables=None,
            alternate_field_mapping=None,
            custom_output_fields=None,
            precision_type="GLOBAL_EXTRA_HIGH",
            version_compatibility="CURRENT_VERSION"
        )


def _get_locator_fields(addresses_table=None, cities_table=None, counties_table=None, parcels_table=None, tiger_table=None) -> list[str]:
    values_mapping = []
    print("Running!")

    if addresses_table:
        values_mapping.extend([
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
        f'PointAddress.POSTAL {addresses_table}.ZIP5',
        f'PointAddress.POSTAL_EXT {addresses_table}.ZIPEXT',
        ])
        # f'PointAddress.COUNTRY_CODE {addresses_table}.country_code', # Apparently "US" is an invalid country code
        # 'PointAddress.BUILDING_NAME {addresses_table}.building_name_usps'  # 100% null values as of October 2025 release

    # Parcels layer
    if parcels_table:
        values_mapping.extend([
            f'Parcel.PARCEL_NAME {parcels_table}.SITE_ADDR',
            f'Parcel.PARCEL_JOIN_ID {parcels_table}.PARCEL_LID',
            f'Parcel.SUBREGION_JOIN_ID {parcels_table}.FIPS_CODE',

            # TODO: Check that this has the full county name
            f'Parcel.SUBREGION {parcels_table}.COUNTY',  # Matches County in UI
            f'Parcel.SUBREGION_JOIN_ID {parcels_table}.COUNTY_FIPS',  # Matches County Join ID in UI
            f'Parcel.HOUSE_NUMBER {parcels_table}.SITE_HOUSE_NUMBER',

            f'Parcel.STREET_PREFIX_DIR {parcels_table}.SITE_DIRECTION',
            f'Parcel.STREET_NAME {parcels_table}.SITE_STREET_NAME',  # Matches Street Name in UI - not Full Street Name
            f'Parcel.FULL_STREET_NAME {parcels_table}.SITE_ADDR',
            f'Parcel.STREET_SUFFIX_TYPE {parcels_table}.SITE_MODE',
            f'Parcel.STREET_SUFFIX_DIR {parcels_table}.SITE_QUADRANT',
            f'Parcel.SUB_ADDRESS_UNIT_TYPE {parcels_table}.SITE_UNIT_PREFIX',  # Matches Unit Type in UI
            f'Parcel.SUB_ADDRESS_UNIT {parcels_table}.SITE_UNIT_NUMBER',
            f'Parcel.CITY {parcels_table}.SITE_CITY',
            f'Parcel.REGION {parcels_table}.SITE_STATE',
            f'Parcel.POSTAL {parcels_table}.SITE_ZIP',
            f'Parcel.POSTAL_EXT {parcels_table}.SITE_PLUS_4',
        ])

    if cities_table:
        values_mapping.extend([
            # Cities layer - supports locations with simple partial addresses
            f'City.CITY_JOIN_ID {cities_table}.CENSUS_GEOID',
            f'City.CITY {cities_table}.CDTFA_CITY'
        ])

    if tiger_table:
        values_mapping.extend([
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
            f'StreetAddress.POSTAL_EXT_RIGHT {tiger_table}.PLUS4R'
        ])

    # Counties layer
    if counties_table:
        values_mapping.extend([
            f'Subregion.SUBREGION_JOIN_ID {counties_table}.CENSUS_GEOID',
            f'Subregion.SUBREGION {counties_table}.CDTFA_COUNTY'
            # TODO: CHECK IF THIS HAS THE FULL COUNTY NAME - IT IS RECOMMENDED THAT IT INCLUDES "County"
        ])

    return values_mapping

