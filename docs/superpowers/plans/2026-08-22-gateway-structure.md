# Gateway Structure Refactor Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Reorganize Gateway into consistent runtime, router, schema, service, proxy, and web-resource boundaries without changing public behavior.

**Architecture:** Keep FastAPI composition in `application.py` and the ASGI export in `main.py`, move request workflows into focused services, split proxy transport from transformation, and make `gateway.web` the sole owner of packaged HTML/CSS/JavaScript. Remove obsolete internal import paths instead of preserving shims.

**Tech Stack:** Python 3.12+, FastAPI, Pydantic v2, aiohttp, asyncio, SQLite, unittest, Docker Compose

**Spec:** `docs/superpowers/specs/2026-08-22-gateway-structure-design.md`

## Global Constraints

- Keep `gateway.main:app` as the Uvicorn entry point.
- Preserve every HTTP/WebSocket path, method, status code, request/response field, OpenAPI security scheme, Cookie/Bearer behavior, and management-page behavior.
- Preserve the Supervisor socket protocol, SQLite schema/data, filesystem paths, environment variables, single-container topology, and port `3081`.
- Keep Swagger and management assets fully offline.
- Add no runtime dependency, dependency-injection framework, service container, template engine, frontend framework, or frontend build chain.
- Do not retain obsolete internal Python import paths.
- Use red-green-refactor for every production boundary and run the full suite before every commit.

---

### Task 1: Consolidate Runtime Configuration, Authentication, and ASGI Exports

**Files:**
- Modify: `tests/test_architecture.py`
- Modify: `tests/test_auth.py`
- Modify: `tests/test_proxy.py`
- Modify: `tests/test_contracts.py`
- Modify: `gateway/dependencies.py`
- Modify: `gateway/services/auth.py`
- Modify: `gateway/routers/auth.py`
- Modify: `gateway/application.py`
- Modify: `gateway/main.py`
- Delete: `gateway/auth.py`

**Interfaces:**
- Consumes: `gateway.runtime.config`, `common.security.hash_password`, `common.security.verify_password`.
- Produces: `gateway.services.auth.make_session`, `parse_session`, `FixedWindowLimiter`, `resolve_user`, `request_user`, `require_session`, `require_admin`, `user_role`, `authenticated_response`; `gateway.main.app` as the sole ASGI export.

- [ ] **Step 1: Write failing ownership tests**

Change authentication tests to import the primitives from their target owner:

```python
from gateway.services.auth import FixedWindowLimiter, make_session, parse_session
```

Add architecture assertions that production Gateway contains exactly one `Config.from_environment()` call and `main.py` imports only `app` from `application`:

```python
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
```

Update tests to import `create_app` and Swagger helpers from `gateway.application`, `config` from `gateway.runtime`, and `login` from `gateway.routers.auth`.

- [ ] **Step 2: Run the focused tests and verify RED**

Run:

```bash
.venv/bin/python -m unittest tests.test_architecture tests.test_auth tests.test_proxy tests.test_contracts -v
```

Expected: import failure for authentication primitives in `gateway.services.auth`, plus architecture failures for the duplicate config instance and multi-name `main.py` re-export.

- [ ] **Step 3: Move authentication primitives and unify config**

Move `_b64encode`, `_b64decode`, `make_session`, `parse_session`, `Window`, and `FixedWindowLimiter` into `gateway/services/auth.py` above the current database-backed helpers. Preserve their signatures and implementations exactly.

Change `gateway/dependencies.py` to use the sole runtime instance:

```python
from .runtime import config

session_cookie = APIKeyCookie(name=config.cookie_name, auto_error=False)
```

Change `gateway/routers/auth.py` to import `FixedWindowLimiter` from `services.auth`. Remove `gateway/auth.py` after all imports move.

Update the password-primitive architecture assertion to compare `gateway.routers.auth.hash_password` and `verify_password` with `common.security`; update proxy tests to import `hash_password` from `common.security` rather than from the removed compatibility module.

- [ ] **Step 4: Remove internal entry-point re-exports**

Reduce `gateway/main.py` to:

```python
"""ASGI entry point for DSH Server Manager."""

from .application import app

__all__ = ["app"]
```

Remove `login = auth.login` and the compatibility `__all__` list from `gateway/application.py`; keep `app = create_app()`.

- [ ] **Step 5: Verify GREEN**

Run:

