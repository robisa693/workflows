#!/usr/bin/env python3
"""Monitor SVT's "Valet 2026" live blog and send Discord alerts for new posts."""

import html
import json
import logging
import os
import re
import sys
import time
import urllib.error
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
log = logging.getLogger(__name__)

PAGE_URL = "https://www.svt.se/nyheter/inrikes/senaste-nytt-om-val-2026"
STATE_FILE = Path(__file__).parent / "svt_val_state.json"
MAX_SEEN_IDS = 500

DISCORD_WEBHOOK_URL = os.environ.get("DISCORD_WEBHOOK_URL_SVT")

USER_AGENT = "Mozilla/5.0 (compatible; svt-val-monitor/1.0)"


def fetch(url: str) -> str:
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    with urllib.request.urlopen(req, timeout=30) as resp:
        return resp.read().decode("utf-8", errors="replace")


def extract_posts(page_html: str) -> list[dict]:
    """Pull live-blog posts out of the server-rendered window.__URQL_DATA__ blob."""
    marker = "window.__URQL_DATA__"
    idx = page_html.find(marker)
    if idx == -1:
        raise RuntimeError("Could not find __URQL_DATA__ in page — layout may have changed")
    rest = page_html[idx + len(marker):]
    rest = rest[rest.index("=") + 1:].lstrip()
    urql, _ = json.JSONDecoder().raw_decode(rest)

    for entry in urql.values():
        data = entry.get("data")
        if isinstance(data, str):
            data = json.loads(data)
        stream = ((data or {}).get("page") or {}).get("liveStream")
        if not stream:
            continue
        posts = {}
        for key in ("pinnedPosts", "highlightedPosts", "posts"):
            for p in stream.get(key) or []:
                if p.get("id"):
                    posts[p["id"]] = p
        return list(posts.values())

    raise RuntimeError("No liveStream found in page data — layout may have changed")


def html_to_text(body: str) -> tuple[str, str]:
    """Return (headline, text). Headline is the first <strong> paragraph if present."""
    headline = ""
    m = re.match(r"\s*<p>\s*<strong>(.*?)</strong>\s*</p>", body or "", re.S)
    if m:
        headline = html.unescape(re.sub(r"<[^>]+>", "", m.group(1))).strip()
        body = body[m.end():]
    text = re.sub(r"</p>\s*<p>|<br\s*/?>", "\n\n", body or "")
    text = html.unescape(re.sub(r"<[^>]+>", "", text))
    text = re.sub(r"\n{3,}", "\n\n", text).strip()
    return headline, text


def post_time(post: dict) -> int:
    return (post.get("createdAt") or {}).get("seconds") or 0


def send_discord(post: dict) -> bool:
    headline, text = html_to_text(post.get("body", ""))
    author = (post.get("author") or {}).get("displayName", "")
    ts = post_time(post)

    log.info("New post %s: %s", post["id"], headline or text[:80])

    if not DISCORD_WEBHOOK_URL:
        return False

    if len(text) > 1500:
        text = text[:1500].rsplit(" ", 1)[0] + " …"

    embed = {
        "title": (headline or "Ny uppdatering")[:256],
        "url": PAGE_URL,
        "description": text or None,
        "color": 0x1E6E3C,
        "footer": {"text": f"SVT Valet 2026 · {author}" if author else "SVT Valet 2026"},
    }
    if ts:
        embed["timestamp"] = datetime.fromtimestamp(ts, timezone.utc).isoformat()

    payload = json.dumps({"embeds": [embed]}).encode()
    req = urllib.request.Request(
        DISCORD_WEBHOOK_URL,
        data=payload,
        headers={"Content-Type": "application/json", "User-Agent": USER_AGENT},
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=15) as resp:
            log.info("Discord alert sent (HTTP %d)", resp.status)
            return True
    except urllib.error.HTTPError as e:
        body = e.read().decode("utf-8", errors="replace")[:200]
        log.error("Failed to send Discord alert: HTTP %d, body: %s", e.code, body)
    except Exception as e:
        log.error("Failed to send Discord alert: %s", e)
    return False


def load_state() -> dict:
    if STATE_FILE.exists():
        try:
            with open(STATE_FILE) as f:
                return json.load(f)
        except (json.JSONDecodeError, OSError) as e:
            log.warning("Could not read state file, starting fresh: %s", e)
    return {}


def save_state(state: dict) -> None:
    with open(STATE_FILE, "w") as f:
        json.dump(state, f, indent=2)
        f.write("\n")
    log.info("State file updated.")


def main() -> None:
    try:
        posts = extract_posts(fetch(PAGE_URL))
    except Exception as e:
        log.error("Failed to fetch/parse live blog: %s", e)
        sys.exit(1)
    log.info("Found %d post(s) on page", len(posts))

    if os.environ.get("TEST_SEND") == "true":
        if not DISCORD_WEBHOOK_URL:
            log.error("TEST_SEND requested but DISCORD_WEBHOOK_URL_SVT is not set")
            sys.exit(1)
        latest = max(posts, key=post_time)
        log.info("TEST_SEND: sending latest post %s (state untouched)", latest["id"])
        if not send_discord(latest):
            sys.exit(1)
        return

    state = load_state()
    first_run = "seen_ids" not in state
    seen = list(state.get("seen_ids", []))
    seen_set = set(seen)

    new_posts = sorted((p for p in posts if p["id"] not in seen_set), key=post_time)

    if first_run:
        log.info("First run — recording %d post(s) as baseline, no alerts.", len(new_posts))
    else:
        log.info("%d new post(s)", len(new_posts))
        for i, p in enumerate(new_posts):
            if i:
                time.sleep(1)  # stay well under Discord's webhook rate limit
            send_discord(p)

    seen.extend(p["id"] for p in new_posts)
    state["seen_ids"] = seen[-MAX_SEEN_IDS:]
    if new_posts or first_run:
        save_state(state)


if __name__ == "__main__":
    main()
