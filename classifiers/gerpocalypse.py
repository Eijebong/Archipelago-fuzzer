from fuzz import GenOutcome
from worlds import AutoWorldRegister, WorldSource
import os
import tempfile
import shutil

class Classifier:
    _tmp = None
    def setup(self, args):
        if self._tmp is None:
            self._tmp = tempfile.TemporaryDirectory(prefix="apfuzz")
            with open(os.path.join(self._tmp.name, "kh.yaml"), "w") as fd:
                fd.write("""
name: Player{number}
description: Default Kingdom Hearts Template
game: Kingdom Hearts
Kingdom Hearts: {}
                """)
            args.with_static_worlds = self._tmp.name

            if 'Kingdom Hearts' not in AutoWorldRegister.world_types:
                # This is correct for the ap-yaml-checker container
                if os.path.isfile('/ap/supported_worlds/kh1-0.6.1.apworld'):
                    shutil.copy('/ap/supported_worlds/kh1-0.6.1.apworld', '/ap/archipelago/worlds/kh1.apworld')
                    WorldSource('/ap/archipelago/worlds/kh1.apworld', is_zip=True, relative=False).load()

            if 'Kingdom Hearts' not in AutoWorldRegister.world_types:
                raise RuntimeError("kh1 needs to be loaded")

    def classify(self, outcome, exception):
        message = str(exception).lower()
        if "no connected region" in message or "tried to search through an entrance" in message:
            return GenOutcome.Failure
        return GenOutcome.Success