```bash
.venv/bin/python -m unittest tests.test_architecture tests.test_auth tests.test_proxy tests.test_contracts -v
.venv/bin/python -m unittest discover -s tests -v
```

Expected: all tests pass and OpenAPI paths remain unchanged.

- [ ] **Step 6: Commit**

```bash
git add gateway tests
git commit -m "refactor: consolidate gateway runtime and authentication"
```

---

### Task 2: Consolidate Provider Schemas, Harness RPC, and Provider Workflow

**Files:**
- Modify: `tests/test_architecture.py`
- Modify: `tests/test_providers.py`
- Modify: `tests/test_contracts.py`
- Replace: `gateway/schemas/providers.py`
- Replace: `gateway/services/harness.py`
- Create: `gateway/services/providers.py`
- Modify: `gateway/routers/providers.py`
- Delete: `gateway/providers.py`
- Delete: `gateway/harness_rpc.py`

**Interfaces:**
- Produces: `gateway.schemas.providers.CreateCustomProvider`, `CustomProviderModel`, `CustomProviderCreated`; `gateway.services.harness.call_harness_rpc`, `decode_rpc_response`, `HarnessRpcError`, `HarnessRpcTransportError`, `HarnessRpcBusinessError`; `gateway.services.providers.create_provider(database_path, session, username, provider)` and `ProviderOperationError`.
- `ProviderOperationError` exposes `code: str`, `message: str`, `upstream_code: str | None`, `provider_created: bool`, and `credential_stored: bool | None`.

- [ ] **Step 1: Write failing target-import and service-workflow tests**

Change Provider tests to import from target modules:

```python
from gateway.schemas.providers import CreateCustomProvider
from gateway.services.harness import (
    HarnessRpcBusinessError,
    HarnessRpcTransportError,
    decode_rpc_response,
)
from gateway.services.providers import ProviderOperationError, create_provider
```

Add a real temporary SQLite user and patch only the external RPC transport:

```python
async def test_provider_service_writes_settings_then_credential(self) -> None:
    provider = self.request()
    responses = [
        {"providers": []},
        {"writable": True, "namespaces": [{"ns": "llm-pi-ai", "revision": 4}]},
        {"credentials": {"MY_GATEWAY_API_KEY": {"writable": True}}},
        {"saved": True},
        {"saved": True},
    ]
    with patch(
        "gateway.services.providers.call_harness_rpc",
        new=AsyncMock(side_effect=responses),
    ) as rpc:
        result = await create_provider(
            self.database,
            self.session,
            "alice",
            provider,
        )
    self.assertEqual(result.username, "alice")
    self.assertEqual(result.provider_id, "my-gateway")
    self.assertEqual(rpc.await_args_list[3].args[2], "settings.mutate")
    self.assertEqual(rpc.await_args_list[4].args[2], "credentials.set")
```

Add separate cases for duplicate Provider ID, unwritable settings, malformed upstream shapes, Harness business error, transport failure, and credential failure after settings creation. Assert the last case sets `provider_created=True`, `credential_stored=False`, and never contains the API key in the exception text/details.

Add an architecture assertion that `gateway/schemas/providers.py` does not import `gateway.providers` and obsolete top-level files do not exist.

- [ ] **Step 2: Run focused tests and verify RED**

Run:

```bash
.venv/bin/python -m unittest tests.test_providers tests.test_contracts tests.test_architecture -v
```

Expected: import failure for `gateway.services.providers` and target-owned Provider/Harness symbols.

- [ ] **Step 3: Move canonical models and Harness client**

Move Provider constants, Pydantic models, validators, `credential_ref()`, and `provider_profile()` into `gateway/schemas/providers.py` without re-exporting from another module.

Move the complete contents of `gateway/harness_rpc.py` into `gateway/services/harness.py`. Keep the existing envelope, UUID, HTTP timeout, response validation, and exception fields. Remove the FastAPI-specific `rpc_failure()` function so the Harness service remains transport-neutral.

- [ ] **Step 4: Implement the Provider application service**

Define the typed error:

```python
@dataclass(slots=True)
class ProviderOperationError(Exception):
    code: str
    message: str
    upstream_code: str | None = None
    provider_created: bool = False
    credential_stored: bool | None = None

    def __str__(self) -> str:
        return self.message
```

Implement:

```python
async def create_provider(
    database_path: Path,
    session: aiohttp.ClientSession,
    username: str,
    provider: CreateCustomProvider,
) -> CustomProviderCreated:
```

