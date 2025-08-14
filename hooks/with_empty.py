from fuzz import BaseHook
from worlds import AutoWorldRegister, WorldSource
import worlds
import os
import tempfile
import shutil

def refresh_netdata_package():
    for world_name, world in AutoWorldRegister.world_types.items():
        if world_name not in worlds.network_data_package["games"]:
            worlds.network_data_package["games"][world_name] =  world.get_data_package_data()


class Hook(BaseHook):
    def setup_main(self, args):
        self._tmp = tempfile.TemporaryDirectory(prefix="apfuzz")
        with open(os.path.join(self._tmp.name, "empty.yaml"), "w") as fd:
            fd.write("""
name: Player{number}
description: Empty world to weed restrictive starts out
game: Empty
Empty: {}
            """)
        args.with_static_worlds = self._tmp.name

        if os.path.isfile('/ap/empty.apworld'):
            target_path = '/ap/archipelago/worlds/empty.apworld'
            if not os.path.exists(target_path):
                shutil.copy('/ap/empty.apworld', target_path)

    def setup_worker(self, args):
        if 'Empty' not in AutoWorldRegister.world_types:
            # File should already be copied by setup_main, just load it if it's there
            target_path = '/ap/archipelago/worlds/empty.apworld'
            if os.path.exists(target_path):
                world_source = WorldSource(target_path, is_zip=True, relative=False)
                if not world_source.load():
                    raise RuntimeError(f"Failed to load empty.apworld from {target_path}. Check logs for details.")

        if 'Empty' not in AutoWorldRegister.world_types:
            raise RuntimeError("empty needs to be loaded")

        refresh_netdata_package()

