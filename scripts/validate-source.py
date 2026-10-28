#!/usr/bin/env python3
"""R3: validate an exact source candidate, install once, and retain relocated assets."""
import argparse
import gzip
import hashlib
import importlib.util
import io
import json
import os
from pathlib import Path
import platform
import re
import shutil
import subprocess
import sys
import tarfile
import tempfile

SPEC = importlib.util.spec_from_file_location("source_builder", Path(__file__).with_name("build-source.py"))
BUILDER = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(BUILDER)
MAX_COMPRESSED = 64 * 1024 * 1024
MAX_EXPANDED = 256 * 1024 * 1024
MAX_FILE = 16 * 1024 * 1024
MAX_ENTRIES = 20000
MANIFEST = "SOURCE-DISTRIBUTION/manifest.json"
SUMS = "SOURCE-DISTRIBUTION/SHA256SUMS"


def require(condition, message):
    if not condition:
        raise ValueError(message)


def unique_object(pairs):
    result = {}
    for key, value in pairs:
        require(key not in result, f"duplicate JSON key: {key}")
        result[key] = value
    return result


def decode_json(data):
    return json.loads(data, object_pairs_hook=unique_object)


def inspect_archive(archive, version, revision, tree):
    require(BUILDER.VERSION.fullmatch(version), "invalid version")
    require(re.fullmatch(r"[0-9a-f]{40}", revision), "expected full commit ID")
    require(re.fullmatch(r"[0-9a-f]{40}", tree), "expected full tree ID")
    # Read bounded bytes once; sidecar and parser operate on exactly these bytes.
    with archive.open("rb") as source:
        compressed = source.read(MAX_COMPRESSED + 1)
    require(len(compressed) <= MAX_COMPRESSED, "compressed archive limit exceeded")
    sidecar = archive.with_name(archive.name + ".sha256")
    with sidecar.open("rb") as source:
        checksum = source.read(4097)
    digest = BUILDER.sha(compressed)
    require(checksum == f"{digest}  {archive.name}\n".encode(), "archive sidecar mismatch")
    require(archive.name == f"oximut-{version}-source.tar.gz", "unexpected archive filename")
    files = {}
    prefix = f"oximut-{version}/"
    with gzip.GzipFile(fileobj=io.BytesIO(compressed)) as zipped:
        expanded = zipped.read(MAX_EXPANDED + 1)
        require(len(expanded) <= MAX_EXPANDED, "expanded archive limit exceeded")
        require(not zipped.read(1), "expanded archive limit exceeded")
    with tarfile.open(fileobj=io.BytesIO(expanded), mode="r:") as tar:
        for entry in tar:
            require(len(files) < MAX_ENTRIES, "archive entry limit exceeded")
            require(not entry.pax_headers, "extended archive headers forbidden")
            BUILDER.safe_path(entry.name)
            require(entry.name.startswith(prefix), "unexpected archive root")
            name = entry.name[len(prefix):]
            path = BUILDER.safe_path(name)
            require(name not in files, f"duplicate archive member: {name}")
            require(entry.type in (tarfile.REGTYPE, tarfile.AREGTYPE), "regular files only")
            require(not entry.linkname and not entry.sparse, "links/sparse files forbidden")
            require(entry.mode in (0o644, 0o755), "unexpected file mode")
            require((entry.uid, entry.gid, entry.uname, entry.gname) == (0, 0, "", ""),
                    "unexpected archive ownership")
            require(0 <= entry.size <= MAX_FILE, "archive member size limit exceeded")
            require(not BUILDER.excluded(path), f"excluded payload: {name}")
            data = tar.extractfile(entry).read(entry.size + 1)
            require(len(data) == entry.size, "truncated archive member")
            files[name] = (entry.mode, data, entry.mtime)
        require(not any(expanded[tar.offset:]), "nonzero trailing archive data")
    require(MANIFEST in files and SUMS in files, "missing distribution metadata")
    manifest = decode_json(files[MANIFEST][1])
    require(manifest["formatVersion"] == 1 and manifest["name"] == "oximut"
            and manifest["distribution"] == "source" and manifest["version"] == version,
            "distribution identity mismatch")
    require(manifest["source"]["commit"] == revision and manifest["source"]["tree"] == tree,
            "source revision/tree mismatch")
    require(type(manifest["source"]["commitTime"]) is int and all(
        value[2] == manifest["source"]["commitTime"] for value in files.values()),
        "commit timestamp mismatch")
    require(re.fullmatch(r"[0-9a-f]{64}", manifest["builderSha256"]), "invalid builder digest")
    payload = {name: (mode, data) for name, (mode, data, _) in files.items()
               if name not in (MANIFEST, SUMS)}
    require("scripts/build-source.py" in payload, "missing archived builder metadata")
    require(manifest["builderSha256"] == BUILDER.sha(payload["scripts/build-source.py"][1]),
            "builder digest mismatch")
    require(not any(name.startswith("SOURCE-DISTRIBUTION/") for name in payload),
            "unexpected distribution metadata")
    expected_inventory = [{"path": name, "mode": f"{mode:04o}", "size": len(data),
                           "sha256": BUILDER.sha(data)}
                          for name, (mode, data) in sorted(payload.items())]
    require(manifest["inventory"] == expected_inventory, "inventory mismatch")
    expected_sums = "".join(f"{BUILDER.sha(data)}  {name}\n"
                            for name, (_, data, _) in sorted(files.items()) if name != SUMS)
    require(files[SUMS][1] == expected_sums.encode(), "internal checksums mismatch")
    require(files[SUMS][0] == files[MANIFEST][0] == 0o644, "metadata mode mismatch")
    require(manifest["toolchains"] == BUILDER.validate_metadata(payload, version),
            "toolchain metadata mismatch")
    require(manifest["dependencies"] == {name: BUILDER.sha(payload[name][1])
            for name in ("Cargo.lock", "package-lock.json")}, "lockfile digest mismatch")
    require(manifest["npmInstall"] == "npm ci --include=dev --ignore-scripts --no-audit --no-fund",
            "npm installation metadata mismatch")
    return files, manifest, digest


