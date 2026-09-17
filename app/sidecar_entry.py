"""PyInstaller entry point for the frozen sidecar.

`uvicorn app.sidecar:app --host ... --port ...` (used in dev, via the CLI)
relies on uvicorn's import-string resolution, which doesn't work once
everything is frozen into a single executable. This script imports the
FastAPI `app` object directly and calls `uvicorn.run()` on it instead, so
it behaves identically whether it's `python app/sidecar_entry.py` in dev
or `presentia-sidecar.exe` once built.

Usage (matches what app.go invokes):
    presentia-sidecar.exe --host 127.0.0.1 --port 7788
"""

from __future__ import annotations

import argparse
import multiprocessing

import uvicorn

from app.sidecar import app


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=7788)
    args = parser.parse_args()

    uvicorn.run(app, host=args.host, port=args.port, log_level="warning")


if __name__ == "__main__":
    # Required on Windows when the frozen exe spawns subprocesses
    # (onnxruntime / mediapipe threads count as such in some builds).
    multiprocessing.freeze_support()
    main()
