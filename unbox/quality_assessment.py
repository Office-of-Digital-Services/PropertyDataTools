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
CDT = {"name": "State", "field_name": "CDT", "id": 1}
ESRI = {"name": "StreetMapPremium", "field_name": "SMP", "id": 2}
BING = {"name": "Bing API", "field_name": "Bing", "id": 3}
AZURE = {"name": "Azure Maps", "field_name": "Azure", "id": 4}
GOOGLE = {"name": "Google Maps API", "field_name": "Google", "id": 5}
LIGHTBOX = {"name": "LightBox Geocoding API", "field_name": "LB", "id": 6}


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
    comparisons: dict[str, dict] = dict()
    name: str = None

    def __init__(self, name: str, text_path: Optional[Path, str] = None) -> None:
        self.name = name
        self.text_path = text_path

    def compare(self, base: GeocodedDataset, comparison: GeocodedDataset, id_field: str = "ID"):
        """
        Compare two geocoded datasets and report on quality metrics.
        """
        # do comparison work here
        # run near
        comparison_field = f"NEAR_DIST_{comparison.geocoder['field_name']}"
        arcpy.analysis.Near(base.points_path, comparison.points_path, method="GEODESIC", field_names=comparison_field)

        # report percentiles
        pct_results = percentiles(base.points_path, fields=(comparison_field,))
        print(f"Percentiles for {base} vs {comparison}:")
        print(pct_results)

        # make connecting lines
        merged_points = arcpy.management.Merge([base.points_path, comparison.points_path],
                                               f"in_memory/merged_{base.geocoder}_{comparison.geocoder}")
        connecting_lines = arcpy.management.PointsToLine(merged_points,
                                                         f"in_memory/lines_{base.geocoder}_{comparison.geocoder}",
                                                         id_field)

        # insert into map
        #aprx = arcpy.mp.ArcGISProject("CURRENT")
        #map_obj = aprx.activeMap
        #map_obj.addDataFromPath(connecting_lines)

        self.comparisons[base.geocoder["id"]] = {
            comparison.geocoder["id"]: {
                "points": base.points_path,
                "near_field": comparison_field,
                "comparison_points": comparison.points_path,
                "lines": connecting_lines,
                "percentiles": pct_results,
            }
        }
        # make it work to reference it from the perspective of either geocoder when we want to look it up later
        self.comparisons[comparison.geocoder["id"]][base.geocoder["id"]] = self.comparisons[base.geocoder["id"]][comparison.geocoder["id"]]