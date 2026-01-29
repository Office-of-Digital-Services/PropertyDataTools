import os
import csv
import requests
import datetime

from local_secrets import BING_API_KEY as API_KEY

MAX_RESULTS = 1

ENDPOINT="http://dev.virtualearth.net/REST/v1/Locations/US/"
# {adminDistrict}/{postalCode}/{locality}/{addressLine}?includeNeighborhood={includeNeighborhood}&include={includeValue}&maxResults={maxResults}&key={BingMapsKey}
INPUTS = os.path.join(os.path.dirname(os.path.abspath(__file__)), "inputs", "adgeo1.csv")
OUTPUTS = os.path.join(os.path.dirname(os.path.abspath(__file__)), "outputs", "bing_geocodes.csv")

# results under resourceSets[0]->resources[0]->geocodePoints[0]
# pull subkey ->coordinates->[lat,lon]
# and subkey calculationMethod

# confidence at resourceSets[0]->resources[0]->confidence
# entityType at resourceSets[0]->resources[0]->entityType

if __name__ == "__main__":
    input_data_rows = []
    with open(INPUTS, 'r') as csvfile:
        reader = csv.DictReader(csvfile)
        for row in reader:
            input_data_rows.append(row)

    s = requests.Session()
    with open(OUTPUTS, 'w', newline="\n") as output_file:
        writer = csv.DictWriter(output_file, fieldnames=["ID", "STREET", "CITY", "STATE", "ZIP", "X", "Y", "calculationMethod", "confidence", "entityType"], lineterminator='\n')
        writer.writeheader()
        i = 0
        last_time = datetime.datetime.now(tz=datetime.timezone.utc)
        first_time = last_time
        for row in input_data_rows:
            url = f"{ENDPOINT}{row['STATE']}/{row['ZIP']}/{row['CITY']}/{row['STREET']}?include=false&maxResults={MAX_RESULTS}&key={API_KEY}"
            response = s.get(url)
            response_data = response.json()

            try:
                r = response_data["resourceSets"][0]["resources"][0]
                point = r["geocodePoints"][0]
                coords = point["coordinates"]
            except IndexError:
                continue
            except KeyError:
                continue

            try:
                calculation_method = point["calculationMethod"]
            except KeyError:
                calculation_method = "unknown"

            try:
                confidence = r["confidence"]
            except KeyError:
                confidence = "unknown"

            try:
                entity_type = r["entityType"]
            except KeyError:
                entity_type = "unknown"

            result = {
                "X": coords[1],
                "Y": coords[0],
                "calculationMethod": calculation_method,
                "confidence": confidence,
                "entityType": entity_type,
                "ID": row["ID"],
                "STREET": row["STREET"],
                "CITY": row["CITY"],
                "STATE": row["STATE"],
                "ZIP": row["ZIP"],
            }
            writer.writerow(result)
            #print(result)
            i += 1
            if i % 100 == 0:
                new_time = datetime.datetime.now(tz=datetime.timezone.utc)
                this_delta = new_time - first_time
                full_delta = new_time - last_time
                print(f"{i}: {this_delta.total_seconds()} seconds for this 100, {full_delta.total_seconds()} seconds total")
                last_time = new_time



