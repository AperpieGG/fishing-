# fishing-

Python tools for LRF shore fishing condition checks and catch logging.

The repo contains two scripts:

- `fishing.py` - checks current LRF fishing conditions for a location and prints the best windows over the next 24 hours.
- `catch_logger.py` - records a specific catch, enriches it with sun, weather, wind, pressure, and marine conditions, then appends the result to a CSV file.

## Requirements

- Python 3.9 or newer
- Internet access for weather, marine, and sunrise/sunset API calls

Install the Python packages used by the scripts:

```bash
python -m pip install requests python-dateutil meteostat
```

`fishing.py` uses `requests` and `python-dateutil`.
`catch_logger.py` also uses `meteostat` for observed/historical weather data.

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

Run `catch_logger.py` when you want to log a real catch and save the conditions around that catch time.

```bash
python catch_logger.py \
  --fish "sargos" \
  --date 2026-07-08 \
  --time 20:00 \
  --lat 37.9420 \
  --lon 23.6469 \
  --timezone Europe/Athens \
  --notes "Rocky mark, light jighead"
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

- Fish name, coordinates, timezone, and catch time
- Matched weather and marine timestamps
- Sunrise, sunset, and civil twilight
- Temperature, humidity, precipitation, pressure, and pressure trend
- Wind speed, wind direction, Beaufort force, and cloud cover
- Wave, wind-wave, and swell data
- Experimental LRF score
- Notes

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
