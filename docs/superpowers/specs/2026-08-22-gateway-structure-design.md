# Gateway Structure Design

## Objective

Reorganize `gateway/` into consistent HTTP, application-service, proxy, schema, and web-resource boundaries without changing externally observable behavior. The refactor removes development-stage internal compatibility exports, duplicate configuration instances, inverted imports, and duplicated upload-spool code.

## Scope Classification

This is an architectural refactor because it changes internal module ownership across Provider management, Harness RPC, uploads, proxying, authentication, and web injection. It remains one bounded project because every affected flow already exists and all public behavior is preserved.

## Constraints

- Keep `gateway.main:app` as the Uvicorn entry point.
- Preserve all HTTP and WebSocket paths, methods, status codes, request and response fields, OpenAPI security schemes, Cookie/Bearer semantics, and page behavior.
- Preserve the Supervisor Unix-socket wire protocol, SQLite schema and data, filesystem paths, environment variables, single-container topology, and published port `3081`.
- Keep offline Swagger and management assets bundled in the image.
- Do not add a dependency-injection framework, template engine, frontend build chain, new runtime dependency, or service container.
- Do not preserve obsolete internal Python import paths. Tests and production callers move directly to the owning modules.
- Keep `common` dependency-neutral and keep `supervisor` independent of `gateway`.

## Approaches Considered

### 1. Minimal cleanup

Only unify configuration and move Provider schemas. This has the lowest immediate risk but leaves Provider orchestration in a route handler, duplicate spool writers, split Harness RPC ownership, inline widget resources, and the mixed HTTP/WebSocket/transform proxy module.

### 2. Domain-oriented consolidation — selected

Move each existing responsibility to one clear owner, extract only duplicated or independently testable behavior, and retain FastAPI plus the existing function-oriented style. This removes the current structural inconsistencies without adding abstraction frameworks.

### 3. Full layered rewrite

Introduce repositories, interfaces, dependency injection, service containers, and generic transport abstractions. This would make every dependency replaceable but adds substantial indirection to a small single-container application and is rejected as unnecessary.

## Target Structure

```text
gateway/
├── __init__.py
├── main.py                    # ASGI export only
├── application.py             # FastAPI factory, lifespan and router order
├── config.py                  # immutable Config definition
├── runtime.py                 # sole Config instance and packaged paths
├── dependencies.py            # Cookie/Bearer FastAPI dependencies
│
├── routers/
│   ├── __init__.py
│   ├── auth.py                # auth HTTP endpoints and pages
│   ├── management.py          # management HTML pages
│   ├── users.py               # administrative user reads
│   ├── plugins.py
│   ├── providers.py
│   ├── uploads.py
│   └── proxy.py               # catch-all HTTP/WebSocket routes
│
├── schemas/
│   ├── __init__.py
│   ├── auth.py
│   ├── users.py
│   ├── plugins.py
│   ├── providers.py           # real Provider models and validation
│   └── uploads.py
│
├── services/
│   ├── __init__.py
│   ├── auth.py                # sessions, limiter, user resolution and cookies
│   ├── supervisor.py          # Unix-socket client adapter
│   ├── harness.py             # Harness RPC protocol, transport and errors
│   ├── providers.py           # custom Provider use case
│   └── spool.py               # safe bounded request-body spooling
│
├── proxy/
│   ├── __init__.py
│   ├── headers.py             # upstream URL and header policy
│   ├── transforms.py          # bounded HTML/connection-module transforms
│   ├── http.py                # HTTP streaming proxy
│   └── websocket.py           # WebSocket proxy and Cookie extraction
│
└── web/
    ├── __init__.py            # resource roots and raw template reader
    ├── pages.py               # management/auth page rendering
    ├── fragments.py           # cached proxy-injection fragments
    ├── templates/
    │   ├── login.html
    │   ├── register.html
    │   ├── admin.html
    │   ├── plugins.html
    │   ├── upload-widget.html
    │   ├── admin-widget.html
    │   └── logout-widget.html
    └── static/
        ├── management/
        └── swagger-ui/
```

The top-level `auth.py`, `harness_rpc.py`, `providers.py`, `proxy.py`, `pages.py`, `plugin_ui.py`, and `upload_ui.py` are removed after their callers and tests move to the target owners.

## Dependency Direction

```text
application -> routers, runtime, web
routers     -> schemas, services, proxy, web, common.storage
services    -> schemas, common.storage
proxy       -> web.fragments
web         -> packaged templates/static only
gateway     -> common
```

`schemas` never imports service, router, proxy, or former top-level domain modules. Services do not import FastAPI request or response classes except `services.auth`, whose cookie-response helpers are explicitly HTTP-facing. Proxy modules do not query users or authenticate requests; the proxy router resolves the authenticated tenant before calling them.

## Configuration and Entry Point

`runtime.py` is the only module that calls `Config.from_environment()`. `dependencies.py` imports that singleton when constructing the Cookie security scheme, preventing authentication and route code from observing different configuration objects.

`main.py` contains only the stable ASGI export:

```python
from .application import app
```

`create_app`, route handlers, runtime configuration, and asset paths are imported from their owning modules in tests. No internal compatibility re-exports remain.

## Authentication

The current top-level authentication primitives and `services.auth` behavior are consolidated into `services/auth.py`. It owns session signing/parsing, fixed-window login limiting, database-backed session resolution, role enforcement, token creation, and Cookie response helpers. `routers/auth.py` retains request validation, password hashing/verification orchestration, rate-limit policy instances, Supervisor registration calls, and HTTP error mapping.

