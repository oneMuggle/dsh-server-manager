ARG DSM_BUILD_DEBIAN_MIRROR=""
ARG DSM_BUILD_NPM_REGISTRY=""
ARG DSM_BUILD_PIP_INDEX=""

FROM node:24-bookworm AS builder

ARG DSM_BUILD_NPM_REGISTRY

ENV COREPACK_ENABLE_DOWNLOAD_PROMPT=0
WORKDIR /opt/deepseek-harness

RUN corepack enable

COPY deepseek-harness/ ./

RUN if [ -n "$DSM_BUILD_NPM_REGISTRY" ]; then \
      export COREPACK_NPM_REGISTRY="$DSM_BUILD_NPM_REGISTRY"; \
      export NPM_CONFIG_REGISTRY="$DSM_BUILD_NPM_REGISTRY"; \
    fi \
    && pnpm install --frozen-lockfile

ARG DSH_CLIENT_COMMIT_HASH=141eb6fef83422698aef7a981029e843e8161534
ENV DSH_CLIENT_COMMIT_HASH=${DSH_CLIENT_COMMIT_HASH}

RUN pnpm run build

FROM node:24-bookworm-slim AS runtime

ARG DSM_BUILD_DEBIAN_MIRROR
ARG DSM_BUILD_NPM_REGISTRY
ARG DSM_BUILD_PIP_INDEX

ENV DEBIAN_FRONTEND=noninteractive

COPY --from=builder /etc/ssl/certs/ca-certificates.crt /tmp/ca-certificates.crt

RUN set -eu; \
    mkdir -p /etc/ssl/certs; \
    cp /tmp/ca-certificates.crt /etc/ssl/certs/ca-certificates.crt; \
    case "$DSM_BUILD_DEBIAN_MIRROR" in ""|http://*|https://*) ;; *) echo "invalid DSM_BUILD_DEBIAN_MIRROR" >&2; exit 1;; esac; \
    sources=/etc/apt/sources.list.d/debian.sources; \
    if [ -n "$DSM_BUILD_DEBIAN_MIRROR" ]; then \
      sed -i "s|http://deb.debian.org/debian$|${DSM_BUILD_DEBIAN_MIRROR%/}|" "$sources"; \
      awk 'BEGIN { RS=""; ORS="\n\n" } !/bookworm-security/ { print }' "$sources" > "$sources.tmp"; \
      mv "$sources.tmp" "$sources"; \
    fi; \
    apt-get update \
    && apt-get install --no-install-recommends -y \
      bubblewrap \
      build-essential \
      ca-certificates \
      iptables \
      passwd \
      python3 \
      python3-venv \
      tini \
      util-linux \
    && rm -f /tmp/ca-certificates.crt \
    && rm -rf /var/lib/apt/lists/*

COPY --from=builder /opt/deepseek-harness /opt/deepseek-harness
COPY requirements.txt /opt/dsh-server-manager/requirements.txt

RUN python3 -m venv /opt/venv \
    && if [ -n "$DSM_BUILD_PIP_INDEX" ]; then \
      /opt/venv/bin/pip install --no-cache-dir --index-url "$DSM_BUILD_PIP_INDEX" -r /opt/dsh-server-manager/requirements.txt; \
    else \
      /opt/venv/bin/pip install --no-cache-dir -r /opt/dsh-server-manager/requirements.txt; \
    fi \
    && if [ -n "$DSM_BUILD_NPM_REGISTRY" ]; then \
      npm install --global --registry="$DSM_BUILD_NPM_REGISTRY" pnpm@11.7.0; \
    else \
      npm install --global pnpm@11.7.0; \
    fi \
    && groupadd --gid 19998 dsh-plugin-installer \
    && useradd \
      --uid 19998 \
      --gid dsh-plugin-installer \
      --home-dir /nonexistent \
      --no-create-home \
      --shell /usr/sbin/nologin \
      dsh-plugin-installer \
    && groupadd --gid 19999 dsh-gateway \
    && useradd \
      --uid 19999 \
      --gid dsh-gateway \
      --home-dir /nonexistent \
      --no-create-home \
      --shell /usr/sbin/nologin \
      dsh-gateway

COPY gateway /opt/dsh-server-manager/gateway
COPY supervisor /opt/dsh-server-manager/supervisor
COPY common /opt/dsh-server-manager/common

RUN mkdir -p \
      /var/lib/dsh \
      /run/dsh \
    && chmod 755 /opt/dsh-server-manager \
    && find /opt/dsh-server-manager -type d -exec chmod 755 {} + \
    && find /opt/dsh-server-manager -type f -exec chmod 644 {} +

ENV PYTHONPATH=/opt/dsh-server-manager
WORKDIR /opt/dsh-server-manager

EXPOSE 8080

ENTRYPOINT ["/usr/bin/tini", "--", "/opt/venv/bin/python", "-m", "supervisor.main"]
