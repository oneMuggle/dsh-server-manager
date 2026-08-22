# DSH LAN Deployment Modular Architecture Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Convert the current Gateway/Supervisor code into a domain-oriented modular monolith while preserving every external contract and the single-container deployment.

**Architecture:** Move database and control-socket contracts into dependency-neutral `common` modules, then split Supervisor and Gateway by responsibility behind compatibility re-exports. Each commit leaves the application runnable and removes a compatibility shim only after all callers have migrated.

**Tech Stack:** Python 3.11/3.12, FastAPI 0.116.1, Pydantic 2.11.7, aiohttp 3.12.15, SQLite, asyncio Unix sockets, unittest, Docker Compose.

**Spec:** `docs/superpowers/specs/2026-08-22-modular-architecture-design.md`

## Global Constraints

- Keep `gateway.main:app` and `python -m supervisor.main` as entry points.
- Keep the existing single-container process model and Compose port `3081`.
- Keep HTTP paths, public field names, status codes, Cookie/Bearer behavior, IPC action names, and JSON response shapes.
- Keep SQLite paths, tables, columns, legacy JSON migration, and existing data.
- Keep filesystem paths, environment variables, shared plugin release layout, and offline Swagger assets.
- Do not add a frontend framework, frontend build chain, dependency-injection framework, or extra container.
- Run `.venv/bin/python -m unittest discover -s tests -v` before every commit.
- Run `git diff --check` before every commit.

## Commit Stack

1. `feat: checkpoint single-container management baseline` — preserve the already-tested pre-refactor implementation.
2. `docs: record modular architecture and migration plan` — add the approved design and this executable plan.
3. `test: lock external compatibility contracts` — capture OpenAPI, SQLite, IPC, page, and dependency-direction behavior.
4. `refactor: extract common user storage` — move connection policy, user schema, legacy migration, and user repository.
5. `refactor: extract common plugin storage` — split catalog, jobs, and assignment repositories.
6. `refactor: add typed supervisor control contracts` — validate IPC actions without changing the wire protocol.
7. `refactor: separate supervisor users and tenants` — move privileged user and process lifecycle code.
8. `refactor: modularize supervisor plugin runtime` — replace `supervisor/plugins.py` with the `supervisor/plugins/` package.
9. `refactor: separate supervisor uploads and ipc server` — leave `supervisor.main` as lifecycle composition.
10. `refactor: split gateway auth and schemas` — centralize API models and authentication dependencies.
11. `refactor: split gateway routers and services` — leave `gateway.main` as FastAPI composition.
12. `refactor: extract management web assets` — move embedded HTML/CSS/JS to templates and static files.
13. `chore: remove compatibility shims and verify deployment` — enforce dependency direction, update documentation, build, and smoke-test Compose.

---

### Task 1: Record the approved architecture

**Files:**
- Create: `docs/superpowers/specs/2026-08-22-modular-architecture-design.md`
- Create: `docs/superpowers/plans/2026-08-22-modular-architecture.md`

**Interfaces:**
- Consumes: the approved compatibility constraints and domain boundaries.
- Produces: the normative design and ordered commit stack used by every later task.

- [ ] **Step 1: Validate document paths and formatting**

Run: `git diff --check -- docs/superpowers`

Expected: exit code `0` with no output.

- [ ] **Step 2: Commit the documents**

```bash
git add docs/superpowers
git commit -m "docs: record modular architecture and migration plan"
```

### Task 2: Lock external compatibility contracts

**Files:**
- Create: `tests/test_contracts.py`
- Create: `tests/test_architecture.py`
- Modify: `tests/test_proxy.py`

**Interfaces:**
- Consumes: `gateway.main.app`, `gateway.supervisor_client._request`, and the current SQLite schema.
- Produces: `PUBLIC_PATHS`, literal database column expectations, IPC request fixtures, and an import-boundary test used throughout the refactor.

- [ ] **Step 1: Add literal HTTP and database contract tests**

Add tests that derive `app.openapi()` and assert the exact public path set, `apiKeyCookie` and `HTTPBearer` schemes, public model properties, and status-code mappings. Initialize a temporary registry and assert literal `PRAGMA table_info` column lists for `users`, `plugin_versions`, `user_plugins`, `plugin_jobs`, and `plugin_job_items`.

- [ ] **Step 2: Add IPC golden-payload tests**

