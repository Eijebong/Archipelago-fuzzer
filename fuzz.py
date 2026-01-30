__version__ = "0.5.0"

import sys
import os

ap_path = os.path.abspath(os.path.dirname(sys.argv[0]))
sys.path.insert(0, ap_path)

# Prevent multiprocess workers from spamming nonsense when KeyboardInterrupted
# I can't wait for this to hide actual issues...
if __name__ == "__mp_main__":
    sys.stderr = None

from worlds import AutoWorldRegister
from Options import (
    get_option_groups,
    Choice,
    Toggle,
    Range,
    ItemSet,
    ItemDict,
    LocationSet,
    NumericOption,
    OptionSet,
    FreeText,
    PlandoConnections,
    OptionCounter,
    OptionList,
    PlandoTexts,
    OptionDict,
    OptionError,
)
from BaseClasses import PlandoOptions
from Utils import __version__ as __ap_version__
import Utils
import settings

from Generate import main as GenMain
from Fill import FillError
from Main import main as ERmain
from settings import get_settings
from argparse import Namespace, ArgumentParser
from concurrent.futures import TimeoutError
from collections import defaultdict
import threading
from contextlib import redirect_stderr, redirect_stdout
from enum import Enum
from functools import wraps
from io import StringIO
from multiprocessing import Pool

import gc
import importlib
import json
import functools
import logging
import multiprocessing
import platform
import random
import shutil
import signal
import string
import tempfile
import time
import traceback
import yaml


OUT_DIR = f"fuzz_output"
settings.no_gui = True
settings.skip_autosave = True
MP_HOOKS = []
MANAGER = None

# This whole thing is to prevent infinite growth of ABC caches
# See https://github.com/python/cpython/issues/92810
from abc import ABCMeta
ABC_CLASSES = [obj for obj in gc.get_objects() if isinstance(obj, ABCMeta)]


def clear_abc_caches():
    for cls in ABC_CLASSES:
        cls._abc_caches_clear()


# We patch this because AP can't keep its hands to itself and has to start a thread to clean stuff up.
# We could monkey patch the hell out of it but since it's an inner function, I feel like the complexity
# of it is unreasonable compared to just reimplement a logger
# especially since it allows us to not have to cheat user_path

# Taken from https://github.com/ArchipelagoMW/Archipelago/blob/0.5.1.Hotfix1/Utils.py#L488
# and removed everythinhg that had to do with files, typing and cleanup
def patched_init_logging(
        name,
        loglevel = logging.INFO,
        write_mode = "w",
        log_format = "[%(name)s at %(asctime)s]: %(message)s",
        exception_logger = None,
        *args,
        **kwargs
):
    loglevel: int = Utils.loglevel_mapping.get(loglevel, loglevel)
    root_logger = logging.getLogger()
    for handler in root_logger.handlers[:]:
        root_logger.removeHandler(handler)
        handler.close()
    root_logger.setLevel(loglevel)

    class Filter(logging.Filter):
        def __init__(self, filter_name, condition) -> None:
            super().__init__(filter_name)
            self.condition = condition

        def filter(self, record: logging.LogRecord) -> bool:
            return self.condition(record)

    stream_handler = logging.StreamHandler(sys.stdout)
    stream_handler.addFilter(Filter("NoFile", lambda record: not getattr(record, "NoStream", False)))
    root_logger.addHandler(stream_handler)

    # Relay unhandled exceptions to logger.
    if not getattr(sys.excepthook, "_wrapped", False):  # skip if already modified
        orig_hook = sys.excepthook

        def handle_exception(exc_type, exc_value, exc_traceback):
            if issubclass(exc_type, KeyboardInterrupt):
                sys.__excepthook__(exc_type, exc_value, exc_traceback)
                return
            logging.getLogger(exception_logger).exception("Uncaught exception",
                                                          exc_info=(exc_type, exc_value, exc_traceback))
            return orig_hook(exc_type, exc_value, exc_traceback)

        handle_exception._wrapped = True

        sys.excepthook = handle_exception

    logging.info(
        f"Archipelago ({__ap_version__}) logging initialized"
        f" on {platform.platform()}"
        f" running Python {sys.version_info.major}.{sys.version_info.minor}.{sys.version_info.micro}"
    )

