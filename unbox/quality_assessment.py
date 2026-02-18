"""
    A pipeline for running comparisons - static data exported from Bing (stored in an encrypted vault),
    and specs for running locally built geocoders after they're built, then exporting all the quality metrics
    (percentile distances between each variant and ways to run the data through, against multiple alternatives)

    I think for this code for now, we should plan to standardize the inputs - they should be point datasets in EPSG 3310
    (or we throw an error) with a specific set of fields - a defined ID field to join things on, some kind of quality/match type field
    Then we can do the point merges, line creation, and distance calculations in a straightforward way. Since
    those other operations only really need to happen the first time, I think that'll work, then we just need
    to make sure that when we run a new geocode with this, that we export it to that same format
    by rewriting the fields we use to that format.
"""

# For LightBox addresses:
# Pull out a stratified sample - 5% of addresses from each county, stratified by parcel size?
#       Keep LB location, then make some perturbed datasets - drop house numbers, or "way"/"street" at the end, or modify them to have incorrect or nonexistent values, mess up the zip code, etc etc
#
# check distance to matched parcel for each input
from typing import Optional
from pathlib import Path

import arcpy
import numpy as np

# Let's make some constants to reference for types of geocoders we'll compare with.
CDT = {"name": "State", "field_name": "CDT"}
ESRI = {"name": "StreetMapPremium", "field_name": "SMP"}
BING = {"name": "Bing API", "field_name": "Bing"}
AZURE = {"name": "Azure Maps", "field_name": "Azure"}
GOOGLE = {"name": "Google Maps API", "field_name": "Google"}
LIGHTBOX = {"name": "LightBox Geocoding API", "field_name": "LB"}


def percentiles(table, pcts=(10, 25, 50, 75, 90, 95, 98, 99), fields=("NEAR_DIST_BING", "NEAR_DIST_SMP")):
    all_values = {fid: {'name': field, 'values': [], 'pcts': {}} for fid, field in list(enumerate(fields))}
    ids = all_values.keys()
    with arcpy.da.SearchCursor(table, fields) as data:
        for row in data:
            for id in ids:
                if row[id] is not None:
                    all_values[id]['values'].append(row[id])
        for id in ids:
            np_data = np.array(all_values[id]['values'])
            for p in pcts:
                all_values[id]['pcts'][p] = np.percentile(np_data, p)
    returns = {value['name']: value['pcts'] for id, value in all_values.items()}
    return returns


class GeocodedDataset:
    geocoder: str = None
    text_path: Optional[Path, str] = None
    points_path: Optional[Path, str] = None

    dataset: "AddressDataset" = None

    def __init__(self, dataset: "AddressDataset", geocoder: str, text_path: Optional[Path, str], points_path) -> None:
        self.dataset = dataset
        self.geocoder = geocoder

    def __str__(self) -> str:
        return f"{self.dataset.name} geocoded by {self.geocoder}"


class AddressDataset:
    text_path: Optional[Path, str]  = None
    geocodes: dict[str, GeocodedDataset] = dict()

    name: str = None

    def __init__(self, name: str, text_path: Optional[Path, str] = None) -> None:
        self.name = name
        self.text_path = text_path

    def compare(self, base: GeocodedDataset, comparison: GeocodedDataset):
        # do comparison work here
        # run near
        # report percentiles
        # make connecting lines
        # insert into map
        print("SEE NOTES AT THE TOP")