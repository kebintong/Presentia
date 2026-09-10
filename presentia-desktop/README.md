# Presentia Desktop Shell (Wails v2 + React 19 + TypeScript)

This directory contains the desktop client for **Presentia**, built with [Wails v2](https://wails.io/), React 19, TypeScript, Vite, and Tailwind CSS v4.

For the full project documentation, architecture diagrams, and Python AI sidecar setup, see the root [README.md](../README.md).

---

## Development

Make sure you have installed:
- Go 1.21+
- Wails CLI v2 (`go install github.com/wailsapp/wails/v2/cmd/wails@latest`)
- Node.js 18+ and npm
- Python 3.10–3.12 (with virtual environment configured at root)

### 1. Install frontend packages
```bash
cd frontend
npm install
cd ..
```

### 2. Run in live development mode
```bash
wails dev
```
Running `wails dev` automatically:
1. Spawns the Python FastAPI AI sidecar in the background (`http://127.0.0.1:7788`).
2. Starts the Vite frontend dev server with Hot Module Replacement (HMR).
3. Opens the frameless desktop window.

---

## Production Build

To compile a standalone redistributable executable:
```bash
wails build
```

The output binary will be placed in `build/bin/`.