Utils.init_logging = patched_init_logging


class FuzzerException(Exception):
    def __init__(self, desc, out_buf):
        if isinstance(out_buf, str):
            self.out_buf = out_buf
        else:
            self.out_buf = out_buf.getvalue()
        self.desc = desc
        super().__init__(desc)

    def __reduce__(self):
        return (self.__class__, (self.desc, self.out_buf))

def exception_in_causes(e, ty):
    if isinstance(e, ty):
        return True
    if e.__cause__ is not None:
        return exception_in_causes(e.__cause__, ty)
    return False


def world_from_apworld_name(apworld_name):
    for name, world in AutoWorldRegister.world_types.items():
        if world.__module__.startswith(f"worlds.{apworld_name}"):
            return name, world

    raise Exception(f"Couldn't find loaded world with world: {apworld_name}")


# See https://github.com/yaml/pyyaml/issues/103
yaml.SafeDumper.ignore_aliases = lambda *args: True


def _ensure_list(values):
    return values if isinstance(values, list) else [values]


def apply_constraints(game_options, constraints):
    # Collect mutually_exclusive info for requires_any filtering
    mutual_exclusions = [
        {"option": c.get("option"), "values": c["mutually_exclusive"]}
        for c in constraints if "mutually_exclusive" in c
    ]

    other_constraints = [c for c in constraints if "mutually_exclusive" not in c]

    # Run other constraints twice: once for initial processing, once for dependencies
    for _ in range(2):
        for constraint in other_constraints:
            _apply_single_constraint(game_options, constraint, mutual_exclusions)

    # Run mutually_exclusive last to resolve any conflicts created by additions
    for excl in mutual_exclusions:
        _apply_single_constraint(game_options, {"option": excl["option"], "mutually_exclusive": excl["values"]}, mutual_exclusions)

    # Run other constraints once more to fix any requirements broken by mutually_exclusive
    for constraint in other_constraints:
        _apply_single_constraint(game_options, constraint, mutual_exclusions)

    return game_options


def _apply_single_constraint(game_options, constraint, mutual_exclusions):
    option_name = constraint.get("option")
    if option_name not in game_options:
        return

    option_value = game_options[option_name]

    if "if_selected" in constraint:
        _handle_if_selected(option_value, constraint)

    elif "if_value" in constraint:
        _handle_if_value(game_options, option_value, constraint)

    elif "mutually_exclusive" in constraint:
        _handle_mutually_exclusive(option_value, constraint)

    elif "if_any_selected" in constraint and "requires_any" in constraint:
        _handle_requires_any(option_name, option_value, constraint, mutual_exclusions)

    elif "max_count_of" in constraint:
        _handle_max_count_of(game_options, option_name, option_value, constraint)

    elif "max_remaining_from" in constraint:
        _handle_max_remaining_from(game_options, option_name, option_value, constraint)

    elif "ensure_any" in constraint:
        _handle_ensure_any(option_value, constraint)


def _handle_if_selected(option_value, constraint):
    if constraint["if_selected"] not in option_value:
        return

    for val in _ensure_list(constraint.get("must_include", [])):
        if val not in option_value:
            option_value.append(val)

    for val in _ensure_list(constraint.get("must_exclude", [])):
        if val in option_value:
            option_value.remove(val)


def _handle_if_value(game_options, option_value, constraint):
    if option_value != constraint["if_value"]:
        return

    for target_option, target_value in constraint.get("then", {}).items():
        game_options[target_option] = target_value

    for target_option, excluded in constraint.get("then_exclude", {}).items():
        target = game_options[target_option]
        for val in _ensure_list(excluded):
            if val in target:
                target.remove(val)

    for target_option, included in constraint.get("then_include", {}).items():
        target = game_options[target_option]
        for val in _ensure_list(included):
            if val not in target:
                target.append(val)


