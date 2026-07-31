#!/usr/bin/env bash
# Launch the AI Attendance app (Linux).
cd "$(dirname "$0")"
LD_LIBRARY_PATH=".local-libs:$LD_LIBRARY_PATH" exec .venv/bin/python -m app.main
