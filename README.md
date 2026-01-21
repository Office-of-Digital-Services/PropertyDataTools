This code base compiles all LightBox county-level geodatabases into a single Esri file geodatabase with multiple
enhancements designed to make the data ready for immediate use by subscribing departments.
1. All county-level tables are merged, keeping the schema intact
2. ArcGIS relationship classes have been created to support rapid utilization in ArcGIS Pro and Online
3. Indexes are added to support both common queries and relationship traversal
4. We will produce a locator file based on the full geodatabase to allow for forward and reverse geocoding



# Rough set of steps (notes)

## Lightbox ETL
1. Sort zips by size, track sizes by IDs.
2. Mark the largest ID (`county_init`) to use as the starting point 
1. Extract zips of LightBox FGDB
2. Copy the `county_init` GDB to a new location
3. Read the set of tables in the GDB - these are the names we will append from the others
4. Append all other tables for remaining counties into it
5. Index all join fields and other fields important for filtering
6. Build appropriate relationship classes related to LightBox schema
7. Bundle a zip file of this as deliverable 1

## Get Latest TIGER address range data
1. Download, or pull from a cache we manually update
2. Extract zip

## Intersections Data
1. Either use TIGER, or CaRS

## Places of Interest Data
1. Overture Places will likely be the most complete. Will need to download the parquet
2. Download either the latest overture places release for California *or* places data from geofabrik extract?

## Other ancillary datasets
ArcGIS Locators will backfill some items for items with similar values in ID fields. If we set city names
to an ID field, we can potentially use the city/county boundaries to fill other information? Maybe.
1. Get latest county boundaries
2. Get latest city boundaries
3. Get latest Census place boundaries? Some mail may go there
4. Get latest zip code boundaries???

## Build the geocoder
1. Address points for most address components
2. Parcel boundaries for other address and areal components
3. TIGER for address ranges
4. Whatever we're using for intersections
5. Locality boundaries
6. Places data

## Package and distribute geocoder
* Do we need to index items in the geocoder *after* building? Or just before?
* Distribute as compressed locator format? Or as standard .loc compressed as a zip


# MVP for January
* Documentation of Statewide SmartFabric database (CoP Site)
* Reliable geocoder build and testing
* Documentation of the geocoder (external)
* Documentation of the data/geocoder (internal)

## Nice to haves
* Finish up the developer shim so Mark can try it out
* Adding in parcels for reverse geocoding (if they behave well in geocoder, otherwise delay)

# Using the Dev Shim
This repository comes with a shim that allows you to use ArcGIS locators produced by this project via a REST
API. Running this shim will allow you to send queries to the locator programmatically from any language
that supports HTTP requests.

## First time setup
1. Clone this repository
2. Clone your default arcpy environment within ArcGIS Pro
3. Activate the new environment within ArcGIS Pro
4. Install the packages `fastapi` and `uvicorn` into the environment from the package manager tab
4. Separately, copy the locator files to a path on your computer

## Running the shim
4. Open a project in ArcGIS Pro
5. On the Analysis tab, use the Python dropdown to open the Python Window
6. In the window that pops up, run the following
```python shell
# The following lines should be changed to reflect the path to your cloned repository
# and to your locator file
# The `r` prefix tells Python not to interpret backslashes as escape characters, so the
# path is treated literally
REPOSITORY_PATH = r"C:\Full\Path\To\Cloned\Repository"
LOCATOR_FILE_PATH = r"C:\Full\Path\To\Locator\File.loc"

import sys
sys.path.insert(0, REPOSITORY_PATH)  # add it to the importable directories

from unbox import locator_api_dev_shim
import uvicorn

from arcpy.geocoding import Locator
locator_api_dev_shim.LOCATOR = Locator(LOCATOR_FILE_PATH)

uvicorn.run("locator_api_dev_shim:app", port=8000, host="0.0.0.0")
```
Nothing will print out, but if it shows dots moving left to right, it's listening and you may try sending requests to
the local server on port 8000. If you need to quit the server, click into the command section and press `Ctrl+C`.

## Sending requests to the locator via the shim
Using your preferred browser or API client,

Endpoints:
  - `GET /geocode?address=FULL_ADDRESS&[max_locations=5]`
  - `GET /reverse?lon=-122.4194&lat=37.7749`

Example:
    `curl http://localhost:8000/geocode?address=10860+Gold+Center+Drive+Rancho+Cordova`

Example Response:
```json
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
```