def _handle_mutually_exclusive(option_value, constraint):
    present = [val for val in constraint["mutually_exclusive"] if val in option_value]
    if len(present) > 1:
        keep = random.choice(present)
        for val in present:
            if val != keep:
                option_value.remove(val)


def _handle_requires_any(option_name, option_value, constraint, mutual_exclusions):
    trigger_values = constraint["if_any_selected"]
    required_values = constraint["requires_any"]

    if not any(val in option_value for val in trigger_values):
        return
    if any(val in option_value for val in required_values):
        return

    # Filter candidates that would conflict with mutually_exclusive constraints
    candidates = list(required_values)
    for excl in mutual_exclusions:
        if excl["option"] == option_name:
            present_excl = [v for v in excl["values"] if v in option_value]
            if present_excl:
                candidates = [c for c in candidates if c not in excl["values"]]

    if not candidates:
        candidates = list(required_values)

    choice = random.choice(candidates)
    if choice not in option_value:
        option_value.append(choice)


def _handle_max_count_of(game_options, option_name, option_value, constraint):
    other_value = game_options[constraint["max_count_of"]]
    if option_value > len(other_value):
        game_options[option_name] = len(other_value)


def _handle_max_remaining_from(game_options, option_name, option_value, constraint):
    other_value = game_options[constraint["max_remaining_from"]]
    max_options = int(constraint["max_capacity"])
    if option_value > max_options - len(other_value):
        game_options[option_name] = max_options - len(other_value)


def _handle_ensure_any(option_value, constraint):
    required_values = constraint["ensure_any"]
    if not any(val in option_value for val in required_values):
        choice = random.choice(required_values)
        if choice not in option_value:
            option_value.append(choice)


# Adapted from archipelago'd generate_yaml_templates
# https://github.com/ArchipelagoMW/Archipelago/blob/f75a1ae1174fb467e5c5bd5568d7de3c806d5b1c/Options.py#L1504
def generate_random_yaml(world_name, meta):
    def dictify_range(option):
        data = {option.default: 50}
        for sub_option in ["random", "random-low", "random-high"]:
            if sub_option != option.default:
                data[sub_option] = 0

        notes = {}
        for name, number in getattr(option, "special_range_names", {}).items():
            notes[name] = f"equivalent to {number}"
            if number in data:
                data[name] = data[number]
                del data[number]
            else:
                data[name] = 0

        return data, notes

    def sanitize(value):
        if isinstance(value, frozenset):
            return list(value)
        return value

    game_name, world = world_from_apworld_name(world_name)
    if world is None:
        raise Exception(f"Failed to resolve apworld from apworld name: {world_name}")

    global_meta = meta.get(None, {})
    game_meta = meta.get(game_name, {})

    game_options = {}
    option_groups = get_option_groups(world)
    for group, options in option_groups.items():
        for option_name, option_value in options.items():
            override = global_meta.get(option_name)
            if not override:
                override = game_meta.get(option_name)

            if override is not None:
                game_options[option_name] = override
                continue

            game_options[option_name] = sanitize(
                get_random_value(option_name, option_value)
            )

    if "triggers" in game_meta:
        game_options["triggers"] = game_meta["triggers"]

    fuzz_constraints = game_meta.get("fuzz_constraints", [])
    if fuzz_constraints:
        apply_constraints(game_options, fuzz_constraints)

    yaml_content = {
        "description": f"{game_name} Template, generated with https://github.com/Eijebong/Archipelago-fuzzer/tree/{__version__}",
        "game": game_name,
        "requires": {
            "version": __ap_version__,
        },
        game_name: game_options,
    }

    if "triggers" in meta:
        yaml_content["triggers"] = meta["triggers"]

    res = yaml.safe_dump(yaml_content, sort_keys=False)

    return res


