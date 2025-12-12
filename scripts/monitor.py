import os
import time
import sys

def list_get(l, idx, default=None):
    try:
        return l[idx]
    except IndexError:
        return default

count = int(list_get(sys.argv, 1, default=300))
sleep_duration = int(list_get(sys.argv, 2, default=60))
directory = list_get(sys.argv, 3, r"C:\Users\nick.santos\Downloads\LBX_Delivery_20251015\PROFESSIONAL_FGDB\full_state_smartfabric.gdb")
i = 0
while i < count:
    size_b = sum([os.path.getsize(os.path.join(directory,f)) for f in os.listdir(directory) if os.path.isfile(os.path.join(directory,f))])
    size_mb = round(size_b / 1024 / 1024)
    size_gb = size_mb / 1000  # 1000 here instead of 1024 to ensure only three decimal places. Could use number formatting, but used this.
    print(f"{i}: {size_gb} GB")

    i += 1
    time.sleep(sleep_duration)