#!/usr/bin/env python3
"""Build a locked source installation; never publish to a registry."""
import argparse
import json
import math
import os
from pathlib import Path
import shutil
import signal
import subprocess
import sys
import tempfile


def positive_seconds(value):
    try:
        seconds = float(value)
    except (TypeError, ValueError):
        raise argparse.ArgumentTypeError("timeout must be a positive finite number") from None
    if not math.isfinite(seconds) or seconds <= 0:
        raise argparse.ArgumentTypeError("timeout must be a positive finite number")
    return seconds


def run_phase(command, *, cwd, timeout_seconds, phase, capture_output=False):
    process = subprocess.Popen(command, cwd=cwd, start_new_session=True,
                               stdout=subprocess.PIPE if capture_output else None, text=True)
    try:
        stdout, _ = process.communicate(timeout=timeout_seconds)
    except BaseException as error:
        # Own a session so a timeout stops rustc/npm descendants as well as Cargo/npm.
        while True:
            try:
                os.killpg(process.pid, signal.SIGKILL)
                break
            except InterruptedError:
                continue
            except ProcessLookupError:
                break
            except OSError as termination_error:
                # A group signal failure must still attempt to reap our child.
                try:
                    process.kill()
                except OSError:
                    pass
                # Do not wait on pipes which an unsignalled descendant may own.
                for pipe in (process.stdin, process.stdout, process.stderr):
                    if pipe is not None:
                        try:
                            pipe.close()
                        except OSError:
                            pass
                try:
                    process.wait(timeout=5)
                except (OSError, subprocess.TimeoutExpired) as reap_error:
                    raise SystemExit(
                        f"{phase}: process-group termination failed; "
                        f"direct-child cleanup failed: {reap_error}") from termination_error
                raise SystemExit(
                    f"{phase}: process-group termination failed: {termination_error}") from termination_error
        process.communicate()  # Reap the direct child before staging is removed.
        if isinstance(error, subprocess.TimeoutExpired):
            raise SystemExit(f"{phase} timed out after {timeout_seconds:g} seconds") from None
        raise
    return subprocess.CompletedProcess(command, process.returncode, stdout)


def install(prefix, *, build_timeout_seconds=3600, npm_timeout_seconds=900):
    # Validate programmatic callers too, before creating anything in the prefix.
    build_timeout_seconds = positive_seconds(build_timeout_seconds)
    npm_timeout_seconds = positive_seconds(npm_timeout_seconds)
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
    build = run_phase([
        "cargo", "build", "--locked", "--release", "-p", "oximut-cli",
        "--bin", "oximut", "--target-dir", str(target),
        "--message-format=json-render-diagnostics",
    ], cwd=repo, timeout_seconds=build_timeout_seconds, phase="Cargo build", capture_output=True)
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
        npm = run_phase(["npm", "ci", "--include=dev", "--ignore-scripts", "--no-audit", "--no-fund"],
                        cwd=stage, timeout_seconds=npm_timeout_seconds, phase="npm ci")
        npm.check_returncode()
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
    parser.add_argument("--build-timeout-seconds", type=positive_seconds, default=3600,
                        help="Cargo build deadline in seconds (default: 3600)")
    parser.add_argument("--npm-timeout-seconds", type=positive_seconds, default=900,
                        help="npm ci deadline in seconds (default: 900)")
    args = parser.parse_args()
    install(args.prefix, build_timeout_seconds=args.build_timeout_seconds,
            npm_timeout_seconds=args.npm_timeout_seconds)
