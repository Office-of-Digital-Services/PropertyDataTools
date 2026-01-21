import os, shutil
import datetime
from datetime import datetime

from unbox.compile_gdbs import GDBMerge
from unbox import build_locator
import logging
import sys

REMOVE_EXISTING = False

#PARCELS_WITH_ADDRESSES_OVERRIDE = r"C:\Users\nick.santos\Downloads\LBX_Delivery_20251015\PROFESSIONAL_FGDB\processing_20260117\parcels_with_addresses_tek_xes6\temp_parcels.gdb\parcels_with_addresses"
suffix = "20260120"
delivery_folder = r"C:\Users\nick.santos\Downloads\LBX_DELIVERY_20260120\PROFESSIONAL_FGDB"
gdb_name = f"full_state_smartfabric_{suffix}.gdb"
cities = "https://services3.arcgis.com/uknczv4rpevve42E/arcgis/rest/services/California_Cities_and_Identifiers_Blue_Version_view/FeatureServer/2"
counties = "https://services3.arcgis.com/uknczv4rpevve42E/arcgis/rest/services/California_County_Boundaries_and_Identifiers_Blue_Version_view/FeatureServer/1"
tiger=r"C:\Users\nick.santos\Downloads\LBX_Delivery_20251015\PROFESSIONAL_FGDB\_full_state_smartfabric.gdb\tiger_address_range_2025"

root = logging.getLogger()
root.setLevel(logging.DEBUG)

handler = logging.StreamHandler(sys.stdout)
handler.setLevel(logging.DEBUG)
formatter = logging.Formatter('%(asctime)s - %(name)s - %(levelname)s - %(message)s')
handler.setFormatter(formatter)
root.addHandler(handler)


start_time = datetime.datetime.now()
print(f"Started at {start_time}")

output_gdb = os.path.join(delivery_folder, gdb_name)
temp_folder = os.path.join(delivery_folder, f"processing_{suffix}")

if REMOVE_EXISTING and os.path.exists(temp_folder):
    print(f"Removing existing temp folder {temp_folder}")
    shutil.rmtree(temp_folder)

if REMOVE_EXISTING and os.path.exists(output_gdb):
    print(f"Removing existing output gdb {output_gdb}")
    shutil.rmtree(output_gdb)

m = GDBMerge(input_folder=delivery_folder,
             output_gdb_path=output_gdb,
             temp_folder=temp_folder,
             setup_logging=False)

m.run_merge()

build_time = datetime.datetime.now()
print(f"Build completed at {build_time}")
print(f"Beginning geocoder build")

build_locator.make_locator(output_gdb, os.path.join(temp_folder, f"locator_{suffix}.loc"),
                           cities=cities, counties=counties, tiger=tiger,
                           )
                           #parcels_with_addresses=PARCELS_WITH_ADDRESSES_OVERRIDE,
                           #temp_gdb=r"C:\Users\nick.santos\Downloads\LBX_Delivery_20251015\PROFESSIONAL_FGDB\processing_20260117\parcels_with_addresses_tek_xes6\temp_parcels.gdb")

end_time = datetime.datetime.now()
print(f"Finished at {end_time}")