"""
Export GitHub repository traffic data to append-only CSV files.

Reads GH_TOKEN and REPO from environment variables (set by the GitHub
Actions workflow). Appends new rows to CSV files in data/traffic/,
deduplicating by date (or date+key composite for referrers and paths).

Usage:
    GH_TOKEN=ghp_... REPO=owner/repo python export_traffic.py
"""

from __future__ import annotations

import csv
import os
import sys
from datetime import datetime, timezone
from pathlib import Path

DATA_DIR = Path(__file__).resolve().parent.parent.parent / "data" / "traffic"

TIMEOUT = 10  # seconds


def main() -> None:
    token = os.environ.get("GH_TOKEN", "").strip()
    repo = os.environ.get("REPO", "").strip()

    if not token:
        print("ERROR: GH_TOKEN environment variable is not set.", file=sys.stderr)
        sys.exit(1)
    if not repo:
        print("ERROR: REPO environment variable is not set.", file=sys.stderr)
        sys.exit(1)

    import httpx

    headers = {
        "Authorization": f"Bearer {token}",
        "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28",
    }

    DATA_DIR.mkdir(parents=True, exist_ok=True)

    base = f"https://api.github.com/repos/{repo}/traffic"

    # ── Clones ──────────────────────────────────────────────────────────
    clones_data = _fetch(httpx, f"{base}/clones?per=day", headers)
    _append_timeseries(
        DATA_DIR / "clones.csv",
        ["date", "count", "uniques"],
        clones_data.get("clones", []),
        key_field="timestamp",
    )

    # ── Views ───────────────────────────────────────────────────────────
    views_data = _fetch(httpx, f"{base}/views?per=day", headers)
    _append_timeseries(
        DATA_DIR / "views.csv",
        ["date", "count", "uniques"],
        views_data.get("views", []),
        key_field="timestamp",
    )

    # ── Referrers ───────────────────────────────────────────────────────
    referrers_data = _fetch(httpx, f"{base}/popular/referrers", headers)
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    _append_snapshot(
        DATA_DIR / "referrers.csv",
        ["date", "referrer", "count", "uniques"],
        [
            {"date": today, "referrer": r["referrer"], "count": r["count"], "uniques": r["uniques"]}
            for r in referrers_data
        ],
        dedup_keys=["date", "referrer"],
    )

    # ── Popular paths ───────────────────────────────────────────────────
    paths_data = _fetch(httpx, f"{base}/popular/paths", headers)
    _append_snapshot(
        DATA_DIR / "popular_paths.csv",
        ["date", "path", "title", "count", "uniques"],
        [
            {"date": today, "path": p["path"], "title": p.get("title", ""), "count": p["count"], "uniques": p["uniques"]}
            for p in paths_data
        ],
        dedup_keys=["date", "path"],
    )

    # ── Summary ─────────────────────────────────────────────────────────
    _generate_summary()

    print("Traffic export complete.")


def _fetch(httpx_mod, url: str, headers: dict) -> dict | list:
    """Fetch a GitHub API endpoint. Exit on non-200."""
    resp = httpx_mod.get(url, headers=headers, timeout=TIMEOUT)
    if resp.status_code != 200:
        print(f"ERROR: {url} returned {resp.status_code}", file=sys.stderr)
        print(resp.text, file=sys.stderr)
        sys.exit(1)
    return resp.json()


