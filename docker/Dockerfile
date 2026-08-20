FROM node:24-bookworm AS builder

ENV COREPACK_ENABLE_DOWNLOAD_PROMPT=0
WORKDIR /opt/deepseek-harness

RUN corepack enable

COPY deepseek-harness/ ./

RUN pnpm install --frozen-lockfile

ARG DSH_CLIENT_COMMIT_HASH=141eb6fef83422698aef7a981029e843e8161534
ENV DSH_CLIENT_COMMIT_HASH=${DSH_CLIENT_COMMIT_HASH}

RUN pnpm run build

FROM node:24-bookworm-slim AS runtime

ENV DEBIAN_FRONTEND=noninteractive

RUN apt-get update \
    && apt-get install --no-install-recommends -y nginx tini \
    && rm -rf /var/lib/apt/lists/*

COPY --from=builder /opt/deepseek-harness /opt/deepseek-harness
COPY docker/nginx.conf /etc/nginx/nginx.conf
COPY docker/entrypoint.mjs /usr/local/lib/dsh-lan-entrypoint.mjs
COPY docker/compat.js /usr/local/share/dsh-lan/compat.js

RUN mkdir -p \
      /tmp/nginx/client-body \
      /tmp/nginx/proxy \
      /var/lib/dsh \
      /workspace \
    && nginx -t -c /etc/nginx/nginx.conf \
    && rm -f /tmp/nginx/nginx.pid \
    && chown -R node:node /tmp/nginx /var/lib/dsh /workspace

ENV DSH_HOME=/var/lib/dsh
WORKDIR /workspace
USER node

EXPOSE 8080

ENTRYPOINT ["/usr/bin/tini", "--", "/usr/local/bin/node", "/usr/local/lib/dsh-lan-entrypoint.mjs"]