Patch `asyncio.open_unix_connection` with an in-memory reader/writer and assert `_request("create_user", username="alice")` writes exactly:

```python
b'{"action":"create_user","username":"alice"}\n'
```

Also assert `{ "ok": false, "code": "conflict", "message": "exists" }` becomes `SupervisorError` with unchanged `code` and `message`.

- [ ] **Step 3: Add dependency-direction protection**

Walk Python files under `common/` and `supervisor/` with `ast.parse`. Assert `common` imports neither `gateway` nor `supervisor`, and mark the current Supervisor-to-Gateway imports as an explicit temporary allowlist that shrinks in Tasks 4, 5, and 8.

- [ ] **Step 4: Run the compatibility tests**

Run: `.venv/bin/python -m unittest tests.test_contracts tests.test_architecture tests.test_proxy -v`

Expected: all tests pass against the checkpoint implementation.

- [ ] **Step 5: Run the full suite and commit**

```bash
.venv/bin/python -m unittest discover -s tests -v
git add tests/test_contracts.py tests/test_architecture.py tests/test_proxy.py
git commit -m "test: lock external compatibility contracts"
```

### Task 3: Extract common user storage

**Files:**
- Create: `common/__init__.py`
- Create: `common/storage/__init__.py`
- Create: `common/storage/database.py`
- Create: `common/storage/users.py`
- Modify: `gateway/registry.py`
- Modify: `supervisor/main.py`
- Modify: `tests/test_registry.py`
- Modify: `tests/test_architecture.py`

**Interfaces:**
- Consumes: SQLite path objects and legacy registry dictionaries.
- Produces: `read_connection(path)`, `write_connection(path)`, `initialize_registry(path, legacy_json_path=None)`, `load_registry(path)`, `get_user(path, username)`, `list_users(path)`, `add_user(path, user)`, and `replace_registry(path, registry)`.

- [ ] **Step 1: Write a failing import and persistence test**

Import repository functions from `common.storage.users`, initialize a temporary legacy JSON file, and assert the returned user has literal `username`, `uid`, `port`, `password`, and `role` values.

- [ ] **Step 2: Verify the test fails for the missing package**

Run: `.venv/bin/python -m unittest tests.test_registry -v`

Expected: import failure for `common.storage.users`.

- [ ] **Step 3: Move database and user repository code**

Move connection context managers to `common.storage.database`, move the user schema and repository functions to `common.storage.users`, and import `create_plugin_schema` lazily inside schema initialization until Task 4. Retain `RegistryError` and function re-exports in `gateway.registry` so existing callers remain compatible.

- [ ] **Step 4: Migrate Supervisor imports**

Replace imports from `gateway.registry` with imports from `common.storage.users`. Update the architecture allowlist so user-storage imports from Gateway are no longer permitted.

- [ ] **Step 5: Verify persistence behavior and commit**

```bash
.venv/bin/python -m unittest tests.test_registry tests.test_architecture tests.test_plugins -v
.venv/bin/python -m unittest discover -s tests -v
git add common gateway/registry.py supervisor/main.py tests
git commit -m "refactor: extract common user storage"
```

### Task 4: Extract common plugin storage

**Files:**
- Create: `common/storage/plugin_catalog.py`
- Create: `common/storage/plugin_jobs.py`
- Create: `common/storage/assignments.py`
- Modify: `common/storage/database.py`
- Modify: `common/storage/users.py`
- Modify: `gateway/plugins.py`
- Modify: `gateway/main.py`
- Modify: `supervisor/main.py`
- Modify: `supervisor/plugins.py`
- Modify: `tests/test_plugins.py`
- Modify: `tests/test_architecture.py`

**Interfaces:**
- Consumes: the existing `plugins.sqlite` connection and literal public dictionaries.
- Produces: catalog functions `register_plugin_version`, `get_plugin_version`, `list_plugin_versions`, `list_plugins`; job functions `create_job`, `set_job_status`, `set_job_item_status`, `get_job`, `list_jobs`, `unfinished_job_ids`; assignment functions `get_user_plugin`, `apply_plugin_defaults`, `set_user_plugin`, `remove_user_plugin`, `set_plugin_default`, `enabled_aliases`, `enabled_package_links`, `mark_user_plugins_ready`.

- [ ] **Step 1: Write failing imports for the three repositories**

