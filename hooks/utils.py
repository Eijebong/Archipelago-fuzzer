import sys
import zipimport
import importlib.abc
import importlib.machinery
from pathlib import Path

_apworld_specs = {}
_finder_registered = False


class _APWorldFinder(importlib.abc.MetaPathFinder):
    def find_spec(self, fullname, _path: = None):
        return _apworld_specs.get(fullname)


def _ensure_finder_registered():
    global _finder_registered
    if not _finder_registered:
        sys.meta_path.insert(0, _APWorldFinder())
        _finder_registered = True


def load_apworld(path):
    from worlds import WorldSource

    _ensure_finder_registered()

    world_name = Path(path).stem
    module_name = f"worlds.{world_name}"

    if module_name in sys.modules:
        return True

    importer = zipimport.zipimporter(path)
    spec = importer.find_spec(module_name)
    _apworld_specs[module_name] = spec

    return WorldSource(path, is_zip=True, relative=False).load()

