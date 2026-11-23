#!/usr/bin/env python3
"""Compile the optional zygote-fork Node addon with the system C compiler (Linux/glibc only)."""
import argparse
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile

DEFAULT_SOURCE = Path(__file__).resolve().parents[1] / "packages/vitest-adapter/native/zygote-fork.c"
DEFAULT_OUTPUT = DEFAULT_SOURCE.parent / "build/zygote-fork.node"


def is_glibc():
    try:
        return os.confstr("CS_GNU_LIBC_VERSION").startswith("glibc")
    except (ValueError, OSError, AttributeError):
        return False


def build_addon(source, output, *, node=None, cc=None, timeout_seconds=120):
    """Compile source to output. Returns (built, reason); never raises for an unavailable toolchain."""
    source, output = Path(source), Path(output)
    if not sys.platform.startswith("linux"):
        return False, f"unsupported platform {sys.platform}"
    if not is_glibc():
        return False, "libc is not glibc"
    compiler = shutil.which(cc or os.environ.get("CC") or "cc")
    if compiler is None:
        return False, "no C compiler found"
    node_path = shutil.which(node or "node")
    if node_path is None:
        return False, "node not found"
    prefix = Path(os.path.realpath(node_path)).parent.parent
    include = prefix / "include" / "node"
    for header in ("node_api.h", "uv.h"):
        if not (include / header).is_file():
            return False, f"missing {include / header}"
    if not source.is_file():
        return False, f"missing source {source}"
    try:
        output.parent.mkdir(parents=True, exist_ok=True)
        descriptor, temporary = tempfile.mkstemp(prefix=".zygote-fork-", suffix=".node", dir=output.parent)
        os.close(descriptor)
    except OSError as error:
        return False, f"cannot write {output.parent}: {error}"
    try:
        result = subprocess.run(
            [compiler, "-shared", "-fPIC", "-O2", "-Wall", f"-I{include}", "-o", temporary, str(source)],
            stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, timeout=timeout_seconds)
        if result.returncode != 0:
            tail = "\n".join(result.stderr.strip().splitlines()[-5:])
            return False, f"compiler exited {result.returncode}: {tail}"
        os.replace(temporary, output)
        return True, "built"
    except subprocess.TimeoutExpired:
        return False, f"compiler timed out after {timeout_seconds:g} seconds"
    except OSError as error:
        return False, f"compiler failed to run: {error}"
    finally:
        try:
            os.unlink(temporary)
        except FileNotFoundError:
            pass


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    arguments = parser.parse_args()
    built, reason = build_addon(DEFAULT_SOURCE, arguments.output)
    print(f"built: {arguments.output}" if built else f"skipped: {reason}")
