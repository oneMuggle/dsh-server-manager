# DSH Server Manager Renaming Design

## Identity

- Product name: **DSH Server Manager**
- Abbreviation: **DSM**
- Repository slug: `dsh-server-manager`
- Docker image: `dsh-server-manager:local`
- Compose project: `dsh-server-manager`
- Compose service: `dsm`
- Container installation root: `/opt/dsh-server-manager`
- API title: `DSM · DSH Server Manager`

The name emphasizes management, multi-user operation, and server deployment. The full name is used where discoverability matters; `DSM` is used for concise operational identifiers and log namespaces.

## Rename Scope

The rename covers README branding and clone instructions, Compose project/service/image names, the Docker installation directory, Supervisor child-process paths, API/OpenAPI titles, and Supervisor logger namespaces. Historical architecture documents are retitled so the repository does not present two active product names.

The current local checkout directory is controlled by the Codex workspace host and is not moved during the session. A future clone uses the repository slug `dsh-server-manager`.

## Compatibility Boundary

The following identifiers remain unchanged because they belong to persisted state, public APIs, or the underlying DeepSeek Harness integration rather than the old project name:

- `DSH_*` environment variables
- `/var/lib/dsh` and `docker/data/`
- `/run/dsh/control.sock`
- `dsh_session`
- `dsh-gateway`, `dsh-plugin-installer`, and per-user `dsh-*` Linux accounts
- `@dsh-managed` plugin scope
- `DSH_TENANTS` firewall chain
- HTTP paths, JSON fields, SQLite schema, and stored records
- Python packages `gateway`, `supervisor`, and `common`

Keeping these identifiers avoids data migration, session invalidation, plugin relinking, firewall leakage, and API breakage.

## Deployment Transition

Compose sees a new project and service name, so the old `dsh-lan-deployment` container must be stopped and removed immediately before starting `dsh-server-manager`. Both deployments use the same `docker/data/` bind mount, so the replacement container reads the existing SQLite database, session secret, users, and plugin releases without copying data.

The transition order is:

1. Record SQLite row counts from the running old container.
2. Build `dsh-server-manager:local`.
3. Stop and remove the old Compose project.
4. Start the new Compose project on port `3081`.
5. Verify login, OpenAPI branding, static assets, container paths, and unchanged row counts.

## Testing

Runtime tests assert the FastAPI/OpenAPI title and the Supervisor Gateway command installation root. Compose rendering and the Docker installation layout are verified using the real Compose CLI and built image. Existing tests continue to protect API, IPC, storage, authentication, tenant, upload, and plugin behavior.

## Non-goals

This change does not rename DeepSeek Harness itself, change user-facing DSH feature terminology, alter deployment topology, move persisted data, rename APIs, rename Python domains, or rename the host checkout directory while Codex is using it.
