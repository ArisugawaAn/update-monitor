# update-monitor

Personal, non-commercial social-media update notifier. Periodically checks a
set of public accounts and websites (configured in `monitor/config.py`) and
sends one email per new item. Runs entirely on GitHub Actions (free tier).

## Sources

- Instagram (stories of the configured public accounts)
- X / Twitter (public RSS mirrors)
- YouTube (official channel feeds)
- TikTok (public profile)
- Several official websites (HTML parsing)

## Setup — GitHub Secrets

| Secret | Purpose |
|---|---|
| `IG_COOKIE` | session cookie string for the Instagram source |
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
