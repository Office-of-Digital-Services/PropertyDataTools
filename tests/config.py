ALAMEDA_SMARTFABRIC = r"%USERPROFILE%\Downloads\LBX_Delivery_20251015\PROFESSIONAL_FGDB\processing\SF_Professional_06001.gdb"
ALPINE_SMARTFABRIC = r"%USERPROFILE%\Downloads\LBX_Delivery_20251015\PROFESSIONAL_FGDB\processing\SF_Professional_06003.gdb"
AMADOR_SMARTFABRIC = r"%USERPROFILE%\Downloads\LBX_Delivery_20251015\PROFESSIONAL_FGDB\processing\SF_Professional_06005.gdb"

# ----------------------------------------------------------------------------------
# 2026-10-20 delivery - used by the data quality tests in test_validation_data_quality
#
# These point at a completed statewide merge and the per-county source geodatabases it
# was built from. The constants above are left untouched so the existing locator and
# small merge tests keep working against the older delivery.
# ----------------------------------------------------------------------------------

DELIVERY_2026_10_ROOT = r"%USERPROFILE%\Downloads\LightBox Deliveries 2026-10\LBX_DELIVERY_20261020\PROFESSIONAL_FGDB"

# The merged statewide output to validate.
DELIVERY_2026_10_OUTPUT_GDB = DELIVERY_2026_10_ROOT + r"\full_state_smartfabric_20261020.gdb"

# The 57 extracted per-county source geodatabases. Los Angeles (06037) is absent
# because it was promoted to become the merge base - see counts.base_county_reconciliation.
DELIVERY_2026_10_SOURCE_FOLDER = DELIVERY_2026_10_ROOT + r"\processing_20261020"

# The county that became the merge base and therefore has no source geodatabase.
DELIVERY_2026_10_BASE_FIPS = "06037"

# Counties used for the fast subset run. A full statewide data quality sweep takes
# roughly 10-20 minutes; these three keep the default test run to a few minutes.
DELIVERY_2026_10_SAMPLE_COUNTIES = ("06001", "06003", "06005")