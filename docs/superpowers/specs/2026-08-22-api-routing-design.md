# DSM API Routing and Router Package Design

## Goal

Replace the implementation-oriented `/api/gateway/v1` surface with a concise,
REST-oriented `/api/v1` API. Group route-handler code by its first business
resource rather than mirroring every URL segment in the filesystem.

This change reorganizes paths and route ownership only. Cookie and Bearer
authentication retain their current behavior. The management pages, tenant
proxy, WebSocket proxy, persistence, Supervisor control protocol, schemas, and
services remain functionally unchanged.

## Compatibility Policy

DSM is still in development. The old `/api/gateway/v1/**` routes are removed
without redirects, aliases, deprecation responses, or compatibility routers.
All packaged browser assets, tests, documentation, and the Swagger OAuth2 token
URL switch to the new paths in the same commit stack.

The HTML and infrastructure paths are not part of the versioned API and remain
unchanged:

- `/login`
- `/register`
- `/admin`
- `/admin/plugins`
- `/docs`
- `/openapi.json`
- `/static/swagger-ui/**`
- `/gateway-assets/**`
- `/` and the tenant HTTP/WebSocket catch-all

## Path Conventions

- The API root is `/api/v1`.
- Resource names are plural nouns.
- Authorization is policy, not a URL namespace; `/admin` is removed from API
  paths.
- HTTP methods express create, read, update, and delete operations.
- Domain-specific subordinate resources remain below their parent domain so
  their handlers can share one Router package.
- OAuth2's token endpoint remains a pragmatic authentication endpoint rather
  than being forced into a generic resource abstraction.

## Route Map

### System

| Method | Old path | New path |
| --- | --- | --- |
| GET | `/api/gateway/v1/health` | `/api/v1/system/health` |

### Authentication

| Method | Old path | New path |
| --- | --- | --- |
| POST | `/api/gateway/v1/auth/register` | `/api/v1/auth/registrations` |
| POST | `/api/gateway/v1/auth/login` | `/api/v1/auth/sessions` |
| POST | `/api/gateway/v1/auth/token` | `/api/v1/auth/token` |
| POST | `/api/gateway/v1/auth/logout` | `DELETE /api/v1/auth/sessions/current` |
| GET | `/api/gateway/v1/auth/me` | `GET /api/v1/auth/sessions/current` |

The OAuth2 password flow uses `/api/v1/auth/token` as its `tokenUrl`. JSON
session creation continues to set the HttpOnly Cookie and return the existing
token response. Deleting the current session removes the Cookie. Bearer tokens
remain stateless and are discarded by their clients.

Public registration remains controlled by `DSM_REGISTRATION_ENABLED`.

### Users

| Method | Old path | New path |
| --- | --- | --- |
| GET | `/api/gateway/v1/admin/users` | `/api/v1/users` |
| POST | `/api/gateway/v1/admin/users` | `/api/v1/users` |
| GET | `/api/gateway/v1/admin/users/{username}` | `/api/v1/users/{username}` |
| PATCH | `/api/gateway/v1/admin/users/{username}` | `/api/v1/users/{username}` |
| DELETE | `/api/gateway/v1/admin/users/{username}` | `/api/v1/users/{username}` |
| POST | `/api/gateway/v1/admin/users/{username}/providers` | `/api/v1/users/{username}/providers` |

All operations retain their existing administrator policy. The DELETE handler
remains an authenticated `501 Not Implemented` placeholder with no side effect.

### Plugins

| Method | Old path | New path |
| --- | --- | --- |
| GET | `/api/gateway/v1/admin/plugins` | `/api/v1/plugins` |
| GET | `/api/gateway/v1/admin/plugin-jobs` | `/api/v1/plugins/jobs` |
| POST | `/api/gateway/v1/admin/plugin-jobs` | `/api/v1/plugins/jobs` |
| GET | `/api/gateway/v1/admin/plugin-jobs/{job_id}` | `/api/v1/plugins/jobs/{job_id}` |
| POST | `/api/gateway/v1/admin/plugins/online` | `/api/v1/plugins/imports/registry` |
| POST | `/api/gateway/v1/admin/plugins/offline` | `/api/v1/plugins/imports/archive` |
| GET | `/api/gateway/v1/admin/plugins/registry-versions` | `/api/v1/plugins/registry/versions` |

