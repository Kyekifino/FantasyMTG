"""Guard the layering: core must never depend on an interface, transport or database."""
import ast
from pathlib import Path

import pytest

SRC = Path(__file__).resolve().parents[1] / "src" / "fantasymtg"
FORBIDDEN = {
    "core": {"discord", "aiohttp", "sqlite3", "fantasymtg.adapters", "fantasymtg.discord_bot",
             "fantasymtg.presentation"},
    "presentation": {"discord", "aiohttp", "sqlite3", "fantasymtg.adapters", "fantasymtg.discord_bot"},
    "adapters": {"discord", "fantasymtg.discord_bot", "fantasymtg.presentation"},
}


def _imports(path: Path, package: str) -> set[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"))
    names = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            names |= {a.name for a in node.names}
        elif isinstance(node, ast.ImportFrom):
            if node.level:  # resolve relative imports against fantasymtg.<package>
                base = ["fantasymtg", package][: max(0, 2 - (node.level - 1))]
                names.add(".".join(base + ([node.module] if node.module else [])))
            elif node.module:
                names.add(node.module)
    return names


@pytest.mark.parametrize("package", sorted(FORBIDDEN))
def test_layer_imports(package):
    for path in (SRC / package).glob("*.py"):
        for name in _imports(path, package):
            for bad in FORBIDDEN[package]:
                assert not (name == bad or name.startswith(bad + ".")), f"{path.name} imports {name}"
