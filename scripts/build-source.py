#!/usr/bin/env python3
"""Create a deterministic, unpublished source release from an explicit Git commit."""
import argparse
import gzip
import hashlib
import io
import json
import shutil
from pathlib import Path, PurePosixPath
import re
import subprocess
import tarfile
import tempfile
import tomllib


VERSION = re.compile(
    r"(?:0|[1-9][0-9]*)\.(?:0|[1-9][0-9]*)\.(?:0|[1-9][0-9]*)"
    r"(?:-(?:0|[1-9][0-9]*|[0-9]*[A-Za-z-][0-9A-Za-z-]*)"
    r"(?:\.(?:0|[1-9][0-9]*|[0-9]*[A-Za-z-][0-9A-Za-z-]*))*)?"
    r"(?:\+[0-9A-Za-z-]+(?:\.[0-9A-Za-z-]+)*)?"
)
EXCLUDED_PARTS = {
    ".git", "node_modules", "target", "dist", "coverage", ".oximut",
    ".vitest", "__pycache__", ".github",
}
REQUIRED = {
    "Cargo.toml", "Cargo.lock", "package.json", "package-lock.json", ".npmrc",
    ".node-version", "rust-toolchain.toml", "LICENSE", "README.md",
    "packages/vitest-adapter/package.json", "packages/vitest-adapter/index.mjs",
    "scripts/install-source.py", "scripts/oximut-launcher.py", "scripts/smoke-install.py",
    "scripts/build-source.py",
    *{f"packages/vitest-adapter/{name}" for name in (
        "m1-runner.mjs", "m1-worker.ts", "m2-runner.mjs", "m2-worker.ts",
        "m3-selection.ts", "m3-setup.ts", "protocol.ts", "supervisor.py", "worker.mjs")},
}


def git(repo, *args, input=None):
    return subprocess.check_output(["git", "-C", str(repo), *args], input=input, stderr=subprocess.PIPE)


def safe_path(name):
    path = PurePosixPath(name)
    if (not name or path.is_absolute() or "\\" in name or ":" in name
            or any(ord(c) < 32 or ord(c) == 127 for c in name)
            or any(p in ("", ".", "..") for p in name.split("/"))):
        raise ValueError(f"unsafe archive path: {name!r}")
    return path


def excluded(path):
    return (any(p in EXCLUDED_PARTS or p.startswith((".experiment-", ".stryker-tmp", ".env"))
                for p in path.parts)
            or path.parts[:2] == ("benchmarks", "results")
            or str(path) in {".coderabbit.yaml", "PLAN.md"}
            or path.suffix in {".pyc", ".log"})


def sha(data):
    return hashlib.sha256(data).hexdigest()


def json_bytes(value):
    return (json.dumps(value, indent=2, sort_keys=True, ensure_ascii=True) + "\n").encode()


def validate_metadata(files, version):
    required = set(REQUIRED)
    for runner in ("m1-runner.mjs", "m2-runner.mjs", "protocol.ts"):
        source = files.get(f"packages/vitest-adapter/{runner}")
        if source:
            # Inspect selected Git blobs, never the current checkout's runners.
            for name in re.findall(r"from\s+['\"]\./(protocol\.(?:mjs|ts))['\"]", source[1].decode()):
                required.add(f"packages/vitest-adapter/{name}")
    missing = required - files.keys()
    if missing:
        raise ValueError(f"missing required source files: {sorted(missing)}")
    read_json = lambda name: json.loads(files[name][1])
    read_toml = lambda name: tomllib.loads(files[name][1].decode())
    cargo = read_toml("Cargo.toml")
    lock = read_toml("Cargo.lock")
    npm = read_json("package.json")
    npm_lock = read_json("package-lock.json")
    adapter = read_json("packages/vitest-adapter/package.json")
    versions = {"workspace": cargo["workspace"]["package"]["version"],
                "npm": npm["version"], "npm lock": npm_lock["version"],
                "npm lock root": npm_lock["packages"][""]["version"],
                "adapter": adapter["version"]}
    for member in cargo["workspace"]["members"]:
        safe_path(member)
        manifest = read_toml(f"{member}/Cargo.toml")["package"]
        if manifest.get("version") != {"workspace": True}:
            raise ValueError(f"workspace member must inherit version: {member}")
        entries = [p for p in lock["package"] if p["name"] == manifest["name"] and "source" not in p]
        if len(entries) != 1:
            raise ValueError(f"missing/ambiguous workspace lock entry: {member}")
        versions[f"Cargo.lock {manifest['name']}"] = entries[0]["version"]
    for name, actual in versions.items():
        if actual != version:
            raise ValueError(f"version mismatch: {name} is {actual!r}, expected {version!r}")
    for key in ("name", "dependencies", "devDependencies", "optionalDependencies", "engines"):
        if npm.get(key) != npm_lock["packages"][""].get(key):
            raise ValueError(f"npm lock root {key} mismatch")
    return {
        "rust": read_toml("rust-toolchain.toml")["toolchain"]["channel"],
        "node": files[".node-version"][1].decode().strip(),
        "packageManager": npm["packageManager"],
    }


