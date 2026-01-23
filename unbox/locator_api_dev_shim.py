# locator_api_dev_shim.py
"""
Dev REST API wrapping a *local* ArcGIS locator (.loc) via `arcgis.geocoding.Locator`.

Run this in an arcgis-based Python environment - it's likely safe to run this from a notebook in
ArcGIS Pro (Nick to test)

Endpoints:
  - GET /geocode?address=FULL_ADDRESS&[max_locations=5]
  - GET /reverse?lon=-122.4194&lat=37.7749

Example:
    curl http://localhost:8000/geocode?address=10860+Gold+Center+Drive+Rancho+Cordova

    Response:
            {
          "input": {
            "address": "10860 Gold Center Drive Rancho Cordova"
          },
          "results": [
            {
              "Match_addr": "10860 Gold Center Dr, Rancho Cordova, 95670",
              "Status": "M",
              "Score": 100,
              "LongLabel": "10860 Gold Center Dr, Rancho Cordova, 95670",
              "ShortLabel": "10860 Gold Center Dr",
              "Addr_type": "StreetAddress",
              "Type": "",
              "PlaceName": "",
              "Place_addr": "10860 Gold Center Dr, Rancho Cordova, 95670",
              "Phone": "",
              "URL": "",
              "Rank": 20,
              "AddBldg": "",
              "AddNum": "10860",
              "AddNumFrom": "10700",
              "AddNumTo": "10998",
              "AddRange": "10700-10998",
              "Side": "R",
              "StPreDir": "",
              "StPreType": "",
              "StName": "Gold Center Dr",
              "StType": "",
              "StDir": "",
              "BldgType": "",
              "BldgName": "",
              "LevelType": "",
              "LevelName": "",
              "UnitType": "",
              "UnitName": "",
              "SubAddr": "",
              "StAddr": "10860 Gold Center Dr",
              "Block": "",
              "Sector": "",
              "Nbrhd": "",
              "District": "",
              "City": "Rancho Cordova",
              "MetroArea": "",
              "Subregion": "Sacramento County",
              "Region": "",
              "RegionAbbr": "",
              "Territory": "",
              "Zone": "",
              "Postal": "95670",
              "PostalExt": "",
              "Country": "",
              "CntryName": "USA",
              "LangCode": "ENG",
              "Distance": 0,
              "X": -121.2797034602203,
              "Y": 38.591489370123995,
              "DisplayX": -121.2797034602203,
              "DisplayY": 38.591489370123995,
              "Xmin": -121.27971346022031,
              "Xmax": -121.2796934602203,
              "Ymin": 38.59147937012399,
              "Ymax": 38.591499370124,
              "ExInfo": "",
              "CountryCode": "",
              "AttributeNames": null
            }
          ]
        }

Install:
    Activate your Python environment, then `python -m pip install fastapi uvicorn`
    You may also install these from the package manager in ArcGIS Pro, but must duplicate
    the Python environment first.

Run:
  uvicorn unbox:locator_api_dev_shim --reload --host 0.0.0.0 --port 8000

  Core of this code primarily developed by GenAI with Nick Santos - designed to be used *only*
  for QA of built locators in comparison to other geocoding APIs - not for use in production.
  Edited and tested by Nick to add more documentation, fix errors, etc.
"""

from __future__ import annotations
import os

from typing import Any, Dict, Optional

import arcpy
from fastapi import FastAPI, HTTPException, Query

try:
    from arcpy.geocoding import Locator
except Exception as e:  # pragma: no cover
    raise RuntimeError(
        "Could not import arcgis.geocoding.Locator. "
        "Run this service inside an environment that has arcpy available."
    ) from e


