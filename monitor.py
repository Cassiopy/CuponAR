"""
CuponAR - World Cup 2026 Goal Monitor
=============================================

What this script does:
    Polls ESPN's public (unofficial) scoreboard API for the FIFA World Cup,
    detects when any team's score changes, and sends a push notification
    to your phone via ntfy.sh the moment a goal happens.

Data source:
    ESPN's hidden/undocumented JSON API. No API key required, no auth.
    It's not officially supported, but it's been stable for years and
    is widely used by the developer community for exactly this purpose.

Notifications:
    ntfy.sh is a free, nosignup push notification service. You "subscribe"
    to a topic name (basically a password-less channel) from an app on
    your phone, and this script posts messages to that same topic.

Quick start:
    1. Install the ntfy app on your phone: https://ntfy.sh
    2. Subscribe to a topic of your choice (set it in NTFY_TOPIC below)
    3. cd into this folder
    4. py -m pip install -r requirements.txt
    5. py monitor.py

Errors are NOT printed to the console, they're written to errors.log
in this same folder with a timestamp and full stack trace so the
console stays clean while this runs in the background.

=============================================
Ahora en lenguaje argentino, oid mortales.

Estaba cansada de no sacar ningún cupón de cierta aplicación roja de delivery
y dije "no laburo con Python en el día a día pero por algo pago la subscripción
de Claude y por algo fui a la universidad". Me levanté de la siesta con esta 
idea no revolucionaria pero atada con alambres como toda magia argentina, culo,
silla y Claude.
Gracias al motivo de este proyecto, está configurado un rate
animal cada 1 segundo para los requests a la api de ESPN. Me comprometo a
avisar si algún día recibo un bloqueo.
Si notás que hay cosas obvias explicadas, es una nota mental para irme
familiarizando con Python. Trabajo con lenguajes muy estructurados, teneme
paciencia.

Nota: porque lo haya hecho con Claude no significa que no le haya pegado una leída
para ver si tenía sentido. Agregué varias cosas en la marcha, recibo sugerencias.
=============================================
"""

import requests
import time
import os
import logging
import traceback
from datetime import datetime, timedelta, timezone

# ─────────────────────────────────────────────────────────────────────────────
# CONFIGURATION
# ─────────────────────────────────────────────────────────────────────────────

# The ntfy.sh "channel" this script publishes to. Anyone subscribed to the
# same topic name gets the notification so pick something unique.
# You can also set this via an environment variable instead of editing the
# code (useful if you ever deploy this to a server).
NTFY_TOPIC = os.getenv("NTFY_TOPIC", "YourNTFYName")  # <-- change this to your own topic

# How often (in seconds) we re-check the API while a match is live.
# NOTE: 1 second is very aggressive. ESPN has no published rate limit,
# but polling this fast for hours risks getting temporarily blocked
# (HTTP 429). If that starts happening, raising this to 10-20s is the fix.
POLL_LIVE = 1

# Fallback polling interval, only used if we couldn't figure out when the
# next match starts (see the scheduling logic further down).
POLL_IDLE = 1

# When there's nothing live right now, how many days ahead do we look to
# find the next scheduled match? This keeps us from polling uselessly
# during the hours/days with no games.
SCHEDULE_LOOKAHEAD_DAYS = 14

# Start polling a little before the official kickoff time, just in case
# the API or the broadcast starts slightly early.
START_BUFFER_SECONDS = 60

# ESPN's public soccer endpoints. "fifa.world" is ESPN's internal league
# code for the FIFA World Cup.
ESPN_SCOREBOARD = "https://site.api.espn.com/apis/site/v2/sports/soccer/fifa.world/scoreboard"
ESPN_SUMMARY    = "https://site.api.espn.com/apis/site/v2/sports/soccer/fifa.world/summary"

# Where the error log file lives. Using the script's own folder (instead of
# a relative path) means it works correctly no matter where you run it from.
ERROR_LOG_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "errors.log")

# ─────────────────────────────────────────────────────────────────────────────
# FILE-BASED ERROR LOGGING
# ─────────────────────────────────────────────────────────────────────────────
# This is separate from the console output. The console only shows normal
# activity (matches found, goals detected); anything that goes wrong gets
# appended to errors.log with a timestamp and full Python traceback, so you
# can leave this running unattended and check the log later if something
# seems off.

