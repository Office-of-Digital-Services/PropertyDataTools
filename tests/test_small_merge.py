import shutil
from datetime import datetime
import os

from unbox.compile_gdbs import GDBMerge

def test_small_merge(delivery_folder=r"C:\Users\nick.santos\Downloads\LBX_Delivery_20251015\TESTING_DATA"):
    start_time = datetime.now()
    print(f"Started at {start_time}")

    output_gdb = os.path.join(delivery_folder,"rc_output.gdb")
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
                  setup_logging=True)
    m.run_merge()

    end_time = datetime.now()
    print(f"Ended at {end_time}")


    assert True