## What this PR does

Adds a nightly GitHub Actions workflow that exports repository traffic data
(clones, views, referrers, popular paths) to append-only CSV files before the
14-day GitHub API retention window expires. A summary Markdown file is
regenerated on each run showing 30-day totals and top referrers.

## Files changed

Infrastructure:
- `.github/workflows/traffic-metrics.yml` — cron workflow (02:00 UTC nightly)
- `.github/scripts/export_traffic.py` — API export and CSV deduplication logic

Data:
- `data/traffic/.gitkeep` — seeds the data directory

## Setup required before this PR is useful

Before merging, add the `TRAFFIC_TOKEN` secret to the repository:

1. Generate a GitHub PAT with `repo` scope at https://github.com/settings/tokens
2. Add it as a repository secret named `TRAFFIC_TOKEN` at
   https://github.com/lakshgk/distill/settings/secrets/actions

## How to verify

After merging, trigger the workflow manually:
Actions -> Traffic metrics export -> Run workflow

Then confirm:
```bash
cat data/traffic/clones.csv       # rows with date, count, uniques
cat data/traffic/summary.md       # 30-day totals
```

## Merge order

Independent of all other PRs. Can merge any time after `feat/docs`.
No code dependencies — only the `TRAFFIC_TOKEN` secret must be set first.
