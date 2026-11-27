#!/usr/bin/env python3
"""Enable Oximut's adapter when npm installs it under node_modules."""
import os
from pathlib import Path
import sys


if __name__ == "__main__":
    bin_dir = Path(__file__).resolve().parent
    preload = bin_dir.parent / "lib/oximut/packages/vitest-adapter/npm-typescript.mjs"
    flags = os.environ.get("NODE_OPTIONS", "")
    os.environ["NODE_OPTIONS"] = f"{flags} --import={preload.as_uri()}".strip()
    launcher = str(bin_dir / "oximut")
    os.execv(launcher, [launcher, *sys.argv[1:]])