Cookie and Bearer precedence, seven-day expiry, password-version invalidation, disabled-user rejection, HttpOnly/SameSite behavior, and logout semantics remain unchanged.

## Provider and Harness RPC Flow

`schemas/providers.py` becomes the canonical owner of Provider request/response models, field aliases, validation, credential-reference generation, and Harness profile serialization.

`services/harness.py` becomes the sole owner of RPC envelope creation, response decoding, transport errors, business errors, timeouts, and the `aiohttp` call. The top-level `harness_rpc.py` and service re-export pattern are removed.

`services/providers.py` owns the complete custom-Provider workflow:

```text
validated Provider request
  -> load and validate target user
  -> read Harness provider catalog and settings namespace
  -> reject duplicate provider or unwritable settings
  -> inspect credential writability when an API key is supplied
  -> mutate provider settings with the expected revision
  -> store the credential
  -> return the existing response fields
```

It raises typed Provider operation errors with stable reason codes. `routers/providers.py` authenticates the administrator, invokes the service, and maps those codes to the exact existing HTTP status/detail shapes. The existing partial-success response for “Provider created but credential write failed” is preserved without exposing the API key.

## Upload Spooling

`services/spool.py` implements the shared bounded stream-to-file operation used by regular uploads and offline plugin imports. It receives the stream, destination directory, generated suffix, maximum size, declared `Content-Length`, and empty-file policy. It creates the file with `O_CREAT | O_EXCL | O_NOFOLLOW`, writes complete chunks, calls `fsync`, and removes partial files on every failure.

The service raises typed spool errors rather than `HTTPException`. Each router preserves its existing messages and policies: regular uploads permit an empty file and use `.upload`; offline plugins reject empty content and use `.plugin.tgz`.

Supervisor installation, filename/path validation, ownership checks, and final atomic placement remain unchanged.

## Proxy Boundary

The current proxy module is split by independently testable responsibility:

- `headers.py` owns hop-by-hop filtering, forwarded-header removal, Host replacement, and upstream URL construction.
- `transforms.py` owns the UUID shim, HTML injection, connection-module patch, and bounded response reads.
- `http.py` owns upstream HTTP calls, transformed responses, and streaming release semantics.
- `websocket.py` owns Cookie extraction, upstream connection setup, bidirectional copying, close codes, cancellation, and cleanup.

The catch-all router remains last in application registration order. Authentication and tenant-port resolution stay in `routers/proxy.py`, so lower proxy modules receive only an already-authorized port.

## Web Resources

Full management pages and injected fragments are all owned by `gateway.web`. `web/pages.py` replaces the small top-level page wrapper modules. `web/fragments.py` reads and caches upload, admin, and logout widget templates as bytes for proxy transformation.

Admin/logout markup moves out of Python string literals into templates. Their CSS and JavaScript move to the existing management static directory and are referenced by local `/assets/...` URLs. No remote assets or frontend build step are introduced.

## Error Handling

- Routers are the only layer that raises or maps `HTTPException` for Provider and spool operations.
- Harness transport failures retain HTTP 503 behavior.
- Harness business conflicts retain HTTP 409; other business validation errors retain HTTP 422.
- Invalid upstream response shapes retain HTTP 502.
- Supervisor `bad_request` errors retain HTTP 400 and other Supervisor failures retain HTTP 503.
- Proxy connection failures, oversized transforms, WebSocket close codes, and resource cleanup remain unchanged.
- No error message logs or returns passwords, session tokens, API keys, or credential values.

## Testing

The refactor follows red-green-refactor in independently deployable commits. Tests will cover:

- exactly one runtime `Config` instance used by dependencies and routes;
- `gateway.main` exporting only `app`;
- Provider models at `gateway.schemas.providers` with no reverse import;
- the complete Provider service workflow, including duplicate IDs, unwritable settings, RPC failures, and partial credential failure;
- Harness RPC decoding and transport errors at `gateway.services.harness`;
- shared spool success, declared/streamed size limits, empty-file policies, partial-write cleanup, and router error mappings;
- proxy header filtering, HTML/module transforms, streaming release, WebSocket copying and close behavior through the new package paths;
- all page/fragments using packaged templates and local static assets;
- strict architecture tests that reject obsolete top-level modules and duplicate `Config.from_environment()` calls;
- the existing OpenAPI, authentication, user, plugin, upload, Provider, proxy, and web-asset contracts.

Final verification includes the complete Python suite, bytecode compilation, `git diff --check`, Docker image build, Compose deployment on `3081`, health/login/docs/static-asset checks, an authenticated identity request, and unchanged SQLite table counts.

## Commit Boundaries

The implementation is divided into behavior-preserving commits:

1. Lock Gateway boundaries and remove duplicate runtime configuration.
2. Consolidate Provider schemas, Harness RPC, and Provider service orchestration.
3. Extract shared request-body spooling.
4. Move page and injected-fragment ownership into `gateway.web`.
5. Split the HTTP/WebSocket proxy package.
6. Remove obsolete internal exports, strengthen architecture tests, and verify deployment.

## Non-goals

- No new endpoint, UI feature, authentication mode, Provider capability, upload behavior, proxy capability, or plugin behavior.
- No public API version change.
- No database, IPC, filesystem layout, Linux account, Docker topology, or port change.
- No generalized repository/interface framework or dependency-injection container.
