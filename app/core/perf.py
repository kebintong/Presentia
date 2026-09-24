"""Processing-device and performance settings for the face models.

Two user settings, persisted next to the database and applied when the
FaceEngine is (re)built:

- ``device``: "auto", "cpu", or "gpu:<index>" for a specific graphics
  adapter. Adapters are discovered on the machine at run time — nothing is
  tied to a particular card or vendor. On Windows every DirectX 12 capable
  GPU (AMD, NVIDIA, Intel) works through DirectML when the sidecar ships
  with ``onnxruntime-directml``; elsewhere CUDA is used when
  ``onnxruntime-gpu`` is installed.
- ``high_performance``: favour recognition speed over leaving CPU for other
  programs (more model threads, above-normal process priority, no pacing of
  analysis passes). Off by default, which keeps the live preview and the
  meeting app itself smooth.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import threading
from pathlib import Path

_lock = threading.Lock()
_DEFAULTS = {"device": "auto", "high_performance": False}
_settings: dict | None = None

# Balanced mode paces screen-monitoring analysis at this rate; the preview
# is unaffected (it runs on its own thread at full rate).
BALANCED_ANALYSIS_FPS = 8.0


def _settings_path() -> Path:
    from app.data import db

    return Path(db.DB_PATH).parent / "performance.json"


def get_settings() -> dict:
    global _settings
    with _lock:
        if _settings is None:
            s = dict(_DEFAULTS)
            try:
                s.update(json.loads(_settings_path().read_text(encoding="utf-8")))
            except Exception:  # noqa: BLE001 - missing/corrupt file = defaults
                pass
            _settings = s
        return dict(_settings)


def save_settings(device: str | None = None, high_performance: bool | None = None) -> dict:
    global _settings
    s = get_settings()
    if device is not None:
        s["device"] = device
    if high_performance is not None:
        s["high_performance"] = bool(high_performance)
    with _lock:
        _settings = s
        try:
            _settings_path().write_text(json.dumps(s), encoding="utf-8")
        except Exception:  # noqa: BLE001 - settings still apply for this run
            pass
    apply_process_priority()
    return dict(s)


# ── Hardware discovery ───────────────────────────────────────────────────────

def available_providers() -> list[str]:
    try:
        import onnxruntime as ort

        return list(ort.get_available_providers())
    except Exception:  # noqa: BLE001
        return []


def _dxgi_adapters() -> list[dict]:
    """Every hardware graphics adapter Windows reports, in DXGI order.

    The index is what DirectML's ``device_id`` refers to. Software adapters
    (Microsoft Basic Render Driver) are skipped but keep their index slot.
    """
    import ctypes
    from ctypes import wintypes

    class LUID(ctypes.Structure):
        _fields_ = [("LowPart", wintypes.DWORD), ("HighPart", wintypes.LONG)]

    class DESC1(ctypes.Structure):
        _fields_ = [
            ("Description", ctypes.c_wchar * 128),
            ("VendorId", wintypes.UINT),
            ("DeviceId", wintypes.UINT),
            ("SubSysId", wintypes.UINT),
            ("Revision", wintypes.UINT),
            ("DedicatedVideoMemory", ctypes.c_size_t),
            ("DedicatedSystemMemory", ctypes.c_size_t),
            ("SharedSystemMemory", ctypes.c_size_t),
            ("AdapterLuid", LUID),
            ("Flags", wintypes.UINT),
        ]

    class GUID(ctypes.Structure):
        _fields_ = [("Data1", wintypes.DWORD), ("Data2", wintypes.WORD),
                    ("Data3", wintypes.WORD), ("Data4", ctypes.c_ubyte * 8)]

    iid_factory1 = GUID(0x770AAE78, 0xF26F, 0x4DBA,
                        (ctypes.c_ubyte * 8)(0xA8, 0x29, 0x25, 0x3C, 0x83, 0xD1, 0xB3, 0x87))

    def method(obj: ctypes.c_void_p, index: int, *argtypes):
        vtbl = ctypes.cast(obj, ctypes.POINTER(ctypes.POINTER(ctypes.c_void_p))).contents
        return ctypes.WINFUNCTYPE(ctypes.c_long, ctypes.c_void_p, *argtypes)(vtbl[index])

    dxgi = ctypes.WinDLL("dxgi")
    factory = ctypes.c_void_p()
    if dxgi.CreateDXGIFactory1(ctypes.byref(iid_factory1), ctypes.byref(factory)) != 0:
        return []
    out: list[dict] = []
    try:
        # IDXGIFactory1 vtable: IUnknown(0-2), IDXGIObject(3-6),
        # IDXGIFactory(7-11), EnumAdapters1 = 12.
        enum_adapters1 = method(factory, 12, wintypes.UINT, ctypes.POINTER(ctypes.c_void_p))
        index = 0
        while index < 16:
            adapter = ctypes.c_void_p()
            if enum_adapters1(factory, index, ctypes.byref(adapter)) != 0:
                break  # DXGI_ERROR_NOT_FOUND: no more adapters
            try:
                # IDXGIAdapter1 vtable: ... GetDesc1 = 10.
                desc = DESC1()
                if method(adapter, 10, ctypes.POINTER(DESC1))(adapter, ctypes.byref(desc)) == 0:
                    software = bool(desc.Flags & 0x2) or desc.VendorId == 0x1414
                    if not software:
                        out.append({
                            "index": index,
                            "name": desc.Description.strip(),
                            "vram_mb": int(desc.DedicatedVideoMemory // (1024 * 1024)),
                        })
            finally:
                method(adapter, 2)(adapter)  # Release
            index += 1
    finally:
        method(factory, 2)(factory)  # Release
    return out


def _cuda_devices() -> list[dict]:
    names: list[str] = []
    try:
        res = subprocess.run(
            ["nvidia-smi", "--query-gpu=name,memory.total", "--format=csv,noheader,nounits"],
            capture_output=True, text=True, timeout=3,
            creationflags=0x08000000 if sys.platform == "win32" else 0,
        )
        names = [ln.strip() for ln in res.stdout.splitlines() if ln.strip()]
    except Exception:  # noqa: BLE001
        pass
    out = []
    for i, line in enumerate(names or ["NVIDIA GPU"]):
        name, _, mem = line.partition(",")
        vram = int(mem.strip()) if mem.strip().isdigit() else 0
        out.append({"index": i, "name": name.strip(), "vram_mb": vram})
    return out


_gpu_cache: list[dict] | None = None


def list_gpus() -> list[dict]:
    """GPUs usable for the face models on this machine, each with the
    backend that would drive it. Cached for the life of the process."""
    global _gpu_cache
    if _gpu_cache is not None:
        return _gpu_cache
    providers = available_providers()
    gpus: list[dict] = []
    if sys.platform == "win32":
        try:
            for a in _dxgi_adapters():
                gpus.append({**a, "id": f"gpu:{a['index']}", "backend": "DirectML",
                             "usable": "DmlExecutionProvider" in providers})
        except Exception:  # noqa: BLE001 - no DXGI (very old Windows / VM)
            pass
    if not gpus and "CUDAExecutionProvider" in providers:
        for a in _cuda_devices():
            gpus.append({**a, "id": f"gpu:{a['index']}", "backend": "CUDA", "usable": True})
    _gpu_cache = gpus
    return gpus


def gpu_runtime() -> str | None:
    """Which GPU backend this build of onnxruntime can use, if any."""
    providers = available_providers()
    if "DmlExecutionProvider" in providers:
        return "DirectML"
    if "CUDAExecutionProvider" in providers:
        return "CUDA"
    return None


# ── Engine configuration ─────────────────────────────────────────────────────

def cpu_threads(high_performance: bool) -> int:
    cores = os.cpu_count() or 4
    # Balanced: leave half the machine for capture, encoding, the webview and
    # the meeting app, which is what keeps the live preview from stuttering.
    balanced = max(2, cores // 2)
    if high_performance:
        return max(balanced, cores - 1)
    return balanced


def engine_plans(settings: dict | None = None) -> list[dict]:
    """Provider setups to try, best first; the CPU is always the last resort.

    Each plan: {"providers", "provider_options", "device", "backend"}.
    """
    s = settings or get_settings()
    device = str(s.get("device", "auto"))
    cpu = {"providers": ["CPUExecutionProvider"], "provider_options": [{}],
           "device": "CPU", "backend": "CPU"}
    if device == "cpu":
        return [cpu]

    gpus = [g for g in list_gpus() if g["usable"]]
    chosen = None
    if device.startswith("gpu:"):
        chosen = next((g for g in gpus if g["id"] == device), None)
    if chosen is None and gpus:
        # Auto (or a card that has since been removed): the adapter with the
        # most dedicated memory — a discrete GPU over an integrated one.
        chosen = max(gpus, key=lambda g: g["vram_mb"])
    if chosen is None:
        return [cpu]

    if chosen["backend"] == "DirectML":
        gpu = {"providers": ["DmlExecutionProvider", "CPUExecutionProvider"],
               "provider_options": [{"device_id": str(chosen["index"])}, {}],
               "device": chosen["name"], "backend": "DirectML"}
    else:
        gpu = {"providers": ["CUDAExecutionProvider", "CPUExecutionProvider"],
               "provider_options": [{"device_id": str(chosen["index"])}, {}],
               "device": chosen["name"], "backend": "CUDA"}
    return [gpu, cpu]


def session_options(backend: str, high_performance: bool):
    import onnxruntime as ort

    so = ort.SessionOptions()
    so.intra_op_num_threads = cpu_threads(high_performance)
    so.inter_op_num_threads = 1
    so.graph_optimization_level = ort.GraphOptimizationLevel.ORT_ENABLE_ALL
    if backend == "DirectML":
        # Required by the DirectML execution provider.
        so.enable_mem_pattern = False
        so.execution_mode = ort.ExecutionMode.ORT_SEQUENTIAL
    return so


def analysis_min_interval() -> float:
    """Minimum seconds between screen-analysis passes (0 = as fast as possible)."""
    return 0.0 if get_settings().get("high_performance") else 1.0 / BALANCED_ANALYSIS_FPS


def apply_process_priority() -> None:
    """High performance runs the sidecar above normal priority (Windows)."""
    if sys.platform != "win32":
        return
    try:
        import ctypes

        cls = 0x8000 if get_settings().get("high_performance") else 0x20  # ABOVE_NORMAL / NORMAL
        k32 = ctypes.windll.kernel32
        k32.SetPriorityClass(k32.GetCurrentProcess(), cls)
    except Exception:  # noqa: BLE001
        pass
