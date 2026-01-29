import os.path

import googlemaps
from werkzeug.utils import secure_filename

from local_secrets import GOOGLE_API_KEY as API_KEY

LIMIT = 10


DUMP_FOLDER = os.path.join(os.path.dirname(os.path.abspath(__file__)), "outputs")
GMAPS = googlemaps.Client(key=API_KEY)

# Geocoding an address

def run_geocode(limit=LIMIT):

    results = []
    for i in range(limit):
        print(f"Geocoding address {i}")
        results.append(google_geocode())

def google_geocode(address, google_api=GMAPS, dump_folder=DUMP_FOLDER):
    geocode_result = google_api.geocode(address)

    # we're going to dump it to a file to cache the result in case something happens
    result = {"input": address, "result": geocode_result}
    with open(os.path.join(dump_folder, f"{secure_filename(address)}.json"), "w") as f:
        f.write(str(result))

