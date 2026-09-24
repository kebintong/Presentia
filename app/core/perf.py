"""Processing device, performance profile and check-in security settings.

Settings (persisted next to the database in ``performance.json``):

- ``device``: "auto", "cpu", or "gpu:<index>". Adapters are discovered on the
  machine at run time (DXGI on Windows, CUDA elsewhere) — nothing is tied to
  a particular card or vendor.
- ``profile``: "auto", "low", "balanced" or "high" (see PROFILES). Auto uses
  the result of a short hardware check that times face detection on the CPU
  and on every usable GPU. The check runs on first launch and again whenever
  the CPU, memory, graphics card, graphics driver or onnxruntime changes.
- ``random_challenges``: randomised liveness prompts at check-in.
- ``antispoof``: photo / screen-replay detection at check-in.
"""

from __future__ import annotations

import json
import os
import platform
import subprocess
import sys
import threading
import time
from pathlib import Path

import numpy as np

_lock = threading.Lock()
_DEFAULTS = {"device": "auto", "profile": "auto", "random_challenges": True, "antispoof": False}
_settings: dict | None = None

# What each profile changes. The recogniser (ArcFace) is the same in all of
# them, so switching never affects who matches whom.
PROFILES: dict[str, dict] = {
    "low": {
        "label": "Low",
        "summary": "For older or low-power computers: smaller detectors, 15 fps preview, "
                   "4 recognition passes a second.",
        "cam_detector": "yunet", "cam_det_size": 320,
        "meet_detector": "scrfd_2.5g", "meet_det_size": 640,
        "preview_fps": 15, "preview_max_w": 960, "jpeg_q": 65,
        "analysis_fps": 4.0, "monitor_every": 2,
        "threads": "half", "priority": "normal",
    },
    "balanced": {
        "label": "Balanced",
        "summary": "Light detector, full-rate preview, 8 recognition passes a second.",
        "cam_detector": "scrfd_2.5g", "cam_det_size": 480,
        "meet_detector": "scrfd_2.5g", "meet_det_size": 640,
        "preview_fps": 24, "preview_max_w": 1280, "jpeg_q": 70,
        "analysis_fps": 8.0, "monitor_every": 1,
        "threads": "half", "priority": "normal",
    },
    "high": {
        "label": "High",
        "summary": "Full-size detector, recognition as often as possible, higher priority. "
                   "Best for large classes on fast computers.",
        "cam_detector": "scrfd_10g", "cam_det_size": 640,
        "meet_detector": "scrfd_10g", "meet_det_size": 640,
        "preview_fps": 24, "preview_max_w": 1280, "jpeg_q": 72,
        "analysis_fps": 0.0, "monitor_every": 1,
        "threads": "most", "priority": "above",
    },
}


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
            # 1.3.0 had a "high performance" switch instead of profiles.
            if "high_performance" in s:
                if s.pop("high_performance") and s.get("profile", "auto") == "auto":
                    s["profile"] = "high"
            _settings = s
        return dict(_settings)


def save_settings(**changes) -> dict:
    global _settings
    s = get_settings()
    for k, v in changes.items():
        if v is not None:
            s[k] = v
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


