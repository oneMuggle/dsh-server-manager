# User Management, Registration Control, and Swagger Authentication Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add administrator-controlled user creation, lookup, password reset, and enable/disable operations; disable public registration by default; and let Swagger Authorize exchange a username and password for a Bearer token.

**Architecture:** Gateway remains the HTTP/UI adapter and sends typed mutations over the existing Unix socket. Supervisor remains the sole coordinator of SQLite, tenant processes, operating-system accounts, and firewall rules. Gateway and management assets consume a strict `DSM_` configuration surface while upstream Harness variables retain `DSH_` names.

**Tech Stack:** Python 3.12+, FastAPI 0.116.1, Pydantic 2.11.7, aiohttp, asyncio, SQLite, vanilla HTML/CSS/JavaScript, unittest, Docker Compose

**Spec:** `docs/superpowers/specs/2026-08-22-user-management-registration-design.md`

## Global Constraints

- Preserve the single-container deployment, port `3081`, SQLite schema, existing users, plugin data, filesystem paths, Cookie login, and direct Bearer tokens.
- User deletion must always return 501 and perform no lookup or mutation.
- `admin` cannot be disabled; its password can be reset.
- Username and role are immutable.
- Public registration defaults to disabled and never restricts administrator creation.
- Do not retain aliases for renamed `DSH_` DSM-management variables.
- Never log, return, or commit plaintext passwords, password records, session secrets, npm tokens, or Bearer tokens.
- Use red-green-refactor for every production behavior and run the full suite before every commit.

---

### Task 1: Establish the DSM Environment Contract

**Files:**
- Modify: `.env.example`
- Modify: `README.md`
- Modify: `gateway/config.py`
- Modify: `supervisor/config.py`
- Modify: `supervisor/gateway_process.py`
- Modify: `supervisor/main.py`
- Modify: `supervisor/plugins/commands.py`
- Modify: `supervisor/tenants/accounts.py`
- Modify: `tests/test_supervisor.py`
- Modify: `tests/test_upload.py`
- Modify: `tests/test_architecture.py`

**Interfaces:**
- Produces: `parse_environment_bool(name: str, default: bool) -> bool` in `gateway.config`; `Config.registration_enabled: bool`; DSM variable names from the spec.
- Preserves: `DSH_HOME` in tenant processes and `DSH_CLIENT_COMMIT_HASH` in the Docker build.

- [ ] **Step 1: Write failing configuration and naming tests**

Add table-driven tests that patch environment dictionaries and require:

```python
with patch.dict(os.environ, {"DSM_REGISTRATION_ENABLED": "true"}, clear=True):
    self.assertTrue(Config.from_environment().registration_enabled)

with patch.dict(os.environ, {"DSM_COOKIE_SECURE": "false"}, clear=True):
    self.assertFalse(Config.from_environment().cookie_secure)

with patch.dict(os.environ, {"DSM_REGISTRATION_ENABLED": "yes"}, clear=True):
    with self.assertRaisesRegex(ValueError, "DSM_REGISTRATION_ENABLED"):
        Config.from_environment()
```

Update Supervisor and bootstrap tests to use `DSM_STATE_ROOT` and
`DSM_ADMIN_PASSWORD`. Add an architecture scan over active Python, Compose,
Dockerfile, `.env.example`, and README text that rejects these obsolete names:

```python
OBSOLETE_ENVIRONMENT_NAMES = {
    "DSH_ADMIN_PASSWORD", "DSH_STATE_ROOT", "DSH_CONTROL_SOCKET",
    "DSH_COOKIE_SECURE", "DSH_PLUGIN_NPM_REGISTRY", "DSH_PLUGIN_NPM_TOKEN",
}
```

Explicitly exclude historical design/plan documents from that active scan.

- [ ] **Step 2: Run focused tests and verify RED**

Run:

```bash
.venv/bin/python -m unittest tests.test_supervisor tests.test_upload tests.test_architecture -v
```

