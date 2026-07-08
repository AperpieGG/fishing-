# fishing-

Python tools for LRF shore fishing condition checks and catch logging.

The repo contains three scripts:

- `fishing.py` - checks current LRF fishing conditions for a location and prints the best windows over the next 24 hours.
- `catch_logger.py` - records a real fishing session, including blank sessions, catch count, session duration, and environmental conditions.
- `fishing_planner.py` - ranks upcoming windows from real session logs and catch rate patterns.
- `web_logger.py` - mobile-friendly web page for starting a session, logging catches, and finishing the session.

## Requirements

- Python 3.9 or newer
- Internet access for weather, marine, and sunrise/sunset API calls

Install the Python packages used by the scripts:

```bash
python -m pip install -r requirements.txt
```

`fishing.py` uses `requests` and `python-dateutil`.
`catch_logger.py` also uses `meteostat` for observed/historical weather data.
`web_logger.py` also uses `flask`.

## Mobile Web Logger

Run the web logger on your laptop before going fishing:

```bash
python web_logger.py --host 0.0.0.0 --port 5000
```

Then open it from your phone while connected to the same network:

```text
http://YOUR_COMPUTER_IP:5000
```

The web page lets you:

- Start a session
- Use your phone location or enter coordinates manually
- Select a fish each time you catch one
- Store weather, wind, pressure, sun, and marine conditions at catch time
- Finish the session even if it was blank
- Export the SQLite data as CSV from `/export.csv`

Data is stored in `fishing_log.db`.

### Login Protection

The web logger requires login. Configure these environment variables when deploying:

```text
WEB_LOGGER_USERNAME=your_username
WEB_LOGGER_PASSWORD=a_long_random_password
SECRET_KEY=a_long_random_secret
DATABASE_PATH=fishing_log.db
```

For local testing, the default login is:

```text
username: angler
password: fishing
```

Change those before putting the app online.

### Deployment Notes

The repo includes a `Procfile` for platforms that support Python web apps:

```text
web: gunicorn web_logger:app
```

Use a host that provides HTTPS and persistent storage. SQLite is fine for personal use, but if your host has an ephemeral filesystem, `fishing_log.db` can disappear after redeploys unless you attach a persistent disk or export backups.

Security basics:

- Do not deploy without changing `WEB_LOGGER_PASSWORD`.
- Use HTTPS, not plain HTTP, for public internet use.
- Do not use Flask's development server for public deployment.
- Do not add port forwarding to your home laptop unless you understand the risk.
- Keep exported CSV/database files private.

## Current Conditions Report

Run `fishing.py` to get a current LRF conditions report for a location.

```bash
python fishing.py
```

By default, it uses Athens, Greece:

- Latitude: `37.9838`
- Longitude: `23.7275`
- Timezone: `Europe/Athens`

You can provide another location:

```bash
python fishing.py \
  --name "Piraeus, Greece" \
  --lat 37.9420 \
  --lon 23.6469 \
  --timezone Europe/Athens
```

The report includes:

- Sunrise, sunset, and civil twilight
- Best light windows around sunrise and sunset
- Temperature, pressure, pressure trend, wind, Beaufort force, and cloud cover
- Wave height, direction, period, wind-wave height, and swell data
- Experimental LRF score out of 10
- Highest-scoring LRF windows for the next 24 hours

## Catch Logger

Run `catch_logger.py` after every real session. Log blank sessions too; they are required for proper statistics.

```bash
python catch_logger.py \
  --fish "melanouri" \
  --date 2026-07-08 \
  --time 20:00 \
  --catch-count 1 \
  --lat 37.9420 \
  --lon 23.6469 \
  --spot-name "Rocky harbour" \
  --session-start 18:30 \
  --session-end 22:15 \
  --timezone Europe/Athens \
  --notes "Rocky mark, light jighead"
```

For a blank session, omit `--time` and set `--catch-count 0`:

```bash
python catch_logger.py \
  --date 2026-07-09 \
  --catch-count 0 \
  --lat 37.9420 \
  --lon 23.6469 \
  --spot-name "Rocky harbour" \
  --session-start 19:00 \
  --session-end 22:30 \
  --timezone Europe/Athens \
  --notes "No bites"
```

By default, catches are appended to `lrf_catches.csv`.

Use a custom CSV path:

```bash
python catch_logger.py \
  --fish "lavraki" \
  --date 2026-07-08 \
  --time 05:45 \
  --lat 37.9420 \
  --lon 23.6469 \
  --csv catches.csv
```

If Meteostat has no observed weather data for the location/time, you can allow an Open-Meteo fallback:

```bash
python catch_logger.py \
  --fish "sargos" \
  --date 2026-07-08 \
  --time 20:00 \
  --lat 37.9420 \
  --lon 23.6469 \
  --fallback-openmeteo
```

The CSV row includes:

- Record type, fish name, spot name, coordinates, timezone
- Session start, session end, session duration, and catch count
- Fish name, coordinates, timezone, and catch time
- Condition time used for weather lookup
- Matched weather and marine timestamps
- Sunrise, sunset, and civil twilight
- Temperature, humidity, precipitation, pressure, and pressure trend
- Wind speed, wind direction, Beaufort force, and cloud cover
- Wave, wind-wave, and swell data
- Lure, technique, water clarity, and notes
- Experimental LRF score, kept for reference only
- Notes

## Fishing Planner

Use `fishing_planner.py` after you have logged real sessions, including blanks.

```bash
python fishing_planner.py melanouri --spot-name "Rocky harbour" --show-model --top 10
```

The planner ignores old catch-only rows. It needs session rows with:

- `session_start_time`
- `session_end_time`
- `catch_count`

This is intentional. A planner cannot learn true best hours from catches alone, because it also needs to know when you fished and caught nothing.

The planner ranks upcoming hours from:

- Catch rate by time bucket
- Catch rate by light phase
- Catch rate by pressure state
- Catch rate by Beaufort force
- Catch rate by wind direction
- Similarity to successful-session weather and marine conditions

It does not use `lrf_score`.

## Data Sources

The scripts use:

- Sunrise/sunset data from `api.sunrise-sunset.org`
- Forecast weather from Open-Meteo
- Marine/wave data from Open-Meteo Marine
- Observed/historical weather in `catch_logger.py` from Meteostat

## LRF Score

The LRF score is a simple practical heuristic, not a biological model.

It gives points for:

- Fishing near sunrise or sunset
- Stable or falling pressure
- Light to moderate wind, especially Beaufort 2-3
- Moderate wave movement

Use it as a quick planning aid, then adjust based on local marks, season, target species, water clarity, and experience.

## Data Quality

Do not use generated/random catches for planning. Keep them in a separate file if you need examples.

For useful patterns, log every trip to the same spot:

- successful sessions
- blank sessions
- start and end time
- catch count
- lure or bait
- technique
- water clarity
- notes about current, lights, baitfish, and fishing pressure
