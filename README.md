Archipelago fuzzer
==================

This is a fairly dumb fuzzer that will generate multiworlds with N random YAMLs and record failures.

## How to run this?

You need to run archipelago from source. Copy the `fuzz.py` at the root of the archipelago project, you can then run the fuzzer like this:

```
python3 fuzz.py -r 100 -j16 -g alttp -n1
```

This will run 100 tests on the alttp world, with 1 YAML per generation, using 16 jobs.
The output will be available in `./fuzz_output`.

> [!IMPORTANT]
> This will blow up your `logs` folder in your archipelago installation as
> every generation generates a file there. Make sure to clean it up regularly

## Flags

- `-g` selects the apworld to fuzz. If omitted, every run will take a random loaded world
- `-j` specifies the number of jobs to run in parralel. Defaults to 10, recommended value is the number of cores of your CPU.
- `-r` specifies the number of generations to do. This is a mandatory setting
- `-n` specifies how many YAMLs to use per generation. Defaults to 1.
- `-t` specifies the maximum time per generation in seconds. Defaults to 15s.