def get_random_value(name, option):
    if name == "item_links":
        # Let's not fuck with item links right now, I'm scared
        return option.default

    if name == "megamix_mod_data":
        # Megamix is a special child and requires this to be valid JSON. Since we can't provide that, just ignore it
        return option.default

    if issubclass(option, (PlandoConnections, PlandoTexts)):
        # See, I was already afraid with item_links but now it's plain terror. Let's not ever touch this ever.
        return option.default

    if name == "gfxmod":
        # XXX: LADX has this and it should be a choice but is freetext for some reason...
        # Putting invalid values here means the gen fails even though it doesn't affect any logic
        # Just return Link for now.
        return "Link"

    if issubclass(option, OptionCounter):
        # ItemDict subclasses like StartInventory might not have valid_keys and
        # instead rely on verify_item_name for runtime validation against world.item_names
        if not option.valid_keys:
            return option.default
        selected_keys = random.sample(
            list(option.valid_keys),
            k=random.randint(0, len(option.valid_keys))
        )
        min_val = option.min if option.min is not None else 0
        max_val = option.max if option.max is not None else 1000
        return {key: random.randint(min_val, max_val) for key in selected_keys}

    if issubclass(option, OptionDict):
        # This is for example factorio's start_items and worldgen settings. I don't think it's worth randomizing those as I'm not expecting the generation outcome to change from them.
        # Plus I have no idea how to randomize them in the first place :)
        return option.default

    if issubclass(option, (Choice, Toggle)):
        valid_choices = [key for key in option.options.keys() if key not in option.aliases]
        if not valid_choices:
            valid_choices = list(option.options.keys())

        return random.choice(valid_choices)

    if issubclass(option, Range):
        return random.randint(option.range_start, option.range_end)

    if issubclass(option, (ItemSet, LocationSet)):
        # I don't know what to do here so just return the default value instead of a random one.
        # This affects options like local items, non local items so it's not the end of the world
        # if they don't get randomized but we might want to look into that later on
        return option.default

    if issubclass(option, OptionSet):
        return random.sample(
            list(option.valid_keys), k=random.randint(0, len(option.valid_keys))
        )

    if issubclass(option, OptionList):
        return random.sample(
            list(option.valid_keys), k=random.randint(0, len(option.valid_keys))
        )

    if issubclass(option, NumericOption):
        return option("random").value

    if issubclass(option, FreeText):
        special_symbols = '&<>"\'\\/@#$%^*()[]{}|;:,.'
        whitespace = ' \t\n'
        multibyte_utf8 = (
            'ÀÁÂÃÄÅÆÇÈÉÊËΒΓΔбвг'
            '中文日本語한글'
            '🎮🎯🎲🔥💀𝕳𝖊𝖑𝖑𝖔'
        )

        all_chars = string.ascii_letters + string.digits + special_symbols + whitespace + multibyte_utf8

        return "".join(random.choice(all_chars) for _ in range(random.randint(0, 255)))

    return option.default


def call_generate(yaml_path, args, output_path):
    from settings import get_settings

    settings = get_settings()

    args = Namespace(
        **{
            "weights_file_path": settings.generator.weights_file_path,
            "sameoptions": False,
            "player_files_path": yaml_path,
            "seed": random.randint(0, 1000000000),
            "multi": 1,
            "spoiler": 1,
            "outputpath": output_path,
            "race": False,
            "meta_file_path": "meta-doesnt-exist.yaml",
            "log_level": "info",
            "yaml_output": 1,
            "plando": PlandoOptions.items | PlandoOptions.connections | PlandoOptions.texts | PlandoOptions.bosses,
            "skip_prog_balancing": False,
            "skip_output": args.skip_output,
            "csv_output": False,
            "log_time": False,
            "spoiler_only": False,
        }
    )
    for hook in MP_HOOKS:
        hook.before_generate(args)

    erargs, seed = GenMain(args)
    return ERmain(erargs, seed)


