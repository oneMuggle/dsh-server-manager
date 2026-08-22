# DSH Server Manager Modular Architecture Design

## Objective

Refactor the existing single-container deployment into a domain-oriented modular monolith without changing its external behavior. The refactor must keep existing HTTP APIs, authentication semantics, SQLite data, IPC wire protocol, filesystem paths, environment variables, Compose deployment, and port `3081` compatible.

## Constraints

- Keep `gateway.main:app` as the ASGI entry point.
- Keep `python -m supervisor.main` as the container entry point.
- Keep Gateway and Supervisor in one container with the existing process model.
- Keep all HTTP paths, public request/response fields, security schemes, and status codes.
- Keep the control socket path, action names, and JSON response shape `{ok, code, message}`.
- Keep the SQLite file path, table names, columns, migrations, and existing records.
- Keep the existing data mounts, plugin release layout, user homes, and environment variable names.
- Keep Swagger fully offline and the externally published Compose port at `3081`.
- Do not add a frontend framework, frontend build chain, dependency-injection framework, or new service container.
- Every migration commit must be independently testable and deployable.

## Target Architecture

```text
common/
├── errors.py
├── contracts/
│   └── control.py
└── storage/
    ├── database.py
    ├── users.py
    ├── plugin_catalog.py
    ├── plugin_jobs.py
    └── assignments.py

gateway/
├── main.py
├── dependencies.py
├── schemas/
├── routers/
├── services/
└── web/
    ├── templates/
    └── static/

supervisor/
├── main.py
├── ipc_server.py
├── users.py
├── tenants.py
├── uploads.py
├── firewall.py
└── plugins/
    ├── manager.py
    ├── registry.py
    ├── installer.py
    ├── profile.py
    └── assignments.py
```

The only shared dependency direction is:

```text
gateway ------> common
supervisor ---> common
```

`common` must not import Gateway or Supervisor. Supervisor must not import Gateway. Plugin runtime code is internal to `supervisor.plugins`; it is not a top-level shared package.

## Ownership Boundaries

### Common

`common.storage` owns SQLite connection policy, schema initialization, and repository functions. It contains no HTTP, process, filesystem provisioning, or FastAPI concerns. `common.contracts` owns validated in-process representations of control-socket requests and responses while retaining the current JSON wire shape. `common.errors` contains stable errors used across both entry points.

### Gateway

Gateway owns HTTP routing, Cookie/Bearer token authentication, request validation, admin pages, Harness RPC, and HTTP/WebSocket proxying. `gateway.main` becomes composition and lifespan code only. Privileged operations are requested through `gateway.services.supervisor`.

### Supervisor

Supervisor is the sole owner of privileged mutations: Linux users, tenant processes, firewall rules, safe filesystem writes, and shared plugin releases. It is the primary writer of user records so OS provisioning and database state remain coordinated.

## User Lifecycle

Registration follows this unchanged observable flow:

```text
HTTP registration
  -> Gateway validation and password hashing
  -> CreateUser control request
  -> Supervisor OS user/UID/port/firewall provisioning
  -> SQLite user record
  -> tenant startup
  -> Gateway token response and HttpOnly cookie
```

User persistence moves to `common.storage.users`. Gateway reads through that repository. Supervisor writes through the same repository after privileged provisioning succeeds. `gateway.routers.admin_users` exposes administrative reads; `supervisor.users` and `supervisor.tenants` own privileged lifecycle work.

## Plugin Lifecycle

A plugin version is stored once in the shared release directory. Per-user state consists of assignments, configuration, and links to the shared release rather than copied package trees.

Online import, offline import, registry version discovery, batch assignment, enable/disable, async jobs, per-user results, and rollback remain supported. The target flow is:

```text
Gateway plugin route
  -> validated control request
  -> Supervisor plugin job queue
  -> registry or offline package resolution
  -> shared release installation
  -> per-user profile/link update
  -> tenant restart/readiness check
  -> success or rollback
```

`supervisor.plugins.manager` coordinates jobs. Registry access, package installation, profile mutation, and assignment rollback live in focused modules within the same package.

## IPC and Error Handling

The Unix socket transport and peer credential checks remain unchanged. Typed models validate each action at the IPC boundary, but serialization preserves existing action names, payload keys, and response objects. Malformed and unknown actions continue to map to the current `bad_request` behavior.

Internal errors are grouped into validation, conflict, unavailable, and operation failures. Each exception is logged once at its owning boundary. Gateway preserves current HTTP mappings and never returns secret request data in error details.

## Web Assets

Embedded management-page HTML is moved to templates, and page CSS/JavaScript is moved to static files. Existing URLs, DOM behavior, API calls, redirects, and authentication behavior remain unchanged. Swagger assets remain vendored for offline operation. No Node-based frontend build is introduced.

## Migration Strategy

The migration is incremental:

1. Checkpoint the tested current feature set on `refactor/modular-architecture`.
2. Record compatibility contracts and architecture documentation.
3. Extract SQLite repositories into `common.storage` with temporary compatibility re-exports.
4. Add typed IPC contracts and separate the client/server transport adapters.
5. Split privileged user, tenant, upload, and plugin responsibilities inside Supervisor.
6. Split Gateway schemas, dependencies, services, and routers.
7. Extract templates and static assets.
8. Remove compatibility shims only after all callers and tests use the new modules.
9. Build the image and smoke-test Compose against a copy of existing data before updating the running deployment.

## Acceptance Criteria

- The existing 45-test baseline remains green throughout the migration.
- Compatibility tests cover OpenAPI paths/security/field names, SQLite schema and legacy migration, IPC payloads, page routes, and offline Swagger assets.
- Existing user, plugin, assignment, and job data remains readable without destructive migration.
- Login, registration, Bearer/Cookie authentication, uploads, provider creation, proxying, plugin import/discovery/assignment/rollback, and tenant readiness are verified.
- The Docker image builds successfully and Compose serves the application on `3081`.
- No Supervisor module imports from `gateway` in the final state.

## Non-goals

This refactor does not introduce new product behavior, redesign public APIs, redesign the database schema, split the container, or replace the current frontend technology.
