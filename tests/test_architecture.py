from __future__ import annotations

import ast
import unittest
from pathlib import Path

import common.security
import gateway.routers.auth.registrations
import gateway.services.auth
import supervisor.application
import supervisor.main
import supervisor.tenants.firewall


PROJECT_ROOT = Path(__file__).resolve().parents[1]
TEMPORARY_SUPERVISOR_GATEWAY_IMPORTS = {}
OBSOLETE_ENVIRONMENT_NAMES = {
    "DSH_ADMIN_PASSWORD",
    "DSH_STATE_ROOT",
    "DSH_CONTROL_SOCKET",
    "DSH_COOKIE_SECURE",
    "DSH_PLUGIN_NPM_REGISTRY",
    "DSH_PLUGIN_NPM_TOKEN",
}


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
    def test_versioned_api_uses_domain_router_packages(self) -> None:
        router_root = PROJECT_ROOT / "gateway" / "routers"
        for domain in ("auth", "user", "plugin", "file", "system"):
            with self.subTest(domain=domain):
                self.assertTrue((router_root / domain / "__init__.py").is_file())
                self.assertTrue((router_root / domain / "router.py").is_file())
        for obsolete in ("auth.py", "users.py", "providers.py", "plugins.py", "uploads.py"):
            with self.subTest(obsolete=obsolete):
                self.assertFalse((router_root / obsolete).exists())

    def test_obsolete_api_prefix_is_removed_from_active_sources(self) -> None:
        active_files = [
            *(PROJECT_ROOT / "gateway").rglob("*.py"),
            *(PROJECT_ROOT / "gateway" / "web").rglob("*.js"),
            PROJECT_ROOT / "README.md",
        ]
        violations = [
            path.relative_to(PROJECT_ROOT).as_posix()
            for path in active_files
            if "/api/gateway/v1" in path.read_text(encoding="utf-8")
        ]
        self.assertEqual(violations, [])

    def test_active_configuration_uses_dsm_environment_names(self) -> None:
        active_files = [
            *(PROJECT_ROOT / "gateway").rglob("*.py"),
            *(PROJECT_ROOT / "supervisor").rglob("*.py"),
            PROJECT_ROOT / ".env.example",
            PROJECT_ROOT / "README.md",
            PROJECT_ROOT / "docker" / "Dockerfile",
            PROJECT_ROOT / "docker" / "compose.yml",
        ]
        violations: dict[str, list[str]] = {}
        for path in active_files:
            text = path.read_text(encoding="utf-8")
            found = sorted(name for name in OBSOLETE_ENVIRONMENT_NAMES if name in text)
            if found:
                violations[path.relative_to(PROJECT_ROOT).as_posix()] = found
        self.assertEqual(violations, {})

    def test_container_build_mirrors_are_wired_and_custom_mirror_drops_security(self) -> None:
        dockerfile = (PROJECT_ROOT / "docker" / "Dockerfile").read_text()
        compose = (PROJECT_ROOT / "docker" / "compose.yml").read_text()
        workflow = (
            PROJECT_ROOT / ".github" / "workflows" / "publish-image.yml"
        ).read_text()
        for name in (
            "DSM_BUILD_DEBIAN_MIRROR",
            "DSM_BUILD_NPM_REGISTRY",
        ):
            with self.subTest(name=name):
                self.assertIn(f"ARG {name}", dockerfile)
                self.assertIn(name, compose)
                self.assertIn(name, workflow)
        self.assertIn("!/bookworm-security/", dockerfile)
        self.assertNotIn("DSM_BUILD_DEBIAN_SECURITY_MIRROR", dockerfile)
        self.assertIn("/tmp/ca-certificates.crt", dockerfile)
        self.assertIn("ca-certificates", dockerfile)

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
        self.assertIs(
            gateway.routers.auth.registrations.hash_password,
            common.security.hash_password,
        )
        self.assertIs(
            gateway.services.auth.verify_password,
            common.security.verify_password,
        )

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
        for name in (
            "plugins.py",
            "registry.py",
            "supervisor_client.py",
            "auth.py",
            "harness_rpc.py",
            "providers.py",
            "proxy.py",
            "pages.py",
            "plugin_ui.py",
            "upload_ui.py",
            "routers/admin_users.py",
        ):
            with self.subTest(name=name):
                self.assertFalse((PROJECT_ROOT / "gateway" / name).exists())

    def test_gateway_schemas_do_not_depend_on_runtime_layers(self) -> None:
        forbidden_roots = {
            "gateway.routers",
            "gateway.services",
            "gateway.proxy",
            "gateway.auth",
            "gateway.harness_rpc",
            "gateway.providers",
            "gateway.pages",
            "gateway.plugin_ui",
            "gateway.upload_ui",
        }
        violations: dict[str, list[str]] = {}
        for path in (PROJECT_ROOT / "gateway" / "schemas").rglob("*.py"):
            tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
            imports = {
                node.module
                for node in ast.walk(tree)
                if isinstance(node, ast.ImportFrom) and node.module
            }
            imports.update(
                alias.name
                for node in ast.walk(tree)
                if isinstance(node, ast.Import)
                for alias in node.names
            )
            forbidden = sorted(
                name
                for name in imports
                if any(
                    name == root or name.startswith(f"{root}.")
                    for root in forbidden_roots
                )
            )
            if forbidden:
                violations[path.relative_to(PROJECT_ROOT).as_posix()] = forbidden
        self.assertEqual(violations, {})

    def test_gateway_proxy_is_a_package_with_focused_modules(self) -> None:
        proxy_root = PROJECT_ROOT / "gateway" / "proxy"
        self.assertTrue(proxy_root.is_dir())
        self.assertEqual(
            {path.name for path in proxy_root.glob("*.py")},
            {"__init__.py", "headers.py", "http.py", "transforms.py", "websocket.py"},
        )

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
