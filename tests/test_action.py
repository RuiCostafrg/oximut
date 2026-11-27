import hashlib
import io
import json
import os
from pathlib import Path
import subprocess
import sys
import tarfile
import tempfile
import unittest


ACTION = Path(__file__).resolve().parents[1] / "action"
ARCHIVE = "validated-prefix.tar.gz"
COMMIT = "a" * 40

GH = '''\
import hashlib
import json
import os
from pathlib import Path
import shutil
import sys

args = sys.argv[1:]
option = lambda flag: args[args.index(flag) + 1]
assert os.environ["GH_TOKEN"] == "action-token"
assert os.environ["GH_HOST"] == "github.com"
assert option("--repo") == "RuiCostafrg/oximut"
archive = "validated-prefix.tar.gz"
case = os.environ["TEST_ATTESTATION"]
if args[:2] == ["release", "download"]:
    assert args[2] == "v1.0.0"
    for asset in (archive, archive + ".sha256"):
        assert asset in args
        shutil.copyfile(Path(os.environ["TEST_ASSETS"]) / asset, Path(option("--dir")) / asset)
elif args[:2] == ["attestation", "verify"]:
    assert option("--cert-identity") == (
        "https://github.com/RuiCostafrg/oximut/.github/workflows/release.yml@refs/tags/v1.0.0"
    )
    assert option("--source-ref") == "refs/tags/v1.0.0"
    assert option("--cert-oidc-issuer") == "https://token.actions.githubusercontent.com"
    assert option("--predicate-type") == "https://slsa.dev/provenance/v1"
    assert "--deny-self-hosted-runners" in args
    assert option("--format") == "json"
    commit = os.environ["OXIMUT_ACTION_RELEASE_COMMIT"]
    if commit:
        assert option("--signer-digest") == option("--source-digest") == "a" * 40
    else:
        assert "--signer-digest" not in args and "--source-digest" not in args
    if case == "invalid-signature":
        sys.exit(1)
    digest = hashlib.sha256(Path(args[2]).read_bytes()).hexdigest()
    subject = {"name": archive, "digest": {"sha256": digest}}
    if case == "wrong-subject":
        subject["name"] = "another-artifact.tar.gz"
    subjects = [subject]
    if case == "conflicting-digest":
        subjects.append({"name": archive, "digest": {"sha256": "b" * 64}})
    repo_id = "123" if case == "wrong-repository-id" else "1431290496"
    print(json.dumps([{"verificationResult": {
        "signature": {"certificate": {"sourceRepositoryIdentifier": repo_id}},
        "statement": {"subject": subjects},
    }}]))
else:
    raise AssertionError(args)
'''

LAUNCHER = '''\
#!/usr/bin/env python3
import json
import os
from pathlib import Path
import sys

if sys.argv[1:] == ["--version"]:
    assert "GH_TOKEN" not in os.environ and "GITHUB_TOKEN" not in os.environ
    print("oximut " + os.environ.get("TEST_REPORTED_VERSION", "1.0.0"))
else:
    print(json.dumps([str(Path.cwd()), *sys.argv[1:]]))
    sys.exit(17)
'''


class ActionTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(prefix="oximut action tests ")
        self.addCleanup(temporary.cleanup)
        self.base = Path(temporary.name)
        self.workspace = self.base / "project"
        self.temp = self.base / "runner temp"
        self.assets = self.base / "assets"
        self.tools = self.base / "tools"
        for directory in (self.workspace, self.temp, self.assets, self.tools):
            directory.mkdir()
        self.env = dict(os.environ, PATH=f"{self.tools}:{os.environ['PATH']}",
                        GH_HOST="wrong.example.com", GH_TOKEN="action-token", GITHUB_TOKEN="other-token",
                        OXIMUT_ACTION_VERSION="v1.0.0", OXIMUT_ACTION_RELEASE_COMMIT=COMMIT,
                        OXIMUT_ACTION_RUN="false", RUNNER_TEMP=str(self.temp), GITHUB_WORKSPACE=str(self.workspace),
                        GITHUB_OUTPUT=str(self.base / "output"), GITHUB_PATH=str(self.base / "path"),
                        TEST_ASSETS=str(self.assets), TEST_ATTESTATION="valid", TEST_NODE_VERSION="v26.3.1",
                        TEST_REPORTED_VERSION="1.0.0", TEST_HOST="24.04", TEST_ARCH="x86_64")
        self.write_tool("gh", f"#!{sys.executable}\n{GH}")
        self.write_tool("node", '#!/bin/sh\ntest -z "${GH_TOKEN}${GITHUB_TOKEN}" || exit 1\n'
                                '[ "$1" = --version ] && printf "%s\\n" "$TEST_NODE_VERSION"\n')
        self.make_archive()

    def write_tool(self, name, body):
        path = self.tools / name
        path.write_text(body)
        path.chmod(0o755)

    def make_archive(self, *, bad_member=None):
        path = self.assets / ARCHIVE
        root = "relocated prefix with spaces"
        with tarfile.open(path, "w:gz") as tar:
            directory = tarfile.TarInfo(root)
            directory.type = tarfile.DIRTYPE
            tar.addfile(directory)
            bundle = "lib/oximut"
            adapter = f"{bundle}/packages/vitest-adapter"
            for name, text, mode in (
                ("bin/oximut", LAUNCHER, 0o755), (f"{bundle}/oximut", "executable fixture", 0o755),
                (f"{adapter}/m1-runner.mjs", "", 0o644), (f"{adapter}/m2-runner.mjs", "", 0o644),
                (f"{adapter}/native/build/zygote-fork.node", "", 0o644),
                (f"{bundle}/node_modules/vitest/package.json", "{}", 0o644),
            ):
                entry = tarfile.TarInfo(f"{root}/{name}")
                entry.mode = mode
                data = text.encode()
                entry.size = len(data)
                tar.addfile(entry, io.BytesIO(data))
            link = tarfile.TarInfo(f"{root}/{bundle}/node_modules/.bin/vitest")
            link.type = tarfile.SYMTYPE
            link.linkname = "../vitest/package.json"
            tar.addfile(link)
            if bad_member:
                name, kind, target = bad_member
                bad = tarfile.TarInfo(f"{root}/{name}")
                bad.type, bad.linkname = kind, target
                tar.addfile(bad)
        checksum = hashlib.sha256(path.read_bytes()).hexdigest()
        path.with_suffix(".gz.sha256").write_text(f"{checksum}  {ARCHIVE}\n")

    def install(self, **env):
        # Exercise the action's real CLI with an Ubuntu host identity, even on a
        # development system with another Linux distribution or Python path.
        driver = """\
import os, platform, runpy, sys
platform.system = lambda: 'Linux'
platform.machine = lambda: os.environ['TEST_ARCH']
platform.freedesktop_os_release = lambda: {'ID': 'ubuntu', 'VERSION_ID': os.environ['TEST_HOST']}
runpy.run_path(sys.argv[1], run_name='__main__')
"""
        return subprocess.run([sys.executable, "-IB", "-c", driver, str(ACTION / "install.py")],
                              env=self.env | env, cwd=self.workspace, capture_output=True, text=True)

    def assert_failed_install(self, result, diagnostic):
        self.assertNotEqual(result.returncode, 0, result.stdout)
        self.assertIn(diagnostic, result.stderr)
        self.assertFalse(Path(self.env["GITHUB_PATH"]).exists())
        self.assertFalse(Path(self.env["GITHUB_OUTPUT"]).exists())
        self.assertEqual(list(self.temp.iterdir()), [])

    def test_installs_verified_relocatable_prefix_and_exports_path(self):
        result = self.install()
        self.assertEqual(result.returncode, 0, result.stderr)
        outputs = dict(line.split("=", 1) for line in Path(self.env["GITHUB_OUTPUT"]).read_text().splitlines())
        self.assertEqual(outputs["version"], "1.0.0")
        self.assertEqual(Path(self.env["GITHUB_PATH"]).read_text(), f"{outputs['bin-dir']}\n")
        executable = Path(outputs["executable"])
        self.assertEqual(executable.parent, Path(outputs["bin-dir"]))
        self.assertTrue(executable.is_relative_to(self.temp))
        self.assertFalse(executable.is_relative_to(self.workspace))
        prefix = executable.parents[1]
        self.assertEqual((prefix / "lib/oximut/node_modules/.bin/vitest").read_text(), "{}")
        self.assertEqual(list(prefix.parent.iterdir()), [prefix])

    def test_version_only_install(self):
        result = self.install(OXIMUT_ACTION_VERSION="1.0.0", OXIMUT_ACTION_RELEASE_COMMIT="")
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_rejects_bad_inputs_prerequisites_and_runner_paths(self):
        for env, diagnostic in (
            ({"OXIMUT_ACTION_VERSION": ""}, "pinned stable release"),
            ({"OXIMUT_ACTION_VERSION": "latest"}, "pinned stable release"),
            ({"OXIMUT_ACTION_VERSION": "1.0.0-rc.3"}, "pinned stable release"),
            ({"OXIMUT_ACTION_VERSION": "1.0.00"}, "pinned stable release"),
            ({"OXIMUT_ACTION_RELEASE_COMMIT": "main"}, "full lowercase Git commit SHA"),
            ({"OXIMUT_ACTION_RUN": "yes"}, "run must be true or false"),
            ({"TEST_NODE_VERSION": "v26.3.2"}, "install Node.js 26.3.1"),
            ({"TEST_HOST": "26.04"}, "requires Ubuntu 24.04 x86-64"),
            ({"TEST_ARCH": "aarch64"}, "requires Ubuntu 24.04 x86-64"),
            ({"RUNNER_TEMP": str(self.workspace)}, "outside the checkout"),
        ):
            with self.subTest(env=env):
                self.assert_failed_install(self.install(**env), diagnostic)

    def test_rejects_bad_checksum_without_asking_gh_to_verify(self):
        (self.assets / f"{ARCHIVE}.sha256").write_text(f"{'0' * 64}  {ARCHIVE}\n")
        self.assert_failed_install(self.install(TEST_ATTESTATION="invalid-signature"), "checksum mismatch")

    def test_rejects_invalid_or_unrelated_attestations(self):
        for case in ("invalid-signature", "wrong-subject", "wrong-repository-id", "conflicting-digest"):
            with self.subTest(case=case):
                expected = "attestation" if case == "invalid-signature" else "no verified attestation names"
                self.assert_failed_install(self.install(TEST_ATTESTATION=case), expected)

    def test_rejects_unsafe_attested_prefix_members(self):
        for member, diagnostic in (
            (("../outside", tarfile.REGTYPE, ""), "unexpected prefix archive path"),
            (("bin/oximut", tarfile.REGTYPE, ""), "duplicate prefix archive path"),
            (("external", tarfile.SYMTYPE, "../../outside"), "outside the destination"),
            (("hardlink", tarfile.LNKTYPE, "bin/oximut"), "unsupported prefix archive member"),
        ):
            with self.subTest(member=member):
                self.make_archive(bad_member=member)
                self.assert_failed_install(self.install(), diagnostic)

    def test_rejects_wrong_cli_version(self):
        self.assert_failed_install(self.install(TEST_REPORTED_VERSION="2.0.0"), "unexpected version")

    def test_optional_run_passes_literal_arguments_and_cli_result(self):
        self.assertEqual(self.install().returncode, 0)
        outputs = dict(line.split("=", 1) for line in Path(self.env["GITHUB_OUTPUT"]).read_text().splitlines())
        arguments = ["--source", "a file.ts", "  spaces and \\  ", "$(touch injected)", "*"]
        result = subprocess.run(["bash", str(ACTION / "run.sh")], cwd=self.workspace, text=True,
                                capture_output=True, env=self.env | {"OXIMUT_ACTION_EXECUTABLE": outputs["executable"],
                                                                    "OXIMUT_ACTION_ARGS": "\n\n".join(arguments)})
        self.assertEqual(result.returncode, 17, result.stderr)
        self.assertEqual(json.loads(result.stdout), [str(self.workspace), *arguments])
        self.assertFalse((self.workspace / "injected").exists())