Update `tests/test_plugins.py` to import each function from its target `common.storage` module and retain the existing literal assertions for shared releases, defaults, assignments, and job results.

- [ ] **Step 2: Verify the imports fail**

Run: `.venv/bin/python -m unittest tests.test_plugins -v`

Expected: import failure for the new plugin repository modules.

- [ ] **Step 3: Move schema and repository functions without changing SQL**

Copy each existing SQL statement and dictionary shape unchanged into its owning module. Keep `gateway.plugins` as a compatibility re-export and keep `PluginStoreError` available at its current import path.

- [ ] **Step 4: Migrate production imports**

Gateway reads from the three `common.storage` modules. Supervisor writes through the same modules. Remove all Supervisor imports from `gateway.plugins` and shrink the architecture allowlist accordingly.

- [ ] **Step 5: Verify SQLite contracts and commit**

```bash
.venv/bin/python -m unittest tests.test_plugins tests.test_registry tests.test_contracts tests.test_architecture -v
.venv/bin/python -m unittest discover -s tests -v
git add common gateway supervisor tests
git commit -m "refactor: extract common plugin storage"
```

### Task 5: Add typed Supervisor control contracts

**Files:**
- Create: `common/contracts/__init__.py`
- Create: `common/contracts/control.py`
- Create: `common/errors.py`
- Create: `gateway/services/__init__.py`
- Create: `gateway/services/supervisor.py`
- Modify: `gateway/supervisor_client.py`
- Modify: `supervisor/main.py`
- Modify: `tests/test_contracts.py`

**Interfaces:**
- Consumes: newline-delimited dictionaries with the current `action` values.
- Produces: `parse_control_request(value: object) -> ControlRequest`, `success_response(**payload) -> dict`, `error_response(code, message) -> dict`, and `SupervisorClient` methods matching current wrapper signatures.

- [ ] **Step 1: Write failing parser tests**

Assert that each current action parses into a typed model, extra required fields are rejected, unknown actions map to `bad_request`, and `model_dump(exclude_none=True)` reproduces the current literal payload.

- [ ] **Step 2: Verify parser tests fail**

Run: `.venv/bin/python -m unittest tests.test_contracts -v`

Expected: import failure for `common.contracts.control`.

- [ ] **Step 3: Implement discriminated request models and response helpers**

Define Pydantic request models for `create_user`, `install_upload`, `list_upload_directories`, `import_online_plugin`, `discover_plugin_versions`, `import_offline_plugin`, and `assign_plugin`. Preserve existing action strings and field aliases.

- [ ] **Step 4: Move the IPC client adapter**

Place transport code in `gateway.services.supervisor`. Keep `gateway.supervisor_client` as a re-export so existing imports remain valid. Parse at the Supervisor dispatch boundary before calling handlers.

- [ ] **Step 5: Verify golden payloads and commit**

```bash
.venv/bin/python -m unittest tests.test_contracts tests.test_proxy tests.test_plugins -v
.venv/bin/python -m unittest discover -s tests -v
git add common gateway supervisor/main.py tests
git commit -m "refactor: add typed supervisor control contracts"
```

### Task 6: Separate Supervisor users and tenants

**Files:**
- Create: `supervisor/users.py`
- Create: `supervisor/tenants.py`
- Modify: `supervisor/main.py`
- Modify: `tests/test_upload.py`
- Create: `tests/test_tenants.py`

**Interfaces:**
- Consumes: user dictionaries, registry paths, tenant ports, and subprocess factories.
- Produces: `admin_password_from_environment()`, `new_user_record(...)`, `normalize_user_roles(registry)`, `provision_os_user(user)`, `valid_password_record(value)`, `http_ready(port)`, and `TenantManager`.

- [ ] **Step 1: Write failing module-boundary tests**

Import password/bootstrap helpers from `supervisor.users` and `TenantManager` from `supervisor.tenants`. Assert the existing fixed-admin role rule and readiness/process-stop behavior using literal fixtures.

- [ ] **Step 2: Verify the new imports fail**

Run: `.venv/bin/python -m unittest tests.test_upload tests.test_tenants -v`

Expected: import failure for the new Supervisor modules.

- [ ] **Step 3: Move user provisioning and tenant lifecycle code**

Move functions without changing environment names, UID/port allocation, subprocess arguments, signal behavior, or readiness timing. Inject only the subprocess/readiness seams needed by the tests.

