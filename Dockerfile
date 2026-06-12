# Always-on container for the news agent.
#
# One image, two roles (see docker-compose.yml):
#   - listener: long-running Slack Socket Mode daemon (`news-agent slack`)
#   - cron:     supercronic running the daily/priority/weekly/backup cadences
#
# Secrets are NEVER baked in. They arrive at runtime via `env_file: .env`
# (.env is git-ignored and excluded by .dockerignore). The image is safe to
# build and share; it carries no keys.

FROM python:3.11-slim

# uv for reproducible, lockfile-pinned installs (build-time only).
COPY --from=ghcr.io/astral-sh/uv:latest /uv /uvx /bin/

# supercronic: a cron built for containers — runs unprivileged, logs to stdout,
# honours the TZ env var. Multi-arch so the same Dockerfile builds on an
# x86 VPS or an arm64 Raspberry Pi / Apple-silicon host.
ARG TARGETARCH
ARG SUPERCRONIC_VERSION=v0.2.33
ADD https://github.com/aptible/supercronic/releases/download/${SUPERCRONIC_VERSION}/supercronic-linux-${TARGETARCH} /usr/local/bin/supercronic
RUN chmod +x /usr/local/bin/supercronic

# tzdata so cron fires at your local 10:00, not UTC; ca-certificates for HTTPS.
RUN apt-get update \
    && apt-get install -y --no-install-recommends tzdata ca-certificates \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# Dependency layer first so edits to src/ don't bust the cached install.
COPY pyproject.toml uv.lock ./
COPY src ./src
RUN uv sync --frozen --no-dev

COPY config ./config
COPY deploy ./deploy

# Run via the resolved venv directly — no uv at runtime, no network resolve.
ENV PATH="/app/.venv/bin:$PATH" \
    NEWS_AGENT_DB=/data/news_agent.db \
    NEWS_AGENT_BACKUP_DIR=/data/backups \
    PYTHONUNBUFFERED=1

# SQLite DB + rotated backups live on a volume so they survive redeploys.
VOLUME ["/data"]

# Default role is the always-on listener; the cron service overrides this.
CMD ["news-agent", "slack"]
