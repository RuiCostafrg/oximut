#!/usr/bin/env python3
"""Build a locked source installation; never publish to a registry."""
import argparse
import os
from pathlib import Path
import shutil
import subprocess
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
    # Explicit target-dir keeps artifact lookup deterministic even with Cargo config.
    target = repo / "target"
    subprocess.run([
        "cargo", "build", "--locked", "--release", "-p", "oximut-cli",
        "--bin", "oximut", "--target-dir", str(target),
    ], cwd=repo, check=True)
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
        shutil.copy2(target / "release" / "oximut", stage / "oximut")
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
