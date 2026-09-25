"""Keep core module docstrings present and current as the runtime evolves."""

from __future__ import annotations

import ast
from pathlib import Path


CORE_MODULES = [
    "main.py",
    "app_support.py",
    "web_routes.py",
    "zb_api.py",
    "admin_routes.py",
    "utilities.py",
    "config.py",
    "models.py",
]


def _module_path(name: str) -> Path:
    """Resolve one core module path relative to the test file."""
    return Path(__file__).resolve().parents[1] / name


def _missing_docstrings(path: Path) -> list[str]:
    """Return missing module/class/function docstrings for one core module.

    The check is intentionally scoped to top-level objects and class methods so
    nested closures used inside route handlers do not create noisy failures.
    """
    tree = ast.parse(path.read_text(encoding="utf-8"))
    missing: list[str] = []

    if ast.get_docstring(tree) is None:
        missing.append("<module>")

    for node in tree.body:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            if ast.get_docstring(node) is None:
                missing.append(node.name)
            if isinstance(node, ast.ClassDef):
                for child in node.body:
                    if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef)):
                        if ast.get_docstring(child) is None:
                            missing.append(f"{node.name}.{child.name}")
    return missing


def test_core_modules_have_module_class_and_function_docstrings():
    """Core modules should keep module/class/function docstring coverage."""
    failures: list[str] = []

    for module_name in CORE_MODULES:
        missing = _missing_docstrings(_module_path(module_name))
        if missing:
            failures.append(f"{module_name}: {', '.join(missing)}")

    assert not failures, "Missing docstrings:\n" + "\n".join(failures)