def gen_wrapper(yaml_path, apworld_name, i, args, queue, tmp):
    global MP_HOOKS

    out_buf = StringIO()

    timer = None
    if args.timeout > 0:
        myself = os.getpid()
        def stop():
            queue.put_nowait((myself, apworld_name, i, yaml_path, out_buf))
            queue.join()
        timer = threading.Timer(args.timeout, stop)


    raised = None
    mw = None

    try:
        with redirect_stdout(out_buf), redirect_stderr(out_buf), tempfile.TemporaryDirectory(prefix="apfuzz", dir=tmp) as output_path:
            try:
                # If we have hooks defined in args but they're not registered yet, register them
                if args.hook and not MP_HOOKS:
                    for hook_class_path in args.hook:
                        hook = find_hook(hook_class_path)
                        hook.setup_worker(args)
                        MP_HOOKS.append(hook)

                if timer:
                    timer.start()

                mw = call_generate(yaml_path, args, output_path)
            except Exception as e:
                raised = e
            finally:
                try:
                    for hook in MP_HOOKS:
                        hook.after_generate(mw, output_path)
                finally:
                    # Make sure to always stop the timeout timer, whatever happens
                    # If we don't, the timer could fire while we're stopping AP or
                    # dumping YAMLs, and that would be bad.
                    if timer is not None:
                        timer.cancel()
                        if timer.ident is not None:
                            timer.join()

                    clear_abc_caches()

                root_logger = logging.getLogger()
                handlers = root_logger.handlers[:]
                for handler in handlers:
                    root_logger.removeHandler(handler)
                    handler.close()

                outcome = GenOutcome.Success
                if raised:
                    is_timeout = isinstance(raised, TimeoutError)
                    is_option_error = exception_in_causes(raised, OptionError)

                    if is_timeout:
                        outcome = GenOutcome.Timeout
                    elif is_option_error:
                        outcome = GenOutcome.OptionError
                    else:
                        outcome = GenOutcome.Failure

                for hook in MP_HOOKS:
                    outcome, raised = hook.reclassify_outcome(outcome, raised)

                if outcome == GenOutcome.Success:
                    return outcome

                if outcome == GenOutcome.OptionError and not args.dump_ignored:
                    return outcome

                if outcome == GenOutcome.Timeout:
                    extra = f"[...] Generation killed here after {args.timeout}s"
                else:
                    extra = "".join(traceback.format_exception(raised))

                dump_generation_output(outcome, apworld_name, i, yaml_path, out_buf, extra)

                return outcome, raised
    except Exception as e:
        raise FuzzerException("Fuzzer error", out_buf) from e


def dump_generation_output(outcome, apworld_name, i, yamls_dir, out_buf, extra=None):
    if outcome == GenOutcome.Success:
        return

    if outcome == GenOutcome.OptionError:
        error_ty = "ignored"
    elif outcome == GenOutcome.Timeout:
        error_ty = "timeout"
    else:
        error_ty = "error"

    error_output_dir = os.path.join(OUT_DIR, error_ty, apworld_name, str(i))
    os.makedirs(error_output_dir)

    for yaml_file in os.listdir(yamls_dir):
        shutil.copy(os.path.join(yamls_dir, yaml_file), error_output_dir)

    error_log_path = os.path.join(error_output_dir, f"{i}.log")
    with open(error_log_path, "w", encoding='utf-8') as fd:
        fd.write(out_buf.getvalue())
        if extra is not None:
            fd.write(extra)


class GenOutcome:
    Success = 0
    Failure = 1
    Timeout = 2
    OptionError = 3


IS_TTY = sys.stdout.isatty()
SUCCESS = 0
FAILURE = 0
TIMEOUTS = 0
OPTION_ERRORS = 0
SUBMITTED = 0
REPORT = defaultdict(lambda: defaultdict(lambda: defaultdict(lambda: [])))


