from fuzz import BaseHook
from worlds import AutoWorldRegister, WorldSource
import os
import tempfile
import shutil

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

    def setup_worker(self, args):
        if 'Empty' not in AutoWorldRegister.world_types:
            # This is correct for the ap-yaml-checker container
            if os.path.isfile('/ap/empty.apworld'):
                shutil.copy('/ap/empty.apworld', '/ap/archipelago/worlds/empty.apworld')
                WorldSource('/ap/archipelago/worlds/empty.apworld', is_zip=True, relative=False).load()

        if 'Empty' not in AutoWorldRegister.world_types:
            raise RuntimeError("empty needs to be loaded")

