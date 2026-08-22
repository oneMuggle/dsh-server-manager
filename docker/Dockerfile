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
    && apt-get install --no-install-recommends -y \
      bubblewrap \
      iptables \
      passwd \
      python3 \
      python3-venv \
      tini \
      util-linux \
    && rm -rf /var/lib/apt/lists/*

COPY --from=builder /opt/deepseek-harness /opt/deepseek-harness
COPY requirements.txt /opt/dsh-lan-deployment/requirements.txt

RUN python3 -m venv /opt/venv \
    && /opt/venv/bin/pip install --no-cache-dir -r /opt/dsh-lan-deployment/requirements.txt \
    && npm install --global pnpm@11.7.0 \
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

COPY gateway /opt/dsh-lan-deployment/gateway
COPY supervisor /opt/dsh-lan-deployment/supervisor

RUN mkdir -p \
      /var/lib/dsh \
      /run/dsh \
    && chmod 755 /opt/dsh-lan-deployment \
    && find /opt/dsh-lan-deployment -type d -exec chmod 755 {} + \
    && find /opt/dsh-lan-deployment -type f -exec chmod 644 {} +

ENV PYTHONPATH=/opt/dsh-lan-deployment
WORKDIR /opt/dsh-lan-deployment

EXPOSE 8080

ENTRYPOINT ["/usr/bin/tini", "--", "/opt/venv/bin/python", "-m", "supervisor.main"]
