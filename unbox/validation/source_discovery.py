"""
    Discovers the per-county source geodatabases and maps each to its FIPS code.

    The source folder holds one extracted geodatabase per county, named with the FIPS
    code as the last five digits, e.g.

        LBX_PARCEL_PROFESSIONAL_FGDB_06001.gdb   ->  06001  (Alameda)

    Los Angeles (06037) is deliberately absent: move_largest_to_output() promoted it to
    become the merged output, so it has no source geodatabase to compare against. Its
    contribution is recovered arithmetically instead - see checks/counts.py.

    This module only inspects folder names and does not open any geodatabase, so it
    needs no arcpy.
"""

from __future__ import annotations

import dataclasses
import os
import re
from typing import List, Optional, Sequence, Tuple

# Matches the trailing five-digit FIPS code on a .gdb folder name.
#
# The (?<!\d) guard requires the five digits to be a complete number rather than the
# tail of a longer one. Without it, the output geodatabase itself -
# full_state_smartfabric_20261020.gdb - would be read as county "61020" if it ever sat
# in the source folder, and a fictitious county would quietly enter the comparison.
FIPS_CODE_PATTERN = re.compile(r"(?<!\d)(\d{5})\.gdb$", re.IGNORECASE)

GEODATABASE_EXTENSION = ".gdb"


@dataclasses.dataclass(frozen=True)
class SourceGeodatabase:
    """
        One county's source geodatabase.
    """

    fips_code: str
    gdb_path: str

    @property
    def folder_name(self) -> str:
        return os.path.basename(self.gdb_path)


@dataclasses.dataclass(frozen=True)
class SourceDiscovery:
    """
        The outcome of scanning a source folder.

        `skipped_folder_names` records directories that looked like they might have been
        county geodatabases but were not usable - a partially extracted delivery, or a
        working folder someone left behind. These are surfaced in the report rather than
        silently ignored, because a county quietly missing from the comparison is
        exactly the kind of gap this module exists to catch.
    """

    source_geodatabases: Tuple[SourceGeodatabase, ...]
    skipped_folder_names: Tuple[str, ...]

    @property
    def fips_codes(self) -> Tuple[str, ...]:
        return tuple(source.fips_code for source in self.source_geodatabases)


def extract_fips_code(folder_name: str) -> Optional[str]:
    """
        Pulls the five-digit FIPS code off a geodatabase folder name, or None.
    """
    match = FIPS_CODE_PATTERN.search(folder_name)
    return match.group(1) if match else None


def discover_source_geodatabases(source_folder: str,
                                 counties: Sequence[str] = ()) -> SourceDiscovery:
    """
        Scans a folder for per-county source geodatabases.

        :param source_folder: the folder holding the extracted county geodatabases.
        :param counties: optional FIPS codes to restrict the result to.
        :returns: the discovered geodatabases, sorted by FIPS code, plus any skips.
        :raises FileNotFoundError: if the source folder itself does not exist.
    """
    if not os.path.isdir(source_folder):
        raise FileNotFoundError(
            f"The source geodatabase folder does not exist: {source_folder}"
        )

    wanted_counties = set(counties)
    discovered: List[SourceGeodatabase] = []
    skipped: List[str] = []

    for entry_name in sorted(os.listdir(source_folder)):
        entry_path = os.path.join(source_folder, entry_name)

        if not os.path.isdir(entry_path):
            continue

        if not entry_name.lower().endswith(GEODATABASE_EXTENSION):
            # Not a geodatabase at all - a zip, a log folder, a scratch directory.
            # Not worth reporting, since the folder legitimately holds other things.
            continue

        fips_code = extract_fips_code(entry_name)
        if fips_code is None:
            # It is a .gdb but carries no recognisable county code. Worth surfacing,
            # because it might be a county whose name did not follow the convention.
            skipped.append(entry_name)
            continue

        if wanted_counties and fips_code not in wanted_counties:
            continue

        discovered.append(SourceGeodatabase(fips_code=fips_code, gdb_path=entry_path))

    discovered.sort(key=lambda source: source.fips_code)

    return SourceDiscovery(
        source_geodatabases=tuple(discovered),
        skipped_folder_names=tuple(skipped),
    )