def gen_callback(yamls_dir, apworld_name, i, args, outcome):
    try:
        if isinstance(outcome, tuple):
            outcome, exc = outcome
        else:
            exc = None

        global SUCCESS, FAILURE, SUBMITTED, OPTION_ERRORS, TIMEOUTS
        SUBMITTED -= 1

        if outcome == GenOutcome.Success:
            SUCCESS += 1
            if IS_TTY:
                print(".", end="")
        elif outcome == GenOutcome.Failure:
            REPORT[apworld_name][type(exc)][str(exc)].append(i)
            FAILURE += 1
            if IS_TTY:
                print("F", end="")
        elif outcome == GenOutcome.Timeout:
            REPORT[apworld_name][TimeoutError][""].append(i)
            TIMEOUTS += 1
            if IS_TTY:
                print("T", end="")
        elif outcome == GenOutcome.OptionError:
            OPTION_ERRORS += 1
            if IS_TTY:
                print("I", end="")

        # If we're not on a TTY, print progress every once in a while
        if not IS_TTY:
            checks_done = SUCCESS + FAILURE + TIMEOUTS + OPTION_ERRORS
            step = args.runs // 50
            if step == 0 or (checks_done % step) == 0:
                print(f"{checks_done} / {args.runs} done. {FAILURE} failures, {TIMEOUTS} timeouts, {OPTION_ERRORS} ignored.")

        sys.stdout.flush()
        try:
            # Technically not useful but this will prevent me from removing things I don't want when I inevitably mix up the args somewhere...
            if 'apfuzz' in yamls_dir:
                shutil.rmtree(yamls_dir)
        except: # noqa: E722
            pass
    except Exception as e:
        print("Error while handling fuzzing result:")
        traceback.print_exception(e)
        print("This is most likely a fuzzer bug and should be reported")


def error(yamls_dir, apworld_name, i, args, raised):
    try:
        msg = StringIO()
        if isinstance(raised, FuzzerException):
            msg.write(raised.out_buf)
        msg.write("\n".join(traceback.format_exception(raised)))

        dump_generation_output(GenOutcome.Failure, apworld_name, i, yamls_dir, msg)
        return gen_callback(yamls_dir, apworld_name, i, args, GenOutcome.Failure)
    except Exception as e:
        print("Error while handling fuzzing result:")
        traceback.print_exception(e)
        print("This is most likely a fuzzer bug and should be reported")


def print_status():
    print()
    print("Success:", SUCCESS)
    print("Failures:", FAILURE)
    print("Timeouts:", TIMEOUTS)
    print("Ignored:", OPTION_ERRORS)
    print()
    print("Time taken:{:.2f}s".format(time.perf_counter() - START))


def find_hook(hook_path):
    modulepath, objectpath = hook_path.split(':')
    obj = importlib.import_module(modulepath)
    for inner in objectpath.split('.'):
        obj = getattr(obj, inner)

    if not isinstance(obj, type):
        raise RuntimeError("the hook argument should refer to a class in a module")

    if issubclass(obj, BaseHook):
        raise RuntimeError("the hook {} is not a subclass of `fuzz.BaseHook`)".format(hook_path))

    return obj()


class BaseHook:
    def setup_main(self, args):
        """
        This function is guaranteed to only ever be called once, in the main process.
        """
        pass

    def setup_worker(self, args):
        """
        This function is guaranteed to only ever be called once per worker process. It can be used to load extra apworlds for example.
        """
        pass

    def reclassify_outcome(self, outcome, raised):
        """
        This function is called once after a generation outcome has been decided.
        You can reclassify the outcome with this before it is returned to the main process by returning a new `GenOutcome`
        Note that because timeouts are processed by the main process and not by the worker itself (as it is busy timing out),
        this function can be called from both the main process and the workers.
        """
        return outcome, raised

    def before_generate(self, args):
        pass

    def after_generate(self, mw, output_path):
        pass

    def finalize(self):
        pass


