Archipelago fuzzer
==================

This is a fairly dumb fuzzer that will generate multiworlds with N random YAMLs and record failures.

## How to run this?

You need to run archipelago from source. Copy the `fuzz.py` at the root of the archipelago project, you can then run the fuzzer like this:

```
python3 fuzz.py -r 100 -j 16 -g alttp -n 1
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

