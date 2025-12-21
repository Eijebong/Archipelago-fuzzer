from collections import Counter
import copy
import gc
import os
import pickle
import struct
import subprocess
import sys
from abc import ABCMeta


class DeterminismError(Exception):
    pass


def send_msg(pipe, obj):
    data = pickle.dumps(obj, protocol=pickle.HIGHEST_PROTOCOL)
    pipe.write(struct.pack('<I', len(data)))
    pipe.write(data)
    pipe.flush()


def read_exact(pipe, n):
    data = b''
    while len(data) < n:
        chunk = pipe.read(n - len(data))
        if not chunk:
            raise EOFError("Pipe closed")
        data += chunk
    return data


def recv_msg(pipe):
    header = read_exact(pipe, 4)
    length = struct.unpack('<I', header)[0]
    data = read_exact(pipe, length)
    return pickle.loads(data)


def serialize_item(item):
    return {
        "name": item.name,
        "player": item.player,
        "code": item.code,
        "classification": item.classification,
    }


def serialize_location(loc):
    return {
        "address": loc.address,
        "progress_type": loc.progress_type,
        "item": serialize_item(loc.item) if loc.item else None,
    }


def serialize_entrance(ent):
    return {
        "parent_region": ent.parent_region.name if ent.parent_region else None,
        "connected_region": ent.connected_region.name if ent.connected_region else None,
    }


def serialize_region(reg):
    return {"locations": [loc.name for loc in reg.locations]}


def serialize_options(mw):
    all_options = {}
    for player in mw.player_ids:
        world = mw.worlds[player]
        player_output = {"Game": mw.game[player], "Name": mw.get_player_name(player)}
        for option_key, option in world.options_dataclass.type_hints.items():
            player_output[option_key] = getattr(world.options, option_key).current_option_name
        all_options[player] = player_output
    return all_options


def serialize_multiworld(mw):
    return {
        "options": serialize_options(mw),
        "itempool": [serialize_item(item) for item in mw.itempool],
        "start_inventory": {
            player: [serialize_item(item) for item in items]
            for player, items in mw.precollected_items.items()
        },
        "regions": {
            player: {region.name: serialize_region(region) for region in regions.values()}
            for player, regions in mw.regions.region_cache.items()
        },
        "entrances": {
            player: {ent.name: serialize_entrance(ent) for ent in entrances.values()}
            for player, entrances in mw.regions.entrance_cache.items()
        },
        "locations": {
            player: {loc.name: serialize_location(loc) for loc in locations.values()}
            for player, locations in mw.regions.location_cache.items()
        },
    }


def compare_options(o1, o2):
    differences = []
    for player in o1:
        if player not in o2:
            differences.append(f"Player {player} missing in second run")
            continue
        for key in set(o1[player].keys()) | set(o2[player].keys()):
            v1 = o1[player].get(key)
            v2 = o2[player].get(key)
            if v1 != v2:
                differences.append(f"Option {key} for player {player}: {v1} vs {v2}")
    return differences


def compare_items(name, i1, i2):
    differences = []
    c1 = Counter(tuple(sorted(item.items())) for item in i1)
    c2 = Counter(tuple(sorted(item.items())) for item in i2)
    if c1 != c2:
        only_in_1 = c1 - c2
        only_in_2 = c2 - c1
        differences.append(f"{name}: Different items (not just order)\n  only in run1: {dict(only_in_1)}\n  only in run2: {dict(only_in_2)}")
        return differences
    if i1 != i2:
        first_diff = None
        for idx, (item1, item2) in enumerate(zip(i1, i2)):
            if item1 != item2:
                first_diff = f"{name}[{idx}]: {item1['name']} vs {item2['name']}"
                break
        differences.append(f"{name}: Same items but different order (first diff: {first_diff})")
    return differences


def compare_regions(r1, r2):
    differences = []
    for player in r1:
        if player not in r2:
            differences.append(f"Player {player} missing in second run regions")
            continue
        keys1, keys2 = set(r1[player].keys()), set(r2[player].keys())
        if keys1 != keys2:
            differences.append(f"Player {player} has different regions: {keys1 ^ keys2}")
            continue
        for region_name in r1[player]:
            locs1 = r1[player][region_name]["locations"]
            locs2 = r2[player][region_name]["locations"]
            if set(locs1) != set(locs2):
                differences.append(f"Region '{region_name}' player {player}: different locations\n  run1: {locs1}\n  run2: {locs2}")
            elif locs1 != locs2:
                differences.append(f"Region '{region_name}' player {player}: same locations, different order\n  run1: {locs1}\n  run2: {locs2}")
    return differences


def compare_entrances(e1, e2):
    differences = []
    for player in e1:
        if player not in e2:
            differences.append(f"Player {player} missing in second run entrances")
            continue
        keys1, keys2 = set(e1[player].keys()), set(e2[player].keys())
        if keys1 != keys2:
            differences.append(f"Player {player} has different entrances: {keys1 ^ keys2}")
            continue
        for ent_name in e1[player]:
            if e1[player][ent_name] != e2[player][ent_name]:
                differences.append(f"Entrance '{ent_name}' player {player}:\n  run1: {e1[player][ent_name]}\n  run2: {e2[player][ent_name]}")
    return differences


