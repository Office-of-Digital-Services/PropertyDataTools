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