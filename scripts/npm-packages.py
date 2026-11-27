#!/usr/bin/env python3
"""Repackage a checksummed R3 prefix as an installable npm tarball."""
import argparse
import gzip
import hashlib
import importlib.util
import io
import json
from pathlib import Path, PurePosixPath
import platform
import re
import struct
import tarfile
import tempfile


def load_script(name):
    spec = importlib.util.spec_from_file_location(name, Path(__file__).with_name(f"{name}.py"))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


VALIDATOR = load_script("validate-source")
BUILDER = VALIDATOR.BUILDER
require = VALIDATOR.require
read_regular = VALIDATOR.SANDBOX.read_regular
ROOT = Path(__file__).resolve().parents[1]
PREFIX = "relocated prefix with spaces"
ARCHIVE = "validated-prefix.tar.gz"
BUNDLE = "lib/oximut"
LAUNCHER = "bin/oximut"
BINARY = f"{BUNDLE}/oximut"
ADDON = f"{BUNDLE}/packages/vitest-adapter/native/build/zygote-fork.node"
LICENSE = f"{BUNDLE}/LICENSE"
MAX_COMPRESSED = 96 * 1024 * 1024
MAX_EXPANDED = 256 * 1024 * 1024
MAX_FILE = 128 * 1024 * 1024
MAX_ENTRIES = 20_000
MTIME = 499162500


def check_evidence(evidence, version, revision, tree):
    require(BUILDER.VERSION.fullmatch(version) and "+" not in version, "invalid npm version")
    for value in (revision, tree):
        require(re.fullmatch(r"[0-9a-f]{40}", value), "full source commit and tree IDs required")
    data = VALIDATOR.decode_json(read_regular(evidence, limit=1024 * 1024))
    require(
        data["gate"] == "R3" and data["status"] == "passed" and data["version"] == version
        and data["source"]["commit"] == revision and data["source"]["tree"] == tree
        and data["buildCount"] == 1 and data["extractedSourceRemoved"] is True
        and data["isolation"] == "bubblewrap-pid-mount-v1"
        and re.fullmatch(r"[0-9a-f]{64}", data["binarySha256"])
        and re.fullmatch(r"[0-9a-f]{64}", data["archiveSha256"]),
        "expected successful R3 evidence for the requested version, commit and tree",
    )
    tests = data["tests"]
    require(sorted(test["phase"] for test in tests) == ["installed", "relocated"]
            and all(test["passed"] is True for test in tests),
            "expected passing installed and relocated smoke")
    host = data["host"]
    require(host["system"] == "Linux" and host["machine"] == "x86_64"
            and host["platform"].endswith("-x86_64-with-glibc2.39"),
            "expected Ubuntu 24.04 x86-64 R3 evidence")
    return data


def expand_prefix(assets, expanded):
    # Check and parse the same bytes, with independent limits on compressed and
    # expanded sizes. Store the uncompressed tar on disk, without extracting it.
    compressed = read_regular(assets / ARCHIVE, limit=MAX_COMPRESSED)
    checksum = read_regular(assets / f"{ARCHIVE}.sha256", limit=4096)
    digest = BUILDER.sha(compressed)
    require(checksum == f"{digest}  {ARCHIVE}\n".encode(), "prefix checksum mismatch")
    with gzip.GzipFile(fileobj=io.BytesIO(compressed)) as source:
        while chunk := source.read(1024 * 1024):
            require(expanded.tell() + len(chunk) <= MAX_EXPANDED, "expanded prefix limit exceeded")
            expanded.write(chunk)
    expanded.seek(0)
    return digest


def prefix_files(archive):
    entries = {}
    for entry in archive:
        require(len(entries) < MAX_ENTRIES, "prefix entry limit exceeded")
        path = BUILDER.safe_path(entry.name)
        require(path.parts[0] == PREFIX and not entry.pax_headers and entry.sparse is None,
                f"unexpected root or tar metadata: {entry.name}")
        name = str(PurePosixPath(*path.parts[1:]))
        require(name in (".", "bin", "lib", BUNDLE, LAUNCHER) or name.startswith(f"{BUNDLE}/"),
                f"unexpected prefix member: {entry.name}")
        require(name not in entries, f"duplicate prefix member: {name}")
        require(entry.type in (tarfile.REGTYPE, tarfile.AREGTYPE, tarfile.DIRTYPE, tarfile.SYMTYPE),
                f"unsupported prefix member type: {name}")
        require(0 <= entry.size <= MAX_FILE and (entry.isfile() or entry.size == 0),
                f"invalid prefix member size: {name}")
        require(not entry.mode & ~0o777, f"unsafe prefix mode: {name}")
        entries[name] = entry
    archive.fileobj.seek(archive.offset)
    while chunk := archive.fileobj.read(1024 * 1024):
        require(not any(chunk), "nonzero trailing prefix data")

    files = {}
    for name, entry in entries.items():
        path = PurePosixPath(name)
        if name == ".":
            require(entry.isdir(), "prefix root must be a directory")
        else:
            parent = entries.get(str(path.parent))
            require(parent is not None and parent.isdir(), f"prefix parent is not a directory: {name}")
        if entry.issym():
            # npm does not extract links. Permit only npm-generated command
            # aliases that point to regular files in the same dependency tree.
            require(path.parent.name == ".bin" and path.parent.parent.name == "node_modules"
                    and name.startswith(f"{BUNDLE}/node_modules/"),
                    f"unexpected prefix symlink: {name}")
            require(entry.linkname.startswith("../"), f"invalid dependency command alias: {name}")
            target = str(path.parent.parent / BUILDER.safe_path(entry.linkname[3:]))
            require(target in entries and entries[target].isfile(),
                    f"invalid dependency command alias: {name}")
        elif entry.isfile():
            require(not entry.linkname and path.name != ".gitignore",
                    f"npm would rewrite or discard prefix member: {name}")
            files[name] = entry
    return files


