"""spec §3.2：lib 子 package 自足——禁止 import hippo 其他模組。"""
import ast
from pathlib import Path

LIB_DIR = Path(__file__).resolve().parents[1] / "paulsha_hippo" / "lib"


def _violations() -> list[str]:
    found: list[str] = []
    for py in sorted(LIB_DIR.rglob("*.py")):
        tree = ast.parse(py.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            names: list[str] = []
            if isinstance(node, ast.Import):
                names = [a.name for a in node.names]
            elif isinstance(node, ast.ImportFrom) and node.level == 0 and node.module:
                names = [node.module]
            for name in names:
                if name.startswith("paulsha_hippo") and not name.startswith("paulsha_hippo.lib"):
                    found.append(f"{py.relative_to(LIB_DIR.parent.parent)}: {name}")
    return found


def test_lib_dir_exists():
    assert LIB_DIR.is_dir(), "paulsha_hippo/lib/ 不存在"


def test_lib_has_no_internal_hippo_imports():
    assert _violations() == []
