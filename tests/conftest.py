"""Shared test setup.

The environment is set before anything from `app` is imported: the database
path is decided at import time, and PRESENTIA_SKIP_ENGINE stops the sidecar
from loading (or downloading) the face models.
"""

import os
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

os.environ.setdefault("PRESENTIA_DATA_DIR", tempfile.mkdtemp(prefix="presentia-test-"))
os.environ["PRESENTIA_SKIP_ENGINE"] = "1"
os.environ.pop("PRESENTIA_CLOUD_URL", None)

import numpy as np  # noqa: E402
import pytest  # noqa: E402

from app.data import db  # noqa: E402


@pytest.fixture
def fresh_db(tmp_path, monkeypatch):
    """An empty, fully migrated database used only by this test."""
    monkeypatch.setattr(db, "DB_PATH", tmp_path / "attendance.db")
    db.init_db()
    return db


def embedding(seed: int = 0) -> np.ndarray:
    """A random, normalised 512-d face template."""
    v = np.random.default_rng(seed).standard_normal(512).astype(np.float32)
    return v / np.linalg.norm(v)
