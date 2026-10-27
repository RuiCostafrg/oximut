#!/usr/bin/env python3
"""Build a locked source installation; never publish to a registry."""
import argparse
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile


def install(prefix):
    repo = Path(__file__).resolve().parents[1]
    prefix = prefix.expanduser().resolve()
    launcher = prefix / "bin" / "oximut"
    bundle = prefix / "lib" / "oximut"
    if os.path.lexists(launcher) or os.path.lexists(bundle):
        raise SystemExit(f"Refusing to replace existing installation: {prefix}")
    (prefix / "lib").mkdir(parents=True, exist_ok=True)
    (prefix / "bin").mkdir(parents=True, exist_ok=True)
    # Cargo reports the actual executable even when build.target is configured.
    target = repo / "target"
    build = subprocess.run([
        "cargo", "build", "--locked", "--release", "-p", "oximut-cli",
        "--bin", "oximut", "--target-dir", str(target),
        "--message-format=json-render-diagnostics",
    ], cwd=repo, stdout=subprocess.PIPE, text=True)
    executables = set()
    for line in build.stdout.splitlines():
        message = json.loads(line)
        if message.get("reason") == "compiler-message":
            rendered = message.get("message", {}).get("rendered")
            if rendered:
                print(rendered, end="", file=sys.stderr)
        if (message.get("reason") == "compiler-artifact"
                and message.get("target", {}).get("name") == "oximut"
                and "bin" in message.get("target", {}).get("kind", [])
                and message.get("executable")):
            executables.add(Path(message["executable"]))
    build.check_returncode()
    if len(executables) != 1:
        raise SystemExit("Cargo did not report exactly one oximut executable")
    executable = executables.pop()
    if not executable.is_absolute():
        executable = repo / executable
    if not executable.is_file():
        raise SystemExit(f"Cargo executable does not exist: {executable}")
    with tempfile.TemporaryDirectory(prefix=".oximut-install-", dir=prefix / "lib") as temporary:
        stage = Path(temporary) / "bundle"
        stage.mkdir()
        for name in ("package.json", "package-lock.json", ".npmrc", "LICENSE"):
            shutil.copy2(repo / name, stage / name)
        shutil.copytree(repo / "packages" / "vitest-adapter", stage / "packages" / "vitest-adapter",
                        ignore=shutil.ignore_patterns("node_modules", ".experiment-*", "__pycache__"))
        # Full lockfile intentionally includes developer tools until a standalone
        # adapter package is released. Never silently reuse checkout node_modules.
        subprocess.run(["npm", "ci", "--include=dev", "--ignore-scripts", "--no-audit", "--no-fund"],
                       cwd=stage, check=True)
        shutil.copy2(executable, stage / "oximut")
        staged_launcher = Path(temporary) / "launcher"
        shutil.copy2(repo / "scripts" / "oximut-launcher.py", staged_launcher)
        staged_launcher.chmod(0o755)
        # All expensive/fallible preparation is complete before exposing a command.
        stage.rename(bundle)
        try:
            staged_launcher.rename(launcher)
        except BaseException:
            shutil.rmtree(bundle)
            raise
    print(f"Installed {launcher}")
    print(f"Add {prefix / 'bin'} to PATH; Node and Python must remain available.")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--prefix", type=Path, default=Path.home() / ".local")
    install(parser.parse_args().prefix)