def total_ram_gb() -> float:
    try:
        if sys.platform == "win32":
            import ctypes

            class MEMSTAT(ctypes.Structure):
                _fields_ = [("dwLength", ctypes.c_uint32), ("dwMemoryLoad", ctypes.c_uint32),
                            ("ullTotalPhys", ctypes.c_uint64), ("ullAvailPhys", ctypes.c_uint64),
                            ("ullTotalPageFile", ctypes.c_uint64), ("ullAvailPageFile", ctypes.c_uint64),
                            ("ullTotalVirtual", ctypes.c_uint64), ("ullAvailVirtual", ctypes.c_uint64),
                            ("ullAvailExtendedVirtual", ctypes.c_uint64)]

            m = MEMSTAT()
            m.dwLength = ctypes.sizeof(MEMSTAT)
            ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(m))
            return m.ullTotalPhys / 2**30
        return os.sysconf("SC_PAGE_SIZE") * os.sysconf("SC_PHYS_PAGES") / 2**30
    except Exception:  # noqa: BLE001
        return 0.0


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
                        # Driver version (used to notice driver updates and
                        # re-check performance): IDXGIAdapter::CheckInterfaceSupport = 9.
                        umd = ctypes.c_longlong(0)
                        iid_device = GUID(0x54EC77FA, 0x1377, 0x44E6,
                                          (ctypes.c_ubyte * 8)(0x8C, 0x32, 0x88, 0xFD, 0x5F, 0x44, 0xC8, 0x4C))
                        drv = ""
                        try:
                            if method(adapter, 9, ctypes.POINTER(GUID), ctypes.POINTER(ctypes.c_longlong))(
                                    adapter, ctypes.byref(iid_device), ctypes.byref(umd)) == 0:
                                v = umd.value
                                drv = f"{v >> 48 & 0xFFFF}.{v >> 32 & 0xFFFF}.{v >> 16 & 0xFFFF}.{v & 0xFFFF}"
                        except Exception:  # noqa: BLE001
                            pass
                        out.append({
                            "index": index,
                            "name": desc.Description.strip(),
                            "vram_mb": int(desc.DedicatedVideoMemory // (1024 * 1024)),
                            "driver": drv,
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


# ── Hardware check (benchmark) ───────────────────────────────────────────────

def hardware_signature() -> str:
    try:
        import onnxruntime as ort

        ort_v = ort.__version__
    except Exception:  # noqa: BLE001
        ort_v = "?"
    gpus = ";".join(f"{g['name']}|{g['vram_mb']}|{g.get('driver', '')}" for g in list_gpus())
    return "|".join([platform.processor() or platform.machine(), str(os.cpu_count()),
                     f"{round(total_ram_gb())}", gpus, ort_v, ",".join(available_providers())])


def _plan(device_id: str) -> dict | None:
    """Provider setup for "cpu" or "gpu:<n>", or None if that GPU is gone."""
    if device_id == "cpu":
        return {"id": "cpu", "providers": ["CPUExecutionProvider"], "provider_options": [{}],
                "device": "CPU", "backend": "CPU"}
    g = next((g for g in list_gpus() if g["id"] == device_id and g["usable"]), None)
    if g is None:
        return None
    ep = "DmlExecutionProvider" if g["backend"] == "DirectML" else "CUDAExecutionProvider"
    return {"id": g["id"], "providers": [ep, "CPUExecutionProvider"],
            "provider_options": [{"device_id": str(g["index"])}, {}],
            "device": g["name"], "backend": g["backend"]}


def cpu_threads(threads: str = "half") -> int:
    cores = os.cpu_count() or 4
    # "half" leaves room for capture, encoding, the webview and the meeting
    # app itself — which is what keeps the live preview from stuttering.
    half = max(2, cores // 2)
    return max(half, cores - 1) if threads == "most" else half


def session_options(backend: str, threads: str = "half"):
    import onnxruntime as ort

    so = ort.SessionOptions()
    so.intra_op_num_threads = cpu_threads(threads)
    so.inter_op_num_threads = 1
    so.graph_optimization_level = ort.GraphOptimizationLevel.ORT_ENABLE_ALL
    so.log_severity_level = 3
    if backend == "DirectML":
        # Required by the DirectML execution provider.
        so.enable_mem_pattern = False
        so.execution_mode = ort.ExecutionMode.ORT_SEQUENTIAL
    return so


def session_maker(plan: dict, threads: str = "half"):
    import onnxruntime as ort

    so = session_options(plan["backend"], threads)

    def make(path: str):
        return ort.InferenceSession(path, sess_options=so, providers=plan["providers"],
                                    provider_options=plan["provider_options"])

    return make


_bench_lock = threading.Lock()


def benchmark(force: bool = False) -> dict:
    """Time face detection on the CPU and every usable GPU; pick the fastest
    device and the profile that suits this computer. Cached until the
    hardware, driver or onnxruntime changes."""
    with _bench_lock:
        sig = hardware_signature()
        stored = get_settings().get("benchmark")
        if stored and stored.get("signature") == sig and not force:
            return stored

        from app.core import face_models as fm

        det_path = fm.model_path("scrfd_2.5g")
        rng = np.random.default_rng(0)
        img = rng.integers(0, 255, (480, 640, 3), dtype=np.uint8)
        results = []
        for dev in ["cpu"] + [g["id"] for g in list_gpus() if g["usable"]]:
            plan = _plan(dev)
            if plan is None:
                continue
            try:
                det = fm.SCRFD(det_path, session_maker(plan, "half"), det_size=640)
                ms = fm.time_it(lambda d=det: d.detect(img), runs=7, warmup=3)
                results.append({"id": dev, "device": plan["device"], "backend": plan["backend"],
                                "ms": round(ms, 1)})
            except Exception as exc:  # noqa: BLE001 - a GPU that cannot run it
                results.append({"id": dev, "device": plan["device"], "backend": plan["backend"],
                                "error": str(exc)[:200]})
        ok = [r for r in results if "ms" in r]
        best = min(ok, key=lambda r: r["ms"]) if ok else {"id": "cpu", "ms": 999.0}
        cores, ram = os.cpu_count() or 1, total_ram_gb()
        if cores <= 2 or (0 < ram < 4.5):
            rec = "low"
        elif best["ms"] <= 6:
            rec = "high"
        elif best["ms"] <= 30:
            rec = "balanced"
        else:
            rec = "low"
        out = {"signature": sig, "results": results, "best": best["id"], "best_ms": best["ms"],
               "recommended": rec, "cores": cores, "ram_gb": round(ram, 1),
               "measured_at": time.strftime("%Y-%m-%d %H:%M")}
        save_settings(benchmark=out)
        return out


# ── What to run with ─────────────────────────────────────────────────────────

def effective_profile(settings: dict | None = None) -> str:
    s = settings or get_settings()
    p = s.get("profile", "auto")
    if p in PROFILES:
        return p
    return (s.get("benchmark") or {}).get("recommended", "balanced")


def profile_params(settings: dict | None = None) -> dict:
    return PROFILES[effective_profile(settings)]


def engine_plans(settings: dict | None = None) -> list[dict]:
    """Provider setups to try, best first; the CPU is always the last resort."""
    s = settings or get_settings()
    device = str(s.get("device", "auto"))
    cpu = _plan("cpu")
    if device == "cpu":
        return [cpu]
    chosen = _plan(device) if device.startswith("gpu:") else None
    if chosen is None:
        best = (s.get("benchmark") or {}).get("best")
        if best:
            chosen = _plan(best)  # auto: the fastest device measured
        else:
            gpus = [g for g in list_gpus() if g["usable"]]
            if gpus:
                chosen = _plan(max(gpus, key=lambda g: g["vram_mb"])["id"])
    if chosen is None or chosen["id"] == "cpu":
        return [cpu]
    return [chosen, cpu]


def analysis_min_interval() -> float:
    """Minimum seconds between screen-analysis passes (0 = as fast as possible)."""
    fps = profile_params()["analysis_fps"]
    return 1.0 / fps if fps else 0.0


def apply_process_priority() -> None:
    """The High profile runs the sidecar above normal priority (Windows)."""
    if sys.platform != "win32":
        return
    try:
        import ctypes

        cls = 0x8000 if profile_params()["priority"] == "above" else 0x20  # ABOVE_NORMAL / NORMAL
        k32 = ctypes.windll.kernel32
        k32.SetPriorityClass(k32.GetCurrentProcess(), cls)
    except Exception:  # noqa: BLE001
        pass
