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

## Ocean Map

`ocean_map.py` runs an interactive world map for bathymetry inspection. It
loads the included EMODnet Greece DTM tiles automatically, uses the included
high-resolution NetCDF products where they overlap, and queries EMODnet's
online point service when local data is not available.

Install the dependencies and start it with:

```bash
python -m pip install -r requirements.txt
python ocean_map.py --host 0.0.0.0 --port 5050
```

Open `http://127.0.0.1:5050` on the same computer. To use it from another
device on the same network, find the computer's LAN address and open
`http://COMPUTER_IP:5050` on that device. On macOS, the address is commonly
shown by:

```bash
ipconfig getifaddr en0
```

The map supports a global bathymetry layer, cursor depth inspection, point
selection with marine conditions, local high-resolution contours, a two-point
distance measurement tool, and an optional animated wind field. Wind speed,
direction, and Beaufort force are shown at an estimated 5 m above ground or
sea level. The weather API provides its standard wind value at 10 m, so the
application applies a neutral 1/7 power-law adjustment to estimate 5 m wind.
For responsiveness, cursor hover sampling uses local files only; clicking a point
can also use EMODnet's online depth fallback.
The distance tool measures from any chosen coastline point to a target point;
it does not infer the nearest coast.

The global map layer is for visual context. Point sampling is most detailed
where a local raster or EMODnet high-resolution product exists. Free global
bathymetry is much coarser than 5 m, and near-shore accuracy depends on the
survey data available for that location. Download additional EMODnet
high-resolution products for a region with:

```bash
python download_emodnet_hr_bathymetry.py \
  --bbox "23.0,37.5,24.5,38.5" \
  --output data/emodnet_hr_bathymetry \
  --extract
```

For Athens and the Saronic Gulf, EMODnet currently has no HR-DTM product in
the catalogue. Download the current 1/16 arc-minute EMODnet DTM subset with:

```bash
python download_emodnet_bathymetry.py \
  --bbox "23.25,37.25,24.25,38.25" \
  --resolution 0.0010416667 \
  --output data/emodnet_athens_saronic_2024.nc
```

The map detects this file automatically and uses it after the HR products.
You can also provide a GEBCO 2026 NetCDF file as a global fallback by placing
it at `data/gebco_2026.nc` or passing `--gebco PATH` when starting the map.

For a threaded local deployment, install Gunicorn and run:

```bash
gunicorn --workers 2 --threads 4 --bind 0.0.0.0:5050 ocean_map:app
```

For a local GEBCO or EMODnet GeoTIFF/NetCDF, pass it explicitly with
`--bathymetry PATH`. The map is not a navigation chart.

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
- Record actual water movement, water clarity, and current strength
- Select a fish each time you catch one
- Report a missed fish when it gets hooked or bites but is not landed
- Store weather, wind, pressure, sun, moon, sea temperature, and marine conditions at catch time
- Finish the session even if it was blank
- Export the SQLite data as CSV from `/export.csv`

Data is stored in `fishing_log.db`.

Session-level manual fields:

- `actual_water_movement`: `flat`, `small chop`, `moving`, `rough`
- `water_clarity`: `clear`, `stained`, `dirty`
- `current_strength`: `none`, `weak`, `strong`

Catch-level automatic fields include:

- Air temperature, humidity, pressure, wind, cloud cover, and precipitation
- Wave height, wave direction, wave period, wind-wave, and swell
- Sea surface temperature
- Sunrise, sunset, and civil twilight
- Moon phase, moon age, and moon illumination

### Add To Phone Home Screen

After deploying the logger online and opening it on your phone:

- iPhone: Safari -> Share -> Add to Home Screen
- Android: Chrome -> menu -> Add to Home screen or Install app

The app includes a web manifest, icon, and mobile metadata so it opens like a standalone app. For best results, use the deployed HTTPS URL. Phone location access usually requires HTTPS.

### Login Protection

The web logger requires login. Configure these environment variables when deploying:

```text
WEB_LOGGER_USERNAME=your_username
WEB_LOGGER_PASSWORD=a_long_random_password
SECRET_KEY=a_long_random_secret
DATABASE_PATH=fishing_log.db
WWO_API_KEY=your_world_weather_online_key
```

`WWO_API_KEY` is optional but recommended. When set, the web logger uses
WorldWeatherOnline Marine API as a fallback if Open-Meteo is rate-limited or
temporarily unavailable.

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
- Actual water movement, water clarity, and current strength
- Fish event outcome: `caught`, `missed`, `note`, or `blank`
- `catch_count` for landed fish and `missed_count` for hooked/lost fish
- Fish name, coordinates, timezone, and catch time
- Condition time used for weather lookup
- Matched weather and marine timestamps
- Sunrise, sunset, and civil twilight
- Moon phase, moon age, and moon illumination
- Temperature, humidity, precipitation, pressure, and pressure trend
- Wind speed, wind direction, Beaufort force, and cloud cover
- Sea surface temperature, wave, wind-wave, and swell data
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

It can also write a PDF fit chart:

```bash
python fishing_planner.py all \
  --csv demo_lrf_catches.csv \
  --spot-name "Rocky harbour" \
  --show-model \
  --top 10 \
  --plot-fit fit.pdf
```

It does not use `lrf_score`.

## Data Sources

The scripts use:

- Sunrise/sunset data from `api.sunrise-sunset.org`
- Forecast weather from Open-Meteo
- Marine, wave, and sea-surface-temperature data from Open-Meteo Marine
- Observed/historical weather in `catch_logger.py` from Meteostat
- Approximate moon phase/illumination calculated locally in `web_logger.py`

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
- actual water movement
- current strength
- notes about lights, baitfish, lure color/weight, and fishing pressure
