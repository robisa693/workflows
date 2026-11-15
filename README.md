# Discord Alert Workflows

A collection of scheduled GitHub Actions workflows that keep an eye on things
I find interesting and send an alert to Discord when something changes.

Each workflow is a small, dependency-free Python script plus a workflow file:

1. An external scheduler ([cron-job.org](https://cron-job.org)) starts the workflow
   through GitHub's API, or you run it manually from the Actions tab.
2. The script fetches its source and compares it with what it saw last time.
3. Anything new is posted to a Discord channel through a webhook.
4. The updated state is committed back to the repo as a small JSON file, so the
   next run knows what has already been reported.

The first run of each workflow only records a baseline and sends no alerts.

## Workflows

| Workflow | Watches | Schedule | Discord secret |
|---|---|---|---|
| [Plugin Hub installs](#plugin-hub-install-count-monitor) | Install counts of my RuneLite plugins | Fridays 17:30 (Stockholm) | `DISCORD_WEBHOOK_URL` |
| [SVT Valet 2026](#svt-valet-2026-live-blog-monitor) | SVT's live blog on Swedish politics | Every 30 minutes | `DISCORD_WEBHOOK_URL_SVT` |

## Setup

1. **Create a Discord webhook** for each channel you want alerts in:
   Channel settings → Integrations → Webhooks → New Webhook → copy the URL.
2. **Add it as a repository secret:** Settings → Secrets and variables →
   Actions → New repository secret, using the secret name from the table above.
   If a secret is missing, alerts are only printed to the workflow log.
3. **Run the workflow once manually** (Actions → pick the workflow → Run
   workflow) to record the baseline.
4. **Schedule it on cron-job.org.** The workflows have no GitHub `schedule`
   trigger, because GitHub often starts scheduled runs hours late or skips them.
   Instead, a cron-job.org job sends this request on the schedule in the table:
   - `POST https://api.github.com/repos/robisa693/workflows/actions/workflows/<workflow file>/dispatches`
   - Headers: `Authorization: Bearer <token>`, `Accept: application/vnd.github+json`,
     `X-GitHub-Api-Version: 2022-11-28`, `Content-Type: application/json`,
     `User-Agent: <any name>`
   - Body: `{"ref":"main"}`

   The token is a fine-grained GitHub token limited to this repo with only
   **Actions: Read and write**. GitHub answers `204` on success. When the token
   expires, create a new one and update the `Authorization` header in each job.

## Adding a new workflow

Copy an existing pair (`check_*.py` + `.github/workflows/check-*.yml`), give it
its own state file and its own `DISCORD_WEBHOOK_URL_*` secret, add a cron-job.org
job for it, and add a row to the table above. Workflows that commit state share the `state-commit`
concurrency group and run `git pull --rebase` before pushing, so they don't
collide.

---

## Plugin Hub install count monitor

**Files:** `check_installs.py`, `.github/workflows/check-installs.yml`, `installs_state.json`

Tracks the "active installs" count of every plugin by the author `robisa693` on
the RuneLite Plugin Hub and sends an alert when a count goes up.

- **Plugin discovery:** fetches the Plugin Hub manifest from `repo.runelite.net`
  and picks out all plugins by the configured author, so newly published
  plugins are tracked automatically.
- **Install counts:** come from the public RuneLite API,
  `https://api.runelite.net/pluginhub`.
- **Alerts:** one message per plugin whose count increased since the last run,
  e.g. `music-cape-helper install count increased: 26 → 31 (+5)`. Decreases are
  logged but not alerted.
- **Extra channel:** besides Discord, it can also push to
  [ntfy.sh](https://ntfy.sh) if the `NTFY_TOPIC` secret is set.
- **Failures:** if a plugin is missing from the API response, the run logs an
  error and fails, but the other plugins are still checked.

## SVT Valet 2026 live blog monitor

**Files:** `check_svt_val.py`, `.github/workflows/check-svt-val.yml`, `svt_val_state.json`

Watches SVT's live blog
[Valet 2026 – senaste nytt om svensk politik](https://www.svt.se/nyheter/inrikes/senaste-nytt-om-val-2026)
and posts every new update to Discord as an embed with the headline, text,
author, time and a link to the page.

- **Source:** posts are read from the data SVT embeds in the page
  (`window.__URQL_DATA__`), which holds the ~10 most recent posts. If SVT posts
  more than ~10 updates between two checks, the oldest ones may be missed.
- **State:** IDs of posts already sent are stored in `svt_val_state.json`.
  Edits to an existing post don't trigger a new alert.
- **Test the Discord send:** Actions → "Check SVT Valet 2026 Live Blog" → Run
  workflow → tick **test_send**. This posts the newest live-blog post without
  changing the state, and the run fails if Discord rejects the message.
- **Layout changes:** if SVT changes how the page is built, the run fails
  instead of silently sending nothing.
