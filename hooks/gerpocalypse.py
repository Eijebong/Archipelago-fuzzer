from fuzz import GenOutcome, BaseHook
from worlds import AutoWorldRegister, WorldSource
from Utils import __version__ as ap_version
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
        with open(os.path.join(self._tmp.name, "kh.yaml"), "w") as fd:
            fd.write("""
name: Player{number}
description: Default Kingdom Hearts Template
game: Kingdom Hearts
Kingdom Hearts: {}
            """)
        args.with_static_worlds = self._tmp.name

        # This is correct for the ap-yaml-checker container
        if os.path.isfile(f'/ap/supported_worlds/kh1-{ap_version}.apworld'):
            target_path = "/ap/archipelago/worlds/kh1.apworld"
            if not os.path.exists(target_path):
                shutil.copy(f'/ap/supported_worlds/kh1-{ap_version}.apworld', target_path)

    def setup_worker(self, args):
        if 'Kingdom Hearts' not in AutoWorldRegister.world_types:
            # File should already be copied by setup_main, just load it if it's there
            target_path = "/ap/archipelago/worlds/kh1.apworld"
            if os.path.exists(target_path):
                world_source = WorldSource(target_path, is_zip=True, relative=False)
                if not world_source.load():
                    raise RuntimeError(f"Failed to load kh1.apworld from {target_path}. Check logs for details.")

        if 'Kingdom Hearts' not in AutoWorldRegister.world_types:
            raise RuntimeError("kh1 needs to be loaded")

        refresh_netdata_package()

    def reclassify_outcome(self, outcome, exception):
        message = str(exception).lower()
        if "no connected region" in message or "tried to search through an entrance" in message:
            return GenOutcome.Failure, exception
        return GenOutcome.Success, None