`registry` and `archive` identify import sources. They stay separate because
the registry request is JSON while the archive request is multipart. Combining
them into one path would require manual content-type dispatch and would weaken
the generated OpenAPI contract.

Plugin jobs remain resources: POST creates a job, GET lists jobs, and GET with
an ID reads one job. Every plugin route retains administrator authorization.

### Files

| Method | Old path | New path |
| --- | --- | --- |
| GET | `/api/gateway/v1/files/directories` | `/api/v1/files/directories` |
| POST | `/api/gateway/v1/files/upload` | `/api/v1/files` |

GET lists directories below the authenticated user's workspace. POST creates a
file from the multipart upload. Both retain their current-user authorization
and validation behavior.

## Router Package Structure

The filesystem groups route handlers by the first business domain in the new
API. It does not reproduce every nested URL segment.

```text
gateway/routers/
├── auth/
│   ├── __init__.py
│   ├── router.py
│   ├── registrations.py
│   └── sessions.py
├── user/
│   ├── __init__.py
│   ├── router.py
│   ├── accounts.py
│   └── providers.py
├── plugin/
│   ├── __init__.py
│   ├── router.py
│   ├── catalog.py
│   ├── imports.py
│   ├── jobs.py
│   └── registry.py
├── file/
│   ├── __init__.py
│   ├── router.py
│   └── operations.py
├── system/
│   ├── __init__.py
│   └── router.py
├── management.py
└── proxy.py
```

Each domain `router.py` owns one `APIRouter` with its shared `/api/v1/<domain>`
prefix, tags, and subordinate router inclusion. Handler modules use paths
relative to that prefix. For example, the user accounts module owns `/` and
`/{username}`, while the provider module owns `/{username}/providers`; both are
inside `gateway.routers.user`.

`management.py` becomes the single owner of unversioned Gateway HTML pages:
`/login`, `/register`, `/admin`, and `/admin/plugins`. Moving the login and
registration page handlers out of the auth API package keeps the auth package's
shared `/api/v1/auth` prefix unambiguous. Public registration page visibility
continues to use `DSM_REGISTRATION_ENABLED`.

`proxy.py` remains responsible for the tenant HTTP/WebSocket catch-all. Neither
unversioned page handlers nor proxy handlers move into the API packages.

The existing `gateway/schemas` and `gateway/services` boundaries are preserved.
Moving them provides no benefit to the routing objective and would unnecessarily
expand this refactor.

## Application Composition

`gateway.application.create_app` includes only the seven top-level route
owners: auth, user, plugin, file, system, management, and proxy. The health
handler moves out of `application.py` into the system Router package.

The domain Router packages define their prefixes once. Full API path literals
must not be repeated in leaf handler modules. Proxy routers are included last so
the catch-all cannot hide API, documentation, management, or static routes.

## Packaged Client Updates

The management JavaScript assets update every fetch URL:

- user management uses `/api/v1/users`;
- plugin management uses `/api/v1/plugins/**`;
- login, registration, logout, and proxy widgets use `/api/v1/auth/**`;
- upload widgets use `/api/v1/files/**`.

No client-side compatibility resolver or central old-to-new mapping is added.
The source contains only the new paths after the refactor.

## Error and Response Contracts

Except for the intentional logout method change from POST to DELETE, status
codes, request models, response models, authentication requirements, Cookie
attributes, and error details remain unchanged. The path reorganization must
not expose internal user fields or plugin storage details.

The OAuth2 token response remains standard `access_token`, `token_type`, and
`expires_in`. The JSON session and registration responses retain their existing
camelCase fields.

## Testing

The implementation uses a contract-first migration:

1. Update the expected OpenAPI path table and security token URL so tests fail
   against the old routes.
2. Move handlers into domain packages and compose relative routers.
3. Update packaged browser assets and assertions.
4. Add an architecture test that rejects `/api/gateway/v1`, versioned API
   literals in leaf modules, and obsolete top-level route modules.
5. Verify route authorization, request/response models, static assets, and the
   tenant catch-all remain intact.
6. Run the full unit suite, Python compilation, JavaScript syntax checks,
   Compose rebuild, API smoke tests, and browser checks for login, management,
   Swagger OAuth2 authorization, and tenant proxy navigation.

Deployment preserves the SQLite data volume. User and plugin table counts are
recorded before and after the Compose rebuild.
