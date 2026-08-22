# Supervisor Code Structure Design

## Objective

Reorganize `supervisor/` so that the executable entry point, application lifecycle, privileged tenant operations, control transport, and shared plugin runtime each have one clear owner. This is an internal refactor with no externally observable behavior changes.

## Compatibility Constraints

- Keep `python -m supervisor.main` as the container entry point.
- Preserve every control-socket action, JSON field, response, error mapping, peer-credential check, socket path, and permission.
- Preserve all HTTP behavior exposed through Gateway, the SQLite schema and data, filesystem paths, Linux account names, environment variables, and port `3081`.
- Keep Gateway and Supervisor in one container and keep Supervisor as the only privileged writer.
- Keep `common` dependency-neutral and prevent Supervisor from importing Gateway.
- Do not add a dependency-injection framework, service container, subprocess abstraction framework, or compatibility shim for obsolete internal module paths.

## Selected Structure

```text
supervisor/
├── main.py                 # root check, signal wait, process exit code
├── application.py          # Supervisor composition and lifecycle
├── config.py               # immutable paths, accounts, limits and ports
├── state.py                # persistent/runtime directory initialization
├── gateway_process.py      # child Gateway start and termination
├── control/
│   ├── __init__.py
│   └── server.py           # Unix socket authentication, framing, dispatch
├── tenants/
│   ├── __init__.py
│   ├── accounts.py         # account records and OS provisioning
│   ├── manager.py          # tenant process lifecycle and readiness
│   ├── uploads.py          # safe workspace traversal and atomic file writes
│   └── firewall.py         # tenant loopback isolation
└── plugins/
    ├── __init__.py
    ├── manager.py          # public facade, queue and job coordination
    ├── packages.py         # registry/offline import and shared releases
    ├── commands.py         # bounded privileged subprocess execution
    ├── assignments.py      # assignment execution and rollback
    ├── profile.py
    ├── installer.py
    └── registry.py
```

The domain-oriented layout was selected over a flat split because the flat directory does not communicate ownership, and over generic `services/` and `runtime/` layers because those layers would group unrelated behavior and add indirection.

## Responsibilities and Data Flow

`SupervisorConfig.from_environment()` resolves all deployment paths once. `Supervisor` receives that immutable configuration, constructs the tenant manager, plugin manager, and control server, then owns startup and shutdown ordering.

`main.py` performs the root check, installs signal handlers, starts `Supervisor`, and converts a Gateway exit, fatal control error, firewall error, or startup error into the existing process exit code. It contains no storage, user, upload, plugin, or subprocess implementation.

Control requests retain the existing typed models. `ControlServer` authenticates the Unix peer, parses one newline-delimited JSON request, calls the public methods on its owner, and maps exceptions exactly as before.

Tenant account creation remains coordinated: validate the request, provision the OS user, calculate firewall rules, persist the SQLite record, start the tenant, apply plugin defaults, and reconcile the profile. Upload operations continue to validate the stored user before performing descriptor-relative filesystem work in a worker thread.

The plugin manager remains the public API used by `Supervisor`. It owns queue lifecycle and job status transitions. A package repository owns registry discovery, pnpm staging, offline artifacts, immutable releases, and resolver links. Assignment execution and profile rollback remain isolated from package import.

## Error Handling

Domain validation continues to raise `ValueError` or `PluginOperationError`. Duplicate usernames raise `FileExistsError`. Firewall failures remain fatal and are logged at the control/application boundary. Unexpected control failures remain private and return the existing generic error response. Subprocess output remains bounded and timeouts continue to terminate the entire process group.

## Testing

- Architecture tests enforce dependency direction and the thin entry point.
- Existing contract tests protect IPC wire behavior and peer authentication.
- Existing user, tenant, upload, and plugin tests move to the new domain paths.
- New focused tests exercise configuration derivation and plugin collaborator boundaries.
- The full Python suite, bytecode compilation, image build, Compose deployment, login, static assets, and SQLite row counts are verified before completion.

## Non-goals

No API, database, authentication, UI, plugin feature, upload behavior, tenant process model, deployment topology, or runtime dependency changes are included.