error_logger = logging.getLogger("worldcup_monitor")
error_logger.setLevel(logging.ERROR)

_file_handler = logging.FileHandler(ERROR_LOG_PATH, encoding="utf-8")
_file_handler.setFormatter(logging.Formatter(
    "%(asctime)s | %(levelname)s | %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
))
error_logger.addHandler(_file_handler)
error_logger.propagate = False  # don't also dump this to the console


def log_error(context: str, exc: Exception | None = None):
    """
    Write one entry to errors.log.

    'context' is a short human-readable description of what we were doing
    when the problem happened. If an exception object is passed in, its
    full traceback gets appended too and that's the part you'd paste into a
    bug report.
    """
    if exc is not None:
        tb = traceback.format_exc()
        error_logger.error(f"{context} | {exc}\n{tb}")
    else:
        error_logger.error(context)

# ─────────────────────────────────────────────────────────────────────────────
# IN-MEMORY STATE
# ─────────────────────────────────────────────────────────────────────────────
# Everything here lives only in RAM and resets if you restart the script.
# Each one is a dictionary/set keyed by ESPN's internal match ID, which is
# what lets this script track multiple simultaneous matches independently
# (e.g. two World Cup games kicking off at the same time).

# Last known score per match: { match_id: (home_score, away_score) }
known_scores: dict[str, tuple[int, int]] = {}

# Last known game clock per match, e.g. "67'". Used only to log when the
# clock actually changes, straight from the API and not a timer we run
# ourselves. Handy for measuring real world delay vs. a live broadcast.
known_clocks: dict[str, str] = {}

# IDs of matches that have already finished, so we stop checking them
# forever instead of re-processing the same final score every poll.
finished_matches: set[str] = set()

# ─────────────────────────────────────────────────────────────────────────────
# MATCH STATUS CATEGORIES
# ─────────────────────────────────────────────────────────────────────────────
# ESPN reports a match's status as a string like "STATUS_IN_PROGRESS".
# Rather than trying to list every possible "live" status (kickoff,
# halftime, extra time, penalties...), we instead define what counts as
# CLEARLY finished or CLEARLY not started yet. Anything else is treated as
# live by default. This is safer than the opposite approach, because it
# means an unexpected/new status from ESPN still gets picked up instead of
# silently ignored.

STATUS_FINAL = {
    "STATUS_FINAL",        # normal full time finish
    "STATUS_FULL_TIME",    # alternate name for the same thing
    "STATUS_FINAL_PEN",    # finished via penalty shootout
    "STATUS_FINAL_AET",    # finished after extra time
}

STATUS_INACTIVE = {
    "STATUS_SCHEDULED", "STATUS_PRE", "STATUS_POSTPONED",
    "STATUS_CANCELED",  "STATUS_SUSPENDED", "STATUS_DELAYED",
}

# Statuses we already know mean "match is live". This set only exists so we
# can print a heads up if ESPN ever sends something we don't recognize
# it does NOT gate whether we treat the match as live (see process_match).
STATUS_KNOWN_LIVE = {
    "STATUS_IN_PROGRESS", "STATUS_HALFTIME",   "STATUS_END_PERIOD",
    "STATUS_EXTRA_TIME",  "STATUS_SHOOTOUT",   "STATUS_OVERTIME",
    "STATUS_FIRST_HALF",  "STATUS_SECOND_HALF",
}

# ─────────────────────────────────────────────────────────────────────────────
# HELPERS
# ─────────────────────────────────────────────────────────────────────────────

def log(msg: str):
    """Print a timestamped line to the console. This is the 'visible' log."""
    print(f"[{datetime.now().strftime('%H:%M:%S')}] {msg}")


def notify(title: str, body: str):
    """
    Send a push notification by POSTing to ntfy.sh/<your-topic>.
    Anyone subscribed to that topic on their phone/PC gets it instantly.
    """
    try:
        resp = requests.post(
            f"https://ntfy.sh/{NTFY_TOPIC}",
            data=body.encode("utf-8"),
            headers={
                "Title":    title,
                "Priority": "urgent",   # makes it bypass Do Not Disturb on most setup
                "Tags":     "soccer,trophy",  # ntfy turns these into emoji in the notification, just because it looks nice
            },
            timeout=6,
        )
        if resp.status_code == 200:
            log(f"[NOTIF] {title}")
        else:
            log(f"[NOTIF ERROR] HTTP {resp.status_code}")
            log_error(f"ntfy returned HTTP {resp.status_code} | title={title!r} body={body!r}")
    except Exception as e:
        log(f"[NOTIF ERROR] {e}")
        log_error("Failed to send notification to ntfy.sh", e)


