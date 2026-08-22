# Supervisor Code Structure Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Reorganize Supervisor into explicit application, control, tenant, and plugin boundaries without changing external behavior.

**Architecture:** Resolve deployment values through one immutable configuration object, keep `main.py` as the executable shell, group privileged tenant operations in `supervisor.tenants`, and keep plugin queue coordination separate from package acquisition. Existing typed control contracts remain the boundary between Gateway and Supervisor.

**Tech Stack:** Python 3.12+, asyncio, FastAPI/Uvicorn child process, Unix sockets, SQLite, unittest, Docker Compose

**Spec:** `docs/superpowers/specs/2026-08-22-supervisor-structure-design.md`

## Global Constraints

- Keep `python -m supervisor.main`, all control actions and JSON fields, SQLite schema/data, paths, environment variables, Linux users, single-container deployment, and port `3081` unchanged.
- Do not introduce new runtime dependencies or compatibility modules for removed internal imports.
- Follow red-green-refactor for each production boundary and run the full suite before every commit.

---

### Task 1: Lock the Target Module Boundaries

**Files:**
- Modify: `tests/test_architecture.py`
- Modify: `tests/test_contracts.py`
- Modify: `tests/test_tenants.py`
- Modify: `tests/test_upload.py`

**Interfaces:**
- Consumes: current Supervisor behavior and control models.
- Produces: required imports from `supervisor.application`, `supervisor.config`, `supervisor.control`, and `supervisor.tenants`.

- [ ] Add imports for `Supervisor`, `SupervisorConfig`, `ControlServer`, `TenantManager`, upload validators, and the concrete `FirewallError` at their target paths.
- [ ] Add an architecture assertion that `supervisor.main` imports application lifecycle only and does not import storage or tenant/plugin implementation modules.
- [ ] Run `.venv/bin/python -m unittest tests.test_architecture tests.test_contracts tests.test_tenants tests.test_upload -v` and confirm the target-module imports fail.

### Task 2: Centralize Configuration and Group Tenant/Control Domains

**Files:**
- Create: `supervisor/config.py`
- Create: `supervisor/control/__init__.py`
- Move: `supervisor/ipc_server.py` to `supervisor/control/server.py`
- Create: `supervisor/tenants/__init__.py`
- Move: `supervisor/users.py` to `supervisor/tenants/accounts.py`
- Move: `supervisor/tenants.py` to `supervisor/tenants/manager.py`
- Move: `supervisor/uploads.py` to `supervisor/tenants/uploads.py`
- Move: `supervisor/firewall.py` to `supervisor/tenants/firewall.py`
- Modify: affected tests and relative imports

**Interfaces:**
- Produces: `SupervisorConfig.from_environment()`, `ControlServer`, `TenantManager`, tenant account helpers, upload helpers, `FirewallError`, and `apply_firewall`.

- [ ] Implement `SupervisorConfig` with the exact current path, account, port, size-limit, regex, and CLI defaults.
- [ ] Move modules with history-preserving renames and update only their relative imports.
- [ ] Export stable domain names from `control/__init__.py` and `tenants/__init__.py`.
- [ ] Run the focused tests and confirm they pass.
- [ ] Commit as `refactor: group supervisor control and tenant domains`.

### Task 3: Extract State, Gateway Process, Application, and Entry Point

**Files:**
- Create: `supervisor/state.py`
- Create: `supervisor/gateway_process.py`
- Create: `supervisor/application.py`
- Modify: `supervisor/main.py`
- Modify: `tests/test_architecture.py`

**Interfaces:**
- Produces: `initialize_gateway_state(config, logger)`, `start_gateway(config)`, `stop_gateway(process)`, and `Supervisor(config=None)` with the existing control-owner methods.

- [ ] Move registry loading and filesystem initialization into `state.py`, parameterized by `SupervisorConfig`.
- [ ] Move the exact `runuser env -i ... uvicorn` child command and process-group termination into `gateway_process.py`.
- [ ] Move Supervisor composition, request operations, startup ordering, and shutdown ordering into `application.py`.
- [ ] Reduce `main.py` to root validation, signal coordination, error-to-exit-code mapping, and the module entry statement.
- [ ] Run focused and full tests, then commit as `refactor: extract supervisor application lifecycle`.

### Task 4: Split Plugin Package Acquisition from Job Coordination

**Files:**
- Create: `supervisor/plugins/commands.py`
- Create: `supervisor/plugins/packages.py`
- Modify: `supervisor/plugins/manager.py`
- Modify: `supervisor/plugins/assignments.py`
- Modify: `tests/test_plugins.py`

**Interfaces:**
- Produces: `PluginCommandRunner` for bounded subprocess/staging operations and `SharedPackageRepository` for discovery/import/publish/resolver operations; `SharedPluginManager` retains its existing constructor and public methods.

- [ ] Add failing tests that construct the new collaborators and exercise registry discovery plus bounded output/timeout boundaries without asserting on mock existence.
- [ ] Extract command execution, staging, npm authentication, and pnpm installation into `commands.py`.
- [ ] Extract discovery, online/offline import, immutable release publication, and resolver links into `packages.py`.
- [ ] Keep queue lifecycle, job status transitions, user assignment, profile reconciliation, and the public facade in `manager.py`.
- [ ] Run plugin tests and the full suite, then commit as `refactor: separate supervisor plugin package operations`.

### Task 5: Verify and Deploy

**Files:**
- Modify only if verification exposes a regression.

**Interfaces:**
- Verifies all compatibility constraints from the design.

- [ ] Run `.venv/bin/python -m unittest discover -s tests -v` and require zero failures.
- [ ] Run `.venv/bin/python -m compileall -q common gateway supervisor tests` and `git diff --check`.
- [ ] Record SQLite table counts, rebuild with `docker compose -f docker/compose.yml up -d --build`, and verify the counts are unchanged.
- [ ] Verify login, `/docs`, `/assets/admin.js`, and `/static/swagger-ui/swagger-ui-bundle.js` on port `3081`.
- [ ] Confirm the worktree is clean and report the final commit stack.
