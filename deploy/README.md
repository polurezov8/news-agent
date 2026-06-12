# Always-on hosting

The macOS `launchd` jobs (`news-agent schedule install`) only run while your Mac
is awake and unlocked — every restart or sleep means a missed digest, and
"real-time" priority DMs aren't real-time. This Docker setup runs the agent
24/7 on a host that doesn't sleep.

## Your secrets never leave a machine you trust

Hosting does **not** mean committing or uploading your `.env`. Keys stay out of
git (`.env` is git-ignored) and out of the image (`.dockerignore` excludes it).
At runtime they're injected as environment variables via `env_file: .env`.

Where the `.env` physically lives is your choice of host:

| Host | Where `.env` lives | Who can read it |
|---|---|---|
| **Box you own** (Raspberry Pi, mini-PC, always-on laptop) | a file on your hardware, `chmod 600` | only you — same as your Mac today |
| **VPS you control** | a file on that server | only you (shell access) |
| **Managed (Railway/Fly)** | the provider's encrypted secret store | the vendor's infra, encrypted at rest |

If you don't want any third party near your keys, run it on **hardware you own**.
The trust profile is identical to your Mac — it just doesn't sleep.

## Run it

```bash
cp .env.example .env      # fill in keys; set TZ to your timezone
docker compose up -d --build
```

Three services come up:

- **listener** — the Slack Socket Mode daemon (reactions → learning, the chat assistant)
- **cron** — supercronic firing the cadences in [`crontab`](crontab): daily 10:00, priority hourly, weekly Sunday 10:00, backup 09:00
- **rsshub** — only needed for Twitter/X lists; harmless otherwise

The SQLite DB and rotated backups live on the `agent-data` volume, so they
survive rebuilds and redeploys. Both processes share that one DB file safely —
SQLite WAL plus a busy-timeout serialises the cron writes against the listener,
and backups use SQLite's hot-backup API.

```bash
docker compose logs -f listener   # watch the daemon
docker compose logs -f cron       # watch scheduled runs
docker compose exec cron news-agent status   # counters + month-to-date spend
docker compose exec cron news-agent doctor    # health checks inside the container
docker compose down               # stop (volume + your data persist)
```

Changing a cadence is a one-line edit to [`crontab`](crontab) followed by
`docker compose up -d --build`.

## Deploying to a managed host

The image is host-agnostic. On Railway/Fly, point the service at this repo,
set the same env vars in the provider's secret store (not a committed file),
attach a persistent volume mounted at `/data`, and run two processes — one with
`news-agent slack`, one with `supercronic /app/deploy/crontab` — or a single
`listener` service if you'd rather trigger cadences with the provider's own
scheduler hitting `news-agent run --cadence <c>`.
