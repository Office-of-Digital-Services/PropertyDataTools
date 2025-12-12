# locator_api.py
"""
Dev REST API wrapping a *local* ArcGIS locator (.loc) via `arcgis.geocoding.Locator`.

Endpoints:
  - GET /geocode?address=FULL_ADDRESS[&max_locations=5]
  - GET /reverse?lon=-122.4194&lat=37.7749[&distance=100]

Run (recommended):
  uvicorn locator_api:app --reload --host 0.0.0.0 --port 8000

  This code primarily developed by GenAI with Nick Santos - designed to be used *only*
  for QA of built locators in comparison to other geocoding APIs - not for use in production.
"""

from __future__ import annotations

from typing import Any, Dict, Optional

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
# LOCATOR_LOC_PATH = r"C:\data\locators\MyLocator.loc"
#
# Example (Linux):
# LOCATOR_LOC_PATH = "/opt/data/locators/MyLocator.loc"
LOCATOR_LOC_PATH = r"/ABSOLUTE/PATH/TO/YOUR/LOCATOR.loc"

DEFAULT_MAX_LOCATIONS = 5


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


def _build_locator() -> Locator:
    if not LOCATOR_LOC_PATH or LOCATOR_LOC_PATH.strip() in {"/ABSOLUTE/PATH/TO/YOUR/LOCATOR.loc"}:
        raise RuntimeError("LOCATOR_LOC_PATH is not configured. Set it to your local .loc path.")
    return Locator(LOCATOR_LOC_PATH)


LOCATOR = _build_locator()

app = FastAPI(title="Local Locator Dev API", version="0.1.0")


@app.get("/geocode")
def geocode(
    address: str = Query(..., description="Full address string to geocode"),
    max_locations: int = Query(DEFAULT_MAX_LOCATIONS, ge=1, le=50),
) -> Dict[str, Any]:
    """Calls Locator.geocode(address, ...)."""
    try:
        results = LOCATOR.geocode(address, max_locations=max_locations)
        return {
            "input": {"address": address, "max_locations": max_locations},
            "results": _as_jsonable(results),
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Geocode failed: {e!s}")


@app.get("/reverse")
def reverse_geocode(
    lon: float = Query(..., ge=-180.0, le=180.0, description="WGS84 longitude"),
    lat: float = Query(..., ge=-90.0, le=90.0, description="WGS84 latitude"),
    distance: Optional[float] = Query(None, gt=0, description="Optional search distance"),
) -> Dict[str, Any]:
    """Calls Locator.reverse_geocode(location={x,y,wkid=4326}, ...)."""
    try:
        location = {"x": lon, "y": lat, "spatialReference": {"wkid": 4326}}
        kwargs: Dict[str, Any] = {}
        if distance is not None:
            kwargs["distance"] = distance

        result = LOCATOR.reverse_geocode(location=location, **kwargs)
        return {
            "input": {"lon": lon, "lat": lat, "distance": distance},
            "result": _as_jsonable(result),
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Reverse geocode failed: {e!s}")


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(app, host="0.0.0.0", port=8000)