Expected: config lacks `registration_enabled`, DSM variables are not consumed,
and the active-name scan finds old names.

- [ ] **Step 3: Implement strict DSM configuration**

In `gateway/config.py`, parse only case-insensitive `true` and `false`:

```python
def parse_environment_bool(name: str, default: bool) -> bool:
    raw = os.environ.get(name)
    if raw is None:
        return default
    normalized = raw.strip().lower()
    if normalized == "true":
        return True
    if normalized == "false":
        return False
    raise ValueError(f"{name} must be true or false")
```

Use `DSM_STATE_ROOT`, `DSM_CONTROL_SOCKET`, `DSM_COOKIE_SECURE`, and
`DSM_REGISTRATION_ENABLED`. Rename Supervisor state-root reads, the environment
passed to Gateway, admin bootstrap, plugin registry/token reads, and log level.
Do not change `DSH_HOME`, `DSH_CLIENT_COMMIT_HASH`, or the `DSH_TENANTS`
iptables chain name because none is a DSM environment variable.

- [ ] **Step 4: Update deployment documentation**

Set `.env.example` to:

```dotenv
DSM_ADMIN_PASSWORD=replace-with-a-strong-password
DSM_REGISTRATION_ENABLED=false
# DSM_COOKIE_SECURE=false
# DSM_PLUGIN_NPM_REGISTRY=https://registry.example.com/
# DSM_PLUGIN_NPM_TOKEN=replace-with-a-read-only-token
# DSM_LOG_LEVEL=INFO
```

Update README commands and descriptions to the same names. Do not touch the
untracked `.env` until deployment, when its key is renamed without displaying
the value.

- [ ] **Step 5: Verify GREEN and commit**

Run the focused tests, full suite, compileall, and `git diff --check`; then:

```bash
git add .env.example README.md gateway supervisor tests
git commit -m "refactor: namespace manager environment variables"
```

---

### Task 2: Control Public Registration and Share Credential Verification

**Files:**
- Modify: `gateway/services/auth.py`
- Modify: `gateway/routers/auth.py`
- Modify: `gateway/web/pages.py`
- Modify: `gateway/web/templates/login.html`
- Modify: `tests/test_auth.py`
- Modify: `tests/test_proxy.py`
- Modify: `tests/test_contracts.py`

**Interfaces:**
- Produces: `verify_credentials(database_path: Path, username: str, password: str) -> dict | None`; `auth_page(mode: str, *, registration_enabled: bool = True) -> str`.
- Consumes: `gateway.runtime.config.registration_enabled`.

- [ ] **Step 1: Write failing registration-state tests**

Test the rendered login page with registration enabled and disabled, direct GET
registration behavior, and POST behavior by patching runtime config:

```python
self.assertIn("注册新账号", auth_page("login", registration_enabled=True))
self.assertNotIn("注册新账号", auth_page("login", registration_enabled=False))
```

Call `register_page` and `register` with disabled config and assert 404 and 403
with `注册功能已关闭`. Assert `create_user` is never called in the disabled POST.

- [ ] **Step 2: Run focused tests and verify RED**

Run:

```bash
.venv/bin/python -m unittest tests.test_auth tests.test_proxy tests.test_contracts -v
```

Expected: `auth_page` lacks the flag and both registration routes remain open.

- [ ] **Step 3: Extract credential verification**

Move the database lookup, disabled check, real scrypt verification, and dummy
hash timing equalization from the login router into the async service with the
exact signature `async def verify_credentials(database_path: Path, username:
str, password: str) -> dict | None`.

Translate `RegistryError` to a service error or preserve a distinct exception
that routers map to 503. Both JSON login and the OAuth token endpoint in Task 5
must call this function.

- [ ] **Step 4: Enforce and render the registration flag**