Move the complete target-user lookup, Provider catalog validation, settings namespace validation, credential writability check, settings mutation, credential write, and response creation out of the router. Translate storage and Harness failures into stable codes: `unavailable`, `not_found`, `disabled`, `invalid_target`, `invalid_upstream`, `conflict`, `business`, `transport`, and `credential_partial`.

- [ ] **Step 5: Make the Provider router HTTP-only**

Keep the existing route decorator. After `require_admin`, call the service with `config.user_database_path`, `request.app.state.http`, `username`, and the validated model. Map service codes to the existing response behavior:

```python
STATUS_BY_CODE = {
    "unavailable": 503,
    "not_found": 404,
    "disabled": 409,
    "invalid_target": 503,
    "invalid_upstream": 502,
    "conflict": 409,
    "business": 422,
    "transport": 503,
    "credential_partial": 502,
}
```

For `business` with `upstream_code == "settings-conflict"`, use 409. For `credential_partial`, preserve the dictionary detail with `providerCreated`, `credentialStored`, and optional `upstreamCode`. All other details remain strings matching current Chinese messages.

Delete the obsolete top-level modules after imports move.

- [ ] **Step 6: Verify GREEN**

Run:

```bash
.venv/bin/python -m unittest tests.test_providers tests.test_contracts tests.test_architecture tests.test_proxy -v
.venv/bin/python -m unittest discover -s tests -v
```

Expected: all tests pass and the Provider OpenAPI component retains `providerId`, `displayName`, `baseURL`, `api`, `apiKey`, and `models`.

- [ ] **Step 7: Commit**

```bash
git add gateway tests
git commit -m "refactor: isolate gateway provider operations"
```

---

### Task 3: Extract Safe Shared Request-Body Spooling

**Files:**
- Create: `gateway/services/spool.py`
- Create: `tests/test_spool.py`
- Modify: `gateway/routers/uploads.py`
- Modify: `gateway/routers/plugins.py`
- Modify: `tests/test_contracts.py`

**Interfaces:**
- Produces: `SpoolResult(name: str, path: Path, size: int)`, `SpoolError(code: str, message: str)`, and `receive_spool(stream, root, *, suffix, max_bytes, content_length, allow_empty)`.
- Consumes: an `AsyncIterator[bytes]`, a Gateway-owned spool directory, and literal per-route policies.

- [ ] **Step 1: Write failing spool behavior tests**

Create `tests/test_spool.py` with a small async generator:

```python
async def chunks(*values: bytes):
    for value in values:
        yield value
```

Cover real filesystem behavior:

```python
async def test_writes_complete_stream_and_reports_size(self) -> None:
    result = await receive_spool(
        chunks(b"abc", b"def"),
        self.root,
        suffix=".upload",
        max_bytes=10,
        content_length="6",
        allow_empty=True,
    )
    self.assertEqual(result.size, 6)
    self.assertEqual(result.path.read_bytes(), b"abcdef")
    self.assertRegex(result.name, r"^[0-9a-f]{32}\.upload$")
```

Add cases for malformed/negative `Content-Length`, declared oversize, streamed oversize with partial-file cleanup, permitted empty regular upload, rejected empty plugin upload, and `.plugin.tgz` naming.

- [ ] **Step 2: Run tests and verify RED**

Run:

```bash
.venv/bin/python -m unittest tests.test_spool -v
```

Expected: import failure because `gateway.services.spool` does not exist.

- [ ] **Step 3: Implement the shared spool service**

Define:

```python
@dataclass(frozen=True, slots=True)
class SpoolResult:
    name: str
    path: Path
    size: int


class SpoolError(RuntimeError):
    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code
```

Implement `receive_spool()` using `O_WRONLY | O_CREAT | O_EXCL | O_NOFOLLOW`, complete memoryview writes, `fsync`, and unconditional cleanup on failure. Use codes `invalid_length`, `declared_empty`, `too_large`, `empty`, and `write_failed`. Do not import FastAPI.

- [ ] **Step 4: Replace both router implementations**

In `routers/uploads.py`, call with `suffix=".upload"` and `allow_empty=True`. In `routers/plugins.py`, call with `suffix=".plugin.tgz"` and `allow_empty=False`.

Map codes to existing route behavior:

- regular upload: invalid length → 400, oversize → 413;
- offline plugin: invalid length → 400, declared empty/oversize → 413, streamed empty → 400;
- preserve existing Chinese detail strings and delete the spool when Supervisor rejects it.

