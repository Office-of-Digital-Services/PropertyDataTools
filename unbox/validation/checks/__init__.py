"""
    The four check families.

    Importing this package imports each submodule, and importing a submodule runs its
    @register_check decorators. That import side effect is the whole mechanism by which
    the registry gets populated, so the imports below are load-bearing even though
    nothing references the names directly.

    Check functions live here; they never import arcpy. They ask
    `context.accessor` for facts and `context.expectations` for what those facts should
    be, which is what lets every one of them be unit-tested against a fake accessor.
"""

from __future__ import annotations

from unbox.validation.checks import counts      # noqa: F401  (registers the count checks)
from unbox.validation.checks import schema      # noqa: F401  (registers the schema checks)
from unbox.validation.checks import integrity   # noqa: F401  (registers the integrity checks)
from unbox.validation.checks import performance # noqa: F401  (registers the performance checks)

__all__ = ["counts", "schema", "integrity", "performance"]
