# User Management, Registration Control, and Swagger Authentication Design

## Goal

Extend DSM's administrator interface with user creation, lookup, password reset,
and enable/disable operations; control public registration with a DSM-owned
environment variable; and let Swagger UI obtain a Bearer token from a username
and password. Preserve the single-container architecture, SQLite storage,
Cookie authentication, direct Bearer authentication, tenant isolation, and port
`3081`.

User deletion is explicitly out of scope. A documented DELETE endpoint will be
registered but must always return `501 Not Implemented` without changing a
process, operating-system account, database row, plugin assignment, or file.

## Environment Variable Ownership

DSM-owned variables use the `DSM_` prefix:

- `DSM_ADMIN_PASSWORD`
- `DSM_STATE_ROOT`
- `DSM_CONTROL_SOCKET`
- `DSM_COOKIE_SECURE`
- `DSM_PLUGIN_NPM_REGISTRY`
- `DSM_PLUGIN_NPM_TOKEN`
- `DSM_LOG_LEVEL`
- `DSM_REGISTRATION_ENABLED`

There are no aliases for the former `DSH_` names. This project is still in
development, so invalid or obsolete names should fail visibly instead of
creating two supported configuration surfaces.

`DSM_REGISTRATION_ENABLED` and `DSM_COOKIE_SECURE` accept only case-insensitive
`true` or `false` and default to `false`. An invalid value stops startup with an
error that names the variable. `DSM_LOG_LEVEL` defaults to `INFO`.

The upstream variables `DSH_HOME` and `DSH_CLIENT_COMMIT_HASH` retain their
names. Standard process variables such as `HOME`, `PATH`, and `PYTHONPATH` are
unchanged. The untracked local `.env` is renamed in place without printing or
committing its current password value. `.env.example` and README document only
the DSM names.

## Public Registration

Gateway runtime configuration owns `registration_enabled`.

When registration is disabled:

- the login page does not render the registration link;
- `GET /register` returns 404;
- `POST /api/gateway/v1/auth/register` returns 403 with `注册功能已关闭`;
- the admin user-creation API remains available.

When enabled, the existing validation, rate limiting, Supervisor creation,
Cookie creation, and response contract remain unchanged. The registration API
remains in OpenAPI in both states so disabled installations have a stable
contract and an explicit 403 response.

## Administrator User API

All endpoints require the existing administrator Cookie or Bearer token.

### Read

- `GET /api/gateway/v1/admin/users` retains the current list response.
- `GET /api/gateway/v1/admin/users/{username}` returns the same public user
  representation: `username`, `role`, `disabled`, and `created_at`.
- Internal UID, operating-system username, port, password record, and password
  version are never returned.

### Create

`POST /api/gateway/v1/admin/users` accepts:

```json
{"username": "alice", "password": "change-me-123"}
```

It returns the public user representation with status 201. Username and
password constraints are identical to public registration. New users are
ordinary users and start enabled. This operation reuses the existing
Supervisor `create_user` control action and is not affected by the registration
flag.

### Update

`PATCH /api/gateway/v1/admin/users/{username}` accepts at least one of:

```json
{"password": "new-password-123", "disabled": true}
```

Username and role are immutable. Password reset increments `password_version`,
invalidating every existing Cookie and Bearer token for that user. Disabling
the fixed `admin` account is rejected with 409; resetting its password is
allowed. Repeating the current enabled/disabled state is idempotent.

### Delete Placeholder

`DELETE /api/gateway/v1/admin/users/{username}` authenticates the administrator
and always returns status 501 with `用户删除暂未实现`. It performs no lookup and no
mutation. The management UI does not display a delete control.

### Error Mapping

- 404: target user does not exist.
- 409: duplicate username or attempt to disable `admin`.
- 422: invalid username, password, or empty update.
- 501: deletion placeholder.
- 503: Supervisor or persistent user store unavailable.

Error details must not contain password values or password hashes.

## Supervisor Ownership and State Transitions

Gateway validates HTTP models, hashes passwords, and sends typed control
messages. Supervisor is the only component that coordinates the user database,
tenant processes, operating-system accounts, and firewall.

The control contract gains `UpdateUserRequest` with optional password record and
disabled state. Supervisor performs it under the existing `registry_lock`.

Password updates validate the scrypt record, replace it, increment
`password_version`, and atomically persist the registry.

`TenantManager` gains a focused `stop_user(username)` operation that cancels and
awaits one tenant task, terminates its process group through the existing
cancellation path, and removes its task/readiness state so the same user can be
started again.

For disable, Supervisor stops the tenant before persisting `disabled=true`, then
applies the candidate firewall rules. For enable, it applies the candidate
firewall rules and persists `disabled=false` before starting the tenant. A
firewall failure retains the existing fatal-container behavior so DSM never
continues with uncertain tenant isolation. Re-enabled users receive the same
plugin reconciliation used during startup.

## Swagger OAuth2 Password Authorization

The OpenAPI Bearer scheme becomes OAuth2 Password Flow with token URL
`/api/gateway/v1/auth/token`. Swagger's Authorize dialog therefore asks for a
username and password, exchanges them once, and automatically sends the
returned Bearer token.

The token endpoint consumes the standard form fields `username` and `password`
and returns:

```json
{"access_token": "...", "token_type": "bearer", "expires_in": 604800}
```

It shares credential verification and login rate limits with the JSON login
endpoint, but does not set a Cookie. The existing JSON `/auth/login` endpoint,
HttpOnly Cookie flow, and direct `Authorization: Bearer <token>` clients remain
supported. `python-multipart` is added as the single dependency needed for
FastAPI's standard OAuth2 password form parser.

## Management Interface

The existing `/admin` page remains framework-free and uses packaged HTML, CSS,
and JavaScript under `/gateway-assets`.

- A create-user form accepts username and initial password.
- Each non-admin row exposes enable/disable and reset-password controls.
- The admin row exposes reset password but no disable control.
- A native dialog collects a replacement password and confirms state changes.
- Successful operations reload the list and show a concise status message.
- Failed operations show the server detail without inserting untrusted HTML.
- No delete button is rendered.

## Testing and Deployment

Tests use red-green-refactor and cover:

- strict DSM variable parsing and absence of obsolete active names;
- registration link rendering plus disabled GET/POST behavior;
- typed update control-message parsing and round trips;
- password-version invalidation;
- tenant stop/re-enable lifecycle;
- admin-disable rejection and idempotent state updates;
- list/detail/create/update/delete-placeholder HTTP contracts;
- management-page controls and field names;
- OAuth2 OpenAPI security scheme and standard token response;
- preservation of direct Bearer and Cookie authentication.

After the full unit suite and compile checks pass, Compose is rebuilt in place.
Verification records SQLite table counts before and after, exercises registration
disabled behavior, and verifies read-only authenticated endpoints against the
existing administrator. Create, password-reset, enable/disable, and DELETE
behavior are exercised against isolated temporary databases and fake tenant
processes rather than mutating deployed users. Browser validation checks the
admin UI and Swagger Authorize dialog. Existing administrator and plugin data
must remain unchanged.
