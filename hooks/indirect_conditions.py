from BaseClasses import MultiWorld
from fuzz import BaseHook, GenOutcome


class MissingIndirectConditionError(Exception):
    pass


def get_spheres_locations(mw: MultiWorld) -> list[frozenset[str]]:
    return [frozenset(loc.name for loc in sphere) for sphere in mw.get_spheres()]


class Hook(BaseHook):
    def __init__(self):
        self._error = None

    def before_generate(self, args):
        self._error = None

    def after_generate(self, mw: MultiWorld, output_path):
        if not mw:
            return

        worlds_using_explicit = []
        for player in mw.player_ids:
            world = mw.worlds[player]
            if world.explicit_indirect_conditions:
                worlds_using_explicit.append((player, world))

        if not worlds_using_explicit:
            return

        explicit_spheres = get_spheres_locations(mw)

        for player, world in worlds_using_explicit:
            world.explicit_indirect_conditions = False

        implicit_spheres = get_spheres_locations(mw)

        for player, world in worlds_using_explicit:
            world.explicit_indirect_conditions = True

        if explicit_spheres != implicit_spheres:
            diffs = []
            for i, (e, im) in enumerate(zip(explicit_spheres, implicit_spheres)):
                missing = im - e
                extra = e - im
                if missing:
                    diffs.append(f"  Sphere {i}: implicit has {len(missing)} locations not in explicit: {sorted(missing)[:10]}")
                if extra:
                    diffs.append(f"  Sphere {i}: explicit has {len(extra)} locations not in implicit: {sorted(extra)[:10]}")
            if len(explicit_spheres) != len(implicit_spheres):
                diffs.append(f"  Sphere count: explicit={len(explicit_spheres)}, implicit={len(implicit_spheres)}")

            games = ", ".join(f"{world.game} (player {player})" for player, world in worlds_using_explicit)
            self._error = MissingIndirectConditionError(
                f"Missing indirect conditions detected for: {games}\n" + "\n".join(diffs)
            )

    def reclassify_outcome(self, outcome, raised):
        if self._error is not None and outcome == GenOutcome.Success:
            return GenOutcome.Failure, self._error
        if outcome != GenOutcome.Success and not isinstance(raised, MissingIndirectConditionError):
            return GenOutcome.OptionError, raised
        return outcome, raised
