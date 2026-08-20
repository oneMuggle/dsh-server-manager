# DSH LAN Deployment Design

## Goal

Build a minimal, single-container Docker deployment that exposes an unmodified official DeepSeek Harness checkout at `http://<LAN-IP>:3080` while keeping the Harness HTTP server bound to its default loopback address.

## Scope

The first phase supports one trusted user on a trusted local network. It includes source-based image construction, an Nginx loopback reverse proxy, persistent Harness state, a host-visible workspace, process supervision, and a LAN smoke test.

The first phase does not include authentication, TLS, multi-user isolation, a management portal, public-internet hardening, HTML or JavaScript response injection, plugin installation, or changes to DeepSeek Harness source code.

## Upstream Source

The deployment repository is an independent Git repository at `dsh-lan-deployment/`. The official `https://github.com/deepseek-ai/deepseek-harness.git` repository is checked out inside it as the `deepseek-harness/` Git submodule and pinned initially to official commit `141eb6fef83422698aef7a981029e843e8161534` (`dsh-v0.1.0-rc.8`).

The existing sibling checkout `/home/haruto/Documents/Code/dsh/deepseek-harness` is not copied, referenced, or used as a Docker build input.

## Container Architecture

One container runs two long-lived processes under `tini`:

1. DeepSeek Harness listens on its unchanged default `127.0.0.1:3080` address.
2. Nginx listens on `0.0.0.0:8080` and proxies to Harness over the same container loopback interface.

Docker Compose publishes host port `3080` to container port `8080`. No Harness port is published directly.

The entrypoint starts both processes, forwards termination to both, and exits when either process exits so Docker can report and restart a failed container instead of leaving a partial deployment running.

## Source Build and Runtime

The Docker build uses the checked-out `deepseek-harness/` submodule as its only Harness source. It activates the repository-declared pnpm version, runs `pnpm install --frozen-lockfile`, and runs `pnpm run build` inside the checkout. It does not install a published `@deepseek-ai/*` Harness package from npm.

The runtime image retains `/opt/deepseek-harness` with the official source, workspace dependencies, and generated build artifacts. It launches `/opt/deepseek-harness/apps/cli/lib/bin.js`, which is the built CLI produced from that checkout. The process current directory is `/workspace`, preserving the official rule that the invoking directory is the default workspace rather than making the Harness source tree the agent workspace.

The only Web application argument is `--no-open`, which prevents a headless container from attempting to open a desktop browser. The deployment does not pass `--host`, `--port`, or `--trusted-host`.

## Proxy Behavior

Nginx forwards all paths to `http://127.0.0.1:3080` using HTTP/1.1. It applies only the changes required for this loopback facade:

- replace `Host` with `127.0.0.1:3080`;
- remove the browser `Origin` header so it cannot conflict with the rewritten Host;
- forward WebSocket `Upgrade` and `Connection` headers;
- disable proxy buffering for streaming responses;
- keep long read and send timeouts for agent responses and WebSocket connections.

The proxy does not inject scripts, alter response bodies, implement authentication, or add application routes.

## Data and Configuration

Compose bind-mounts repository-local `data/` at `/var/lib/dsh` and `workspace/` at `/workspace`. `DSH_HOME` is `/var/lib/dsh`, so profiles, settings, credentials, sessions, and other Harness state survive container replacement. Agent-created workspace files remain directly visible under the host `workspace/` directory.

Compose only passes through configuration already owned by DeepSeek Harness, beginning with optional `DEEPSEEK_API_KEY`. The deployment introduces no custom application configuration schema.

## Expected Remote-Browser Behavior

The browser address remains the server's LAN IP, so official client code identifies the page as non-loopback even though Nginx presents requests to the server with a loopback Host. Core page loading, session transport, WebSocket downlinks, and conversation operations are the first-phase acceptance target.

Official client features that intentionally require a loopback page, including parts of durable settings, credential management, and native host actions, may remain unavailable. The first phase does not rewrite official frontend assets to bypass that client-side policy.

## Failure Handling

- A missing submodule fails the Docker build with a direct source-path error.
- A dependency or Harness build failure fails the image build.
- An invalid Nginx configuration fails during image verification.
- If Harness or Nginx exits at runtime, the entrypoint terminates the other process and exits nonzero.
- Requests arriving before Harness is ready may receive a temporary `502`; Compose restart behavior and the smoke test distinguish startup delay from a persistent failure.

## Verification

The implementation is accepted when all of the following are demonstrated from fresh commands:

1. The submodule remote and pinned commit are the official upstream values.
2. Entrypoint behavior tests prove that failure or termination of either child cleans up the other child.
3. Nginx configuration validation succeeds.
4. `docker compose config` succeeds.
5. The Docker image builds from the checked-out Harness source.
6. The container starts and serves the Web UI through `http://127.0.0.1:3080` on the host.
7. A request sent to the host's actual LAN IPv4 address on port `3080` returns the Web UI.
8. The container logs show Harness listening only on loopback and do not show an attempted browser launch.

## Security Boundary

Rewriting requests as loopback deliberately exposes powerful Harness operations to every client that can reach published port `3080`. This phase is suitable only for a trusted LAN. Public deployment requires a later design adding authentication and TLS before the port is exposed to the internet.