Remove direct `os`, `secrets`, descriptor, stream-copy, and `fsync` code from both routers.

- [ ] **Step 5: Verify GREEN**

Run:

```bash
.venv/bin/python -m unittest tests.test_spool tests.test_upload tests.test_plugins tests.test_contracts -v
.venv/bin/python -m unittest discover -s tests -v
```

Expected: all tests pass; empty regular files and non-empty plugin packages retain current behavior.

- [ ] **Step 6: Commit**

```bash
git add gateway tests/test_spool.py tests/test_contracts.py
git commit -m "refactor: share gateway upload spooling"
```

---

### Task 4: Move Management Pages and Injected Widgets into Gateway Web

**Files:**
- Create: `gateway/web/pages.py`
- Create: `gateway/web/fragments.py`
- Create: `gateway/web/templates/admin-widget.html`
- Create: `gateway/web/templates/logout-widget.html`
- Create: `gateway/web/static/management/proxy-widgets.css`
- Create: `gateway/web/static/management/logout-widget.js`
- Create: `gateway/routers/management.py`
- Create: `gateway/routers/users.py`
- Modify: `gateway/routers/auth.py`
- Modify: `gateway/routers/proxy.py`
- Modify: `gateway/application.py`
- Modify: `gateway/routers/__init__.py`
- Modify: `gateway/proxy.py`
- Modify: `tests/test_proxy.py`
- Modify: `tests/test_plugins.py`
- Modify: `tests/test_web_assets.py`
- Modify: `tests/test_contracts.py`
- Delete: `gateway/pages.py`
- Delete: `gateway/plugin_ui.py`
- Delete: `gateway/upload_ui.py`
- Delete: `gateway/routers/admin_users.py`

**Interfaces:**
- Produces: `gateway.web.pages.auth_page`, `admin_page`, `plugin_admin_page`; `gateway.web.fragments.UPLOAD_WIDGET`, `ADMIN_WIDGET`, `LOGOUT_WIDGET`; `management.router` for HTML pages and `users.router` for the admin-user API.

- [ ] **Step 1: Write failing target-owner and local-asset tests**

Move test imports to:

```python
from gateway.web.pages import admin_page, auth_page, plugin_admin_page
from gateway.web.fragments import ADMIN_WIDGET, LOGOUT_WIDGET, UPLOAD_WIDGET
```

Add assertions that injected admin/logout fragments contain local asset references and no inline style/script bodies:

```python
self.assertIn(b'/assets/proxy-widgets.css?v=1', LOGOUT_WIDGET)
self.assertIn(b'/assets/logout-widget.js?v=1', LOGOUT_WIDGET)
self.assertNotIn(b'<style>', ADMIN_WIDGET + LOGOUT_WIDGET)
self.assertNotIn(b'fetch(', ADMIN_WIDGET + LOGOUT_WIDGET)
```

Add route-composition assertions that `/admin`, `/admin/plugins`, and `/api/gateway/v1/admin/users` remain registered after `management.router` and `users.router` replace `admin_users.router`.

- [ ] **Step 2: Run focused tests and verify RED**

Run:

```bash
.venv/bin/python -m unittest tests.test_proxy tests.test_plugins tests.test_web_assets tests.test_contracts -v
```

Expected: import failures for `gateway.web.pages` and `gateway.web.fragments`.

- [ ] **Step 3: Create canonical page and fragment owners**

Move the three page-rendering functions to `web/pages.py`. Implement cached fragments in `web/fragments.py`:

```python
from . import render_template

UPLOAD_WIDGET = render_template("upload-widget.html").encode("utf-8")
ADMIN_WIDGET = render_template("admin-widget.html").encode("utf-8")
LOGOUT_WIDGET = render_template("logout-widget.html").encode("utf-8")
```

Move the admin anchor and logout button markup to their templates. Put shared positioning/styles in `proxy-widgets.css` and logout behavior in `logout-widget.js`. `logout-widget.html` references both local assets; `admin-widget.html` contains only the management anchor because the shared stylesheet is always loaded by the logout fragment.

- [ ] **Step 4: Split HTML management routes from user API routes**

Move `/admin` and `/admin/plugins` to `routers/management.py`. Move `/api/gateway/v1/admin/users` to `routers/users.py`. Preserve decorators, response models, redirects, role checks, Cache-Control, tags, summaries, and ordering.

Register routers in `application.py` as auth, management, users, plugins, providers, uploads, then catch-all proxy.

