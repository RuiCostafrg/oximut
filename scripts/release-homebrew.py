#!/usr/bin/env python3
"""Render the Homebrew formula for a qualified release prefix."""
import argparse
import hashlib
import json
from pathlib import Path
import re


def render(prefix, evidence, version):
    if not re.fullmatch(r"[0-9]+\.[0-9]+\.[0-9]+(?:-rc\.[0-9]+)?", version):
        raise ValueError("invalid release version")
    validation = json.loads(evidence.read_text())
    binary_sha = validation.get("binarySha256", "")
    if (validation.get("version") != version or validation.get("status") != "passed"
            or not re.fullmatch(r"[0-9a-f]{64}", binary_sha)):
        raise ValueError("release version or binary digest does not match validation evidence")
    with prefix.open("rb") as stream:
        prefix_sha = hashlib.file_digest(stream, "sha256").hexdigest()
    template = Path(__file__).resolve().parents[1] / "packaging/homebrew/oximut.rb.in"
    return (template.read_text().replace("@VERSION@", version)
            .replace("@PREFIX_SHA256@", prefix_sha).replace("@BINARY_SHA256@", binary_sha))


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--prefix", required=True, type=Path)
    parser.add_argument("--evidence", required=True, type=Path)
    parser.add_argument("--version", required=True)
    args = parser.parse_args()
    try:
        print(render(args.prefix, args.evidence, args.version), end="")
    except (ValueError, TypeError, OSError) as error:
        parser.exit(1, f"formula generation failed: {error}\n")
