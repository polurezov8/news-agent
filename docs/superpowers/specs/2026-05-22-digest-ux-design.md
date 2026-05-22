# Digest UX — Apple-style redesign

**Status:** approved — design ready for implementation plan
**Date:** 2026-05-22
**Scope:** Slack delivery surface for daily digest, priority DM, and weekly recap
**Out of scope:** scoring pipeline, source onboarding, web UI

## Goal

Replace the busy, chrome-heavy Slack digest with a minimalist, functional, stylish presentation that defers to content. Reduce visual noise so the picks themselves carry the message. Make sure every affordance actually works — no façade reactions or buttons that lead nowhere.

## Design principles

1. **Position is hierarchy.** First item under "Editor's Pick" is the strongest. No hero card, border, or color is needed.
2. **Defer to content.** Strip score bars, score numbers, tag chips, and four-button action rows. They are introspection, not content.
3. **Typography does the work.** Bold headline + italic meta line. Small-caps labels for sections. One emoji glyph per label as quiet ornament.
4. **One primary action.** Clicking the title opens the article. No accessory buttons.
5. **Reactions are the only feedback channel.** Wired end-to-end. No chrome unless real.
6. **Per-cadence honesty.** Daily footer shows this run only. Weekly footer shows the week. No cross-pointers.
7. **Silent on empty.** If nothing passes the bar, post nothing.

## Daily digest

### Layout

```
Friday, May 22

⭐️  EDITOR'S PICK

Scaling Laws for Neural Language Models
arxiv · AI · this morning

✨  ALSO TODAY

SwiftUI для справжніх macOS-застосунків
iOSDevsUA · iOS · this morning

How Shopify ships big features in small slices
DOU · Engineering Leadership · 4h ago

1531 scanned · 9 on topic · 3 picks
```

### Block Kit translation

- 1 `header` block — date as headline, no emoji prefix.
- 1 `context` block — `*⭐️  EDITOR'S PICK*`.
- 1 `section` block — `*<url|Title>*\n_source · Topic Label · relative time_`. No `accessory`.
- 1 `context` block — `*✨  ALSO TODAY*` (omitted if only 1 pick).
- N `section` blocks — same shape as the editor's pick section.
- 1 `context` block — compact footer counters.

Total: ~7–9 blocks. Well under the 50-block chunking limit.

### Counters footer (compact form)

Three fields from `PipelineCounters`, no emojis, no labels-for-labels:

- `scanned` ← `fetched`
- `on topic` ← `on_topic`
- `picks` ← `surfaced`

Format: `1531 scanned · 9 on topic · 3 picks`.

`_counters_summary` is rewritten to emit this string. Old six-field format is removed.

### Item count and ordering

- **Hard cap: 3 total** per digest. Cap is absolute — guarantees may displace quality picks but never push the total above 3. One editor's pick + up to two also-today.
- **Selection order:**
  1. Trusted-source guarantees (`min_in_digest`) fill first, sorted by `published_at` desc, up to the cap. If guarantees alone exceed 3, keep the 3 newest.
  2. Remaining slots filled by top `final` score across all eligible (passed `min_score`) items, regardless of topic.
- **Editor's pick:** the single item with the highest `final` score among the 3 chosen, regardless of which path put it there.
- **Order within "Also today":** by `final` desc.

### Relative time

Helper renders `published_at` against `datetime.now(utc)` using elapsed time only — no calendar-segment heuristics. Rules apply in order; the first match wins:

| Elapsed   | Render              |
| --------- | ------------------- |
| < 1 hr    | `just now`          |
| 1–12 hr   | `Nh ago`            |
| 12–36 hr  | `yesterday`         |
| 36 hr–7 d | `Nd ago` (floor)    |
| ≥ 7 d     | `MMM d` (`May 14`)  |

Helper lives in `src/news_agent/notifier/_time.py`. Pure function, no I/O.

### Schedule

10:00 local, daily. Unchanged from v1.1.1.

### Empty state

If `digest_items == []` after routing, post nothing. No status message, no "agent ran" pulse. The next digest at 10:00 will demonstrate the agent is alive.

Trade-off accepted: on a fully silent day, the user won't know whether the pipeline failed or whether there was simply nothing to surface. Mitigation: `news-agent status` shows last-run timestamp and `news-agent doctor` runs health checks on demand.

## Priority DM

Real-time, separate from the digest. Unchanged trigger logic (score ≥ topic.priority_threshold within priority_recency_hours).

### Layout

```
🔔  PRIORITY

Catastrophic Forgetting in Continual Learning
arxiv · AI · 12 min ago
```

Same item shape as the digest. One context block for the `🔔  PRIORITY` label, one section block for the item. No accessory button. No score bar.

## Weekly recap

Sunday 10:00 local. Aggregates the week's activity.

### Layout

