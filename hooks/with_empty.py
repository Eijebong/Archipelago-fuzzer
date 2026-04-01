from fuzz import BaseHook
from worlds import AutoWorldRegister, WorldSource
import worlds
import os
import sys
import tempfile
import shutil
import zipimport
import importlib.abc
import importlib.machinery
from pathlib import Path

_dynamic_apworld_specs = {}

class _DynamicAPWorldFinder(importlib.abc.MetaPathFinder):
    def find_spec(self, fullname, _path=None, _target=None):
        return _dynamic_apworld_specs.get(fullname)

sys.meta_path.insert(0, _DynamicAPWorldFinder())

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
            target_path = '/ap/archipelago/worlds/empty.apworld'
            if os.path.exists(target_path):
                world_name = Path(target_path).stem
                importer = zipimport.zipimporter(target_path)
                spec = importer.find_spec(f"worlds.{world_name}")
                _dynamic_apworld_specs[f"worlds.{world_name}"] = spec

                world_source = WorldSource(target_path, is_zip=True, relative=False)
                if not world_source.load():
                    raise RuntimeError(f"Failed to load empty.apworld from {target_path}. Check logs for details.")

        if 'Empty' not in AutoWorldRegister.world_types:
            raise RuntimeError("empty needs to be loaded")

        refresh_netdata_package()