def member_digest(archive, entry):
    with archive.extractfile(entry) as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def package_files(archive, files, evidence, prefix_digest, version):
    required = {LAUNCHER, BINARY, ADDON, LICENSE, f"{BUNDLE}/package.json",
                f"{BUNDLE}/package-lock.json", f"{BUNDLE}/node_modules/vitest/package.json",
                f"{BUNDLE}/packages/vitest-adapter/m1-runner.mjs",
                f"{BUNDLE}/packages/vitest-adapter/m2-worker.ts"}
    require(required <= files.keys(), f"missing prefix members: {sorted(required - files.keys())}")
    require(all(files[name].mode & 0o100 for name in (LAUNCHER, BINARY)), "prefix CLI is not executable")
    for name in (BINARY, ADDON):
        with archive.extractfile(files[name]) as stream:
            header = stream.read(20)
        require(header[:7] == b"\x7fELF\x02\x01\x01" and len(header) == 20
                and struct.unpack_from("<H", header, 18)[0] == 62,
                f"expected little-endian x86-64 ELF: {name}")
    require(member_digest(archive, files[BINARY]) == evidence["binarySha256"],
            "prefix binary does not match R3 evidence")

    manifest = files[f"{BUNDLE}/package.json"]
    require(manifest.size <= 1024 * 1024, "oversized prefix package manifest")
    with archive.extractfile(manifest) as stream:
        source = VALIDATOR.decode_json(stream.read())
    node = source["engines"]["node"]
    require(source["name"] == "oximut" and source["version"] == version
            and re.fullmatch(r"[0-9]+\.[0-9]+\.[0-9]+", node)
            and evidence["toolVersions"]["node"] == f"v{node}", "prefix package metadata mismatch")
    manifest = {
        "name": "oximut",
        "version": version,
        "description": "Mutation testing for JavaScript and TypeScript with Vitest",
        "license": "MIT",
        "repository": {"type": "git", "url": "git+https://github.com/RuiCostafrg/oximut.git"},
        "bin": {"oximut": LAUNCHER},
        "engines": {"node": node},
        "os": ["linux"],
        "cpu": ["x64"],
        "libc": ["glibc"],
    }
    provenance = {
        "source": evidence["source"],
        "sourceArchiveSha256": evidence["archiveSha256"],
        "prefixSha256": prefix_digest,
        "binarySha256": evidence["binarySha256"],
        "host": evidence["host"],
    }
    with archive.extractfile(files[LICENSE]) as stream:
        license_text = stream.read(1024 * 1024 + 1)
    require(len(license_text) <= 1024 * 1024, "oversized prefix license")
    return {
        "package.json": BUILDER.json_bytes(manifest),
        "BUILD-PROVENANCE.json": BUILDER.json_bytes(provenance),
        "LICENSE": license_text,
        "README.md": (ROOT / "packaging/npm/README.md").read_bytes(),
    }


def executable_mode(entry):
    return 0o755 if entry.mode & 0o111 else 0o644


def write_package(destination, archive, files, generated):
    # Write the npm tarball directly to preserve the prefix's inventory and
    # executable bits. The nested runtime stays outside npm's dependency root.
    with destination.open("xb") as stream, gzip.GzipFile(
        fileobj=stream, mode="wb", mtime=0, filename=""
    ) as zipped:
        with tarfile.open(fileobj=zipped, mode="w|", format=tarfile.GNU_FORMAT) as package:
            for name in sorted(files.keys() | generated.keys()):
                entry = tarfile.TarInfo(f"package/{name}")
                entry.mtime = MTIME
                if name in generated:
                    data = generated[name]
                    entry.size, entry.mode = len(data), 0o644
                    package.addfile(entry, io.BytesIO(data))
                else:
                    source = files[name]
                    entry.size, entry.mode = source.size, executable_mode(source)
                    with archive.extractfile(source) as payload:
                        package.addfile(entry, payload)


