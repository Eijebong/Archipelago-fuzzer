Archipelago fuzzer
==================

This is a fairly dumb fuzzer that will generate multiworlds with N random YAMLs and record failures.

## How to run this?

You need to run archipelago from source. If you don't know how to do that, there's documentation from the archipelago project [here](https://github.com/ArchipelagoMW/Archipelago/blob/main/docs/running%20from%20source.md)

Copy the `fuzz.py` file at the root of the archipelago project, you can then run the fuzzer like any other archipelago entry point:

```
python fuzz.py -r 100 -j 16 -g alttp -n 1
```

This will run 100 tests on the alttp world, with 1 YAML per generation, using 16 jobs.
The output will be available in `./fuzz_output`.

## Flags

- `-g` selects the apworld to fuzz. If omitted, every run will take a random loaded world
- `-j` specifies the number of jobs to run in parallel. Defaults to 10, recommended value is the number of cores of your CPU.
- `-r` specifies the number of generations to do. This is a mandatory setting
- `-n` specifies how many YAMLs to use per generation. Defaults to 1. You can
  also specify ranges like `1-10` to make all generations pick a number between
  1 and 10 YAMLs.
- `-t` specifies the maximum time per generation in seconds. Defaults to 15s.
- `-m` to specify a meta file that overrides specific values
- `--skip-output` specifies to skip the output step of generation.
- `--dump-ignored` makes it so option errors are also dumped in the result.
- `--with-static-worlds` takes a path to a directory containing YAML to include in every generation. Not recursive.
- `--hook` takes a `module:class` string to a hook and can be specified multiple times. More information about that below

## Meta files

You can force some options to always be the same value by providing a meta file via the `-m` flag.
The syntax is very similar to the archipelago meta.yaml syntax:

```yaml
null:
  progression_balancing: 50
Pokemon FireRed and LeafGreen:
  ability_blacklist: []
  move_blacklist: []
```

Note that unlike an archipelago meta file, this will override the values in the
generated YAML, there's no implicit application of options at generation time
so you don't need to provide the meta file to report bugs.

### Triggers

You can also define [triggers](https://archipelago.gg/tutorial/Archipelago/triggers/en)
in meta files. They can be per-game or global.

```yaml
triggers:
  - option_category: Pokemon FireRed and LeafGreen
    option_name: some_option
    option_result: trigger_value
    options:
      Pokemon FireRed and LeafGreen:
        target_option: forced_value

Pokemon FireRed and LeafGreen:
  triggers:
    - option_name: source_option
      option_result: trigger_value
      options:
        Pokemon FireRed and LeafGreen:
          target_option: forced_value
```

**Caveat:** Archipelago triggers only fire when the value matches exactly what
is in the YAML. This can cause some confusion when the option keys don't match
the value you would expect. For example toggles need to be matched to
`'true'`/`'false'` instead of `true`/`false` as archipelago doesn't interpret
the trigger values before comparing them to what got rolled.

### Fuzz Constraints

Constraints prevent the fuzzer from generating invalid option combinations.
They are defined under the `fuzz_constraints` key in a game's meta file and can
be used when archipelago triggers are not good enough.

#### `if_selected` + `must_include` / `must_exclude`

When a value is selected, require or forbid other values in the same option.

```yaml
- option: included_levels
  if_selected: "Hard Mode"
  must_include: "Tutorial"
  must_exclude: "Easy Skip"
```

#### `if_value` + `then` / `then_exclude` / `then_include`

When an option has a specific value, set other options or modify their contents.

```yaml
- option: difficulty
  if_value: expert
  then:
    hints: false
  then_exclude:
    levels: ["Tutorial", "Practice"]
  then_include:
    levels: ["Boss Rush"]
```

#### `if_any_selected` + `requires_any`

When any trigger value is selected, ensure at least one required value is present.

```yaml
- option: sanities
  if_any_selected: ["KeySanity", "CheckpointSanity"]
  requires_any: ["Act A", "Act B"]
```

#### `mutually_exclusive`

Values that cannot coexist. One is randomly kept when both are present.

```yaml
- option: modes
  mutually_exclusive: ["Hard Mode", "Easy Mode"]
```

#### `max_count_of`

Cap a numeric option to the size of another option.

```yaml
- option: num_gates
  max_count_of: allowed_bosses
```

#### `max_remaining_from`

Cap a numeric option so that the total of this option and the size of another option does not exceed a fixed maximum capacity.

```yaml
- option: num_required_levels
  max_remaining_from: excluded_levels
  max_capacity: 20
```

#### `sum_cap`

Cap a set of numeric so that their sum does not exceed a fixed maximum capacity.

```yaml
# sum of base_items, and extra_items cannot exceed 20
- sum_cap:
      - base_items
      - extra_items
  max_capacity: 20
```

#### `ensure_any`

At least one of these values must be present.

```yaml
- option: included_levels
  ensure_any: ["World 1", "World 2", "World 3"]
```

## Hooks

To repurpose the fuzzer for some specific bug testing, it can be useful to
monkeypatch archipelago before generation and/or to reclassify some failures.
That's where a hook comes in.

You can declare a class like this one in a file alongside `fuzz.py` in your
archipelago installation:

```py
from fuzz import BaseHook, GenOutcome

class Hook(BaseHook):
    def setup_main(self, args):
        """
        The args parameter is the `Namespace` containing the parsed arguments from the CLI.
        setup is classed as early as possible after argument parsing in the
        main process. It is guaranteed to be only ever called once. It will
        always be called before any worker process is started
        """
        pass

    def setup_worker(self, args):
        """
        The args parameter is the `Namespace` containing the parsed arguments from the CLI.
        setup is classed as early as possible after starting a worker process.
        It is guaranteed to only ever be called once per worker process, before
        any generation attempt.
        """
        pass

    def reclassify_outcome(self, outcome, exception):
        """
        The outcome is a `GenOutcome` from generation.
        The exception is the exception raised during generation if one happened, None otherwise.

        This function is called in the worker process just after the result is first decided.
        The one exception is for timeouts where the outcome has to be processed on the main process.
        As such, this function must do very minimal work and not make
        assumptions as whether it's running in worker or in the main process.
        """
        return GenOutcome.Success, exception

    def before_generate(self, args):
        """
        This method will be called once per generation, just before we actually
        call into archipelago. The `args` argument contains the `Namespace`
        object passed to archipelago for generation. It can be modified since
        this happens before generation.
        """
        pass

    def after_generate(self, multiworld, output_dir):
        """
        This method will be called once per generation except if the generation timed out.
        If you need to inspect the failure, use `reclassify_outcome` instead.
        If the generation succeeds, multiworld is the object returned by
        archipelago on success, otherwise it's None
        """
        pass

    def finalize(self):
        """
        This method will be called once just before the main process exits. It
        will only be called on the main process
        """
        pass
```

You can then pass the following argument: `--hook your_file:Hook`, note that it should be the name of your file, without the extension.
The `hooks` folder in this repository contains examples of some usage that I personally made of hooks.

### Profiler hook

You can get a profile in a callgrind format by using the provided `profile` hook.

Example:

```
python -O fuzz.py -r 1000 -n 1 -g pokemon_crystal -j24 --hook hooks.profile:Hook
```

The output (`fuzz_output/full.prof`) can be read with a tool such as `qcachegrind`.

### Determinism hook

You can check for generation determinism with the provided `determinism` hook.

> [!IMPORTANT]
> Because the hook creates a subworker to get a second generation, it is
> recommended to run this with half of the usual number of jobs and double the
> timeout.

Example

```
python -O fuzz.py -r 1000 -n 1 -g pokemon_crystal -j12 --hook hooks.determinism:Hook
```

Any failure that isn't a determinism issue will be considered as ignored.