Replace a literal `<!-- registration-link -->` marker in `login.html` with the
registration paragraph only when enabled. `GET /register` raises 404 before
rendering when disabled. `POST /auth/register` raises 403 before rate limiting,
hashing, or Supervisor calls when disabled.

- [ ] **Step 5: Verify GREEN and commit**

Run focused and full tests, then:

```bash
git add gateway tests
git commit -m "feat: control public registration"
```

---

### Task 3: Add Typed Supervisor User Updates and Tenant Stop/Start

**Files:**
- Modify: `common/contracts/control.py`
- Modify: `common/storage/users.py`
- Modify: `supervisor/control/server.py`
- Modify: `supervisor/application.py`
- Modify: `supervisor/tenants/manager.py`
- Modify: `gateway/services/supervisor.py`
- Modify: `tests/test_contracts.py`
- Modify: `tests/test_registry.py`
- Modify: `tests/test_tenants.py`
- Modify: `tests/test_upload.py`

**Interfaces:**
- Produces: `UpdateUserRequest(action="update_user", username, password=None, disabled=None)`; storage `update_user(path: Path, username: str, *, password: dict | None = None, password_version: int | None = None, disabled: bool | None = None) -> dict[str, Any]`; `TenantManager.stop_user(username: str) -> bool`; `Supervisor.update_user(username: str, password: dict[str, Any] | None, disabled: bool | None) -> dict[str, Any]`; Gateway socket client `update_user(socket_path: Path, username: str, password_record: dict[str, Any] | None, disabled: bool | None) -> dict[str, Any]`.

- [ ] **Step 1: Write failing wire, storage, and lifecycle tests**

Add a control parse/round-trip case with literal wire fields. In a temporary
SQLite database, assert password, version, and disabled update atomically while
role/UID/port/created-at remain unchanged. Add an isolated async tenant test:

```python
manager.start(user)
await ready_process_started()
self.assertTrue(await manager.stop_user("alice"))
self.assertNotIn("alice", manager.tasks)
manager.start(user)
self.assertIn("alice", manager.tasks)
```

Use the existing fake subprocess pattern; do not start a real Harness process.
Add Supervisor cases for missing user, disabling admin, idempotent state, and
password-version increment.

- [ ] **Step 2: Run focused tests and verify RED**

Run:

```bash
.venv/bin/python -m unittest tests.test_contracts tests.test_registry tests.test_tenants tests.test_upload -v
```

Expected: missing update request, storage function, and `stop_user`.

- [ ] **Step 3: Implement the typed update path**

Define `UpdateUserRequest` in the discriminated control union and export it.
Dispatch it in `ControlServer.handle`. Add a narrowly parameterized SQLite
update function that reads the full row, changes only supplied values, and
raises a not-found result without upserting.

Add `gateway.services.supervisor.update_user` using:

```json
{"action":"update_user","username":"alice","password":null,"disabled":true}
```

The socket response returns the public fields plus `password_version` for
internal session-invalidation assertions; Gateway never exposes that field.

- [ ] **Step 4: Implement one-user stop and Supervisor orchestration**

`TenantManager.stop_user` pops and cancels one task, awaits it, then removes the
process, readiness event, and pending restart request. Missing users return
false without error.

Under `registry_lock`, Supervisor validates input and applies fail-closed state
transitions from the spec. Password updates validate the record and increment
the stored version exactly once. `admin` plus `disabled=true` raises a conflict
recognized by the control server. Re-enabling starts the tenant and schedules
plugin reconciliation after readiness.

- [ ] **Step 5: Verify GREEN and commit**

Run focused and full tests, then:

```bash
git add common gateway supervisor tests
git commit -m "feat: manage supervisor user state"
```

---

### Task 4: Expose Administrator User CRUD Contracts

**Files:**
- Modify: `gateway/schemas/users.py`
- Modify: `gateway/routers/users.py`
- Modify: `tests/test_contracts.py`
- Create or Modify: `tests/test_users.py`