def build(repo, revision, version, output):
    if not VERSION.fullmatch(version):
        raise ValueError("version must be a valid SemVer without path characters")
    # Ignored build caches are permitted; tracked changes and untracked files are not.
    if git(repo, "status", "--porcelain=v1", "--untracked-files=all", "--ignore-submodules=none"):
        raise ValueError("source checkout must be clean (including untracked files)")
    commit = git(repo, "rev-parse", "--verify", "--end-of-options", revision + "^{commit}").decode().strip()
    tree = git(repo, "rev-parse", commit + "^{tree}").decode().strip()
    timestamp = int(git(repo, "show", "-s", "--format=%ct", commit))
    files = {}
    blobs = []
    for record in git(repo, "ls-tree", "-rz", "--full-tree", commit).split(b"\0"):
        if not record:
            continue
        header, raw_path = record.split(b"\t", 1)
        mode, kind, oid = header.decode().split()
        name = raw_path.decode("utf-8")
        path = safe_path(name)
        if excluded(path):
            continue
        if path.parts[0] == "SOURCE-DISTRIBUTION":
            raise ValueError("reserved source metadata path: " + name)
        if kind != "blob" or mode not in ("100644", "100755"):
            raise ValueError("source payload must contain regular files only: " + name)
        blobs.append((name, 0o755 if mode == "100755" else 0o644, oid))
    # Batch only selected blobs: large historical results are never loaded.
    stream = io.BytesIO(git(repo, "cat-file", "--batch",
                            input="".join(oid + "\n" for _, _, oid in blobs).encode()))
    for name, mode, oid in blobs:
        object_id, kind, size = stream.readline().decode().split()
        if object_id != oid or kind != "blob":
            raise ValueError("unexpected Git blob response: " + name)
        data = stream.read(int(size))
        if len(data) != int(size) or stream.read(1) != b"\n":
            raise ValueError("truncated Git blob response: " + name)
        files[name] = (mode, data)
    tools = validate_metadata(files, version)
    inventory = [{"path": name, "mode": f"{mode:04o}", "size": len(data), "sha256": sha(data)}
                 for name, (mode, data) in sorted(files.items())]
    metadata = {
        "formatVersion": 1, "name": "oximut", "version": version,
        "distribution": "source", "source": {"commit": commit, "tree": tree, "commitTime": timestamp},
        "builderSha256": sha(Path(__file__).read_bytes()), "toolchains": tools,
        "dependencies": {name: sha(files[name][1]) for name in ("Cargo.lock", "package-lock.json")},
        "npmInstall": "npm ci --include=dev --ignore-scripts --no-audit --no-fund",
        "inventory": inventory,
    }
    files["SOURCE-DISTRIBUTION/manifest.json"] = (0o644, json_bytes(metadata))
    sums = "".join(f"{sha(data)}  {name}\n" for name, (_, data) in sorted(files.items()))
    files["SOURCE-DISTRIBUTION/SHA256SUMS"] = (0o644, sums.encode())
    output = output.resolve()
    output.mkdir(parents=True, exist_ok=True)
    archive_name = f"oximut-{version}-source.tar.gz"
    archive = output / archive_name
    sidecar = output / (archive_name + ".sha256")
    if archive.exists() or sidecar.exists():
        raise ValueError("refusing to replace an existing artifact")
    with tempfile.TemporaryDirectory(prefix=".source-build-", dir=output) as temporary:
        staged = Path(temporary) / archive_name
        with staged.open("wb") as raw:
            with gzip.GzipFile(fileobj=raw, mode="wb", filename="", mtime=0) as compressed:
                with tarfile.open(fileobj=compressed, mode="w", format=tarfile.PAX_FORMAT) as tar:
                    for name, (mode, data) in sorted(files.items()):
                        entry = tarfile.TarInfo(f"oximut-{version}/{name}")
                        entry.size, entry.mode, entry.mtime = len(data), mode, timestamp
                        entry.uid = entry.gid = 0
                        entry.uname = entry.gname = ""
                        tar.addfile(entry, io.BytesIO(data))
        checksum = Path(temporary) / "checksum"
        checksum.write_text(f"{sha(staged.read_bytes())}  {archive_name}\n")
        created = []
        try:
            for source, destination in ((staged, archive), (checksum, sidecar)):
                with destination.open("xb") as target:
                    created.append(destination)
                    with source.open("rb") as origin:
                        shutil.copyfileobj(origin, target)
        except BaseException:
            for destination in created:
                destination.unlink(missing_ok=True)
            raise
    return archive


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repo", type=Path, default=Path(__file__).resolve().parents[1])
    parser.add_argument("--revision", required=True, help="explicit committed Git revision")
    parser.add_argument("--version", required=True, help="must match all committed package versions")
    parser.add_argument("--output-dir", type=Path, required=True, help="prefer a directory outside the checkout")
    args = parser.parse_args()
    try:
        print(build(args.repo, args.revision, args.version, args.output_dir))
    except (ValueError, KeyError, UnicodeError, OSError, subprocess.CalledProcessError) as error:
        parser.exit(1, f"source build failed: {error}\n")