def compare_locations(l1, l2):
    differences = []
    for player in l1:
        if player not in l2:
            differences.append(f"Player {player} missing in second run locations")
            continue
        keys1, keys2 = set(l1[player].keys()), set(l2[player].keys())
        if keys1 != keys2:
            differences.append(f"Player {player} has different locations: {keys1 ^ keys2}")
            continue
        for loc_name in l1[player]:
            loc1, loc2 = l1[player][loc_name], l2[player][loc_name]
            if loc1["address"] != loc2["address"]:
                differences.append(f"Location '{loc_name}' player {player} address: {loc1['address']} vs {loc2['address']}")
            if loc1["progress_type"] != loc2["progress_type"]:
                differences.append(f"Location '{loc_name}' player {player} progress_type: {loc1['progress_type']} vs {loc2['progress_type']}")
        for loc_name in sorted(l1[player].keys()):
            if l1[player][loc_name]["item"] != l2[player][loc_name]["item"]:
                differences.append(f"Placement at '{loc_name}' player {player}:\n  run1: {l1[player][loc_name]['item']}\n  run2: {l2[player][loc_name]['item']}")
        locs1, locs2 = list(l1[player].keys()), list(l2[player].keys())
        if locs1 != locs2:
            for idx, (loc1, loc2) in enumerate(zip(locs1, locs2)):
                if loc1 != loc2:
                    differences.append(f"Player {player}: locations in different order (first diff at {idx}: {loc1} vs {loc2})")
                    break
    return differences


def compare_states(s1, s2):
    all_differences = []
    checks = [
        ("ROLLED OPTIONS", compare_options, s1["options"], s2["options"]),
        ("ITEMPOOL", compare_items, "Itempool", s1["itempool"], s2["itempool"]),
    ]
    for player in s1["start_inventory"]:
        checks.append((
            f"START INVENTORY (player {player})",
            compare_items,
            f"Start inventory player {player}",
            s1["start_inventory"].get(player, []),
            s2["start_inventory"].get(player, []),
        ))
    checks.extend([
        ("REGIONS", compare_regions, s1["regions"], s2["regions"]),
        ("ENTRANCES", compare_entrances, s1["entrances"], s2["entrances"]),
        ("LOCATIONS", compare_locations, s1["locations"], s2["locations"]),
    ])
    for name, func, *args in checks:
        diffs = func(*args)
        if diffs:
            all_differences.append(f"=== {name} ===")
            all_differences.extend(diffs)
    return all_differences


def worker_main(proto):
    from io import StringIO
    stdin = sys.stdin.buffer

    import worlds
    from Generate import main as GenMain
    from Main import main as ERmain

    abc_classes = [obj for obj in gc.get_objects() if isinstance(obj, ABCMeta)]

    def clear_abc_caches():
        for cls in abc_classes:
            try:
                cls._abc_caches_clear()
            except:
                pass

    send_msg(proto, "ready")

    while True:
        try:
            args = recv_msg(stdin)
        except (EOFError, OSError):
            break

        capture = StringIO()
        old_stderr = sys.stderr
        sys.stderr = capture
        try:
            erargs, seed = GenMain(args)
            mw = ERmain(erargs, seed)
            send_msg(proto, ("ok", serialize_multiworld(mw)))
        except Exception as e:
            import traceback
            logs = capture.getvalue()
            send_msg(proto, ("error", f"{e}\n{traceback.format_exc()}\n{logs}"))
        finally:
            sys.stderr = old_stderr
            clear_abc_caches()

    os._exit(0)


if __name__ == "__main__":
    proto = sys.stdout.buffer
    sys.stdout = sys.stderr
    sys.path.insert(0, sys.argv[1])
    import Utils
    Utils.init_logging = lambda *args, **kwargs: None
    worker_main(proto)


from fuzz import BaseHook, GenOutcome, ap_path as _AP_PATH


class Hook(BaseHook):
    def __init__(self):
        self._proc = None
        self._stdin = None
        self._proto_r = None
        self._args = None
        self._determinism_error = None

    def setup_worker(self, args):
        ap_path = _AP_PATH

        self._proc = subprocess.Popen(
            [sys.executable, __file__, ap_path],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )

        self._stdin = self._proc.stdin
        self._proto_r = self._proc.stdout

        try:
            msg = recv_msg(self._proto_r)
        except EOFError:
            stderr = self._proc.stderr.read().decode(errors="replace")
            raise RuntimeError(f"Determinism worker failed to start:\n{stderr}")
        if msg != "ready":
            raise RuntimeError(f"Determinism worker sent unexpected message: {msg}")

        # Close stderr to avoid deadlocks. The subprocess logs can fill the pipe buffer
        # and block if we don't drain it, but we only read stderr for startup errors
        self._proc.stderr.close()

    def before_generate(self, args):
        self._args = copy.copy(args)
        self._determinism_error = None

    def after_generate(self, mw, output_dir):
        if mw is None:
            return

        state1 = serialize_multiworld(mw)
        send_msg(self._stdin, self._args)
        status, result = recv_msg(self._proto_r)

        if status == "error":
            self._determinism_error = DeterminismError(
                f"Subprocess generation failed:\n{result}"
            )
            return

        differences = compare_states(state1, result)
        del state1, result
        if differences:
            self._determinism_error = DeterminismError(
                "Non-deterministic generation:\n" + "\n".join(differences)
            )

    def reclassify_outcome(self, outcome, exc):
        if self._determinism_error is not None:
            return GenOutcome.Failure, self._determinism_error
        if outcome == GenOutcome.Failure:
            return GenOutcome.OptionError, exc
        return outcome, exc