- [ ] **Step 5: Update callers and remove obsolete modules**

Import pages from `web.pages` in auth/management routers and fragments from `web.fragments` in the existing proxy module. Update test paths, then delete the four obsolete top-level UI modules and `routers/admin_users.py`.

- [ ] **Step 6: Verify GREEN**

Run:

```bash
.venv/bin/python -m unittest tests.test_proxy tests.test_plugins tests.test_web_assets tests.test_contracts -v
.venv/bin/python -m unittest discover -s tests -v
```

Expected: all tests pass, every asset URL is local, and catch-all proxy routes remain last.

- [ ] **Step 7: Commit**

```bash
git add gateway tests
git commit -m "refactor: consolidate gateway web resources"
```

---

### Task 5: Split HTTP, WebSocket, Header, and Transform Proxy Responsibilities

**Files:**
- Create: `gateway/proxy/__init__.py`
- Create: `gateway/proxy/headers.py`
- Create: `gateway/proxy/transforms.py`
- Create: `gateway/proxy/http.py`
- Create: `gateway/proxy/websocket.py`
- Modify: `gateway/routers/proxy.py`
- Modify: `tests/test_proxy.py`
- Modify: `tests/test_architecture.py`
- Delete: `gateway/proxy.py`

**Interfaces:**
- Produces: `headers.upstream_url`, `request_headers`, `response_headers`; `transforms.patch_html`, `patch_connection_module`, `read_limited`, `UUID_SHIM`, `LOOPBACK_CHECK`; `http.proxy_http`; `websocket.proxy_websocket`, `cookie_from_websocket`.

- [ ] **Step 1: Write failing target-module tests**

Change proxy tests to import:

```python
from gateway.proxy.headers import request_headers
from gateway.proxy.transforms import (
    LOOPBACK_CHECK,
    UUID_SHIM,
    patch_connection_module,
    patch_html,
)
from gateway.proxy.http import proxy_http
from gateway.proxy.websocket import cookie_from_websocket, proxy_websocket
```

Add literal header-policy tests proving Cookie, forwarding, Origin, hop-by-hop, and WebSocket handshake headers are removed while Host is rewritten to `127.0.0.1:<port>` and HTTP requests force `Accept-Encoding: identity`.

Add an HTTP streaming fake whose `release()` flag is asserted after the response iterator finishes. Add a WebSocket test covering unauthenticated Cookie extraction separately from the Router authentication contract.

- [ ] **Step 2: Run focused tests and verify RED**

Run:

```bash
.venv/bin/python -m unittest tests.test_proxy tests.test_architecture -v
```

Expected: import failures because `gateway.proxy` is still a module rather than the target package.

- [ ] **Step 3: Create the proxy package**

Move constants and behavior without changing literals:

- Header sets, upstream URL, request header filtering, and response header filtering → `headers.py`.
- UUID shim, connection-module constants, HTML/module patching, and bounded reads → `transforms.py`.
- Request-body iterator and HTTP proxying → `http.py`.
- Cookie parsing and bidirectional WebSocket forwarding → `websocket.py`.

Use public function names without leading underscores at the new internal boundaries. Keep `proxy/__init__.py` empty except for its module docstring; callers import the owning module directly.

- [ ] **Step 4: Update the catch-all router and delete the old module**

Use:

```python
from ..proxy.http import proxy_http
from ..proxy.websocket import cookie_from_websocket, proxy_websocket
```

Delete `gateway/proxy.py` in the same change so Python never sees both a module and package with the same name.

- [ ] **Step 5: Verify GREEN**

Run:

```bash
.venv/bin/python -m unittest tests.test_proxy tests.test_contracts tests.test_architecture -v
.venv/bin/python -m unittest discover -s tests -v
```

Expected: all tests pass; HTTP transformed/streamed responses and WebSocket behavior are unchanged.

- [ ] **Step 6: Commit**

```bash
git add gateway tests
git commit -m "refactor: split gateway proxy responsibilities"
```

---

### Task 6: Enforce Final Boundaries, Build, and Deploy

**Files:**
- Modify: `tests/test_architecture.py`
- Modify: `README.md` only if module layout documentation is stale
- Modify production files only if verification exposes a regression

**Interfaces:**
- Consumes: all target modules from Tasks 1–5.
- Produces: a strict final architecture, verified image `dsh-server-manager:local`, and running Compose service `dsm` on port `3081`.

- [ ] **Step 1: Add final architecture assertions**

