"""
Detect worlds that change placements (location.item) during generate_output.
generate_output should only produce output files, not modify the multiworld state.
"""

from typing import Any

from worlds import AutoWorld
from BaseClasses import MultiWorld

from fuzz import BaseHook, GenOutcome


class HookTestFailure(Exception):
    pass


def _snapshot_placements(multiworld: MultiWorld):
    return {
        (loc.player, loc.name): (loc.item.name, loc.item.player) if loc.item else None
        for loc in multiworld.get_locations()
    }


def _check_placements(multiworld: MultiWorld, before, context: str):
    after = _snapshot_placements(multiworld)

    changed = []
    for key, before_val in before.items():
        after_val = after.get(key)
        if before_val != after_val:
            changed.append((key, before_val, after_val))

    new_locs = set(after.keys()) - set(before.keys())
    removed_locs = set(before.keys()) - set(after.keys())

    parts = []
    if changed:
        parts.append(f"Changed placements: {changed}")
    if new_locs:
        parts.append(f"New locations: {new_locs}")
    if removed_locs:
        parts.append(f"Removed locations: {removed_locs}")

    if parts:
        detail = "\n".join(parts)
        raise HookTestFailure(f"Placements changed during {context}: {detail}")


class Hook(BaseHook):
    def setup_worker(self, args):
        super().setup_worker(args)

        original_call_single = AutoWorld.call_single
        original_call_stage = AutoWorld.call_stage

        def wrapped_call_single(multiworld: MultiWorld, method_name: str, player: int, *args: Any) -> Any:
            if method_name == "generate_output":
                snapshot = _snapshot_placements(multiworld)
                result = original_call_single(multiworld, method_name, player, *args)
                _check_placements(multiworld, snapshot,
                                  f"generate_output for player {player} ({multiworld.game[player]})")
                return result
            return original_call_single(multiworld, method_name, player, *args)

        def wrapped_call_stage(multiworld: MultiWorld, method_name: str, *args: Any) -> None:
            if method_name == "generate_output":
                snapshot = _snapshot_placements(multiworld)
                result = original_call_stage(multiworld, method_name, *args)
                _check_placements(multiworld, snapshot, "stage_generate_output")
                return result
            return original_call_stage(multiworld, method_name, *args)

        AutoWorld.call_single = wrapped_call_single
        AutoWorld.call_stage = wrapped_call_stage

    def reclassify_outcome(self, outcome, raised):
        if outcome is not GenOutcome.Success and not isinstance(raised, HookTestFailure):
            return GenOutcome.OptionError, None
        return super().reclassify_outcome(outcome, raised)