**Interfaces:**
- Produces: `CreateManagedUser`, `UpdateManagedUser`, and public `RegisteredUser`; list, detail, create, patch, and delete-placeholder routes.
- Consumes: `common.security.hash_password`, Gateway Supervisor clients, and `require_admin`.

- [ ] **Step 1: Write failing schema and router tests**

Use temporary user storage and `AsyncMock` only at the Unix-socket boundary.
Cover exact public fields, 201 creation, 404 detail, 409 duplicate/admin disable,
422 empty patch, password hashing without secret leakage, idempotent enabled
state, 503 Supervisor failure, and 501 DELETE with no socket invocation.

Add OpenAPI assertions for methods and fields:

```python
self.assertEqual(
    set(schema["paths"]["/api/gateway/v1/admin/users/{username}"]),
    {"get", "patch", "delete"},
)
```

- [ ] **Step 2: Run focused tests and verify RED**

Run:

```bash
.venv/bin/python -m unittest tests.test_users tests.test_contracts -v
```

Expected: missing models and routes.

- [ ] **Step 3: Implement strict models and thin routes**

Use Pydantic aliases consistent with existing camelCase responses. Passwords
are 8–256 characters. `UpdateManagedUser` rejects a body where both password
and disabled are absent. Routes call `require_admin` before storage, hashing,
or socket work. Map typed Supervisor codes to the status table in the spec.

The DELETE handler is exactly a permission check followed by:

```python
raise HTTPException(status_code=501, detail="用户删除暂未实现")
```

- [ ] **Step 4: Verify GREEN and commit**

Run focused and full tests, then:

```bash
git add gateway tests
git commit -m "feat: add administrator user APIs"
```

---

### Task 5: Add Swagger OAuth2 Password Authorize

**Files:**
- Modify: `requirements.txt`
- Modify: `gateway/dependencies.py`
- Modify: `gateway/schemas/auth.py`
- Modify: `gateway/routers/auth.py`
- Modify: `tests/test_auth.py`
- Modify: `tests/test_proxy.py`
- Modify: `tests/test_contracts.py`

**Interfaces:**
- Produces: OAuth2 security scheme with token URL `/api/gateway/v1/auth/token`; `OAuthToken(access_token, token_type, expires_in)`; standard form token endpoint.
- Preserves: Cookie fallback and raw `Authorization: Bearer` parsing.

- [ ] **Step 1: Write failing OAuth2 tests**

Assert OpenAPI contains a password flow and no `HTTPBearer` scheme:

```python
scheme = schema["components"]["securitySchemes"]["OAuth2PasswordBearer"]
self.assertEqual(
    scheme["flows"]["password"]["tokenUrl"],
    "/api/gateway/v1/auth/token",
)
```

Call the token handler with a real `OAuth2PasswordRequestForm` dependency value
and patched credential service. Assert snake_case fields, no Set-Cookie header,
same session payload, invalid credentials 401, unavailable store 503, and rate
limit 429. Retain the direct Bearer precedence tests.

- [ ] **Step 2: Run focused tests and verify RED**

Run:

```bash
.venv/bin/python -m unittest tests.test_auth tests.test_proxy tests.test_contracts -v
```

Expected: missing OAuth2 scheme, model, dependency package, and endpoint.

- [ ] **Step 3: Add the form dependency and OAuth2 scheme**

Append `python-multipart==0.0.20` to requirements and install it into the local
test venv with:

```bash
.venv/bin/pip install python-multipart==0.0.20
```

Replace `HTTPBearer(auto_error=False)` with:

```python
OAuth2PasswordBearer(
    tokenUrl="/api/gateway/v1/auth/token",
    auto_error=False,
)
```

Continue preferring an explicit Authorization header over Cookie. A malformed
or non-Bearer Authorization header remains 401.

- [ ] **Step 4: Implement the standard token endpoint**