Assert the obsolete files are absent:

```python
for name in (
    "auth.py",
    "harness_rpc.py",
    "providers.py",
    "proxy.py",
    "pages.py",
    "plugin_ui.py",
    "upload_ui.py",
    "routers/admin_users.py",
):
    self.assertFalse((PROJECT_ROOT / "gateway" / name).exists(), name)
```

Parse imports to assert schemas do not import routers/services/proxy/former top-level modules, `main.py` imports only `app`, only `runtime.py` calls `Config.from_environment()`, Supervisor never imports Gateway, and Common imports neither runtime package.

- [ ] **Step 2: Run static and complete test verification**

Run:

```bash
.venv/bin/python -m unittest discover -s tests -v
.venv/bin/python -m compileall -q common gateway supervisor tests
git diff --check
docker compose -f docker/compose.yml config -q
```

Expected: all commands exit `0` with no warnings or failures.

- [ ] **Step 3: Record database counts before deployment**

Run a read-only query without printing credentials or password records:

```bash
docker compose -f docker/compose.yml exec -T dsm python - <<'PY'
import sqlite3
db = sqlite3.connect('/var/lib/dsh/gateway/users.db')
for table in ('users', 'plugins', 'plugin_versions', 'user_plugins', 'plugin_defaults', 'plugin_jobs', 'plugin_job_items'):
    print(f'{table}={db.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]}')
PY
```

Save the literal output for the post-deployment comparison.

- [ ] **Step 4: Build and deploy the current branch**

Run:

```bash
docker compose -f docker/compose.yml up -d --build
docker compose -f docker/compose.yml ps
```

Expected: `dsh-server-manager-dsm-1` is Up and publishes `0.0.0.0:3081->8080/tcp`.

- [ ] **Step 5: Smoke-test public and authenticated behavior**

Run:

```bash
curl -fsS http://127.0.0.1:3081/api/gateway/v1/health
curl -fsS -o /dev/null http://127.0.0.1:3081/login
curl -fsS -o /dev/null http://127.0.0.1:3081/docs
curl -fsS -o /dev/null http://127.0.0.1:3081/assets/admin.js?v=1
curl -fsS -o /dev/null http://127.0.0.1:3081/static/swagger-ui/swagger-ui-bundle.js
```

Generate a short-lived request token inside the container from the existing session secret and first enabled user, call `/api/gateway/v1/auth/me`, and never print the token:

```bash
docker compose -f docker/compose.yml exec -T dsm python - <<'PY'
import json
import sqlite3
import urllib.request

from gateway.services.auth import make_session

database = sqlite3.connect('/var/lib/dsh/gateway/users.db')
database.row_factory = sqlite3.Row
user = database.execute(
    "SELECT username, role, password_version FROM users WHERE disabled = 0 ORDER BY created_at, username LIMIT 1"
).fetchone()
assert user is not None
secret = open('/var/lib/dsh/gateway/session-secret', 'rb').read()
token = make_session(user['username'], user['password_version'], secret, 60)
request = urllib.request.Request(
    'http://127.0.0.1:8080/api/gateway/v1/auth/me',
    headers={'Authorization': f'Bearer {token}'},
)
with urllib.request.urlopen(request, timeout=10) as response:
    body = json.load(response)
assert body == {'username': user['username'], 'role': user['role']}
print('authenticated identity check: ok')
PY
```

- [ ] **Step 6: Recheck persisted data and container logs**

Repeat the Step 3 table-count query and require exact equality. Then run:

```bash
docker compose -f docker/compose.yml logs --tail=100 dsm
docker compose -f docker/compose.yml exec -T dsm test -d /opt/dsh-server-manager/gateway/proxy
docker compose -f docker/compose.yml exec -T dsm test ! -f /opt/dsh-server-manager/gateway/proxy.py
```

Expected: no traceback/error startup lines, the proxy package exists, the obsolete module is absent, and all table counts are unchanged.

- [ ] **Step 7: Commit final architecture enforcement**

```bash
git add gateway tests README.md
git commit -m "chore: enforce gateway architecture boundaries"
```

- [ ] **Step 8: Final clean-tree verification**

Run:

```bash
.venv/bin/python -m unittest discover -s tests
.venv/bin/python -m compileall -q common gateway supervisor tests
git diff --check
test -z "$(git status --porcelain)"
git log -6 --oneline
```

Expected: the suite passes, the worktree is clean, and the six planned implementation commits appear in order.
