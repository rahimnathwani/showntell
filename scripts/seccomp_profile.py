# /// script
# requires-python = ">=3.12"
# dependencies = []
# ///
"""deploy/chromium-seccomp.json: Docker's default seccomp profile plus the calls Chromium's sandbox needs.

The default profile comes from moby/profiles (`seccomp/default.json`) at a pinned commit, recorded in
the added rule's comment. `--check` says whether a newer default exists; `--commit SHA` rebuilds the
file from that commit (`--commit latest` for the newest). Needs `gh` for the latest commit.

    uv run scripts/seccomp_profile.py --check
    uv run scripts/seccomp_profile.py --commit latest
"""

from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
import urllib.request
from pathlib import Path

OUT = Path(__file__).parent.parent / "deploy" / "chromium-seccomp.json"
CALLS = ["chroot", "clone", "setns", "unshare"]
NOTE = (
    "showntell: Chromium's sandbox makes user namespaces and chroots inside them; the default profile allows clone with "
    "namespace flags, unshare, setns and chroot only with CAP_SYS_ADMIN or CAP_SYS_CHROOT, which the container drops "
    "(docs/self-hosting.md). Docker default from moby/profiles {sha}"
)


def latest() -> str:
    out = subprocess.run(
        ["gh", "api", "repos/moby/profiles/commits?path=seccomp/default.json&per_page=1", "--jq", ".[0].sha"],
        capture_output=True,
        text=True,
        check=True,
    )
    return out.stdout.strip()


def pinned() -> str:
    profile = json.loads(OUT.read_text())
    rule = next(r for r in profile["syscalls"] if r.get("comment", "").startswith("showntell:"))
    m = re.search(r"moby/profiles ([0-9a-f]{7,40})", rule["comment"])
    return m.group(1) if m else ""


def build(sha: str) -> None:
    url = f"https://raw.githubusercontent.com/moby/profiles/{sha}/seccomp/default.json"
    with urllib.request.urlopen(url, timeout=30) as r:
        profile = json.load(r)
    profile["syscalls"].append({"comment": NOTE.format(sha=sha[:7]), "names": CALLS, "action": "SCMP_ACT_ALLOW"})
    OUT.write_text(json.dumps(profile, indent=1) + "\n")
    print(f"wrote {OUT} from moby/profiles {sha[:7]}")


def main() -> None:
    p = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    g = p.add_mutually_exclusive_group(required=True)
    g.add_argument("--check", action="store_true", help="exit 1 if moby/profiles has a newer default profile")
    g.add_argument("--commit", help="rebuild from this moby/profiles commit, or `latest`")
    args = p.parse_args()
    if args.check:
        have, newest = pinned(), latest()
        if newest.startswith(have):
            print(f"up to date: moby/profiles {have}")
            return
        print(f"pinned to {have}; moby/profiles has {newest[:7]}. Rebuild with --commit latest, then run the container check.")
        sys.exit(1)
    build(latest() if args.commit == "latest" else args.commit)


if __name__ == "__main__":
    main()
