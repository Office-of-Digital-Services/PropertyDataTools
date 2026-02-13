"""
build_locator_cli.py

Command line wrapper for building a locator from a single Esri File Geodatabase (FGDB).

Goals:
    - input FGDB path
    - output location (locator path, or output folder + derived locator name)
    - configuration flags (paths or URLs)
"""

from __future__ import annotations

import json
import os
from typing import Dict, Iterable, List, Optional, Tuple

import arcpy
import click

from unbox import build_locator
from unbox.build_locator import BuildConfig

# --------------------------------------------------------------------------------------
# Types / helpers
# --------------------------------------------------------------------------------------



def _normalize_kv_pairs(pairs: Iterable[str]) -> Dict[str, str]:
    """
    Convert repeated KEY=VALUE strings into a dict.

    Example:
        ["foo=bar", "x=1"] -> {"foo": "bar", "x": "1"}
    """
    out: Dict[str, str] = {}
    for item in pairs:
        if "=" not in item:
            raise click.BadParameter(
                f"Invalid --config value {item!r}. Expected format KEY=VALUE."
            )
        k, v = item.split("=", 1)
        k = k.strip()
        v = v.strip()
        if not k:
            raise click.BadParameter(
                f"Invalid --config value {item!r}. KEY must not be empty."
            )
        out[k] = v
    return out


def _ensure_parent_dir(path: str) -> None:
    parent = os.path.dirname(os.path.abspath(path)) or "."
    os.makedirs(parent, exist_ok=True)


def _looks_like_url(value: str) -> bool:
    return value.startswith("http://") or value.startswith("https://")


def _validate_path_or_url(ctx: click.Context, param: click.Parameter, value: Optional[str]) -> Optional[str]:
    if value is None:
        return None
    v = value.strip()
    if not v:
        return None
    if _looks_like_url(v):
        return v
    # local path
    if not os.path.exists(v) and not arcpy.Exists(v):
        raise click.BadParameter(f"Path does not exist: {v}")
    return v


# --------------------------------------------------------------------------------------
# Core logic (stub)
# --------------------------------------------------------------------------------------
def main(cfg: BuildConfig) -> None:
    """
    Implement the actual work here.

    Expected behavior (example):
    - Validate input FGDB exists
    - Build a locator in cfg.output using cfg.cities/cfg.counties/cfg.tiger
    - Respect include_* flags

    Currently: just prints parsed configuration.
    """
    # NOTE: keep this lightweight; click already validates most inputs.
    click.echo("Parsed configuration:")
    click.echo(json.dumps(
        {
            "input_gdb": cfg.input_gdb,
            "output_locator": cfg.output_locator_path,
            "cities": cfg.cities,
            "counties": cfg.counties,
            "tiger": cfg.tiger,
            "zip_boundaries": cfg.zip_boundaries,
            "include_address_points": cfg.include_address_points,
            "include_parcels": cfg.include_parcels,
            "parcels_with_addresses": cfg.parcels_with_addresses,
            "temp_gdb": cfg.temp_gdb,
            "extra": cfg.extra or {},
        },
        indent=2,
        sort_keys=True,
    ))

    cfg.run_build()


# --------------------------------------------------------------------------------------
# CLI
# --------------------------------------------------------------------------------------
CONTEXT_SETTINGS = {
    "help_option_names": ["-h", "--help"],
}


@click.command(context_settings=CONTEXT_SETTINGS)
@click.option(
    "--input_gdb",
    "input_gdb",
    required=True,
    type=click.Path(exists=True, file_okay=False, dir_okay=True, readable=True, path_type=str),
    help="Path to input Esri File Geodatabase (folder ending in .gdb).",
)
@click.option(
    "--output_locator",
    required=True,
    type=click.Path(exists=False, dir_okay=False, file_okay=True, writable=True, path_type=str),
    help="Output location. Typically a locator path like C:\\...\\my.loc (parent folder will be created).",
)
@click.option(
    "--cities",
    default=None,
    callback=_validate_path_or_url,
    help="Cities dataset as a local path or a URL.",
)
@click.option(
    "--counties",
    default=None,
    callback=_validate_path_or_url,
    help="Counties dataset as a local path or a URL.",
)
@click.option(
    "--tiger",
    default=None,
    callback=_validate_path_or_url,
    type=click.Path(exists=False, file_okay=True, dir_okay=True, path_type=str),
    help="TIGER dataset as a local path.",
)
@click.option(
    "--zip_boundaries",
    default=None,
    callback=_validate_path_or_url,
    help="ZIP (postal) boundaries dataset as a local path or a URL (feature class, shapefile, etc.).",
)
@click.option(
    "--include_address_points/--no_include_address_points",
    default=True,
    show_default=True,
    help="Whether to include address points in the locator build.",
)
@click.option(
    "--include_parcels/--no_include_parcels",
    default=True,
    show_default=True,
    help="Whether to include parcels in the locator build.",
)
@click.option(
    "--parcels_with_addresses",
    default=None,
    callback=_validate_path_or_url,
    help="Optional pre-prepared parcels-with-addresses feature class path.",
)
@click.option(
    "--temp_gdb",
    default=None,
    callback=_validate_path_or_url,
    type=click.Path(exists=True, file_okay=False, dir_okay=True, readable=True, path_type=str),
    help="Optional temp file geodatabase to use when preparing intermediate data.",
)
@click.option(
    "--config",
    "config_pairs",
    multiple=True,
    help="Additional config values. Repeatable KEY=VALUE entries.",
)
@click.option(
    "--usage",
    is_flag=True,
    default=False,
    help="Print usage information and exit (alias for --help).",
)
def cli(
    input_gdb: str,
    output_locator: str,
    cities: Optional[str],
    counties: Optional[str],
    tiger: Optional[str],
    zip_boundaries: Optional[str],
    include_address_points: bool,
    include_parcels: bool,
    parcels_with_addresses: Optional[str],
    temp_gdb: Optional[str],
    config_pairs: Tuple[str, ...],
    usage: bool,
) -> None:
    """
    Build a locator from a single FGDB using non-positional arguments.

    Run `python build_locator_cli.py --help` to see full usage.
    """
    if usage:
        # Print click's normal help text and exit 0.
        click.echo(click.get_current_context().get_help())
        raise SystemExit(0)

    if output_locator.lower().endswith(os.sep) or os.path.isdir(output_locator):
        raise click.BadParameter(
            "--output must be a file path (e.g., C:\\path\\to\\locator.loc), not a directory."
        )

    _ensure_parent_dir(output_locator)

    extra = _normalize_kv_pairs(config_pairs)

    cfg = BuildConfig(
        input_gdb=input_gdb,
        output_locator_path=output_locator,
        cities=cities,
        counties=counties,
        tiger=tiger,
        zip_boundaries=zip_boundaries,
        include_address_points=include_address_points,
        include_parcels=include_parcels,
        parcels_with_addresses=parcels_with_addresses,
        temp_gdb=temp_gdb,
        extra=extra,
    )

    main(cfg)


if __name__ == "__main__":
    cli()