import shutil
from datetime import datetime
import os

from unbox.compile_gdbs import GDBMerge

import logging
import sys

root = logging.getLogger()
root.setLevel(logging.DEBUG)

handler = logging.StreamHandler(sys.stdout)
handler.setLevel(logging.DEBUG)
formatter = logging.Formatter('%(asctime)s - %(name)s - %(levelname)s - %(message)s')
handler.setFormatter(formatter)
root.addHandler(handler)

DELIVERY_FOLDER = r"C:\Users\nick.santos\Downloads\LBX_Delivery_20251015\PROFESSIONAL_FGDB\testing"
GDB_NAME = "testing_smartfabric.gdb"

def test_merge(delivery_folder=DELIVERY_FOLDER, gdb_name=GDB_NAME):
    start_time = datetime.now()
    print(f"Started at {start_time}")

    output_gdb = os.path.join(delivery_folder, gdb_name)
    temp_folder = os.path.join(delivery_folder, "processing")

    if os.path.exists(temp_folder):
        print(f"Removing existing temp folder {temp_folder}")
        shutil.rmtree(temp_folder)

    if os.path.exists(output_gdb):
        print(f"Removing existing output gdb {output_gdb}")
        shutil.rmtree(output_gdb)

    m = GDBMerge(input_folder=delivery_folder,
                  output_gdb_path=output_gdb,
                  temp_folder=temp_folder,
                  setup_logging=True,
                  extract_zips=True)
    m.run_merge()

    end_time = datetime.now()
    print(f"Ended at {end_time}")


    assert True

def test_relationships_only(delivery_folder=DELIVERY_FOLDER, gdb_name=GDB_NAME):
    start_time = datetime.now()
    print(f"Started at {start_time}")

    output_gdb = os.path.join(delivery_folder, gdb_name)
    temp_folder = os.path.join(delivery_folder, "processing")

    m = GDBMerge(input_folder=delivery_folder,
                  output_gdb_path=output_gdb,
                  temp_folder=temp_folder,
                  setup_logging=True)
    m._bypass_merge()

    m.create_relationship_classes()