def write_report(report):
    errors = {}

    for game_name, game_report in report.items():
        errors[game_name] = defaultdict(lambda: [])

        for exc_type, exc_report in game_report.items():
            for exc_str, yamls in exc_report.items():
                if exc_type == FillError:
                    errors[game_name]["FillError"].extend(yamls)
                else:
                    if exc_str:
                        errors[game_name][exc_str].extend(yamls)
                    else:
                        errors[game_name][str(exc_type)].extend(yamls)

    stats = {
        "total": SUCCESS + FAILURE + TIMEOUTS + OPTION_ERRORS,
        "success": SUCCESS,
        "failure": FAILURE,
        "timeout": TIMEOUTS,
        "ignored": OPTION_ERRORS,
    }

    computed_report = {"stats": stats, "errors": errors}

    with open(os.path.join(OUT_DIR, "report.json"), "w", encoding='utf-8') as fd:
        fd.write(json.dumps(computed_report))


if __name__ == "__main__":
    MAIN_HOOKS = []

    def main(p, args, tmp):
        global SUBMITTED

        apworld_name = args.game
        if args.meta:
            with open(args.meta, "r", encoding='utf-8-sig') as fd:
                meta = yaml.safe_load(fd.read())
        else:
            meta = {}

        if apworld_name is not None:
            world = world_from_apworld_name(apworld_name)
            if world is None:
                raise Exception(
                    f"Failed to resolve apworld from apworld name: {apworld_name}"
                )

        if os.path.exists(OUT_DIR):
            shutil.rmtree(OUT_DIR)
        os.makedirs(OUT_DIR)

        for hook_class_path in args.hook:
            hook = find_hook(hook_class_path)
            hook.setup_main(args)

            MAIN_HOOKS.append(hook)

        sys.stdout.write("\x1b[2J\x1b[H")
        sys.stdout.flush()

        i = 0
        valid_worlds = [
            world.__module__.split(".")[1]
            for world in AutoWorldRegister.world_types.values()
        ]
        if "apsudoku" in valid_worlds:
            valid_worlds.remove("apsudoku")

        yamls_per_run_bounds = [int(arg) for arg in args.yamls_per_run.split("-")]

        if len(yamls_per_run_bounds) not in {1, 2}:
            raise Exception(
                "Invalid value passed for `yamls_per_run`. Either pass an int or a range like `1-10`"
            )

        if len(yamls_per_run_bounds) == 2:
            if yamls_per_run_bounds[0] >= yamls_per_run_bounds[1]:
                raise Exception("Invalid range value passed for `yamls_per_run`.")

        static_yamls = []
        if args.with_static_worlds:
            for yaml_file in os.listdir(args.with_static_worlds):
                path = os.path.join(args.with_static_worlds, yaml_file)
                if not os.path.isfile(path):
                    continue
                with open(path, "r", encoding='utf-8-sig') as fd:
                    static_yamls.append(fd.read())


        global MANAGER
        MANAGER = multiprocessing.Manager()
        queue = MANAGER.Queue(1000)
        def handle_timeouts():
            while True:
                try:
                    pid, apworld_name, i, yamls_dir, out_buf = queue.get()
                    os.kill(pid, signal.SIGTERM)

                    extra = f"[...] Generation killed here after {args.timeout}s"
                    outcome = GenOutcome.Timeout
                    for hook in MAIN_HOOKS:
                        outcome, _ = hook.reclassify_outcome(outcome, TimeoutError())
                    dump_generation_output(outcome, apworld_name, i, yamls_dir, out_buf, extra)
                    gen_callback(yamls_dir, apworld_name, i, args, outcome)
                except KeyboardInterrupt:
                    break
                except EOFError:
                    break
                except Exception as exc:
                    extra = "[...] Exception while timing out:\n {}".format("\n".join(traceback.format_exception(exc)))
                    dump_generation_output(GenOutcome.Timeout, apworld_name, i, yamls_dir, out_buf, extra)
                    gen_callback(yamls_dir, apworld_name, i, args, outcome)
                    continue

        timeout_handler = threading.Thread(target=handle_timeouts)
        timeout_handler.daemon = True
        timeout_handler.start()

        while i < args.runs:
            if apworld_name is None:
                actual_apworld = random.choice(valid_worlds)
            else:
                actual_apworld = apworld_name

            if len(yamls_per_run_bounds) == 1:
                yamls_this_run = yamls_per_run_bounds[0]
            else:
                # +1 here to make the range inclusive
                yamls_this_run = random.randrange(
                    yamls_per_run_bounds[0], yamls_per_run_bounds[1] + 1
                )

            random_yamls = [
                generate_random_yaml(actual_apworld, meta) for _ in range(yamls_this_run)
            ]

            if i % 100 == 0:
                clear_abc_caches()

            SUBMITTED += 1

            yamls_dir = tempfile.mkdtemp(prefix="apfuzz", dir=tmp)
            for nb, yaml_content in enumerate(random_yamls):
                yaml_path = os.path.join(yamls_dir, f"{i}-{nb}.yaml")
                open(yaml_path, "wb").write(yaml_content.encode("utf-8"))

            for nb, yaml_content in enumerate(static_yamls):
                yaml_path = os.path.join(yamls_dir, f"static-{i}-{nb}.yaml")
                open(yaml_path, "wb").write(yaml_content.encode("utf-8"))

            last_job = p.apply_async(
                gen_wrapper,
                args=(yamls_dir, actual_apworld, i, args, queue, tmp),
                callback=functools.partial(gen_callback, yamls_dir, actual_apworld, i, args),
                error_callback=functools.partial(error, yamls_dir, actual_apworld, i, args),
            )

            while SUBMITTED >= args.jobs * 10:
                # Poll the last job to keep the queue running
                last_job.ready()
                time.sleep(0.001)

            i += 1

        while SUBMITTED > 0:
            last_job.ready()
            time.sleep(0.05)

    parser = ArgumentParser(prog="apfuzz")
    parser.add_argument("-g", "--game", default=None)
    parser.add_argument("-j", "--jobs", default=10, type=int)
    parser.add_argument("-r", "--runs", type=int, required=True)
    parser.add_argument("-n", "--yamls_per_run", default="1", type=str)
    parser.add_argument("-t", "--timeout", default=15, type=int)
    parser.add_argument("-m", "--meta", default=None, type=None)
    parser.add_argument("--dump-ignored", default=False, action="store_true")
    parser.add_argument("--with-static-worlds", default=None)
    parser.add_argument("--hook", action="append", default=[])
    parser.add_argument("--skip-output", default=False, action="store_true")

    args = parser.parse_args()

    # This is just to make sure that the host.yaml file exists by the time we fork
    # so that a first run on a new installation doesn't throw out failures until
    # the host.yaml from the first gen is written
    get_settings()
    crashed = False
    try:
        can_fork = hasattr(os, "fork")
        # fork here is way faster because it doesn't have to reload all worlds, but it's only available on some platforms
        # forking for every job also has the advantage of being sure that the process is "clean". Although I don't know if that actually matters
        start_method = "fork" if can_fork else "spawn"
        multiprocessing.set_start_method(start_method)
        tmp = tempfile.TemporaryDirectory(prefix="apfuzz")
        with Pool(processes=args.jobs, maxtasksperchild=None) as p:
            START = time.perf_counter()
            main(p, args, tmp.name)
    except KeyboardInterrupt:
        pass
    except Exception as e:
        crashed = True
        traceback.print_exc()
    finally:
        for hook in MAIN_HOOKS:
            hook.finalize()

        tmp.cleanup()

        if MANAGER is not None:
            MANAGER._process.kill()

        if not crashed:
            print_status()
            write_report(REPORT)
            os._exit((FAILURE + TIMEOUTS) != 0)

        os._exit(2)

