import os, shutil
from datetime import datetime
from unbox.compile_gdbs import GDBMerge
import logging
import sys

REMOVE_EXISTING = False

delivery_folder = r"C:\Users\nick.santos\Downloads\LBX_Delivery_20251015\PROFESSIONAL_FGDB"
gdb_name = "full_state_smartfabric.gdb"

root = logging.getLogger()
root.setLevel(logging.DEBUG)

handler = logging.StreamHandler(sys.stdout)
handler.setLevel(logging.DEBUG)
formatter = logging.Formatter('%(asctime)s - %(name)s - %(levelname)s - %(message)s')
handler.setFormatter(formatter)
root.addHandler(handler)


start_time = datetime.now()
print(f"Started at {start_time}")

output_gdb = os.path.join(delivery_folder, gdb_name)
temp_folder = os.path.join(delivery_folder, "processing")

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

end_time = datetime.now()
print(f"Ended at {end_time}")