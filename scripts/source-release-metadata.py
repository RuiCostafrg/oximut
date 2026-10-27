#!/usr/bin/env python3
"""Validate source format and write offline SPDX/evidence sidecars; never a release approval."""
import argparse
from datetime import datetime, timezone
import importlib.util
import json
from pathlib import Path
import re
import tarfile
import tempfile
import tomllib
from urllib.parse import quote

_spec = importlib.util.spec_from_file_location("source_builder", Path(__file__).with_name("build-source.py"))
builder = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(builder)


def inventory(npm, cargo, commit, archive_sha, timestamp):
    packages = []
    for path, package in sorted(npm["packages"].items()):
        if not path:
            continue
        if package.get("link") or not package.get("version"):
            raise ValueError(f"unsupported npm lock identity: {path}")
        name = package.get("name", path.rsplit("node_modules/", 1)[-1])
        version = package["version"]
        entry = {
            "name": name, "versionInfo": version,
            "downloadLocation": package.get("resolved", "NOASSERTION"),
            "comment": json.dumps({"lockfile": "package-lock.json", "path": path,
                                   "integrity": package.get("integrity"),
                                   "declaredLicense": package.get("license"),
                                   "dev": package.get("dev", False),
                                   "optional": package.get("optional", False)}, sort_keys=True),
            "externalRefs": [{"referenceCategory": "PACKAGE-MANAGER", "referenceType": "purl",
                              "referenceLocator": f"pkg:npm/{quote(name, safe='/')}@{quote(version, safe='')}"}],
        }
        packages.append(entry)
    for package in sorted(cargo["package"], key=lambda p: (p["name"], p["version"], p.get("source", ""))):
        entry = {
            "name": package["name"], "versionInfo": package["version"],
            "downloadLocation": "NOASSERTION",
            "comment": json.dumps({"lockfile": "Cargo.lock", "source": package.get("source"),
                                   "licenseEvidence": "Cargo.lock contains no license declarations"}, sort_keys=True),
        }
        # Only crates.io registry identities map to the Cargo purl without guessing.
        if package.get("source") == "registry+https://github.com/rust-lang/crates.io-index":
            entry["externalRefs"] = [{"referenceCategory": "PACKAGE-MANAGER", "referenceType": "purl",
                                      "referenceLocator": f"pkg:cargo/{quote(package['name'], safe='')}@{quote(package['version'], safe='')}"}]
        if "checksum" in package:
            if not re.fullmatch(r"[0-9a-f]{64}", package["checksum"]):
                raise ValueError("unsupported Cargo checksum")
            entry["checksums"] = [{"algorithm": "SHA256", "checksumValue": package["checksum"]}]
        packages.append(entry)
    for index, package in enumerate(packages):
        package.update(SPDXID=f"SPDXRef-Package-{index}", filesAnalyzed=False,
                       licenseConcluded="NOASSERTION", licenseDeclared="NOASSERTION",
                       copyrightText="NOASSERTION")
    return {
        "spdxVersion": "SPDX-2.3", "dataLicense": "CC0-1.0", "SPDXID": "SPDXRef-DOCUMENT",
        "name": "oximut locked source dependency inventory",
        "documentNamespace": f"https://spdx.org/spdxdocs/oximut-{commit}-{archive_sha}",
        "creationInfo": {"creators": ["Tool: oximut-source-release-metadata"],
                         "created": datetime.fromtimestamp(timestamp, timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")},
        "comment": "Offline lockfile inventory, including npm development/optional/platform entries and Cargo workspace entries. Not an installed dependency graph or license clearance. Raw npm license declarations are preserved in package comments; no expressions are inferred or certified. Cargo license data is unavailable. No transitive relationships are asserted.",
        "packages": packages,
        "relationships": [{"spdxElementId": "SPDXRef-DOCUMENT", "relationshipType": "DESCRIBES",
                           "relatedSpdxElement": p["SPDXID"]} for p in packages],
    }


def generate(repo, revision, workflow_commit, archive):
    for value in (revision, workflow_commit):
        if not re.fullmatch(r"[0-9a-f]{40}", value):
            raise ValueError("candidate and workflow revisions must be full lowercase commit SHAs")
    if builder.git(repo, "rev-parse", "HEAD").decode().strip() != workflow_commit:
        raise ValueError("trusted checkout HEAD must equal workflow commit")
    # Require candidate review/merge into the trusted workflow's history.
    builder.git(repo, "merge-base", "--is-ancestor", revision, workflow_commit)
    archive = archive.resolve()
    with tempfile.TemporaryDirectory(prefix="oximut-source-recheck-") as temporary:
        expected = builder.build(repo, revision, "1.0.0-rc.1", Path(temporary))
        digest = builder.sha(archive.read_bytes())
        if archive.name != expected.name or digest != builder.sha(expected.read_bytes()):
            raise ValueError("archive differs from trusted builder output for candidate revision")
    root = "oximut-1.0.0-rc.1/"
    with tarfile.open(archive, "r:gz") as tar:
        read = lambda name: tar.extractfile(root + name).read()
        manifest = json.loads(read("SOURCE-DISTRIBUTION/manifest.json"))
        npm_bytes, cargo_bytes = read("package-lock.json"), read("Cargo.lock")
    sbom = inventory(json.loads(npm_bytes), tomllib.loads(cargo_bytes.decode()), revision,
                     digest, manifest["source"]["commitTime"])
    sbom_bytes = builder.json_bytes(sbom)
    evidence = {
        "formatVersion": 1, "version": "1.0.0-rc.1", "candidateCommit": revision,
        "workflowCommit": workflow_commit,
        "builderSha256": manifest["builderSha256"],
        "metadataGeneratorSha256": builder.sha(Path(__file__).read_bytes()),
        "archive": {"name": archive.name, "sha256": digest},
        "inventory": {"name": archive.name + ".spdx.json", "sha256": builder.sha(sbom_bytes)},
        "lockfiles": {"package-lock.json": builder.sha(npm_bytes), "Cargo.lock": builder.sha(cargo_bytes)},
        "validation": {"sourceFormatRebuild": "passed", "exactArtifactBuildInstallRelocation": "NOT_RUN",
                       "releasePublishing": "BLOCKED_PENDING_R3_AND_RELEASE_REVIEW"},
    }
    for suffix, data in ((".spdx.json", sbom_bytes), (".evidence.json", builder.json_bytes(evidence))):
        with Path(str(archive) + suffix).open("xb") as output:
            output.write(data)
    return evidence


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repo", type=Path, default=Path(__file__).resolve().parents[1])
    parser.add_argument("--revision", required=True)
    parser.add_argument("--workflow-commit", required=True)
    parser.add_argument("--archive", type=Path, required=True)
    args = parser.parse_args()
    try:
        print(json.dumps(generate(args.repo, args.revision, args.workflow_commit, args.archive), indent=2))
    except (ValueError, KeyError, OSError, builder.subprocess.CalledProcessError) as error:
        parser.exit(1, f"source metadata failed: {error}\n")
