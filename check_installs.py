#!/usr/bin/env python3
"""Monitor RuneLite Plugin Hub install counts and send alerts on increases."""

import json
import logging
import os
import struct
import sys
import urllib.error
import urllib.request
from pathlib import Path

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
log = logging.getLogger(__name__)

AUTHOR = "robisa693"
BOOTSTRAP_URL = "https://static.runelite.net/bootstrap.json"
MANIFEST_URL_TEMPLATE = "https://repo.runelite.net/plugins/manifest/{}_full.js"
API_URL = "https://api.runelite.net/pluginhub"
STATE_FILE = Path(__file__).parent / "installs_state.json"

DISCORD_WEBHOOK_URL = os.environ.get("DISCORD_WEBHOOK_URL")
NTFY_TOPIC = os.environ.get("NTFY_TOPIC")

USER_AGENT = "check_installs/1.0"


def fetch(url: str, binary: bool = False) -> bytes | str:
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    with urllib.request.urlopen(req, timeout=30) as resp:
        data = resp.read()
    return data if binary else data.decode("utf-8", errors="replace")


def get_runelite_version() -> str:
    data = fetch(BOOTSTRAP_URL)
    version = json.loads(data).get("version")
    if not version:
        raise RuntimeError("Could not find version in bootstrap.json")
    return version


def get_plugins_by_author(version: str) -> list[dict]:
    url = MANIFEST_URL_TEMPLATE.format(version)
    raw = fetch(url, binary=True)

    compressed_size = struct.unpack(">I", raw[:4])[0]
    json_bytes = raw[compressed_size + 4 :]
    manifest = json.loads(json_bytes.decode("utf-8"))
    plugins = manifest.get("display", [])
    return [p for p in plugins if p.get("author") == AUTHOR]


def fetch_all_counts() -> dict[str, int]:
    req = urllib.request.Request(API_URL, headers={"User-Agent": USER_AGENT})
    with urllib.request.urlopen(req, timeout=30) as resp:
        return json.loads(resp.read().decode())


def send_alert(message: str) -> None:
    log.info("Alert: %s", message)

    if DISCORD_WEBHOOK_URL:
        try:
            payload = json.dumps({"content": message}).encode()
            req = urllib.request.Request(
                DISCORD_WEBHOOK_URL,
                data=payload,
                headers={
                    "Content-Type": "application/json",
                    "User-Agent": USER_AGENT,
                },
                method="POST",
            )
            with urllib.request.urlopen(req, timeout=15) as resp:
                log.info("Discord alert sent (HTTP %d)", resp.status)
        except urllib.error.HTTPError as e:
            body = e.read().decode("utf-8", errors="replace")[:200]
            log.error("Failed to send Discord alert: HTTP %d, body: %s", e.code, body)
        except Exception as e:
            log.error("Failed to send Discord alert: %s", e)

    if NTFY_TOPIC:
        try:
            data = message.encode()
            req = urllib.request.Request(
                f"https://ntfy.sh/{NTFY_TOPIC}",
                data=data,
                method="POST",
            )
            urllib.request.urlopen(req, timeout=15)
            log.info("ntfy.sh alert sent.")
        except Exception as e:
            log.error("Failed to send ntfy.sh alert: %s", e)


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
    log.info("State file updated.")


def main() -> None:
    version = get_runelite_version()
    log.info("RuneLite version: %s", version)

    log.info("Discovering plugins by author '%s'...", AUTHOR)
    my_plugins = get_plugins_by_author(version)
    if not my_plugins:
        log.error("No plugins found for author '%s'", AUTHOR)
        sys.exit(1)
    log.info("Found %d plugin(s): %s", len(my_plugins), [p["internalName"] for p in my_plugins])

    log.info("Fetching install counts from API...")
    try:
        all_counts = fetch_all_counts()
    except Exception as e:
        log.error("Failed to fetch install counts: %s", e)
        sys.exit(1)

    log.info("API returned counts for %d plugins", len(all_counts))

    state = load_state()
    previous = state.get("counts", {})
    current: dict[str, int] = {}
    any_failure = False

    for plugin in my_plugins:
        pid = plugin["internalName"]
        count = all_counts.get(pid)
        if count is None:
            log.error(
                "Plugin '%s' not found in API response — it may not be published yet",
                pid,
            )
            any_failure = True
            continue

        current[pid] = count
        log.info("%s: %s active installs", pid, f"{count:,}")

        prev_count = previous.get(pid)
        if prev_count is None:
            log.info("First run for %s — recorded baseline of %s", pid, f"{count:,}")
        elif count > prev_count:
            delta = count - prev_count
            msg = (
                f"{pid} install count increased: "
                f"{prev_count:,} → {count:,} (+{delta:,})"
            )
            log.info(msg)
            send_alert(msg)
        elif count < prev_count:
            log.info(
                "%s install count decreased: %s → %s (no alert)",
                pid,
                f"{prev_count:,}",
                f"{count:,}",
            )
        else:
            log.info("%s: no change (%s)", pid, f"{count:,}")

    state["counts"] = current
    save_state(state)

    if any_failure:
        log.error("One or more plugins failed — exiting with code 1.")
        sys.exit(1)


if __name__ == "__main__":
    main()
