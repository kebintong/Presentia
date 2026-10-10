"""build/release_notes.py: What's new comes from RELEASE_NOTES.md."""

import importlib.util
from pathlib import Path

spec = importlib.util.spec_from_file_location("release_notes", Path(__file__).resolve().parents[1] / "build" / "release_notes.py")
rn = importlib.util.module_from_spec(spec)
spec.loader.exec_module(rn)

DOC = """# Presentia — what's new

Intro.

## Next release

<!-- Write what changed for teachers here, as short bullet points. -->

- Monitoring keeps going when you change pages.
- Half-visible faces still count.

## 1.6.1 — 2026-10-08

- New floating bubble.
"""


def test_next_notes_skip_the_comment():
    assert rn.next_notes(DOC) == "- Monitoring keeps going when you change pages.\n- Half-visible faces still count."


def test_body_has_whats_new_changes_and_download():
    out = rn.body("1.6.2", "1.6.1", "abc", rn.next_notes(DOC), ["- Monitor fixes"])
    assert out.startswith("## What's new\n\n- Monitoring keeps going")
    assert "## Changes since v1.6.1\n\n- Monitor fixes" in out and "SHA-256: `abc`" in out
    assert rn.PLACEHOLDER not in out


def test_empty_next_release_uses_the_placeholder():
    empty = DOC.split("- Monitoring")[0] + "## 1.6.1 — 2026-10-08\n\n- x\n"
    assert rn.next_notes(empty) == ""
    assert rn.PLACEHOLDER in rn.body("1.6.2", "1.6.1", "abc", "", [])


def test_rotate_files_the_notes_under_the_version():
    out = rn.rotate(DOC, "1.6.2", "2026-10-09")
    assert rn.next_notes(out) == ""
    head, rest = out.split("## 1.6.2 — 2026-10-09", 1)
    assert "## Next release" in head and "<!-- Write what changed" in head
    assert rest.strip().startswith("- Monitoring keeps going")
    assert rest.index("## 1.6.1") > rest.index("Half-visible")
    # Rotating again with nothing new changes nothing.
    assert rn.rotate(out, "1.6.3", "2026-10-10") == out


def test_teacher_notes_in_the_app_read_the_published_body():
    """The app shows the part above '## Changes since' (ReleaseNotes.tsx)."""
    out = rn.body("1.6.2", "1.6.1", "abc", rn.next_notes(DOC), ["- x"])
    teacher = out.split("## Changes since")[0]
    assert "Half-visible faces" in teacher and "Edit this" not in teacher


def test_real_file_is_well_formed():
    """Right after a release "Next release" is empty; otherwise it holds bullet points."""
    text = (Path(__file__).resolve().parents[1] / "RELEASE_NOTES.md").read_text(encoding="utf-8")
    notes = rn.next_notes(text)
    assert notes == "" or notes.startswith("- ")
    assert "## Next release" in text
