#!/usr/bin/env python3
"""Locate the source installation's native binary and its matching adapter."""
import os
from pathlib import Path
import sys


def command(prefix, arguments):
    bundle = prefix / "lib" / "oximut"
    execution = "fresh"
    # Consume values exactly as the native CLI does, including repeated options.
    takes_value = {
        "--root", "--config", "--source", "--report", "--timeout-ms",
        "--adapter", "--execution", "--order-seed", "--cache-input",
        "--cache-env", "--workers", "--memory-budget-mib", "--memory-reserve-mib",
    }
    explicit_adapter = False
    index = 0
    while index < len(arguments):
        argument = arguments[index]
        if argument in takes_value:
            if index + 1 < len(arguments):
                if argument == "--execution":
                    execution = arguments[index + 1]
                if argument == "--adapter":
                    explicit_adapter = True
            index += 2
        else:
            index += 1
    defaults = []
    if not explicit_adapter:
        runner = "m2-runner.mjs" if execution == "persistent" else "m1-runner.mjs"
        defaults = ["--adapter", str(bundle / "packages" / "vitest-adapter" / runner)]
    return [str(bundle / "oximut"), *defaults, *arguments]


if __name__ == "__main__":
    argv = command(Path(__file__).resolve().parent.parent, sys.argv[1:])
    os.execv(argv[0], argv)