def _append_timeseries(
    path: Path,
    fieldnames: list[str],
    rows: list[dict],
    key_field: str,
) -> None:
    """Append timeseries rows, deduplicating by date."""
    existing_dates: set[str] = set()
    if path.exists():
        with open(path, newline="") as f:
            reader = csv.DictReader(f)
            for row in reader:
                existing_dates.add(row.get("date", ""))

    new_rows = []
    for row in rows:
        ts = row.get(key_field, "")
        date_str = ts[:10] if ts else ""
        if date_str and date_str not in existing_dates:
            new_rows.append({
                "date": date_str,
                "count": row.get("count", 0),
                "uniques": row.get("uniques", 0),
            })
            existing_dates.add(date_str)

    write_header = not path.exists() or path.stat().st_size == 0
    with open(path, "a", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        if write_header:
            writer.writeheader()
        writer.writerows(new_rows)


def _append_snapshot(
    path: Path,
    fieldnames: list[str],
    rows: list[dict],
    dedup_keys: list[str],
) -> None:
    """Append snapshot rows, deduplicating by composite key."""
    existing_keys: set[tuple] = set()
    if path.exists():
        with open(path, newline="") as f:
            reader = csv.DictReader(f)
            for row in reader:
                key = tuple(row.get(k, "") for k in dedup_keys)
                existing_keys.add(key)

    new_rows = []
    for row in rows:
        key = tuple(row.get(k, "") for k in dedup_keys)
        if key not in existing_keys:
            new_rows.append(row)
            existing_keys.add(key)

    write_header = not path.exists() or path.stat().st_size == 0
    with open(path, "a", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        if write_header:
            writer.writeheader()
        writer.writerows(new_rows)


def _generate_summary() -> None:
    """Regenerate data/traffic/summary.md with 30-day totals."""
    now = datetime.now(timezone.utc)
    cutoff = now.timestamp() - 30 * 86400
    lines: list[str] = ["# Traffic Summary\n"]

    # Clones
    total_clones, unique_cloners = _sum_last_30(DATA_DIR / "clones.csv", cutoff)
    lines.append(f"## Clones (last 30 days)\n")
    lines.append(f"- Total: **{total_clones}**\n")
    lines.append(f"- Unique: **{unique_cloners}**\n\n")

    # Views
    total_views, unique_viewers = _sum_last_30(DATA_DIR / "views.csv", cutoff)
    lines.append(f"## Views (last 30 days)\n")
    lines.append(f"- Total: **{total_views}**\n")
    lines.append(f"- Unique: **{unique_viewers}**\n\n")

    # Top referrers
    lines.append("## Top referrers (last 30 days)\n\n")
    referrers = _top_n(DATA_DIR / "referrers.csv", "referrer", cutoff, 5)
    if referrers:
        lines.append("| Referrer | Count | Uniques |\n|---|---|---|\n")
        for r in referrers:
            lines.append(f"| {r['key']} | {r['count']} | {r['uniques']} |\n")
    else:
        lines.append("_No data yet._\n")
    lines.append("\n")

    # Top paths
    lines.append("## Top paths (last 30 days)\n\n")
    paths = _top_n(DATA_DIR / "popular_paths.csv", "path", cutoff, 5)
    if paths:
        lines.append("| Path | Count | Uniques |\n|---|---|---|\n")
        for p in paths:
            lines.append(f"| {p['key']} | {p['count']} | {p['uniques']} |\n")
    else:
        lines.append("_No data yet._\n")
    lines.append("\n")

    lines.append(f"_Generated {now.strftime('%Y-%m-%d %H:%M:%S')} UTC_\n")

    (DATA_DIR / "summary.md").write_text("".join(lines))


def _sum_last_30(path: Path, cutoff_ts: float) -> tuple[int, int]:
    """Sum count and uniques for rows in the last 30 days."""
    total = 0
    uniques = 0
    if not path.exists():
        return total, uniques
    with open(path, newline="") as f:
        for row in csv.DictReader(f):
            try:
                dt = datetime.strptime(row["date"], "%Y-%m-%d").replace(tzinfo=timezone.utc)
                if dt.timestamp() >= cutoff_ts:
                    total += int(row.get("count", 0))
                    uniques += int(row.get("uniques", 0))
            except (ValueError, KeyError):
                continue
    return total, uniques


def _top_n(path: Path, key_col: str, cutoff_ts: float, n: int) -> list[dict]:
    """Return top N entries by count in the last 30 days."""
    agg: dict[str, dict] = {}
    if not path.exists():
        return []
    with open(path, newline="") as f:
        for row in csv.DictReader(f):
            try:
                dt = datetime.strptime(row["date"], "%Y-%m-%d").replace(tzinfo=timezone.utc)
                if dt.timestamp() < cutoff_ts:
                    continue
                key = row.get(key_col, "")
                if key not in agg:
                    agg[key] = {"key": key, "count": 0, "uniques": 0}
                agg[key]["count"] += int(row.get("count", 0))
                agg[key]["uniques"] += int(row.get("uniques", 0))
            except (ValueError, KeyError):
                continue
    return sorted(agg.values(), key=lambda x: x["count"], reverse=True)[:n]


if __name__ == "__main__":
    main()
