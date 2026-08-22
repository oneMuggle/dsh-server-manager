from __future__ import annotations

import ast
import unittest
from pathlib import Path

import common.security
import gateway.auth
import supervisor.firewall
import supervisor.main


PROJECT_ROOT = Path(__file__).resolve().parents[1]
TEMPORARY_SUPERVISOR_GATEWAY_IMPORTS = {}


def _runtime_imports(path: Path) -> set[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    imports: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imports.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            imports.add(node.module)
    return {
        name
        for name in imports
        if name == "gateway"
        or name.startswith("gateway.")
        or name == "supervisor"
        or name.startswith("supervisor.")
    }


class DependencyDirectionTests(unittest.TestCase):
    def test_common_never_imports_runtime_packages(self) -> None:
        common_root = PROJECT_ROOT / "common"
        violations = {
            path.relative_to(PROJECT_ROOT).as_posix(): sorted(_runtime_imports(path))
            for path in common_root.rglob("*.py")
            if _runtime_imports(path)
        } if common_root.exists() else {}
        self.assertEqual(violations, {})

    def test_supervisor_gateway_imports_match_temporary_allowlist(self) -> None:
        actual = {
            path.relative_to(PROJECT_ROOT).as_posix(): {
                name for name in _runtime_imports(path) if name.startswith("gateway")
            }
            for path in (PROJECT_ROOT / "supervisor").rglob("*.py")
        }
        actual = {path: imports for path, imports in actual.items() if imports}
        self.assertEqual(actual, TEMPORARY_SUPERVISOR_GATEWAY_IMPORTS)

    def test_gateway_application_does_not_consume_compatibility_shims(self) -> None:
        tree = ast.parse((PROJECT_ROOT / "gateway" / "application.py").read_text())
        relative_imports = {
            node.module
            for node in ast.walk(tree)
            if isinstance(node, ast.ImportFrom) and node.level and node.module
        }
        self.assertFalse(relative_imports & {"plugins", "registry", "supervisor_client"})

    def test_gateway_auth_reuses_common_password_primitives(self) -> None:
        self.assertIs(gateway.auth.hash_password, common.security.hash_password)
        self.assertIs(gateway.auth.verify_password, common.security.verify_password)

    def test_obsolete_gateway_compatibility_modules_are_removed(self) -> None:
        for name in ("plugins.py", "registry.py", "supervisor_client.py"):
            with self.subTest(name=name):
                self.assertFalse((PROJECT_ROOT / "gateway" / name).exists())

    def test_supervisor_handles_the_concrete_firewall_error(self) -> None:
        self.assertIs(supervisor.main.FirewallError, supervisor.firewall.FirewallError)


if __name__ == "__main__":
    unittest.main()
