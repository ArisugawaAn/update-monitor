# update-monitor

Personal, non-commercial social-media update notifier. Periodically checks a
set of public accounts and websites (configured in `monitor/config.py`) and
sends one email per new item. Runs entirely on GitHub Actions (free tier).

## Sources

- Instagram (stories of the configured public accounts; checked every 10 min)
- Instagram posts/reels (private API via `instagrapi`, separate source from
  stories — needs its own session, see the secrets table below; checked every
  30 minutes, to stay well clear of Instagram's rate limits)
- X / Twitter (public RSS mirrors; main account every round, secondary
  accounts every 15 min)
- YouTube (official channel feeds; if a feed fails with 404/500/timeout the
  source falls back to YouTube Data API v3 uploads-playlist and notifies the
  same way — needs the `YOUTUBE_API_KEY` secret, skipped silently if unset)
- TikTok (public profile)
- Several official websites (HTML parsing; every 15 min)

## Setup — GitHub Secrets

| Secret | Purpose |
|---|---|
| `IG_COOKIE` | session cookie string for the Instagram source |
| `IG_POST_SESSION_JSON` | instagrapi `session.json` content for the Instagram posts/reels source |
| `SMTP_USER` | sender email account |
| `SMTP_PASS` | sender SMTP app-password |
| `NOTIFY_TO` | recipient email address |
| `YOUTUBE_API_KEY` | YouTube Data API v3 key for the feed fallback (optional) |

Optional: `NOTIFY_TITLE_PREFIX` — subject prefix for sources not explicitly
mapped in `monitor/config.py` (`NOTIFY_PREFIX` / `NOTIFY_PREFIX_BY_ACCOUNT`:
宮本浩次-related → 宮本浩次, Elephant Kashimashi (band site/FC, `@elekashi_ofcl`,
`@paonews_info`) → エレカシ, elephants-inc → elephants).

## Operations

- **Run once manually**: Actions → monitor → Run workflow
- **Verify the pipeline (health check)**: Actions → Run workflow → mode:
  `test-email` — probes every source and sends a test email; use it any time
  you want to confirm the email path works
- **Pause**: Actions → monitor → `···` → Disable workflow
- **Refresh Instagram session**: update the `IG_COOKIE` secret value
- **Change sources/accounts**: edit `monitor/config.py` and push
- **Change frequency**: edit the `cron` in `.github/workflows/monitor.yml`
  (5 minutes minimum — GitHub Actions scheduling limit; intentionally not faster)
- **Tests**: `python -m pytest tests/ -q` (mocked, no network)

Runtime state is stored in `monitor_state.json` and committed back to this
repository by CI (per-source seen/notified/baseline bookkeeping).

Reliability: sources depend on third-party public endpoints and may degrade
independently; failures are logged per source and trigger alert emails after
a consecutive-failure threshold. No guarantee of real-time delivery — the
target cadence is ~5 minutes, subject to GitHub Actions scheduling delays.

Cooldown (ban avoidance): the Instagram posts/reels source talks to Instagram's
private API, so repeated retries after a rate limit can deepen the account flag.
Mirroring the Story source's `checkpoint` handling, **only that source** cools
down on failure: no request at all for the next N rounds (rate-limit errors use
the longer N; a round is one scheduled run ≈5 minutes, so the default rate-limit
cooldown is ≈60 minutes, independent of the source's own interval), skips are
not counted as success/failure, one alert email is sent per incident (re-armed
after recovery), and an hourly sweep does not bypass it. Every other source
keeps its normal cadence. Tune it in `monitor/config.py` (`COOLDOWN_SOURCES`,
`COOLDOWN_TICKS`, `RATE_LIMIT_COOLDOWN_TICKS`, `RATE_LIMIT_MARKERS`); sources
not listed there behave exactly as before.

Silent-blindness guard (story source): Instagram answers an *unauthenticated*
request with a clean-but-empty JSON (`{"reels": {}, "status": "ok"}`) instead of
an error, while an authenticated response always carries a `reels_media` key.
The story source fails loudly (alert email within ~1 hour) when that marker is
missing, instead of silently reporting zero stories — the 2026-09-24/27 outage
(a dead cookie looking "healthy" for 3 days) is exactly what this prevents.

Quiet hours + cookie rotation (story source): `ig_story` sends no requests
between 02:00–07:00 JST (`QUIET_HOURS_JST` in `monitor/config.py`; skips don't
count as checks and polling resumes on the first tick after 07:00), and the
`IG_COOKIE` secret may hold **multiple** cookies (one per line, or separated by
`|||`) — every 10-minute window rotates the starting cookie so each account is
polled 1/N as often, and a rejected cookie fails over to the next one within
the same round. All cookies dead ⇒ loud alert + failure cooldown instead of
hammering Instagram.
