import arcpy

def make_locator(input_smartfabric_gdb, output_locator_path, cities=None, counties=None, TIGER=None):
    if cities is None or counties is None or TIGER is None:  # this is temporary and will be removed later
        raise ValueError("cities, counties, and TIGER must be specified")


    # TODO: Need to specify the actual table names in the field map for the items not in the workspace - this just captures the creation of the test locator
    with arcpy.EnvManager(workspace=input_smartfabric_gdb):
        arcpy.geocoding.CreateLocator(
            country_code="USA",
            primary_reference_data=f"Addresses PointAddress;Parcels Parcel;{cities} MetroArea;{TIGER} StreetAddress;{counties} Subregion",
            field_mapping=['PointAddress.HOUSE_NUMBER Addresses.house_number','PointAddress.STREET_NAME Addresses.street_name','PointAddress.SUB_ADDRESS_UNIT Addresses.unit','PointAddress.CITY Addresses.city','PointAddress.SUBREGION_JOIN_ID Addresses.fips_code','PointAddress.SUBREGION Addresses.county','PointAddress.REGION Addresses.state','PointAddress.POSTAL Addresses.zip','Parcel.PARCEL_NAME Parcels.PARCEL_LID','Parcel.HOUSE_NUMBER Parcels.SHAPE','Parcel.SUBREGION_JOIN_ID Parcels.FIPS_CODE','MetroArea.METRO_AREA_JOIN_ID 2.CENSUS_GEOID','MetroArea.METRO_AREA 2.CDTFA_CITY','StreetAddress.HOUSE_NUMBER_FROM_LEFT tiger_address_range_2025.LFROMHN','StreetAddress.HOUSE_NUMBER_TO_LEFT tiger_address_range_2025.LTOHN','StreetAddress.HOUSE_NUMBER_FROM_RIGHT tiger_address_range_2025.RFROMHN','StreetAddress.HOUSE_NUMBER_TO_RIGHT tiger_address_range_2025.RTOHN','StreetAddress.PARITY_LEFT tiger_address_range_2025.PARITYL','StreetAddress.PARITY_RIGHT tiger_address_range_2025.PARITYR','StreetAddress.STREET_NAME tiger_address_range_2025.FULLNAME','StreetAddress.POSTAL_LEFT tiger_address_range_2025.ZIPL','StreetAddress.POSTAL_RIGHT tiger_address_range_2025.ZIPR','Subregion.SUBREGION_JOIN_ID 1.CENSUS_GEOID','Subregion.SUBREGION 1.CDTFA_COUNTY'],
            out_locator=output_locator_path,
            language_code="ENG",
            alternatename_tables=None,
            alternate_field_mapping=None,
            custom_output_fields=None,
            precision_type="GLOBAL_EXTRA_HIGH",
            version_compatibility="CURRENT_VERSION"
        )