def extract(files, destination):
    # Caller owns a newly created private directory. No tar extraction API or links.
    destination.mkdir()
    for name, (mode, data, _) in sorted(files.items()):
        target = destination / name
        target.parent.mkdir(parents=True, exist_ok=True)
        with target.open("xb") as stream:
            stream.write(data)
        target.chmod(mode)


def run(command, cwd, *, timeout=300):
    result = subprocess.run([str(arg) for arg in command], cwd=cwd, text=True,
                            stdout=subprocess.PIPE, stderr=subprocess.STDOUT, timeout=timeout)
    if result.returncode:
        raise RuntimeError(f"command failed ({result.returncode}): {command}\n{result.stdout}")
    return result.stdout.strip()


def validate(archive, version, revision, tree, output):
    files, manifest, digest = inspect_archive(archive, version, revision, tree)
    output = output.resolve()
    output.mkdir(parents=True, exist_ok=False)
    evidence = {"gate": "R3", "status": "failed", "archiveSha256": digest,
                "version": version, "source": manifest["source"],
                "host": {"system": platform.system(), "machine": platform.machine(),
                         "platform": platform.platform()},
                "tests": [], "toolVersions": {}, "buildCount": 0}
    try:
        for tool in ("rustc", "cargo", "node", "npm", "python3"):
            evidence["toolVersions"][tool] = run([tool, "--version"], output)
        with tempfile.TemporaryDirectory(prefix="oximut-r3-") as temporary:
            work = Path(temporary)
            source = work / f"oximut-{version}"
            extract(files, source)
            # Save trusted validation code/schema separately from the source being installed.
            harness = work / "independent harness"
            (harness / "scripts").mkdir(parents=True)
            (harness / "docs").mkdir()
            trusted = Path(__file__).resolve().parents[1]
            for name in ("scripts/smoke-install.py", "scripts/validate-reports.mjs", "docs/report.schema.json"):
                shutil.copy2(trusted / name, harness / name)
            prefix = output / "original prefix with spaces"
            # One compilation, outside any benchmark; preserve exactly this installed binary.
            evidence["installationLog"] = run([
                sys.executable, source / "scripts/install-source.py", "--prefix", prefix],
                work, timeout=4800)
            evidence["buildCount"] = 1
            binary = prefix / "lib/oximut/oximut"
            evidence["binarySha256"] = BUILDER.sha(binary.read_bytes())
            (harness / "node_modules").symlink_to(prefix / "lib/oximut/node_modules", target_is_directory=True)
            shutil.rmtree(source)
            require(not source.exists(), "extracted source still available")
            evidence["extractedSourceRemoved"] = True
            for phase in ("installed", "relocated"):
                if phase == "relocated":
                    relocated = output / "relocated prefix with spaces"
                    prefix.rename(relocated)
                    prefix = relocated
                    (harness / "node_modules").unlink()
                    (harness / "node_modules").symlink_to(prefix / "lib/oximut/node_modules", target_is_directory=True)
                reports = output / phase
                log = run([sys.executable, harness / "scripts/smoke-install.py", "--prefix", prefix,
                           "--reports-dir", reports, "--validator", harness / "scripts/validate-reports.mjs"],
                          harness, timeout=1200)
                evidence["tests"].append({"phase": phase, "passed": True, "log": log})
                require(BUILDER.sha((prefix / "lib/oximut/oximut").read_bytes()) == evidence["binarySha256"],
                        "installed binary changed")
            evidence["prefix"] = str(prefix)
            evidence["status"] = "passed"
    except BaseException as error:
        evidence["error"] = str(error)
        raise
    finally:
        (output / "evidence.json").write_bytes(BUILDER.json_bytes(evidence))
    return evidence


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--archive", required=True, type=Path)
    parser.add_argument("--version", required=True)
    parser.add_argument("--revision", required=True, help="expected full Git commit ID")
    parser.add_argument("--tree", required=True, help="expected full Git tree ID")
    parser.add_argument("--output-dir", required=True, type=Path, help="new directory outside checkout; retained")
    args = parser.parse_args()
    try:
        validate(args.archive.resolve(), args.version, args.revision, args.tree, args.output_dir)
    except (ValueError, KeyError, TypeError, UnicodeError, OSError, RuntimeError,
            tarfile.TarError, EOFError, subprocess.SubprocessError) as error:
        parser.exit(1, f"source validation failed: {error}\n")
