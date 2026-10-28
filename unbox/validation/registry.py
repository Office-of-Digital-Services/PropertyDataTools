"""
    The check registry.

    A check is an ordinary function that takes a ValidationContext and returns either
    one CheckResult or an iterable of them. Decorating it with @register_check adds it
    to the module-level registry, which the runner then walks.

    Registration happens at import time, so `unbox.validation.checks` imports each check
    submodule to populate the registry. Registering the same check_id twice raises
    immediately rather than silently shadowing an existing check.
"""

from __future__ import annotations

import dataclasses
from typing import Callable, Dict, Iterable, List, Optional, Sequence, Union

from unbox.validation.models import CheckResult, ValidationContext

# A check returns one result or several. Several is useful when one sweep naturally
# produces a result per theme and you want each to pass or fail independently.
CheckReturn = Union[CheckResult, Iterable[CheckResult]]
CheckFunction = Callable[[ValidationContext], CheckReturn]

# The four categories, in the order they run and are reported. Counts come first
# because a missing county is the most consequential finding; performance comes late
# because it is the slowest family.
CATEGORY_ORDER = ("counts", "schema", "integrity", "performance")


class DuplicateCheckError(ValueError):
    """
        Raised when two checks claim the same check_id.

        This is a programming error, caught at import time, because a shadowed check is
        a check that silently stops running.
    """


@dataclasses.dataclass(frozen=True)
class CheckDescriptor:
    """
        A registered check and the metadata the runner and reports need about it.
    """

    check_id: str
    category: str
    title: str
    function: CheckFunction
    # When True, the runner reports SKIP without calling the function if the context
    # carries no source folder. This keeps "I cannot compare" out of the check bodies.
    requires_source_folder: bool = False
    # Registration order, used to keep execution deterministic within a category.
    registration_index: int = 0


class CheckRegistry:
    """
        An ordered collection of CheckDescriptors, keyed by check_id.
    """

    def __init__(self) -> None:
        self._descriptors_by_id: Dict[str, CheckDescriptor] = {}
        self._next_registration_index: int = 0

    def register(self, descriptor: CheckDescriptor) -> CheckDescriptor:
        """
            Adds a descriptor, refusing to overwrite an existing check_id.
        """
        if descriptor.check_id in self._descriptors_by_id:
            raise DuplicateCheckError(
                f"A check with id {descriptor.check_id!r} is already registered. "
                f"Check ids must be unique across the whole validation module."
            )

        # Stamp the descriptor with its position so ordering survives any later sorting.
        ordered_descriptor = dataclasses.replace(
            descriptor, registration_index=self._next_registration_index
        )
        self._next_registration_index += 1
        self._descriptors_by_id[descriptor.check_id] = ordered_descriptor
        return ordered_descriptor

    def unregister(self, check_id: str) -> None:
        """
            Removes a check. Primarily for tests, which register throwaway checks and
            must not leak them into other tests or into a real run.
        """
        self._descriptors_by_id.pop(check_id, None)

    def all_descriptors(self) -> List[CheckDescriptor]:
        """
            Every registered check, sorted by category order then registration order.
        """
        return sorted(self._descriptors_by_id.values(), key=self._sort_key)

    @staticmethod
    def _sort_key(descriptor: CheckDescriptor):
        """
            Sorts by the declared category order, with unknown categories last but still
            deterministic, then by registration order within the category.
        """
        try:
            category_position = CATEGORY_ORDER.index(descriptor.category)
        except ValueError:
            category_position = len(CATEGORY_ORDER)
        return (category_position, descriptor.category, descriptor.registration_index)

    def select(self,
               categories: Optional[Sequence[str]] = None,
               check_ids: Optional[Sequence[str]] = None) -> List[CheckDescriptor]:
        """
            The checks to run for this invocation.

            `categories` and `check_ids` are both optional filters; when both are given a
            check must satisfy both. Passing neither selects everything.
        """
        selected = self.all_descriptors()

        if categories:
            wanted_categories = {category.strip().lower() for category in categories}
            selected = [
                descriptor for descriptor in selected
                if descriptor.category.lower() in wanted_categories
            ]

        if check_ids:
            wanted_ids = {check_id.strip().lower() for check_id in check_ids}
            selected = [
                descriptor for descriptor in selected
                if descriptor.check_id.lower() in wanted_ids
            ]

        return selected

    def categories(self) -> List[str]:
        """
            Distinct categories currently holding at least one registered check, in run
            order.
        """
        ordered: List[str] = []
        for descriptor in self.all_descriptors():
            if descriptor.category not in ordered:
                ordered.append(descriptor.category)
        return ordered

    def __len__(self) -> int:
        return len(self._descriptors_by_id)

    def __contains__(self, check_id: object) -> bool:
        return check_id in self._descriptors_by_id


# The registry the application uses. Check modules decorate into this one at import.
default_registry = CheckRegistry()


def register_check(check_id: str,
                   category: str,
                   title: str,
                   requires_source_folder: bool = False,
                   registry: Optional[CheckRegistry] = None) -> Callable[[CheckFunction], CheckFunction]:
    """
        Decorator registering a check function.

        The function is returned unchanged, so it stays directly callable in a unit test
        with a hand-built context and a fake accessor - which is how every check in this
        module is tested.

        Example:
            @register_check("counts.county_coverage", "counts", "County coverage",
                            requires_source_folder=True)
            def check_county_coverage(context):
                ...
    """
    target_registry = registry if registry is not None else default_registry

    def decorator(check_function: CheckFunction) -> CheckFunction:
        target_registry.register(
            CheckDescriptor(
                check_id=check_id,
                category=category,
                title=title,
                function=check_function,
                requires_source_folder=requires_source_folder,
            )
        )
        return check_function

    return decorator