Use `OAuth2PasswordRequestForm` and the shared credential verifier. Return a
fresh signed session in `access_token`, literal `bearer`, and configured expiry.
Do not call `set_session`. Reuse the login IP/account limiters so the second
endpoint does not bypass brute-force controls.

- [ ] **Step 5: Verify GREEN and commit**

Run focused and full tests, then:

```bash
git add requirements.txt gateway tests
git commit -m "feat: add swagger password authorization"
```

---

### Task 6: Build the User Management Interface

**Files:**
- Modify: `gateway/web/templates/admin.html`
- Modify: `gateway/web/static/management/admin.css`
- Modify: `gateway/web/static/management/admin.js`
- Modify: `tests/test_proxy.py`
- Modify: `tests/test_web_assets.py`

**Interfaces:**
- Consumes: admin list/create/patch APIs.
- Produces: accessible create form and reset/enable/disable controls; no delete UI.

- [ ] **Step 1: Write failing packaged-interface tests**

Render the real admin template and read the real JavaScript. Assert form labels,
dialog labels, endpoints, POST/PATCH methods, safe `textContent` error display,
and absence of a delete method/button. Require every new DOM ID referenced by
JavaScript to exist in the template.

- [ ] **Step 2: Run focused tests and verify RED**

Run:

```bash
.venv/bin/python -m unittest tests.test_proxy tests.test_web_assets -v
```

Expected: missing forms, dialog, and mutation calls.

- [ ] **Step 3: Implement the framework-free interface**

Add a compact create panel above the table and an actions column. Construct all
row content with DOM methods; never use `innerHTML` for server-returned values.
Use one native reset-password dialog. Disable action buttons during requests,
confirm enable/disable changes, reload after success, and redirect to login when
the current admin password is reset and its session becomes invalid.

- [ ] **Step 4: Verify GREEN and commit**

Run focused and full tests, then:

```bash
git add gateway/web tests
git commit -m "feat: add user administration interface"
```

---

### Task 7: Rebuild, Deploy, and Verify Without Mutating Users

**Files:**
- Modify: local ignored `.env` key only; do not stage it.
- Modify production files only if verification exposes a regression.

**Interfaces:**
- Produces: running `dsh-server-manager:local` service on host port `3081`.

- [ ] **Step 1: Rename the local secret key without displaying it**

Use an in-place key-only substitution from `DSH_ADMIN_PASSWORD=` to
`DSM_ADMIN_PASSWORD=` and add `DSM_REGISTRATION_ENABLED=false` only if absent.
Validate key presence with `cut -d= -f1`; never print values.

- [ ] **Step 2: Run final static verification**

Run:

```bash
.venv/bin/python -m unittest discover -s tests -v
.venv/bin/python -m compileall -q common gateway supervisor tests
git diff --check
docker compose -f docker/compose.yml config -q
```

- [ ] **Step 3: Record persisted counts and deploy**

Read only counts for users and all plugin tables, then:

```bash
docker compose -f docker/compose.yml up -d --build
docker compose -f docker/compose.yml ps
```

- [ ] **Step 4: Smoke-test registration and authenticated reads**

Require health 200, login 200 without `注册新账号`, register page 404, register
POST 403, Gateway assets 200, and offline Swagger 200. Generate a short-lived
token inside the container without printing it and require `/auth/me`, user list,
and user detail to return 200. Do not call create, patch, or delete in deployment.

- [ ] **Step 5: Browser-test admin and Swagger**

Use a 120-second diagnostic session token without printing it. Verify `/admin`
shows create/reset/enable controls and no delete control. Open `/docs`, click
Authorize, and verify the OAuth2 dialog contains username and password fields
with the correct token URL. Remove the diagnostic browser cookie afterward.

- [ ] **Step 6: Recheck data, logs, and clean tree**

Require exact table-count equality, no startup traceback, 99+ passing tests, a
clean worktree, and the planned commits in order. Keep
`refactor/modular-architecture` as-is; do not merge or push.
