# DSH Server Manager Renaming Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Rename the product from DSH LAN Deployment to DSH Server Manager, using DSM as its operational abbreviation, without migrating or invalidating existing data.

**Architecture:** Product-facing and deployment-local identifiers change to DSM/DSH Server Manager, while persisted DSH integration identifiers remain stable. Supervisor configuration owns the container installation root so the Dockerfile and child Gateway process cannot drift.

**Tech Stack:** Python 3.12+, FastAPI, Docker, Docker Compose, unittest

**Spec:** `docs/superpowers/specs/2026-08-22-dsm-renaming-design.md`

## Global Constraints

- Use `DSH Server Manager`, `DSM`, and `dsh-server-manager` exactly as defined by the spec.
- Preserve `DSH_*`, `/var/lib/dsh`, `/run/dsh/control.sock`, `dsh_session`, Linux accounts, plugin scope, firewall chain, APIs, SQLite schema, and data.
- Keep port `3081` and the single-container process model.
- Do not move the host checkout directory during the active Codex workspace session.

---

### Task 1: Lock Runtime Branding

**Files:**
- Modify: `tests/test_contracts.py`
- Modify: `tests/test_supervisor.py`
- Modify: `supervisor/config.py`
- Modify: `supervisor/gateway_process.py`
- Modify: `gateway/application.py`

**Interfaces:**
- Produces: `SupervisorConfig.install_root: Path`, `gateway_command(config) -> tuple[str, ...]`, and FastAPI title `DSM · DSH Server Manager`.

- [ ] Add a FastAPI/OpenAPI assertion for the literal DSM product title and a Supervisor assertion for `/opt/dsh-server-manager` in the generated Gateway command.
- [ ] Run `.venv/bin/python -m unittest tests.test_contracts tests.test_supervisor -v` and confirm both assertions fail against the old name.
- [ ] Add `install_root` to `SupervisorConfig`, make `gateway_command` derive `PYTHONPATH` and `cwd` from it, and update the FastAPI title and description.
- [ ] Run the focused tests and the full suite.
- [ ] Commit as `refactor: rename runtime branding to DSM`.

### Task 2: Rename Build and Deployment Identifiers

**Files:**
- Modify: `docker/Dockerfile`
- Modify: `docker/compose.yml`
- Modify: `README.md`
- Modify: `supervisor/application.py`
- Modify: `supervisor/control/server.py`
- Modify: `supervisor/main.py`
- Modify: `supervisor/tenants/manager.py`
- Modify: `docs/superpowers/specs/2026-08-22-modular-architecture-design.md`
- Modify: `docs/superpowers/plans/2026-08-22-modular-architecture.md`

**Interfaces:**
- Produces: image `dsh-server-manager:local`, Compose project `dsh-server-manager`, service `dsm`, installation root `/opt/dsh-server-manager`, and logger namespace `dsm.supervisor`.

- [ ] Replace only old product identifiers; retain every compatibility-boundary identifier listed in the spec.
- [ ] Run `docker compose -f docker/compose.yml config --format json` and assert the rendered project, service, image, bind mount, port, capabilities, and security options.
- [ ] Scan tracked text files and require no active `dsh-lan-deployment`, `DSH LAN Deployment`, `DSH LAN Gateway`, or `dsh.supervisor` references.
- [ ] Run the full test suite and commit as `chore: rename project to DSH Server Manager`.

### Task 3: Replace the Running Compose Project

**Files:**
- No source changes unless verification reveals a regression.

**Interfaces:**
- Consumes: existing `docker/data/` and `.env`.
- Produces: running `dsh-server-manager-dsm-1` container on host port `3081`.

- [ ] Record pre-deployment SQLite counts from `dsh-lan-deployment-dsh-1`.
- [ ] Build the new image using `docker compose -f docker/compose.yml build`.
- [ ] Stop and remove only the old `dsh-lan-deployment` Compose project.
- [ ] Start the new project with `docker compose -f docker/compose.yml up -d`.
- [ ] Verify container status, login, `/openapi.json` title, `/docs`, management assets, Swagger assets, `/opt/dsh-server-manager`, and unchanged SQLite counts.
- [ ] Run the full suite, bytecode compilation, `git diff --check`, and confirm a clean worktree.
