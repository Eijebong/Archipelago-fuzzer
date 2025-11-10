import random

from fuzz import BaseHook, GenOutcome

from BaseClasses import MultiWorld, CollectionState, Location


class HookLogicTestFailure(Exception):
    pass


class Hook(BaseHook):
    """
    Hook that tests that collecting items into a state never reduces accessibility. Made by Mysteryem.

    Archipelago requires that collecting an item must only ever increase accessibility or have no effect on
    accessibility. If accessibility reduces when an item is collected, it can cause generation to crash or behave in a
    number of weird ways.

    This will noticeably increase the fuzzing duration because the hook iteratively collects 1 reachable advancement
    item at a time and finds all reachable locations after each item is collected.

    For better performance when fuzzing multiworlds with multiple slots, the hook assumes that each slot's logic only
    depends on collecting its own items, and not any items belonging to another slot.
    """
    passed: bool = True
    error_message: str = ""

    def reclassify_outcome(self, outcome, raised):
        if outcome == GenOutcome.Success and not self.passed:
            assert raised is None
            return GenOutcome.Failure, HookLogicTestFailure(self.error_message)
        else:
            return super().reclassify_outcome(outcome, raised)

    def after_generate(self, mw, output_path):
        super().after_generate(mw, output_path)
        if mw is None:
            self.passed = False
            self.error_message = "Generation failed before the hook was run. This message should not be seen."
        else:
            self.passed, self.error_message = self._test_collect_logic(mw)

    @staticmethod
    def _test_collect_logic(multiworld: MultiWorld) -> tuple[bool, str | None]:
        """
        This is the 'safer' version of my logic test. If a world fails with this 'safer' version, the world's logic is
        100% broken.

        This version is considered 'safer' because it collects items and events in a natural order based on their
        placements in the multiworld, effectively simulating a possible route that a player could complete a seed.

        The route is randomly seeded based on the multiworld seed, so should produce deterministic results with worlds
        that generate deterministically.

        When fuzzing generations with multiple players, only locations belonging to the player that collected the item
        are checked.
        """
        # Get all locations containing progression items.
        advancements = [loc for loc in multiworld.get_locations() if loc.advancement]

        # Put all the locations in a list for simplicity.
        per_slot_locations = {slot: list(multiworld.get_locations(slot)) for slot in multiworld.get_all_ids()}

        seeded_random = random.Random(multiworld.seed)
        seeded_random.shuffle(advancements)

        # Create an empty (starting inventory only) state.
        state = CollectionState(multiworld)

        # Create a set of all locations, per-slot, that are reachable with the current state.
        # This set will be updated as the state collects more items.
        per_slot_reachable_so_far = {slot: {loc for loc in slot_locations if loc.can_reach(state)}
                                     for slot, slot_locations in per_slot_locations.items()}

        # Store the items in collection order in-case a logic bug only occurs when items are collected in a specific
        # order.
        previous_collects: list[str] = []

        # The loop breaks when the state did not change because no advancements were reachable.
        state_changed = True
        while state_changed:
            state_changed = False

            # The advancements that were not reachable in this loop iteration, to be tried again in the next loop
            # iteration.
            next_advancements: list[Location] = []

            # Find the first reachable advancement and then collect the item and test accessibility.
            # An iterator is used to be able to easily dump all remaining advancements into `next_advancements` once a
            # reachable advancement is found.
            advancements_iter = iter(advancements)
            for location in advancements_iter:
                if location.can_reach(state):
                    player = location.item.player
                    item = location.item

                    # Copy the prog_items for this player prior to collecting the item, so that error messages can
                    # output state information before and after a failing collect.
                    prog_items_before = state.prog_items[player].copy()

                    state.collect(item, True, location)
                    state_changed = True

                    # Find all reachable locations for this player.
                    reachable_locations = {loc for loc in per_slot_locations[player] if loc.can_reach(state)}

                    # Find locations that the player lost access to (should not happen with correct logic).
                    reachable_so_far = per_slot_reachable_so_far[player]
                    lost_access = reachable_so_far - reachable_locations
                    if lost_access:
                        # Test failed.
                        return False, (f"Lost access to {lost_access} upon collecting {item} from {location}."
                                       f"\nPreviously collected in order of collection: {previous_collects}"
                                       f"\nprog_items before: {prog_items_before}"
                                       f"\nprog_items after: {state.prog_items[player]}")

                    # Record the item collection.
                    previous_collects.append(f"{item} from {location}")

                    # Update the locations reachable so far.
                    newly_reachable = reachable_locations - reachable_so_far
                    reachable_so_far.update(newly_reachable)

                    # Append the remaining advancements to check to `next_advancements`.
                    next_advancements.extend(advancements_iter)
                    break
                else:
                    # The advancement could not be reached, so will be tried in the next iteration.
                    next_advancements.append(location)

            # Prepare for the next loop iteration.
            advancements = next_advancements
        return True, None
