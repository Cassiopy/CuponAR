# CuponAR - World Cup Goal Monitor

Real-time push notifications whenever a goal is scored in the FIFA World Cup. No paid API, no signup required.

<img width="749" height="500" alt="1" src="https://github.com/user-attachments/assets/d818d5c9-eee3-4b97-b393-b2d529e4784e" />

<img width="346" height="475" alt="2" src="https://github.com/user-attachments/assets/d6fc7daa-5c45-4a92-a32e-3a9cdc8d1220" />

## How it works

- **Data source:** ESPN's public (unofficial) scoreboard API. No API key needed.
- **Notifications:** [ntfy.sh](https://ntfy.sh), a free push notification service.
- **Polling:** checks the API every second while a match is live. When nothing is being played, it calculates when the next match kicks off and sleeps until then instead of polling uselessly.

This is a small personal-use script, not a production service. Expect ESPN's data to lag the live broadcast by anywhere from a few seconds to about a minute, depending on their backend.

## Setup

### 1. Get the ntfy app

Install **ntfy** from the App Store, Google Play, or use [ntfy.sh/app](https://ntfy.sh/app) in a browser. Subscribe to a topic name of your choice, think of it as a channel name. Anyone who knows the name can subscribe, so pick something reasonably unique (e.g. `worldcup-yourname-2026`).

### 2. Configure the script

Open `monitor.py` and set your topic:

```python
NTFY_TOPIC = os.getenv("NTFY_TOPIC", "your-topic-here")
```

Or, if you'd rather not edit the file, set it as an environment variable instead:

```bash
export NTFY_TOPIC="worldcup-yourname-2026"
```

### 3. Install and run

```bash
pip -m install -r requirements.txt
python monitor.py
```

Leave the terminal running. You'll get a notification on your phone the moment any team scores, in any match.

## What you'll see

The console logs live activity only as kickoffs, score updates, and when it's sleeping until the next match. Errors don't clutter the console, they're written to `errors.log` in the same folder, with a timestamp and full traceback, so you can let this run in the background and check the log later if something looks off.

## Configuration reference

| Variable | What it controls | Default |
|---|---|---|
| `NTFY_TOPIC` | Your notification channel | set your own |
| `POLL_LIVE` | Seconds between checks during a live match | `1` |
| `POLL_IDLE` | Fallback polling interval if no upcoming match could be found | `1` |
| `SCHEDULE_LOOKAHEAD_DAYS` | How far ahead to search for the next match | `14` |
| `START_BUFFER_SECONDS` | Start polling this many seconds before kickoff | `60` |

A note on `POLL_LIVE = 1`: ESPN doesn't publish a rate limit for this endpoint, but polling every second for hours at a time is aggressive and could eventually get you rate limited (HTTP 429). If that happens, bumping it up to 10–20 seconds is the fix and you'll still get notified well within real time relevance for a goal.

## Handles multiple matches at once

Every match is tracked independently by its ESPN match ID, so two (or more) World Cup games being played simultaneously are monitored in parallel without interference.

## Using Telegram instead of ntfy

If you'd rather get a Telegram message, swap out the `notify()` function:

```python
TELEGRAM_TOKEN = "your-bot-token"
TELEGRAM_CHAT_ID = "your-chat-id"

def notify(title, body):
    msg = f"*{title}*\n{body}"
    requests.post(
        f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage",
        json={"chat_id": TELEGRAM_CHAT_ID, "text": msg, "parse_mode": "Markdown"},
        timeout=6,
    )
```

Create a bot via [@BotFather](https://t.me/BotFather) on Telegram to get a token.

## Running it 24/7

This needs to stay running to notify you. Options:

- Leave it running in a terminal on your machine while the tournament is on.
- Run it on a small VPS (~$5/month on most providers) or a Raspberry Pi if you want it always on without keeping your computer awake.
- On Linux/macOS, `nohup python monitor.py &` keeps it alive after closing the terminal.

## Known limitations

- ESPN's API is unofficial and undocumented. It's been stable for years, but it could change or disappear without notice.
- Goal scorer names depend on ESPN's match summary data being populated, which isn't always instant or available for every match.
- No persistence: if you restart the script mid-match, it won't fire a false "goal" for the existing score, but it also won't remember any history from before the restart.

## Requirements

```
requests>=2.31.0
```

Python 3.10+ (uses the `X | Y` type hint syntax).

# Oid mortales.

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
#

