"""
Check that, after each generation step, every `item`'s `.location`'s `.item` is `item`, and vice versa for locations.
So `item.location.item is item` for all items where `item.location is not None`, and
`location.item.location is location` for all locations where `location.item is not None`.
"""

from itertools import chain
from typing import Any

from worlds import AutoWorld
from BaseClasses import MultiWorld

from fuzz import BaseHook, GenOutcome


class HookTestFailure(Exception):
    pass


def _check_for_broken_locations(multiworld: MultiWorld, step_name: str):
    broken_locations = [(loc, loc.item, loc.item.location) for loc in multiworld.get_locations()
                        if loc.item is not None and loc.item.location is not loc]
    if broken_locations:
        raise HookTestFailure(
            f"Broken locations after {step_name} where loc is not loc.item.location: {broken_locations}")


def _check_for_broken_items(multiworld: MultiWorld, step_name: str):
    # Make an iterable of items that should cover all the possible places an item can end up.
    most_items = chain(
        # Core AP does not clear out the itempool once it has performed its main fill, but there could be items left in
        # the item pool that were placed at a location, but then location.item was overridden without also updating
        # item.location, so an item in the item pool could think it is placed at a location, when that location thinks
        # it has a different item placed at it.
        multiworld.itempool,
        (loc.item for loc in multiworld.get_filled_locations()),
        # Precollected items should never be placed at locations (there is an assert for this in
        # Fill.distribute_items_restrictive), but check them anyway because it is another place items can end up in the
        # multiworld.
        chain.from_iterable(multiworld.precollected_items.values()),
    )

    seen_item_ids = set()
    broken_items = []
    for item in most_items:
        if id(item) in seen_item_ids:
            # This item has already been checked, so continue to the next item.
            # There is expected to be overlap between multiworld.itempool and items from filled locations after AP's
            # main fill because most items from multiworld.itempool will be placed somewhere.
            continue
        seen_item_ids.add(id(item))

        if item.location is not None and item.location.item is not item:
            broken_items.append((item, item.location, item.location.item))

    if broken_items:
        raise HookTestFailure(
            f"Broken items after {step_name} where item is not item.location.item: {broken_items}")


def _check_for_broken_placements(multiworld: MultiWorld, step_name: str):
    """
    Check for broken placements where item.location and location.item refer to different objects.

    Known core-verified failures at cda54e0beac2a05ea944d20305dcd8876d8e90eb:
    - Noita: after create_items() (single-slot multiworlds only)
    - Dark Souls 3: after post_fill()
    """
    _check_for_broken_locations(multiworld, step_name)
    _check_for_broken_items(multiworld, step_name)


class Hook(BaseHook):
    def setup_worker(self, args):
        super().setup_worker(args)
        # Monkey patch AutoWorld.call_all to run the check after each step.
        original = AutoWorld.call_all

        def replacement_call_all(multiworld: MultiWorld, method_name: str, *args: Any) -> None:
            original(multiworld, method_name, *args)
            _check_for_broken_placements(multiworld, method_name)

        AutoWorld.call_all = replacement_call_all

    def reclassify_outcome(self, outcome, raised):
        if outcome is not GenOutcome.Success and not isinstance(raised, HookTestFailure):
            # Ignore every non-success that is not the failure being tested for.
            return GenOutcome.OptionError, None
        else:
            return super().reclassify_outcome(outcome, raised)