def get_scoreboard(dates: str | None = None) -> list:
    """
    Fetch the list of matches from ESPN.

    Without 'dates', ESPN returns today's matches by default.
    Pass a 'YYYYMMDD-YYYYMMDD' range to look further ahead (used by the
    scheduling logic below, so we don't have to poll constantly when
    nothing is happening right now).
    """
    params = {"limit": 200}
    if dates:
        params["dates"] = dates
    try:
        r = requests.get(ESPN_SCOREBOARD, params=params, timeout=10)
        r.raise_for_status()
        return r.json().get("events", [])
    except Exception as e:
        log(f"[ESPN ERROR] {e}")
        log_error(f"Failed to fetch scoreboard (dates={dates})", e)
        return []


def get_last_goal(event_id: str) -> str:
    """
    For a given match, ask ESPN for the match summary and pull out the most
    recent scoring play (who scored and at what minute), if available.
    Returns an empty string if this data isn't available for any reason.
    The notification still goes out either way, just with less detail.
    """
    try:
        r = requests.get(ESPN_SUMMARY, params={"event": event_id}, timeout=8)
        r.raise_for_status()
        data = r.json()
        plays = data.get("scoringPlays", [])
        if plays:
            last  = plays[-1]
            text  = last.get("text", "")
            clock = last.get("clock", {}).get("displayValue", "")
            return f"{text} ({clock})" if clock else text
    except Exception as e:
        log_error(f"Failed to fetch match summary for event {event_id}", e)
    return ""


def parse_event_datetime(event: dict) -> datetime | None:
    """
    Parse ESPN's 'date' field (ISO 8601, UTC, e.g. '2026-06-29T18:00Z')
    into a proper Python datetime object we can do math on.
    """
    raw = event.get("date")
    if not raw:
        return None
    try:
        return datetime.fromisoformat(raw.replace("Z", "+00:00"))
    except ValueError as e:
        log_error(f"Could not parse event date: {raw!r}", e)
        return None


def find_next_match_start(today_events: list) -> datetime | None:
    """
    Figure out when the next match kicks off, so we can sleep until then
    instead of hammering the API for no reason.

    Strategy:
      1. Check the matches we already fetched for "today". If any of
         them are scheduled (not yet started), use the earliest one.
      2. If nothing useful was found there, make ONE extra API call asking
         for a wider date range (SCHEDULE_LOOKAHEAD_DAYS ahead) and look
         for the earliest upcoming match there instead.

    Returns None if no upcoming match could be found at all.
    """
    now = datetime.now(timezone.utc)
    candidates = []

    for ev in today_events:
        status = ev["competitions"][0]["status"]["type"]["name"]
        if status in STATUS_INACTIVE:
            dt = parse_event_datetime(ev)
            if dt and dt > now:
                candidates.append(dt)

    if candidates:
        return min(candidates)

    # Nothing scheduled today, ask for a wider window.
    start_str = now.strftime("%Y%m%d")
    end_str   = (now + timedelta(days=SCHEDULE_LOOKAHEAD_DAYS)).strftime("%Y%m%d")
    future_events = get_scoreboard(dates=f"{start_str}-{end_str}")

    for ev in future_events:
        dt = parse_event_datetime(ev)
        if dt and dt > now:
            candidates.append(dt)

    return min(candidates) if candidates else None

# ─────────────────────────────────────────────────────────────────────────────
# CORE LOGIC
# ─────────────────────────────────────────────────────────────────────────────

