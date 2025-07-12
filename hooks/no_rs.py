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
description: YAML used to weed out restrictive starts. Apworld can be found at https://github.com/Eijebong/empty-apworld
game: Empty
Empty: {}
            """)
        args.with_static_worlds = self._tmp.name

    def setup_worker(self, args):
        if 'Empty' not in AutoWorldRegister.world_types:
            # This is correct for the ap-yaml-checker container
            if os.path.isfile('/ap/custom_worlds/empty-0.0.1.apworld'):
                shutil.copy('/ap/custom_worlds/empty-0.0.1.apworld', '/ap/archipelago/worlds/empty.apworld')
                WorldSource('/ap/archipelago/worlds/empty.apworld', is_zip=True, relative=False).load()

        if 'Empty' not in AutoWorldRegister.world_types:
            raise RuntimeError("The `empty` apworld needs to be present. You can get it here: https://github.com/Eijebong/empty-apworld")