- [ ] **Step 4: Reduce Supervisor main to composition calls**

Import the moved functions and class into `supervisor.main`; retain temporary re-exports for the existing tests until their imports have migrated.

- [ ] **Step 5: Verify and commit**

```bash
.venv/bin/python -m unittest tests.test_upload tests.test_tenants tests.test_contracts -v
.venv/bin/python -m unittest discover -s tests -v
git add supervisor tests
git commit -m "refactor: separate supervisor users and tenants"
```

### Task 7: Modularize the Supervisor plugin runtime

**Files:**
- Delete: `supervisor/plugins.py`
- Create: `supervisor/plugins/__init__.py`
- Create: `supervisor/plugins/manager.py`
- Create: `supervisor/plugins/registry.py`
- Create: `supervisor/plugins/installer.py`
- Create: `supervisor/plugins/profile.py`
- Create: `supervisor/plugins/assignments.py`
- Modify: `supervisor/main.py`
- Modify: `tests/test_plugins.py`

**Interfaces:**
- Consumes: common plugin repositories, user repository, command runner, shared release paths, and tenant restart callback.
- Produces: `SharedPluginManager`, `PluginOperationError`, `validate_package_name`, and `validate_requested_version` from `supervisor.plugins`, with focused collaborators behind the same public manager methods.

- [ ] **Step 1: Add failing focused-module tests**

Import registry metadata parsing from `supervisor.plugins.registry`, package staging from `supervisor.plugins.installer`, profile generation from `supervisor.plugins.profile`, and assignment rollback from `supervisor.plugins.assignments`. Assert the existing online version response, import-only package validation, shared symlink target, and rollback state.

- [ ] **Step 2: Verify the focused imports fail**

Run: `.venv/bin/python -m unittest tests.test_plugins -v`

Expected: import failure because `supervisor.plugins` is still a module.

- [ ] **Step 3: Convert the module into a package**

Move registry URL/auth/metadata logic, pnpm/staging/release logic, profile/link/config logic, and per-user rollback logic into their named modules. Keep queue ownership and orchestration in `manager.py`; export the existing public names from `__init__.py`.

- [ ] **Step 4: Verify no package duplication and no Gateway dependency**

Run tests against a temporary shared release and two users. Assert both user links resolve to the same release path and `tests.test_architecture` finds no Gateway import under `supervisor/plugins/`.

- [ ] **Step 5: Verify and commit**

```bash
.venv/bin/python -m unittest tests.test_plugins tests.test_architecture tests.test_contracts -v
.venv/bin/python -m unittest discover -s tests -v
git add supervisor tests
git commit -m "refactor: modularize supervisor plugin runtime"
```

### Task 8: Separate Supervisor uploads and IPC server

**Files:**
- Create: `supervisor/uploads.py`
- Create: `supervisor/ipc_server.py`
- Modify: `supervisor/main.py`
- Modify: `tests/test_upload.py`
- Modify: `tests/test_contracts.py`

**Interfaces:**
- Consumes: typed control requests, peer credentials, user records, directory file descriptors, and upload spool paths.
- Produces: `upload_directory_parts`, `validate_upload_filename`, `list_upload_directories`, `install_uploaded_file`, and `ControlServer`.

- [ ] **Step 1: Write failing public upload-module and dispatch tests**

Import upload validation from `supervisor.uploads` and invoke `ControlServer.dispatch` with literal typed requests. Assert traversal, hidden components, wrong peer UID, unknown action, and successful directory listing behavior.

- [ ] **Step 2: Verify the new imports fail**

Run: `.venv/bin/python -m unittest tests.test_upload tests.test_contracts -v`

Expected: import failure for `supervisor.uploads` or `supervisor.ipc_server`.

- [ ] **Step 3: Move filesystem and socket code**

Preserve `openat`-style directory ownership checks, atomic rename behavior, size limits, `SO_PEERCRED` validation, socket permissions, and newline framing. Use `common.contracts.control` at dispatch.

- [ ] **Step 4: Leave lifecycle composition in main**

`supervisor.main` must initialize storage, users, firewall, tenants, plugins, and `ControlServer`, then manage shutdown. It must no longer parse action dictionaries or manipulate upload paths directly.

- [ ] **Step 5: Verify and commit**

