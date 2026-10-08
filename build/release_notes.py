"""Release notes for the Release workflow, from RELEASE_NOTES.md.

RELEASE_NOTES.md keeps the teacher-facing "What's new" of every version. New
changes are written under "## Next release" as they are made; the Release
workflow then:

  python build/release_notes.py body 1.6.2 1.6.1 <sha256>   -> release-notes.md
  python build/release_notes.py rotate 1.6.2 2026-10-08      -> RELEASE_NOTES.md

`body` builds the GitHub release text (What's new, the commit list, the
download note). It exits with code 3 when nothing was written for the next
release, so the workflow keeps that release as a draft to be filled in.
`rotate` moves the "Next release" notes under a "## 1.6.2 — date" heading and
leaves "Next release" empty for the following changes.
"""

from __future__ import annotations

import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
NOTES = ROOT / "RELEASE_NOTES.md"
NEXT = "## Next release"
PLACEHOLDER = "_Edit this before publishing: say in plain words what changed for teachers._"


def _split(text: str) -> tuple[str, str, str]:
    """(before, next-release notes, after) of RELEASE_NOTES.md."""
    lines = text.replace("\r\n", "\n").split("\n")
    try:
        start = next(i for i, l in enumerate(lines) if l.strip().lower() == NEXT.lower())
    except StopIteration:
        return text, "", ""
    end = next((i for i in range(start + 1, len(lines)) if lines[i].startswith("## ")), len(lines))
    body = "\n".join(lines[start + 1:end])
    # HTML comments are notes to the writer, not to teachers.
    body = re.sub(r"<!--.*?-->", "", body, flags=re.S).strip()
    return "\n".join(lines[:start + 1]), body, "\n".join(lines[end:])


def next_notes(text: str) -> str:
    return _split(text)[1]


def commit_list(previous: str) -> list[str]:
    rng = f"v{previous}..HEAD" if previous else "HEAD"
    try:
        out = subprocess.run(["git", "log", rng, "--no-merges", "--pretty=format:- %s"],
                             cwd=ROOT, capture_output=True, text=True, check=True).stdout
    except (OSError, subprocess.CalledProcessError):
        return []
    return [l for l in out.splitlines() if l.strip() and not re.match(r"^- Release \d", l)]


def body(version: str, previous: str, sha256: str, notes: str, changes: list[str]) -> str:
    parts = ["## What's new", "", notes or PLACEHOLDER, ""]
    if changes:
        parts += [f"## Changes since v{previous}" if previous else "## Changes", "", *changes, ""]
    parts += [
        "## Download", "",
        "Download **PresentiaSetup.exe** below and run it. Installed copies offer this update "
        "in Settings → Updates.", "",
        f"SHA-256: `{sha256}`",
    ]
    return "\n".join(parts) + "\n"


def rotate(text: str, version: str, date: str) -> str:
    before, notes, after = _split(text)
    if not notes:
        return text
    comment = "<!-- Write what changed for teachers here, as short bullet points. -->"
    section = f"## {version} — {date}\n\n{notes}\n"
    rest = after.lstrip("\n")
    return f"{before}\n\n{comment}\n\n{section}" + (f"\n{rest}" if rest else "")


def main(argv: list[str]) -> int:
    cmd = argv[1] if len(argv) > 1 else ""
    text = NOTES.read_text(encoding="utf-8") if NOTES.exists() else ""
    if cmd == "body":
        version, previous, sha256 = argv[2], argv[3], argv[4]
        notes = next_notes(text)
        out = body(version, previous, sha256, notes, commit_list(previous))
        (ROOT / "release-notes.md").write_text(out, encoding="utf-8")
        return 0 if notes else 3
    if cmd == "rotate":
        NOTES.write_text(rotate(text, argv[2], argv[3]), encoding="utf-8")
        return 0
    print(__doc__)
    return 2


if __name__ == "__main__":
    sys.exit(main(sys.argv))
