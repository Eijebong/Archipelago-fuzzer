from fuzz import BaseHook
from BaseClasses import Item, Location, ItemClassification, Region
from worlds.AutoWorld import WebWorld, World
from worlds import network_data_package, AutoWorldRegister
import os
import tempfile


_GAME_NAME = "FuzzerEmpty"


class Hook(BaseHook):
    def setup_main(self, args):
        self._tmp = tempfile.TemporaryDirectory(prefix="apfuzz")
        with open(os.path.join(self._tmp.name, "empty.yaml"), "w") as fd:
            fd.write(f"""name: Player{{number}}
description: Empty world to weed restrictive starts out
game: {_GAME_NAME}
{_GAME_NAME}: {{}}
""")
        args.with_static_worlds = self._tmp.name

    def setup_worker(self, args):
        if _GAME_NAME not in AutoWorldRegister.world_types:
            # Similar setup to `test.general.TestWorld`.
            class EmptyWebWorld(WebWorld):
                tutorials = []

            # The metaclass handles registration into AutoWorldRegister when the class is created.
            class EmptyWorld(World):
                game = _GAME_NAME
                item_name_to_id = {"Nothing": 1}
                location_name_to_id = {f"Location {i+1}": i+1 for i in range(100)}
                hidden = True
                web = EmptyWebWorld()

                def create_items(self) -> None:
                    # Add 100 filler Nothings to the item pool.
                    for _ in range(100):
                        item = Item("Nothing", ItemClassification.filler, 1, self.player)
                        self.multiworld.itempool.append(item)

                def create_regions(self) -> None:
                    # Create 100 locations in the origin region that are accessible from the start.
                    origin = Region(self.origin_region_name, self.player, self.multiworld)
                    self.multiworld.regions.append(origin)
                    for i in range(100):
                        location = Location(self.player, f"Location {i+1}", i+1, origin)
                        origin.locations.append(location)

            network_data_package["games"][EmptyWorld.game] = EmptyWorld.get_data_package_data()

        if _GAME_NAME not in AutoWorldRegister.world_types:
            raise RuntimeError("Empty world failed to register")