```bash
.venv/bin/python -m unittest tests.test_upload tests.test_contracts tests.test_architecture -v
.venv/bin/python -m unittest discover -s tests -v
git add supervisor tests
git commit -m "refactor: separate supervisor uploads and ipc server"
```

### Task 9: Split Gateway authentication and schemas

**Files:**
- Create: `gateway/schemas/__init__.py`
- Create: `gateway/schemas/auth.py`
- Create: `gateway/schemas/users.py`
- Create: `gateway/schemas/plugins.py`
- Create: `gateway/schemas/providers.py`
- Create: `gateway/schemas/uploads.py`
- Create: `gateway/dependencies.py`
- Create: `gateway/services/auth.py`
- Modify: `gateway/main.py`
- Modify: `gateway/providers.py`
- Modify: `tests/test_auth.py`
- Modify: `tests/test_contracts.py`

**Interfaces:**
- Consumes: FastAPI requests, Cookie/Bearer values, user repositories, and current auth primitives.
- Produces: current Pydantic API models, `request_user`, `api_token`, `require_session`, `require_admin`, `make_session_token`, `set_session`, and `authenticated_response`.

- [ ] **Step 1: Write failing schema and dependency imports**

Import every public model from its target schema module and authentication dependencies from `gateway.dependencies`. Assert `model_json_schema()` uses the existing aliases and the OpenAPI contract is byte-for-byte equal after removing generated ordering noise.

- [ ] **Step 2: Verify the new imports fail**

Run: `.venv/bin/python -m unittest tests.test_auth tests.test_contracts -v`

Expected: import failure for `gateway.schemas` or `gateway.dependencies`.

- [ ] **Step 3: Move models and auth orchestration**

Move classes without changing aliases, validation constraints, security scheme definitions, token precedence, cookie flags, or error messages. Keep cryptographic primitives in `gateway.auth` and request-level orchestration in `gateway.services.auth`.

- [ ] **Step 4: Rebuild the app and compare OpenAPI**

Import `gateway.main.app`, generate OpenAPI, and compare it with the literal compatibility assertions from Task 2.

- [ ] **Step 5: Verify and commit**

```bash
.venv/bin/python -m unittest tests.test_auth tests.test_contracts tests.test_proxy -v
.venv/bin/python -m unittest discover -s tests -v
git add gateway tests
git commit -m "refactor: split gateway auth and schemas"
```

### Task 10: Split Gateway routers and services

**Files:**
- Create: `gateway/routers/__init__.py`
- Create: `gateway/routers/auth.py`
- Create: `gateway/routers/admin_users.py`
- Create: `gateway/routers/plugins.py`
- Create: `gateway/routers/providers.py`
- Create: `gateway/routers/uploads.py`
- Create: `gateway/routers/proxy.py`
- Create: `gateway/services/harness.py`
- Modify: `gateway/main.py`
- Modify: `gateway/harness_rpc.py`
- Modify: `tests/test_proxy.py`
- Modify: `tests/test_contracts.py`

**Interfaces:**
- Consumes: schema models, auth dependencies, repositories, Supervisor client, Harness RPC, and proxy functions.
- Produces: one `APIRouter` per domain and `create_app() -> FastAPI`; module global `app = create_app()` remains available.

- [ ] **Step 1: Write failing router import and route-order tests**

Import each router and assert `create_app()` retains exact public paths, static mounts, docs configuration, catch-all HTTP route order, and final WebSocket route.

- [ ] **Step 2: Verify the router imports fail**

Run: `.venv/bin/python -m unittest tests.test_proxy tests.test_contracts -v`

Expected: import failure for `gateway.routers`.

- [ ] **Step 3: Move handlers by domain**

Move handlers without changing decorators, response classes, tags, summaries, dependencies, limits, or exception mapping. Move Harness RPC orchestration to `gateway.services.harness`; keep transport helpers focused.

- [ ] **Step 4: Compose routers in stable order**

Mount offline static assets before registering docs. Include auth, admin users, plugins, providers, uploads, and proxy routers in that order, with catch-all routes last.

- [ ] **Step 5: Verify and commit**

```bash
.venv/bin/python -m unittest tests.test_proxy tests.test_contracts tests.test_providers tests.test_plugins -v
.venv/bin/python -m unittest discover -s tests -v
git add gateway tests
git commit -m "refactor: split gateway routers and services"
```

### Task 11: Extract management web assets