def verify_package(destination, archive, files, generated):
    expected = {name: (executable_mode(entry), member_digest(archive, entry))
                for name, entry in files.items()}
    expected.update({name: (0o644, BUILDER.sha(data)) for name, data in generated.items()})
    observed = set()
    with tarfile.open(destination, mode="r|gz") as package:
        for entry in package:
            name = entry.name.removeprefix("package/")
            require(entry.name == f"package/{name}" and name in expected
                    and name not in observed and entry.isfile(),
                    f"unexpected npm tarball member: {entry.name}")
            require((entry.mode, member_digest(package, entry)) == expected[name],
                    f"npm tarball bytes or mode differ: {name}")
            observed.add(name)
    require(observed == expected.keys(), "npm tarball omitted expected prefix files")
    return expected


def verify_install(prefix, expected):
    VALIDATOR.SANDBOX.safe_tree(prefix)
    installed = {path.relative_to(prefix).as_posix() for path in prefix.rglob("*") if path.is_file()}
    require(installed == expected.keys(),
            f"npm install changed file inventory: missing {sorted(expected.keys() - installed)}, "
            f"extra {sorted(installed - expected.keys())}")
    for name, (mode, digest) in expected.items():
        path = prefix / name
        require(not path.is_symlink(), f"npm install linked a regular file: {name}")
        with path.open("rb") as stream:
            require(hashlib.file_digest(stream, "sha256").hexdigest() == digest,
                    f"npm install changed file bytes: {name}")
        require(bool(path.stat().st_mode & 0o100) == bool(mode & 0o100),
                f"npm install changed executable bit: {name}")


def smoke_install(tarball, expected, evidence):
    distro = platform.freedesktop_os_release()
    require(platform.system() == "Linux" and platform.machine() == "x86_64"
            and distro.get("ID") == "ubuntu" and distro.get("VERSION_ID") == "24.04"
            and platform.libc_ver() == ("glibc", "2.39"),
            "npm runtime smoke requires native Ubuntu 24.04 x86-64")
    with tempfile.TemporaryDirectory(prefix="oximut npm smoke ", dir="/tmp") as temporary:
        project = Path(temporary)
        for tool in ("node", "npm"):
            require(VALIDATOR.run([tool, "--version"], project) == evidence["toolVersions"][tool],
                    f"npm smoke requires the validated prefix's {tool} version")
        (project / "package.json").write_text('{"private":true}\n')
        VALIDATOR.SANDBOX.run([
            "npm", "install", "--offline", "--ignore-scripts", "--no-audit", "--no-fund",
            "--no-update-notifier",
            "--userconfig", project / "user.npmrc", "--globalconfig", project / "global.npmrc",
            "--cache", project / "cache",
            tarball,
        ], cwd=project, writable=[project], readonly=[tarball])
        prefix = project / "node_modules/oximut"
        verify_install(prefix, expected)
        command = project / "node_modules/.bin/oximut"
        require(command.is_symlink() and command.resolve() == prefix / LAUNCHER,
                "npm did not correctly link the oximut command")
        version = VALIDATOR.SANDBOX.run([command, "--version"], cwd=project, readonly=[project])
        require(version.strip() == f"oximut {evidence['version']}", "unexpected installed CLI version")
        load_script("smoke-install").smoke(prefix, isolated=True, require_zygote=True)


def build(assets, evidence_path, version, revision, tree, output, *, smoke=False):
    evidence = check_evidence(evidence_path, version, revision, tree)
    destination = output / f"oximut-{version}.tgz"
    require(not output.exists() and not output.is_symlink(), f"output already exists: {output}")
    output.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix=".npm-package-", dir=output.parent) as temporary:
        staged = Path(temporary) / destination.name
        with tempfile.TemporaryFile(dir=temporary) as expanded:
            digest = expand_prefix(assets, expanded)
            with tarfile.open(fileobj=expanded, mode="r:") as archive:
                files = prefix_files(archive)
                generated = package_files(archive, files, evidence, digest, version)
                write_package(staged, archive, files, generated)
                expected = verify_package(staged, archive, files, generated)
        if smoke:
            smoke_install(staged.resolve(), expected, evidence)
        output.mkdir()
        try:
            staged.rename(destination)
        except OSError:
            output.rmdir()
            raise
    return destination


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--assets", type=Path, required=True, help="directory with the R3 prefix and its SHA-256 sidecar")
    parser.add_argument("--evidence", type=Path, required=True, help="passing evidence.json from the binary validation run")
    parser.add_argument("--version", required=True, help="version recorded by the binary validation run")
    parser.add_argument("--revision", required=True, help="expected full Git commit ID from binary validation")
    parser.add_argument("--tree", required=True, help="expected full Git tree ID from binary validation")
    parser.add_argument("--output", type=Path, required=True, help="new directory for the npm tarball")
    parser.add_argument("--smoke", action="store_true", help="require offline npm installation and mutation smoke on native Ubuntu")
    args = parser.parse_args()
    try:
        print(build(args.assets, args.evidence, args.version, args.revision, args.tree, args.output, smoke=args.smoke))
    except (AttributeError, KeyError, TypeError, ValueError, OSError, EOFError, tarfile.TarError, RuntimeError) as error:
        parser.exit(1, f"npm package failed: {error}\n")
