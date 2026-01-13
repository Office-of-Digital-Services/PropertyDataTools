import pytest
import os

from unbox import build_locator

PARCELS=r"C:\Users\nick.santos\Downloads\LBX_Delivery_20251015\PROFESSIONAL_FGDB\processing\SF_Professional_06067.gdb\parcels"
ASSESSMENTS=r"C:\Users\nick.santos\Downloads\LBX_Delivery_20251015\PROFESSIONAL_FGDB\processing\SF_Professional_06067.gdb\assessments"
OUTPUT_FOLDER=r"C:\Users\nick.santos\Downloads\LBX_Delivery_20251015\PROFESSIONAL_FGDB\processing"
INPUT_SMARTFABRIC_STATEWIDE = r"C:\Users\nick.santos\Downloads\LBX_Delivery_20251015\PROFESSIONAL_FGDB\_full_state_smartfabric.gdb"

def test_parcel_prep(parcels=PARCELS, assessments=ASSESSMENTS, output_folder=OUTPUT_FOLDER):
    build_locator.prepare_locator_data(parcels, assessments, output_folder)

def test_locator_build(smartfabric=INPUT_SMARTFABRIC_STATEWIDE, locator=os.path.join(OUTPUT_FOLDER, "test_statewide_locator"),
                       cities="https://services3.arcgis.com/uknczv4rpevve42E/arcgis/rest/services/California_Cities_and_Identifiers_Blue_Version_view/FeatureServer/2",
                       counties="https://services3.arcgis.com/uknczv4rpevve42E/arcgis/rest/services/California_County_Boundaries_and_Identifiers_Blue_Version_view/FeatureServer/1",
                       tiger=r"C:\Users\nick.santos\Downloads\LBX_Delivery_20251015\PROFESSIONAL_FGDB\_full_state_smartfabric.gdb\tiger_address_range_2025"):
    build_locator.make_locator(smartfabric, locator,
                               cities=cities,
                               counties=counties,
                               TIGER=tiger)