**Files:**
- Create: `gateway/web/__init__.py`
- Create: `gateway/web/templates/login.html`
- Create: `gateway/web/templates/register.html`
- Create: `gateway/web/templates/admin.html`
- Create: `gateway/web/templates/plugins.html`
- Create: `gateway/web/static/css/management.css`
- Create: `gateway/web/static/js/auth.js`
- Create: `gateway/web/static/js/admin.js`
- Create: `gateway/web/static/js/plugins.js`
- Modify: `gateway/pages.py`
- Modify: `gateway/plugin_ui.py`
- Modify: `gateway/upload_ui.py`
- Modify: `gateway/routers/auth.py`
- Modify: `gateway/routers/admin_users.py`
- Modify: `gateway/routers/plugins.py`
- Modify: `docker/Dockerfile`
- Modify: `tests/test_proxy.py`
- Modify: `tests/test_upload_ui_theme.py`

**Interfaces:**
- Consumes: template names and serializable page context.
- Produces: `render_template(name, context) -> str` and versioned static URLs served entirely from the image.

- [ ] **Step 1: Write failing rendered-page behavior tests**

Render each page through the FastAPI route and assert status, login/register links, logout control, management links, plugin online/offline/batch controls, DSH theme tokens, and only local static asset URLs.

- [ ] **Step 2: Verify the test fails for missing extracted assets**

Run: `.venv/bin/python -m unittest tests.test_proxy tests.test_upload_ui_theme -v`

Expected: failure because the routes still return embedded page content instead of the required local asset references.

- [ ] **Step 3: Move markup and assets**

Use Python standard-library template loading or Starlette templates already available through FastAPI; do not add a frontend dependency. Preserve element IDs, API URLs, request bodies, redirects, and existing text used by behavior tests.

- [ ] **Step 4: Copy the web directory into the image**

Ensure the existing `COPY gateway` instruction includes templates and static assets and the runtime permissions command leaves them readable by the Gateway user.

- [ ] **Step 5: Verify and commit**

```bash
.venv/bin/python -m unittest tests.test_proxy tests.test_upload_ui_theme tests.test_contracts -v
.venv/bin/python -m unittest discover -s tests -v
git add gateway docker/Dockerfile tests
git commit -m "refactor: extract management web assets"
```

### Task 12: Remove shims and verify deployment

**Files:**
- Modify: `gateway/registry.py`
- Modify: `gateway/plugins.py`
- Modify: `gateway/supervisor_client.py`
- Modify: `README.md`
- Modify: `tests/test_architecture.py`
- Modify: `tests/test_contracts.py`

**Interfaces:**
- Consumes: all final modules and compatibility tests.
- Produces: a dependency-clean final tree with no obsolete re-export consumers and verified deployment instructions for port `3081`.

- [ ] **Step 1: Make architecture checks strict**

Remove the temporary allowlist. Assert no file under `supervisor/` imports `gateway`, no file under `common/` imports either runtime package, and `gateway.main.py` plus `supervisor.main.py` contain composition rather than domain implementations.

- [ ] **Step 2: Remove unused compatibility shims**

Use `rg` to prove no production or test imports remain for old repository and IPC paths, then delete only unused exports. Retain a shim if it is part of a documented public Python import rather than an internal path.

- [ ] **Step 3: Run static and test verification**

```bash
.venv/bin/python -m compileall -q common gateway supervisor tests
.venv/bin/python -m unittest discover -s tests -v
git diff --check
```

Expected: all commands exit `0`; the full test count is at least 45.

- [ ] **Step 4: Build and smoke-test without clearing live data**

```bash
docker compose build dsh
docker compose up -d dsh
docker compose ps
curl -fsS http://127.0.0.1:3081/api/gateway/v1/health
curl -fsS -o /dev/null http://127.0.0.1:3081/login
curl -fsS -o /dev/null http://127.0.0.1:3081/docs
```

Expected: image build succeeds, the service is running on `3081`, health returns `{"ok":true}`, and both pages return success.

- [ ] **Step 5: Verify existing persisted state**

Query the mounted SQLite database read-only and compare counts for users, plugin versions, assignments, jobs, and job items with the pre-deployment snapshot. Confirm tenant readiness through the existing authenticated API without printing credentials or tokens.

- [ ] **Step 6: Commit the final cleanup**

```bash
git add common gateway supervisor tests README.md
git commit -m "chore: remove compatibility shims and verify deployment"
```
