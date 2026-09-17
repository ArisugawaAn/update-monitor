# update-monitor

Personal, non-commercial social-media update notifier. Periodically checks a
set of public accounts and websites (configured in `monitor/config.py`) and
sends one email per new item. Runs entirely on GitHub Actions (free tier).

## Sources

- Instagram (stories of the configured public accounts)
- Instagram posts/reels (private API via `instagrapi`, separate source from
  stories — needs its own session, see the secrets table below; checked every
  10 minutes like YouTube, to stay well clear of Instagram's rate limits)
- X / Twitter (public RSS mirrors)
- YouTube (official channel feeds)
- TikTok (public profile)
- Several official websites (HTML parsing)

## Setup — GitHub Secrets

| Secret | Purpose |
|---|---|
| `IG_COOKIE` | session cookie string for the Instagram source |
| `IG_POST_SESSION_JSON` | instagrapi `session.json` content for the Instagram posts/reels source |
| `SMTP_USER` | sender email account |
| `SMTP_PASS` | sender SMTP app-password |
| `NOTIFY_TO` | recipient email address |

Optional: `NOTIFY_TITLE_PREFIX` — prefix used in notification email subjects.

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
