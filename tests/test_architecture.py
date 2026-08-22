from __future__ import annotations

import ast
import unittest
from pathlib import Path

import common.security
import gateway.routers.auth
import supervisor.application
import supervisor.main
import supervisor.tenants.firewall


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

    def test_provider_schema_is_the_canonical_owner(self) -> None:
        tree = ast.parse(
            (PROJECT_ROOT / "gateway" / "schemas" / "providers.py").read_text()
        )
        imports = {
            node.module
            for node in ast.walk(tree)
            if isinstance(node, ast.ImportFrom) and node.module
        }
        self.assertNotIn("gateway.providers", imports)

    def test_gateway_auth_reuses_common_password_primitives(self) -> None:
        self.assertIs(gateway.routers.auth.hash_password, common.security.hash_password)
        self.assertIs(gateway.routers.auth.verify_password, common.security.verify_password)

    def test_gateway_has_one_runtime_config_instance(self) -> None:
        calls = []
        for path in (PROJECT_ROOT / "gateway").rglob("*.py"):
            tree = ast.parse(path.read_text(encoding="utf-8"))
            for node in ast.walk(tree):
                if (
                    isinstance(node, ast.Call)
                    and isinstance(node.func, ast.Attribute)
                    and node.func.attr == "from_environment"
                ):
                    calls.append(path.relative_to(PROJECT_ROOT).as_posix())
        self.assertEqual(calls, ["gateway/runtime.py"])

    def test_gateway_main_exports_only_the_asgi_app(self) -> None:
        tree = ast.parse((PROJECT_ROOT / "gateway" / "main.py").read_text())
        imports = [node for node in tree.body if isinstance(node, ast.ImportFrom)]
        self.assertEqual(len(imports), 1)
        self.assertEqual(imports[0].module, "application")
        self.assertEqual([alias.name for alias in imports[0].names], ["app"])

    def test_obsolete_gateway_compatibility_modules_are_removed(self) -> None:
        for name in ("plugins.py", "registry.py", "supervisor_client.py"):
            with self.subTest(name=name):
                self.assertFalse((PROJECT_ROOT / "gateway" / name).exists())

    def test_supervisor_handles_the_concrete_firewall_error(self) -> None:
        self.assertIs(
            supervisor.application.FirewallError,
            supervisor.tenants.firewall.FirewallError,
        )

    def test_supervisor_entrypoint_is_only_the_process_shell(self) -> None:
        tree = ast.parse((PROJECT_ROOT / "supervisor" / "main.py").read_text())
        classes = [node.name for node in tree.body if isinstance(node, ast.ClassDef)]
        relative_imports = {
            node.module
            for node in tree.body
            if isinstance(node, ast.ImportFrom) and node.level and node.module
        }
        self.assertEqual(classes, [])
        self.assertEqual(relative_imports, {"application"})


if __name__ == "__main__":
    unittest.main()
