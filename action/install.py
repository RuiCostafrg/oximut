"""Install the exact release prefix verified by the Oximut composite action."""
import hashlib
import importlib.util
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

REPOSITORY = "RuiCostafrg/oximut"
REPOSITORY_ID = "1431290496"
SIGNER = f"https://github.com/{REPOSITORY}/.github/workflows/release.yml"
ARCHIVE = "validated-prefix.tar.gz"
ROOT = "relocated prefix with spaces"


def require(condition, message):
    if not condition:
        raise ValueError(message)


def verify(assets, version, commit):
    archive = assets / ARCHIVE
    sidecar = assets / f"{ARCHIVE}.sha256"
    env = dict(os.environ, GH_HOST="github.com", GH_PROMPT_DISABLED="1")
    subprocess.run(["gh", "release", "download", f"v{version}", "--repo", REPOSITORY,
                    "--pattern", ARCHIVE, "--pattern", sidecar.name, "--dir", str(assets)],
                   cwd=assets, env=env, check=True)
    with archive.open("rb") as f:
        digest = hashlib.file_digest(f, "sha256").hexdigest()
    expected = f"{digest}  {ARCHIVE}\n".encode()
    with sidecar.open("rb") as f:
        require(f.read(len(expected) + 1) == expected, "release prefix checksum mismatch")

    # Match the exact signing certificate identity, including the tag. A
    # workflow name prefix does not identify a particular workflow or ref.
    ref = f"refs/tags/v{version}"
    command = ["gh", "attestation", "verify", str(archive), "--repo", REPOSITORY,
               "--cert-identity", f"{SIGNER}@{ref}", "--source-ref", ref,
               "--cert-oidc-issuer", "https://token.actions.githubusercontent.com",
               "--deny-self-hosted-runners", "--predicate-type", "https://slsa.dev/provenance/v1",
               "--format", "json"]
    if commit:
        command += ["--signer-digest", commit, "--source-digest", commit]
    verified = json.loads(subprocess.check_output(command, cwd=assets, env=env))
    for entry in verified:
        result = entry["verificationResult"]
        certificate = result["signature"]["certificate"]
        if str(certificate.get("sourceRepositoryIdentifier")) != REPOSITORY_ID:
            continue
        named = [subject["digest"].get("sha256") for subject in result["statement"]["subject"]
                 if subject["name"] == ARCHIVE]
        if named and all(value == digest for value in named):
            return archive
    raise ValueError(f"no verified attestation names {ARCHIVE} with its SHA-256 from {REPOSITORY}")


def extract(archive, prefix):
    # Validate the tar member names before writing anything. npm's relative
    # symlinks are extracted last so regular entries cannot write through them.
    entries = {}
    with tarfile.open(archive, "r:gz", errorlevel=2) as tar:
        for entry in tar:
            name = entry.name
            parts = name.split("/")
            require(parts[0] == ROOT and not any(part in ("", ".", "..") for part in parts),
                    f"unexpected prefix archive path: {name!r}")
            require(name not in entries, f"duplicate prefix archive path: {name!r}")
            require((entry.type in (tarfile.REGTYPE, tarfile.AREGTYPE, tarfile.DIRTYPE, tarfile.SYMTYPE)
                     and not entry.sparse), f"unsupported prefix archive member: {name!r}")
            entries[name] = entry
        require(ROOT in entries and entries[ROOT].isdir(), "missing prefix archive root directory")
        members = [entry.replace(name=name.removeprefix(ROOT + "/"))
                   for name, entry in entries.items() if name != ROOT]
        prefix.mkdir()
        tar.extractall(prefix, sorted(members, key=lambda entry: entry.issym()), filter="data")

    # Apply the same post-extraction symlink check as the R3 artifact gate.
    checker = Path(__file__).resolve().parents[1] / "scripts/candidate-sandbox.py"
    spec = importlib.util.spec_from_file_location("candidate_sandbox", checker)
    sandbox = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(sandbox)
    sandbox.safe_tree(prefix)


def install():
    version = os.environ.get("OXIMUT_ACTION_VERSION", "").removeprefix("v")
    require(re.fullmatch(r"(?:0|[1-9][0-9]*)(?:\.(?:0|[1-9][0-9]*)){2}", version),
            "version must be a pinned stable release such as 1.0.0")
    commit = os.environ.get("OXIMUT_ACTION_RELEASE_COMMIT", "")
    require(not commit or re.fullmatch(r"[0-9a-f]{40}", commit),
            "release-commit must be a full lowercase Git commit SHA")
    require(os.environ.get("OXIMUT_ACTION_RUN", "false") in ("true", "false"), "run must be true or false")
    require(platform.system() == "Linux" and platform.machine() == "x86_64",
            "the release prefix requires Ubuntu 24.04 x86-64")
    distro = platform.freedesktop_os_release()
    require((distro.get("ID"), distro.get("VERSION_ID")) == ("ubuntu", "24.04"),
            "the release prefix requires Ubuntu 24.04 x86-64")
    require(shutil.which("gh"), "GitHub CLI (gh) is required")
    env = {key: value for key, value in os.environ.items() if key not in ("GH_TOKEN", "GITHUB_TOKEN")}
    require(shutil.which("node") and
            subprocess.check_output(["node", "--version"], env=env, text=True).strip() == "v26.3.1",
            "install Node.js 26.3.1 on PATH before using this action")

    temp = Path(os.environ["RUNNER_TEMP"]).resolve(strict=True)
    workspace = Path(os.environ["GITHUB_WORKSPACE"]).resolve(strict=True)
    require(not temp.is_relative_to(workspace), "RUNNER_TEMP must be outside the checkout")
    require(not any(c in str(temp) for c in "\r\n"), "RUNNER_TEMP must be a single-line path")
    work = Path(tempfile.mkdtemp(prefix="oximut-action-", dir=temp))
    prefix = work / "prefix"
    executable = prefix / "bin/oximut"
    try:
        with tempfile.TemporaryDirectory(dir=work) as download:
            extract(verify(Path(download), version, commit), prefix)
        bundle = prefix / "lib/oximut"
        for path in (executable, bundle / "oximut"):
            require(path.is_file() and path.resolve() == path and os.access(path, os.X_OK),
                    f"missing executable in release prefix: {path.relative_to(prefix)}")
        adapter = bundle / "packages/vitest-adapter"
        for path in (adapter / "m1-runner.mjs", adapter / "m2-runner.mjs",
                     adapter / "native/build/zygote-fork.node", bundle / "node_modules/vitest/package.json"):
            require(path.is_file(), f"missing runtime file in release prefix: {path.relative_to(prefix)}")

        reported = subprocess.check_output([executable, "--version"], cwd=work, env=env, text=True)
        require(reported.strip() == f"oximut {version}", "installed executable reports an unexpected version")

        with Path(os.environ["GITHUB_OUTPUT"]).open("a") as output:
            output.write(f"version={version}\nbin-dir={executable.parent}\nexecutable={executable}\n")
        with Path(os.environ["GITHUB_PATH"]).open("a") as path:
            path.write(f"{executable.parent}\n")
    except BaseException:
        shutil.rmtree(work)
        raise
    print(f"Installed Oximut {version} from {REPOSITORY} at {executable}")


if __name__ == "__main__":
    try:
        require(sys.version_info >= (3, 13, 5), "install Python 3.13.5 or newer on PATH before using this action")
        install()
    except (OSError, ValueError, KeyError, TypeError, RuntimeError, tarfile.TarError, subprocess.CalledProcessError) as error:
        sys.exit(f"Oximut setup failed: {error}")
