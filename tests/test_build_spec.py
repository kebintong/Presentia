"""Every Presentia module the sidecar can reach must be listed in the
PyInstaller spec. A missing one only fails in the installed app
(ModuleNotFoundError), never in development, so catch it here."""

import ast
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def _app_imports(module: str) -> set[str]:
    path = ROOT / (module.replace(".", "/") + ".py")
    if not path.exists():
        path = ROOT / module.replace(".", "/") / "__init__.py"
    tree = ast.parse(path.read_text(encoding="utf-8"))
    found = set()
    for node in ast.walk(tree):  # walk: includes imports inside functions
        if isinstance(node, ast.Import):
            found |= {a.name for a in node.names if a.name.startswith("app.")}
        elif isinstance(node, ast.ImportFrom) and node.module and node.level == 0:
            if node.module == "app":
                found |= {f"app.{a.name}" for a in node.names}
            elif node.module.startswith("app."):
                found.add(node.module)
                # "from app.data import db" names a submodule
                for a in node.names:
                    sub = ROOT / (node.module.replace(".", "/")) / f"{a.name}.py"
                    if sub.exists():
                        found.add(f"{node.module}.{a.name}")
    return found


def _reachable(start: str) -> set[str]:
    seen, todo = set(), [start]
    while todo:
        mod = todo.pop()
        if mod in seen:
            continue
        seen.add(mod)
        todo.extend(_app_imports(mod) - seen)
    return seen


def test_spec_lists_every_reachable_module():
    spec = (ROOT / "build" / "presentia-sidecar.spec").read_text(encoding="utf-8")
    listed = set(re.findall(r'"(app(?:\.[a-z0-9_]+)*)"', spec))
    needed = {m for m in _reachable("app.sidecar") if m.count(".") >= 2 or m == "app.sidecar"}
    missing = sorted(needed - listed)
    assert not missing, f"add to hiddenimports in build/presentia-sidecar.spec: {missing}"