# -----------------------------------------------------------------------------
# Configuration constant (local .loc)
# -----------------------------------------------------------------------------
# Set this to the absolute path (or a stable path relative to where you run the service).
# Example (Windows):
# LOCATOR_PATH = r"C:\data\locators\MyLocator.loc"
#
# Example (Linux):
# LOCATOR_PATH = "/opt/data/locators/MyLocator.loc"
# LOCATOR_PATH = r"/ABSOLUTE/PATH/TO/YOUR/LOCATOR.loc"
LOCATOR_PATH = r"C:\Users\nick.santos\Downloads\LBX_Delivery_20251015\PROFESSIONAL_FGDB\processing\test_alpine_locator.loc"

DEFAULT_MAX_LOCATIONS = 5

LOCATOR = None
app = FastAPI(title="Local Locator Dev API", version="0.1.0")

def _as_jsonable(obj: Any) -> Any:
    """Best-effort conversion to JSON-serializable data."""
    if obj is None:
        return None
    if isinstance(obj, (str, int, float, bool, list, dict)):
        return obj
    if hasattr(obj, "as_dict"):
        return obj.as_dict
    if hasattr(obj, "__dict__"):
        return obj.__dict__
    return str(obj)


def set_locator(locator_path=LOCATOR_PATH, set_global=True) -> Locator:
    if not locator_path or locator_path.strip() in {"/ABSOLUTE/PATH/TO/YOUR/LOCATOR.loc"} or not os.path.exists(locator_path):
        raise RuntimeError("locator_path is not configured or does not exist. Set it to your local .loc path.")
    loc = Locator(locator_path)
    if set_global:
        global LOCATOR
        LOCATOR = loc
    return loc


def _preprocess_results(results):
    for i, _ in enumerate(results):
        if "Shape" in results[i]:
            del results[i]["Shape"]

    return results
@app.get("/geocode")
def geocode(
    address: str = Query(..., description="Full address string to geocode"),
    max_locations: int = Query(DEFAULT_MAX_LOCATIONS, ge=1, le=50),
) -> Dict[str, Any]:
    """Calls Locator.geocode(address, ...)."""
    try:
        results = LOCATOR.geocode(address, True, maxResults=max_locations)
        results = _preprocess_results(results)
        return {
            "input": {"address": address},
            "results": _as_jsonable(results),
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Geocode failed: {e!s}")


""" This was a first attempt - I think it'd need to do better tracking returning all inputs
for each output too.
"""

"""
    @app.post("/batch")
    def batch_geocode(
        addresses: List[str],
        max_locations: int = Query(DEFAULT_MAX_LOCATIONS, ge=1, le=50),
    ) -> List[Dict[str, Any]]:
        
        try:
            # batch method typically returns a list of result lists
            batch_results = LOCATOR.batch(addresses)

            response = []
            for address, results in zip(addresses, batch_results):
                processed_results = _preprocess_results(results)
                response.append({
                    "input": {"address": address},
                    "results": _as_jsonable(processed_results),
                })
            return response
        except Exception as e:
            raise HTTPException(status_code=500, detail=f"Batch geocode failed: {e!s}")
"""


@app.get("/reverse")
def reverse_geocode(
    lon: float = Query(..., ge=-180.0, le=180.0, description="WGS84 longitude"),
    lat: float = Query(..., ge=-90.0, le=90.0, description="WGS84 latitude"),
    distance: Optional[float] = Query(None, gt=0, description="Optional search distance"),
) -> Dict[str, Any]:
    """Calls Locator.reverseGeocode(location={x,y,wkid=4326}, ...)."""
    try:
        location = arcpy.PointGeometry(arcpy.Point(X=lon, Y=lat), arcpy.SpatialReference(4326))
        kwargs: Dict[str, Any] = {}

        result = LOCATOR.reverseGeocode(location=location, forStorage=True,**kwargs)
        result = _preprocess_results([result])[0]
        return {
            "input": {"lon": lon, "lat": lat},
            "result": _as_jsonable(result),
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Reverse geocode failed: {e!s}")


if __name__ == "__main__":
    import uvicorn

    LOCATOR = set_locator()

    uvicorn.run(app, host="0.0.0.0", port=8000)
