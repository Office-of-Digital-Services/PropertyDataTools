import pytest
import os

from unbox import build_locator

#cities = "https://services3.arcgis.com/uknczv4rpevve42E/arcgis/rest/services/California_Cities_and_Identifiers_Blue_Version_view/FeatureServer/2",
#counties = "https://services3.arcgis.com/uknczv4rpevve42E/arcgis/rest/services/California_County_Boundaries_and_Identifiers_Blue_Version_view/FeatureServer/1",
PARCELS=r"C:\Users\nick.santos\Downloads\LBX_Delivery_20251015\PROFESSIONAL_FGDB\processing\SF_Professional_06067.gdb\parcels"
ASSESSMENTS=r"C:\Users\nick.santos\Downloads\LBX_Delivery_20251015\PROFESSIONAL_FGDB\processing\SF_Professional_06067.gdb\assessments"
OUTPUT_FOLDER=r"C:\Users\nick.santos\Downloads\LBX_Delivery_20251015\PROFESSIONAL_FGDB\processing"
INPUT_SMARTFABRIC_STATEWIDE = r"C:\Users\nick.santos\Downloads\LBX_Delivery_20251015\PROFESSIONAL_FGDB\_full_state_smartfabric.gdb"

def test_parcel_prep(parcels=PARCELS, assessments=ASSESSMENTS, output_folder=OUTPUT_FOLDER):
    build_locator.prepare_locator_data(parcels, assessments, output_folder)

def test_full_locator_build(smartfabric=INPUT_SMARTFABRIC_STATEWIDE, locator=os.path.join(OUTPUT_FOLDER, "test_statewide_locator.loc"),
                            cities=r"C:\Users\nick.santos\Downloads\LBX_Delivery_20251015\PROFESSIONAL_FGDB\processing\parcels_with_addresses_qzcslfa8\temp_parcels.gdb\cities",
                            counties=r"C:\Users\nick.santos\Downloads\LBX_Delivery_20251015\PROFESSIONAL_FGDB\processing\parcels_with_addresses_qzcslfa8\temp_parcels.gdb\counties",
                       tiger=r"C:\Users\nick.santos\Downloads\LBX_Delivery_20251015\PROFESSIONAL_FGDB\_full_state_smartfabric.gdb\tiger_address_range_2025"):
    build_locator.make_locator(smartfabric, locator,
                               cities=cities,
                               counties=counties,
                               tiger=tiger,
                               parcels_with_addresses=os.path.join(OUTPUT_FOLDER, "temp_parcels.gdb", "parcels_with_addresses")
                               )

ALPINE_SMARTFABRIC = r"C:\Users\nick.santos\Downloads\LBX_Delivery_20251015\PROFESSIONAL_FGDB\processing\SF_Professional_06001.gdb"

def test_alpine_locator_build(smartfabric=ALPINE_SMARTFABRIC, locator=os.path.join(OUTPUT_FOLDER, "test_alpine_locator.loc"),
                            cities=r"C:\Users\nick.santos\Downloads\LBX_Delivery_20251015\PROFESSIONAL_FGDB\processing\parcels_with_addresses_qzcslfa8\temp_parcels.gdb\cities",
                            counties=r"C:\Users\nick.santos\Downloads\LBX_Delivery_20251015\PROFESSIONAL_FGDB\processing\parcels_with_addresses_qzcslfa8\temp_parcels.gdb\counties",
                            tiger=r"C:\Users\nick.santos\Downloads\LBX_Delivery_20251015\PROFESSIONAL_FGDB\_full_state_smartfabric.gdb\tiger_address_range_2025"):
    build_locator.make_locator(smartfabric, locator,
                               cities=cities,
                               counties=counties,
                               tiger=tiger,
                               parcels_with_addresses=r"C:\Users\nick.santos\Downloads\LBX_Delivery_20251015\PROFESSIONAL_FGDB\processing\parcels_with_addresses_qzcslfa8\temp_parcels.gdb\parcels_with_addresses"
                               )