def process_match(event: dict) -> bool:
    """
    Look at a single match (one item from ESPN's 'events' list) and:
      - skip it if it's already finished or hasn't started yet
      - detect if its score changed since the last time we checked
      - fire a notification if a goal happened
      - return True if this match is currently live (used by the main
        loop to decide whether to poll fast or go to sleep)
    """
    try:
        comp     = event["competitions"][0]
        status   = comp["status"]["type"]["name"]
        event_id = event["id"]
    except (KeyError, IndexError) as e:
        # Defensive check: if ESPN ever changes their response shape,
        # we log it instead of crashing the whole script.
        log_error(f"Unexpected event structure: {event}", e)
        return False

    # Already finished and already handled, ignore it permanently.
    if event_id in finished_matches:
        return False

    if status in STATUS_FINAL:
        finished_matches.add(event_id)
        known_scores.pop(event_id, None)
        known_clocks.pop(event_id, None)
        return False

    # Hasn't kicked off yet.
    if status in STATUS_INACTIVE:
        return False

    # Anything that isn't clearly final or clearly not started is treated
    # as live. If it's a status we don't explicitly recognize, we just
    # leave a note about it (handy for debugging / improving this list).
    if status not in STATUS_KNOWN_LIVE:
        log(f"[NEW STATUS] {status} — treating as live")
        log_error(f"Unrecognized status received from ESPN: {status} (event_id={event_id})")

    competitors = comp.get("competitors", [])
    home = next((c for c in competitors if c["homeAway"] == "home"), None)
    away = next((c for c in competitors if c["homeAway"] == "away"), None)
    if not home or not away:
        log_error(f"Could not identify home/away teams for event_id={event_id}")
        return True

    home_name  = home["team"].get("shortDisplayName", home["team"]["name"])
    away_name  = away["team"].get("shortDisplayName", away["team"]["name"])
    home_score = int(home.get("score", 0) or 0)
    away_score = int(away.get("score", 0) or 0)
    clock      = comp["status"].get("displayClock", "?")

    current    = (home_score, away_score)
    prev       = known_scores.get(event_id)
    prev_clock = known_clocks.get(event_id)

    # Print a line every time the official game clock changes (straight
    # from ESPN, not something we calculate ourselves). Useful to eyeball
    # how far behind real-time this feed runs.
    if clock != prev_clock:
        log(f"[LIVE] {home_name} {home_score}-{away_score} {away_name} ({clock})")
        known_clocks[event_id] = clock

    # First time we see this match: just remember the current score as our
    # baseline. We do NOT notify here, otherwise restarting the script
    # mid match would fire a fake "goal" for whatever the score already is.
    if prev is None:
        known_scores[event_id] = current
        return True

    # Score changed since last check = somebody scored.
    if current != prev:
        detail = get_last_goal(event_id)
        body   = detail if detail else f"Minute {clock}"
        notify(
            title = f"GOAL! {home_name} {home_score}-{away_score} {away_name}",
            body  = body,
        )
        known_scores[event_id] = current

    return True


def main():
    log("=" * 50)
    log("World Cup Goal Monitor - 2026")
    log(f"Channel: ntfy.sh/{NTFY_TOPIC}")
    log(f"Live polling interval: {POLL_LIVE}s")
    log(f"Errors are logged to: {ERROR_LOG_PATH}")
    log("Press Ctrl+C to stop")
    log("=" * 50)

    while True:
        try:
            events   = get_scoreboard()
            # any(...) short circuits to True as soon as one live match is
            # found, but process_match() still runs on every event in the
            # list first so multiple simultaneous matches are all
            # checked every single poll, not just the first one.
            has_live = any(process_match(e) for e in events)

            if has_live:
                time.sleep(POLL_LIVE)
                continue

            # Nothing live right now, find out when the next match starts
            # so we can sleep through the downtime instead of polling.
            next_start = find_next_match_start(events)

            if next_start is None:
                log(f"No live or upcoming matches found. Retrying in {POLL_IDLE}s...")
                time.sleep(POLL_IDLE)
                continue

            now = datetime.now(timezone.utc)
            wait_seconds = (next_start - now).total_seconds() - START_BUFFER_SECONDS
            local_start  = next_start.astimezone()  # convert to this machine's local time, for the log line

            if wait_seconds <= 0:
                # The match should already be starting, just poll normally.
                time.sleep(POLL_IDLE)
                continue

            hours = wait_seconds / 3600
            log(f"Next match: {local_start.strftime('%d/%m %H:%M')} (local time). "
                f"Sleeping for {hours:.1f}h without hitting the API...")
            time.sleep(wait_seconds)

        except KeyboardInterrupt:
            log("Stopped.")
            break
        except Exception as e:
            # Catch all so one weird/unexpected error doesn't kill the
            # whole script. We log it and keep going after a short pause.
            log(f"[ERROR] {e}")
            log_error("Unhandled error in main loop", e)
            time.sleep(60)


if __name__ == "__main__":
    main()