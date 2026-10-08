"""Meeting pictures kept per student, and the extra-faces table."""

from __future__ import annotations

import threading

import numpy as np

from app.core.face_gallery import FaceGallery
from app.data import db


def unit(v):
    v = np.asarray(v, dtype=np.float32)
    return v / np.linalg.norm(v)


def rnd(seed, base=None, mix=0.5):
    rng = np.random.default_rng(seed)
    v = rng.normal(size=8)
    if base is not None:
        v = base * (1 - mix) + unit(v) * mix
    return unit(v)


def make(known, max_per=3):
    store: dict[int, tuple[int, np.ndarray]] = {}
    ids = iter(range(1, 1000))

    def add(sid, emb):
        fid = next(ids)
        store[fid] = (sid, emb)
        return fid

    def replace(fid, emb):
        store[fid] = (store[fid][0], emb)

    g = FaceGallery(known, threading.Lock(), lambda sid: [], add, replace, max_per_student=max_per, every=60)
    return g, store


def test_keeps_new_looks_and_skips_repeats():
    main = rnd(0)
    known = [(1, main)]
    g, store = make(known)
    assert not g.offer(1, unit(main + rnd(9) * 0.05), now=0)      # same as the photo
    assert g.offer(1, rnd(1, main), now=100)                          # a new look
    assert not g.offer(1, rnd(2, main), now=120)                      # too soon
    assert len(store) == 1 and len(known) == 2


def test_full_gallery_replaces_the_most_redundant():
    main = rnd(0)
    known = [(1, main)]
    g, store = make(known, max_per=3)
    looks = [rnd(10 + i, main, 0.8) for i in range(5)]
    t = 0
    for v in looks:
        t += 100
        g.offer(1, v, now=t)
    assert len(store) == 3
    assert sum(1 for s, _ in known if s == 1) == 4       # main + 3 kept


def test_extra_faces_are_matched_and_deleted_with_the_student(tmp_path, monkeypatch):
    monkeypatch.setattr(db, "DB_PATH", tmp_path / "a.db")
    db.init_db()
    cid = db.create_class("CS101")["id"]
    sid = db.add_student("2024-1", "Kian", rnd(0), cid, extra=[rnd(1), rnd(2)])
    db.add_face(sid, rnd(3), "meeting")
    assert len(db.all_embeddings(cid)) == 4
    assert len(db.all_embeddings(cid, extra=False)) == 1
    assert [s for _, _, s in db.list_faces(sid)] == ["enrol", "enrol", "meeting"]
    db.delete_student(sid)
    assert db.all_embeddings(cid) == []
    with db._connect() as conn:
        assert conn.execute("SELECT COUNT(*) FROM student_faces").fetchone()[0] == 0