```
Sun, May 24 — week recap

⭐️  TOP THIS WEEK

[3 top items by final score, same item shape]

💭  HIGH-SCORE BUT SKIPPED

[up to 3 items you skipped]

7 runs · 42 surfaced · 14 saved · 5 demoted · top sources: pointfree, dou, arxiv
```

### Block Kit translation

- `header` block: `Sun, May 24 — week recap`
- `context` block: `*⭐️  TOP THIS WEEK*`
- 3 `section` blocks for top items
- `divider`
- `context` block: `*💭  HIGH-SCORE BUT SKIPPED*` (omitted if empty)
- Up to 3 `section` blocks for skipped items
- `context` block: weekly stats footer

### Weekly footer fields

- `runs` — number of daily digest runs in the 7-day window
- `surfaced` — total items surfaced across those runs
- `saved` — count of `SAVE` corrections in the window
- `demoted` — count of `DEMOTE` corrections in the window
- `top sources` — top 3 source ids by surfaced count within the 7-day window

All values come from existing tables (`articles`, `surfaces`, `corrections`). New aggregation queries land in `repository.py`.

## Reactions wiring (path A)

The current state has reaction handlers registered but stubbed: they hardcode `article=ArticleId("unknown")` and the listener daemon doesn't run as a service. Wire them properly.

### Listener service

Add a fourth launchd plist to `schedule.py`:

- Label: `com.polurezov.news-agent.listener`
- ProgramArguments: `news-agent slack`
- `RunAtLoad: true`
- `KeepAlive: true` (restart on crash)
- No `StartInterval` or `StartCalendarInterval` — runs continuously
- Logs to `~/Library/Logs/news-agent/listener.{log,err}`

`schedule install` writes and loads it alongside the cron plists. `schedule restart` and `schedule uninstall` cover it.

### Reaction handler resolution

In `listener.py::handle_reaction`, replace the three `"unknown"` literals with a real lookup:

1. From the event, extract `channel` and `ts` (already done).
2. Open a DB connection.
3. Call `find_surface_target(conn, channel, ts)` (already exists in `repository.py`) to get `(article_id, topic_id, source_id)`.
4. If lookup fails, log and drop the event.
5. Build `CorrectionEvent` with real IDs.
6. Hand to `on_correction` callback (already wired to `upsert_source_prior`).

### Reaction vocabulary

Documented in README and discoverable by reacting to the agent's first message. Pick four:

- `👍` → BOOST (source_priors += learning_rate)
- `🔖` → SAVE (records to `corrections` for weekly recap)
- `💤` → SKIP (records to `corrections`; no prior change)
- `❌` → DEMOTE (source_priors −= learning_rate)

`_REACTION_MAP` already covers these. No code change beyond the handler resolution above.

## Configuration that disappears

The following config knobs are no longer used by the daily digest renderer and become dead in `topics.yaml::delivery`:

- `digest_top_n` — overridden by hard global cap of 3
- `digest_min_score` — still gates routing; unchanged

`digest_top_n` is left in the schema for backwards-compat but the daily renderer ignores it. Priority and weekly use their own thresholds.

## Implementation surface

Files touched:

- `src/news_agent/notifier/slack.py` — rewrite `digest_blocks`, `priority_blocks`, `recap_blocks`, `_counters_summary`. Add `_topic_label_for`, `_relative_time`. Remove `_article_blocks` from digest path (still used by priority and recap, restyled).
- `src/news_agent/notifier/_time.py` — new file: `relative_time(published_at, now)`.
- `src/news_agent/notifier/listener.py` — replace `"unknown"` literals with `find_surface_target` lookup.
- `src/news_agent/pipeline/graph.py` — global top-3 cap in `route()`. Skip notify when `digest_items == [] and priority_items == []`.
- `src/news_agent/pipeline/recap.py` — extend with weekly footer aggregations (runs, saved, demoted, top sources).
- `src/news_agent/cli/schedule.py` — add `Cadence` extension or a separate constant for the listener plist; include in install/uninstall/restart.
- `tests/unit/test_slack_notifier.py` — update assertions for the new shape.
- `tests/unit/test_schedule_plist.py` — add coverage for the listener plist.
- `README.md` — rewrite Delivery section to show the new format and document the reaction vocabulary.

## Success criteria

- `news-agent run --cadence daily` posts a digest matching the Layout above, with real `PipelineCounters` in the footer.
- A real reaction on a real digest item updates `source_priors` for the correct `(source, topic)` row.
- `news-agent schedule install` writes 4 plists (daily, priority, weekly, listener), all load without error.
- Tests pass: existing 147 + new ones for relative time, listener plist, reaction-handler resolution. Target: ≥155 passing.
- Cost cap unchanged ($5/mo) — the rewrite is rendering-only.

## Version bump

`1.1.1 → 1.2.0`. New feature surface (Apple-style delivery), no breaking API; minor by SemVer.
