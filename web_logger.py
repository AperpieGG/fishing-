#!/usr/bin/env python3

import argparse
import csv
import io
import json
import math
import os
import sqlite3
import time
from functools import wraps
from secrets import token_hex
from datetime import datetime, timezone as dt_timezone
from zoneinfo import ZoneInfo

import requests
from dateutil import parser as date_parser
from flask import Flask, Response, g, jsonify, redirect, render_template_string, request, session, url_for


DEFAULT_TIMEZONE = "Europe/Athens"
DEFAULT_FISH = [
    "melanouri",
    "sargos",
    "lavraki",
    "xanos",
    "perka",
    "stira",
    "rofos",
    "gilos",
    "tsipoura",
    "kokali",
    "other",
]

app = Flask(__name__)
app.config["DATABASE"] = os.environ.get("DATABASE_PATH", "fishing_log.db")
app.secret_key = os.environ.get("SECRET_KEY") or token_hex(32)

DB_INITIALIZED = False
OPEN_METEO_CACHE_TTL_SECONDS = 10 * 60
OPEN_METEO_STALE_TTL_SECONDS = 6 * 60 * 60
OPEN_METEO_CACHE = {}


SCHEMA = """
CREATE TABLE IF NOT EXISTS sessions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    spot_name TEXT,
    lat REAL NOT NULL,
    lon REAL NOT NULL,
    timezone TEXT NOT NULL,
    started_at TEXT NOT NULL,
    ended_at TEXT,
    actual_water_movement TEXT,
    water_clarity TEXT,
    current_strength TEXT,
    notes TEXT
);

CREATE TABLE IF NOT EXISTS catches (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    session_id INTEGER NOT NULL,
    outcome TEXT NOT NULL DEFAULT 'caught',
    fish TEXT NOT NULL,
    caught_at TEXT NOT NULL,
    notes TEXT,
    weather_source TEXT,
    matched_weather_time TEXT,
    matched_marine_time TEXT,
    sunrise TEXT,
    sunset TEXT,
    civil_twilight_begin TEXT,
    civil_twilight_end TEXT,
    temperature_c REAL,
    relative_humidity_percent REAL,
    pressure_msl_hpa REAL,
    surface_pressure_hpa REAL,
    pressure_state TEXT,
    delta_pressure_3h_hpa REAL,
    wind_speed_kmh REAL,
    wind_direction_deg REAL,
    wind_direction_cardinal TEXT,
    beaufort_force INTEGER,
    beaufort_description TEXT,
    cloud_cover_percent REAL,
    precipitation_mm REAL,
    wave_height_m REAL,
    wave_direction_deg REAL,
    wave_period_s REAL,
    wind_wave_height_m REAL,
    swell_wave_height_m REAL,
    swell_wave_period_s REAL,
    sea_surface_temperature_c REAL,
    moon_phase_name TEXT,
    moon_age_days REAL,
    moon_illumination_percent REAL,
    FOREIGN KEY(session_id) REFERENCES sessions(id)
);

CREATE TABLE IF NOT EXISTS api_cache (
    cache_key TEXT PRIMARY KEY,
    stored_at REAL NOT NULL,
    response_json TEXT NOT NULL
);
"""


CATCH_MIGRATIONS = {
    "moon_phase_name": "TEXT",
    "moon_age_days": "REAL",
    "moon_illumination_percent": "REAL",
    "sea_surface_temperature_c": "REAL",
    "outcome": "TEXT NOT NULL DEFAULT 'caught'",
}

SESSION_MIGRATIONS = {
    "actual_water_movement": "TEXT",
    "water_clarity": "TEXT",
    "current_strength": "TEXT",
}

CONDITION_COLUMNS = [
    "weather_source",
    "matched_weather_time",
    "matched_marine_time",
    "sunrise",
    "sunset",
    "civil_twilight_begin",
    "civil_twilight_end",
    "temperature_c",
    "relative_humidity_percent",
    "pressure_msl_hpa",
    "surface_pressure_hpa",
    "pressure_state",
    "delta_pressure_3h_hpa",
    "wind_speed_kmh",
    "wind_direction_deg",
    "wind_direction_cardinal",
    "beaufort_force",
    "beaufort_description",
    "cloud_cover_percent",
    "precipitation_mm",
    "wave_height_m",
    "wave_direction_deg",
    "wave_period_s",
    "wind_wave_height_m",
    "swell_wave_height_m",
    "swell_wave_period_s",
    "sea_surface_temperature_c",
    "moon_phase_name",
    "moon_age_days",
    "moon_illumination_percent",
]


PAGE = """
<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <meta name="theme-color" content="#146c5c">
  <meta name="apple-mobile-web-app-capable" content="yes">
  <meta name="apple-mobile-web-app-title" content="Fishing Logger">
  <meta name="apple-mobile-web-app-status-bar-style" content="default">
  <link rel="manifest" href="{{ url_for('manifest') }}">
  <link rel="icon" href="{{ url_for('app_icon') }}" type="image/svg+xml">
  <link rel="apple-touch-icon" href="{{ url_for('app_icon') }}">
  <title>Fishing Logger</title>
  <style>
    :root {
      color-scheme: light;
      font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif;
      background: #f4f7f6;
      color: #15211f;
    }
    body {
      margin: 0;
      padding: 16px;
    }
    main {
      max-width: 720px;
      margin: 0 auto;
    }
    h1 {
      margin: 0 0 12px;
      font-size: 28px;
    }
    .topbar {
      display: flex;
      align-items: center;
      justify-content: space-between;
      gap: 12px;
    }
    .topbar a {
      color: #146c5c;
      font-weight: 700;
      text-decoration: none;
    }
    h2 {
      margin: 24px 0 10px;
      font-size: 20px;
    }
    .panel {
      background: white;
      border: 1px solid #d8e2df;
      border-radius: 8px;
      padding: 14px;
      margin: 12px 0;
    }
    label {
      display: block;
      font-weight: 650;
      margin: 12px 0 6px;
    }
    input, select, textarea, button {
      width: 100%;
      box-sizing: border-box;
      font: inherit;
      border-radius: 8px;
    }
    input, select, textarea {
      border: 1px solid #b8c7c3;
      padding: 11px;
      background: #fff;
    }
    textarea {
      min-height: 80px;
    }
    button, .button {
      display: inline-block;
      border: 0;
      padding: 13px 16px;
      background: #146c5c;
      color: white;
      font-weight: 750;
      text-align: center;
      text-decoration: none;
      cursor: pointer;
    }
    .secondary {
      background: #30433f;
    }
    .danger {
      background: #a83e35;
    }
    .grid {
      display: grid;
      grid-template-columns: 1fr 1fr;
      gap: 10px;
    }
    .status {
      line-height: 1.5;
    }
    .stacked-action {
      margin-top: 34px;
      padding: 16px 12px 12px;
      border: 2px dashed #9aa8a4;
      border-radius: 8px;
      background: #f1f4f3;
    }
    .missed-button {
      background: #5b6870;
    }
    .action-label {
      display: block;
      margin: 0 0 10px;
      color: #4f5d59;
      font-size: 14px;
      font-weight: 750;
      text-transform: uppercase;
    }
    .muted {
      color: #65736f;
      font-size: 14px;
    }
    .flash {
      background: #e8f5ef;
      border: 1px solid #b8dccf;
      border-radius: 8px;
      padding: 10px;
      margin: 10px 0;
    }
    table {
      width: 100%;
      border-collapse: collapse;
      font-size: 14px;
    }
    th, td {
      text-align: left;
      border-bottom: 1px solid #e0e8e5;
      padding: 8px 4px;
      vertical-align: top;
    }
  </style>
</head>
<body>
<main>
  <div class="topbar">
    <h1>Fishing Logger</h1>
    <a href="{{ url_for('logout') }}">Logout</a>
  </div>
  {% if message %}
    <div class="flash">{{ message }}</div>
  {% endif %}

  {% if active %}
    <section class="panel status">
      <strong>Active session</strong><br>
      {{ active["spot_name"] or "Unnamed spot" }}<br>
      Started: {{ active["started_at"] }}<br>
      Location: {{ "%.5f"|format(active["lat"]) }}, {{ "%.5f"|format(active["lon"]) }}
      {% if active["actual_water_movement"] or active["water_clarity"] or active["current_strength"] %}
        <br>
        Water: {{ active["actual_water_movement"] or "not set" }},
        clarity {{ active["water_clarity"] or "not set" }},
        current {{ active["current_strength"] or "not set" }}
      {% endif %}
    </section>

    <section class="panel">
      <h2>Log Fish</h2>
      <form method="post" action="{{ url_for('log_fish_event') }}">
        <input type="hidden" name="outcome" value="caught">
        <label for="fish_caught">Caught fish</label>
        <select id="fish_caught" name="fish">
          {% for fish in fish_options %}
            <option value="{{ fish }}">{{ fish }}</option>
          {% endfor %}
        </select>

        <label for="custom_fish_caught">Custom fish</label>
        <input id="custom_fish_caught" name="custom_fish" placeholder="Use only if not in list">

        <label for="catch_notes">Catch notes</label>
        <textarea id="catch_notes" name="notes" placeholder="Lure, depth, current, lights, baitfish"></textarea>

        <button type="submit">I Caught One</button>
      </form>
      <form class="stacked-action" method="post" action="{{ url_for('log_fish_event') }}">
        <input type="hidden" name="outcome" value="missed">
        <input type="hidden" name="fish" value="unknown">
        <span class="action-label">Hooked but not landed</span>
        <button class="missed-button" type="submit">Missed One</button>
      </form>
    </section>

    <section class="panel">
      <form method="post" action="{{ url_for('finish_session') }}">
        <label for="finish_notes">Session notes</label>
        <textarea id="finish_notes" name="notes" placeholder="What worked, what did not, fishing pressure"></textarea>
        <button class="danger" type="submit">Finish Session</button>
      </form>
    </section>

    <section class="panel">
      <h2>This Session</h2>
      {% if catches %}
        <table>
          <thead><tr><th>Time</th><th>Outcome</th><th>Fish</th><th>Conditions</th></tr></thead>
          <tbody>
          {% for catch in catches %}
            <tr>
              <td>{{ catch["caught_at"][11:16] }}</td>
              <td>{{ catch["outcome"] }}</td>
              <td>{{ catch["fish"] }}</td>
              <td>{{ catch["wind_speed_kmh"] }} km/h, {{ catch["beaufort_force"] }} Bf, wave {{ catch["wave_height_m"] }} m, sea {{ catch["sea_surface_temperature_c"] }}°C, moon {{ catch["moon_illumination_percent"] }}%</td>
            </tr>
          {% endfor %}
          </tbody>
        </table>
      {% else %}
        <p class="muted">No catches logged yet. Finish the session even if it stays blank.</p>
      {% endif %}
    </section>
  {% else %}
    <section class="panel">
      <h2>Start Session</h2>
      <form method="post" action="{{ url_for('start_session') }}">
        <label for="spot_name">Spot name</label>
        <input id="spot_name" name="spot_name" placeholder="Rocky harbour">

        <div class="grid">
          <div>
            <label for="lat">Latitude</label>
            <input id="lat" name="lat" required inputmode="decimal" placeholder="37.90075">
          </div>
          <div>
            <label for="lon">Longitude</label>
            <input id="lon" name="lon" required inputmode="decimal" placeholder="23.49372">
          </div>
        </div>

        <button class="secondary" type="button" onclick="useLocation()">Use My Location</button>

        <label for="timezone">Timezone</label>
        <input id="timezone" name="timezone" value="{{ default_timezone }}" placeholder="auto or Europe/Athens">
        <p class="muted">Use <strong>auto</strong> or leave your phone's detected timezone unless you know the exact timezone name.</p>

        <label for="actual_water_movement">Actual water movement</label>
        <select id="actual_water_movement" name="actual_water_movement">
          <option value="">Not set</option>
          <option value="flat">Flat</option>
          <option value="small chop">Small chop</option>
          <option value="moving">Moving</option>
          <option value="rough">Rough</option>
        </select>

        <label for="water_clarity">Water clarity</label>
        <select id="water_clarity" name="water_clarity">
          <option value="">Not set</option>
          <option value="clear">Clear</option>
          <option value="stained">Stained</option>
          <option value="dirty">Dirty</option>
        </select>

        <label for="current_strength">Current</label>
        <select id="current_strength" name="current_strength">
          <option value="">Not set</option>
          <option value="none">None</option>
          <option value="weak">Weak</option>
          <option value="strong">Strong</option>
        </select>

        <button type="submit">Start Fishing</button>
      </form>
    </section>
  {% endif %}

  <section class="panel">
    <h2>Data</h2>
    <div class="grid">
      <a class="button secondary" href="{{ url_for('sessions') }}">Sessions</a>
      <button class="secondary" type="button" onclick="downloadCsv()">Export CSV</button>
    </div>
    <p class="muted">Export downloads a CSV while keeping the app open. Weather is stored when each fish event is logged.</p>
  </section>
</main>

<script>
function useLocation() {
  if (!navigator.geolocation) {
    alert("Geolocation is not available in this browser.");
    return;
  }
  navigator.geolocation.getCurrentPosition(function(pos) {
    document.getElementById("lat").value = pos.coords.latitude.toFixed(6);
    document.getElementById("lon").value = pos.coords.longitude.toFixed(6);
    setBrowserTimezone();
  }, function(err) {
    alert("Could not get location: " + err.message);
  });
}

function setBrowserTimezone() {
  try {
    const timezone = Intl.DateTimeFormat().resolvedOptions().timeZone;
    if (timezone) {
      document.getElementById("timezone").value = timezone;
    }
  } catch (_error) {
  }
}

setBrowserTimezone();

if ("serviceWorker" in navigator) {
  navigator.serviceWorker.register("{{ url_for('service_worker') }}");
}

async function downloadCsv() {
  try {
    const response = await fetch("{{ url_for('export_csv') }}", { credentials: "same-origin" });
    if (!response.ok) {
      alert("CSV export failed.");
      return;
    }

    const blob = await response.blob();
    const url = URL.createObjectURL(blob);
    const link = document.createElement("a");
    link.href = url;
    link.download = "fishing_log.csv";
    document.body.appendChild(link);
    link.click();
    link.remove();
    URL.revokeObjectURL(url);
  } catch (error) {
    alert("CSV export failed: " + error.message);
  }
}
</script>
</body>
</html>
"""


SESSIONS_PAGE = """
<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <meta name="theme-color" content="#146c5c">
  <meta name="apple-mobile-web-app-capable" content="yes">
  <meta name="apple-mobile-web-app-title" content="Fishing Logger">
  <meta name="apple-mobile-web-app-status-bar-style" content="default">
  <link rel="manifest" href="{{ url_for('manifest') }}">
  <link rel="icon" href="{{ url_for('app_icon') }}" type="image/svg+xml">
  <link rel="apple-touch-icon" href="{{ url_for('app_icon') }}">
  <title>Fishing Sessions</title>
  <style>
    body { font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif; margin: 0; padding: 16px; background: #f4f7f6; color: #15211f; }
    main { max-width: 900px; margin: 0 auto; }
    a { color: #146c5c; font-weight: 700; }
    table { width: 100%; border-collapse: collapse; background: white; border: 1px solid #d8e2df; }
    th, td { text-align: left; border-bottom: 1px solid #e0e8e5; padding: 8px; vertical-align: top; }
  </style>
</head>
<body>
<main>
  <p><a href="{{ url_for('index') }}">Back</a></p>
  <h1>Sessions</h1>
  <table>
    <thead>
      <tr><th>ID</th><th>Spot</th><th>Started</th><th>Ended</th><th>Water</th><th>Caught</th><th>Missed</th></tr>
    </thead>
    <tbody>
    {% for session in sessions %}
      <tr>
        <td>{{ session["id"] }}</td>
        <td>{{ session["spot_name"] }}</td>
        <td>{{ session["started_at"] }}</td>
        <td>{{ session["ended_at"] or "active" }}</td>
        <td>
          {{ session["actual_water_movement"] or "-" }},
          {{ session["water_clarity"] or "-" }},
          {{ session["current_strength"] or "-" }}
        </td>
        <td>{{ session["catch_count"] or 0 }}</td>
        <td>{{ session["missed_count"] or 0 }}</td>
      </tr>
    {% endfor %}
    </tbody>
  </table>
</main>
</body>
</html>
"""


LOGIN_PAGE = """
<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <meta name="theme-color" content="#146c5c">
  <meta name="apple-mobile-web-app-capable" content="yes">
  <meta name="apple-mobile-web-app-title" content="Fishing Logger">
  <meta name="apple-mobile-web-app-status-bar-style" content="default">
  <link rel="manifest" href="{{ url_for('manifest') }}">
  <link rel="icon" href="{{ url_for('app_icon') }}" type="image/svg+xml">
  <link rel="apple-touch-icon" href="{{ url_for('app_icon') }}">
  <title>Fishing Logger Login</title>
  <style>
    body { font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif; margin: 0; padding: 16px; background: #f4f7f6; color: #15211f; }
    main { max-width: 420px; margin: 40px auto; background: white; border: 1px solid #d8e2df; border-radius: 8px; padding: 16px; }
    label { display: block; font-weight: 650; margin: 12px 0 6px; }
    input, button { width: 100%; box-sizing: border-box; font: inherit; border-radius: 8px; }
    input { border: 1px solid #b8c7c3; padding: 11px; }
    button { border: 0; padding: 13px 16px; background: #146c5c; color: white; font-weight: 750; margin-top: 14px; }
    .error { background: #f8e7e4; border: 1px solid #e0aaa4; border-radius: 8px; padding: 10px; }
  </style>
</head>
<body>
<main>
  <h1>Fishing Logger</h1>
  {% if error %}
    <p class="error">{{ error }}</p>
  {% endif %}
  <form method="post" action="{{ url_for('login') }}">
    <label for="username">Username</label>
    <input id="username" name="username" autocomplete="username" required>
    <label for="password">Password</label>
    <input id="password" name="password" type="password" autocomplete="current-password" required>
    <button type="submit">Log In</button>
  </form>
</main>
</body>
</html>
"""


def configured_username():
    return os.environ.get("WEB_LOGGER_USERNAME", "angler")


def configured_password():
    return os.environ.get("WEB_LOGGER_PASSWORD", "fishing")


def login_required(view):
    @wraps(view)
    def wrapped(*args, **kwargs):
        if not session.get("authenticated"):
            return redirect(url_for("login"))

        return view(*args, **kwargs)

    return wrapped


@app.before_request
def ensure_db_initialized():
    global DB_INITIALIZED

    if not DB_INITIALIZED:
        init_db()
        DB_INITIALIZED = True


def get_db():
    if "db" not in g:
        g.db = sqlite3.connect(app.config["DATABASE"])
        g.db.row_factory = sqlite3.Row

    return g.db


@app.teardown_appcontext
def close_db(_error):
    db = g.pop("db", None)

    if db is not None:
        db.close()


def init_db():
    db_path = app.config["DATABASE"]

    try:
        initialize_database_at(db_path)
    except (OSError, sqlite3.Error) as error:
        fallback_path = "fishing_log.db"

        if db_path == fallback_path:
            raise

        print(
            f"Warning: could not open DATABASE_PATH={db_path!r}: {error}. "
            f"Falling back to {fallback_path!r}. Data may not persist after redeploy."
        )
        app.config["DATABASE"] = fallback_path
        initialize_database_at(fallback_path)


def initialize_database_at(db_path):
    db_dir = os.path.dirname(db_path)

    if db_dir:
        os.makedirs(db_dir, exist_ok=True)

    with sqlite3.connect(db_path) as db:
        db.executescript(SCHEMA)
        migrate_db(db)


def migrate_db(db):
    existing_catch_columns = {
        row[1]
        for row in db.execute("PRAGMA table_info(catches)")
    }

    for column, column_type in CATCH_MIGRATIONS.items():
        if column not in existing_catch_columns:
            db.execute(f"ALTER TABLE catches ADD COLUMN {column} {column_type}")

    existing_session_columns = {
        row[1]
        for row in db.execute("PRAGMA table_info(sessions)")
    }

    for column, column_type in SESSION_MIGRATIONS.items():
        if column not in existing_session_columns:
            db.execute(f"ALTER TABLE sessions ADD COLUMN {column} {column_type}")


def now_local(timezone):
    if not valid_timezone(timezone):
        timezone = DEFAULT_TIMEZONE

    return datetime.now(ZoneInfo(timezone))


def valid_timezone(timezone):
    try:
        ZoneInfo(timezone)
        return True
    except Exception:
        return False


def detect_timezone_from_coordinates(lat, lon):
    response = requests.get(
        "https://api.open-meteo.com/v1/forecast",
        params={
            "latitude": lat,
            "longitude": lon,
            "timezone": "auto",
            "forecast_days": 1,
            "current": "temperature_2m",
        },
        timeout=20,
    )
    response.raise_for_status()
    timezone = response.json().get("timezone")

    if timezone and valid_timezone(timezone):
        return timezone

    return DEFAULT_TIMEZONE


def resolve_timezone(lat, lon, requested_timezone):
    requested_timezone = (requested_timezone or "").strip()

    if requested_timezone and requested_timezone.lower() != "auto" and valid_timezone(requested_timezone):
        return requested_timezone

    try:
        return detect_timezone_from_coordinates(lat, lon)
    except Exception:
        return DEFAULT_TIMEZONE


def parse_api_datetime(value, timezone):
    dt = date_parser.isoparse(value)

    if dt.tzinfo is None:
        return dt.replace(tzinfo=ZoneInfo(timezone))

    return dt.astimezone(ZoneInfo(timezone))


def find_nearest_hour_index(times, target_dt, timezone):
    parsed = []

    for value in times:
        dt = datetime.fromisoformat(value)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=ZoneInfo(timezone))
        parsed.append(dt)

    differences = [abs((dt - target_dt).total_seconds()) for dt in parsed]
    return differences.index(min(differences))


def wind_direction_cardinal(degrees):
    if degrees is None:
        return "N/A"

    directions = ["N", "NE", "E", "SE", "S", "SW", "W", "NW"]
    return directions[round(degrees / 45) % 8]


def wind_speed_to_beaufort(speed_kmh):
    if speed_kmh is None:
        return None, "N/A"
    if speed_kmh < 1:
        return 0, "calm"
    if speed_kmh <= 5:
        return 1, "light air"
    if speed_kmh <= 11:
        return 2, "light breeze"
    if speed_kmh <= 19:
        return 3, "gentle breeze"
    if speed_kmh <= 28:
        return 4, "moderate breeze"
    if speed_kmh <= 38:
        return 5, "fresh breeze"
    if speed_kmh <= 49:
        return 6, "strong breeze"
    if speed_kmh <= 61:
        return 7, "near gale"
    if speed_kmh <= 74:
        return 8, "gale"
    if speed_kmh <= 88:
        return 9, "strong gale"
    if speed_kmh <= 102:
        return 10, "storm"
    if speed_kmh <= 117:
        return 11, "violent storm"

    return 12, "hurricane force"


def pressure_state(delta_pressure):
    if delta_pressure is None:
        return "unknown"
    if delta_pressure > 1.0:
        return "rising"
    if delta_pressure < -1.0:
        return "falling"
    return "stable"


def safe_float(value):
    if value in (None, ""):
        return None

    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def moon_conditions(target_dt):
    """
    Approximate lunar phase and illumination.

    The calculation is good enough for fishing-log pattern analysis, but not
    intended for astronomical navigation.
    """
    synodic_month = 29.53058867
    known_new_moon = datetime(2000, 1, 6, 18, 14, tzinfo=dt_timezone.utc)
    target_utc = target_dt.astimezone(dt_timezone.utc)
    days_since_new = (target_utc - known_new_moon).total_seconds() / 86400
    moon_age = days_since_new % synodic_month
    illumination = (1 - math.cos(2 * math.pi * moon_age / synodic_month)) / 2 * 100

    if moon_age < 1.85 or moon_age >= 27.68:
        phase = "new moon"
    elif moon_age < 5.54:
        phase = "waxing crescent"
    elif moon_age < 9.23:
        phase = "first quarter"
    elif moon_age < 12.92:
        phase = "waxing gibbous"
    elif moon_age < 16.61:
        phase = "full moon"
    elif moon_age < 20.30:
        phase = "waning gibbous"
    elif moon_age < 23.99:
        phase = "last quarter"
    else:
        phase = "waning crescent"

    return {
        "moon_phase_name": phase,
        "moon_age_days": round(moon_age, 2),
        "moon_illumination_percent": round(illumination, 1),
    }


def query_sun(lat, lon, day, timezone):
    response = requests.get(
        "https://api.sunrise-sunset.org/json",
        params={
            "lat": lat,
            "lng": lon,
            "date": day,
            "formatted": 0,
        },
        timeout=20,
    )
    response.raise_for_status()
    data = response.json()

    if data.get("status") != "OK":
        raise RuntimeError(f"Sunrise/sunset API error: {data}")

    results = data["results"]
    return {
        "sunrise": parse_api_datetime(results["sunrise"], timezone),
        "sunset": parse_api_datetime(results["sunset"], timezone),
        "civil_twilight_begin": parse_api_datetime(results["civil_twilight_begin"], timezone),
        "civil_twilight_end": parse_api_datetime(results["civil_twilight_end"], timezone),
    }


def cached_open_meteo_get(url, params, cache_key):
    now = time.time()
    cache_id = json.dumps(cache_key, separators=(",", ":"), sort_keys=True)
    cached = OPEN_METEO_CACHE.get(cache_id)

    if cached and now - cached["stored_at"] < OPEN_METEO_CACHE_TTL_SECONDS:
        return cached["data"]

    persistent_cached = get_db().execute(
        "SELECT stored_at, response_json FROM api_cache WHERE cache_key = ?",
        (cache_id,),
    ).fetchone()

    if persistent_cached:
        cached = {
            "stored_at": persistent_cached["stored_at"],
            "data": json.loads(persistent_cached["response_json"]),
        }
        OPEN_METEO_CACHE[cache_id] = cached

        if now - cached["stored_at"] < OPEN_METEO_CACHE_TTL_SECONDS:
            return cached["data"]

    try:
        response = requests.get(url, params=params, timeout=20)
        response.raise_for_status()
    except requests.HTTPError as error:
        if error.response is not None and error.response.status_code == 429:
            if cached and now - cached["stored_at"] < OPEN_METEO_STALE_TTL_SECONDS:
                return cached["data"]
            raise RuntimeError(
                "Open-Meteo rate limit reached and no cached forecast is available yet. "
                "Wait a few minutes and try again."
            ) from error
        raise

    data = response.json()
    stored_at = now
    OPEN_METEO_CACHE[cache_id] = {
        "stored_at": stored_at,
        "data": data,
    }
    get_db().execute(
        """
        INSERT INTO api_cache (cache_key, stored_at, response_json)
        VALUES (?, ?, ?)
        ON CONFLICT(cache_key)
        DO UPDATE SET stored_at = excluded.stored_at, response_json = excluded.response_json
        """,
        (cache_id, stored_at, json.dumps(data)),
    )
    get_db().commit()
    return data


def open_meteo_cache_key(endpoint, lat, lon, timezone, day, hourly_fields):
    return (
        endpoint,
        round(float(lat), 4),
        round(float(lon), 4),
        timezone,
        day,
        tuple(hourly_fields),
    )


def query_weather(lat, lon, timezone, day):
    hourly_fields = [
        "temperature_2m",
        "relative_humidity_2m",
        "pressure_msl",
        "surface_pressure",
        "wind_speed_10m",
        "wind_direction_10m",
        "cloud_cover",
        "precipitation",
    ]
    params = {
        "latitude": lat,
        "longitude": lon,
        "timezone": timezone,
        "start_date": day,
        "end_date": day,
        "hourly": ",".join(hourly_fields),
    }
    return cached_open_meteo_get(
        "https://api.open-meteo.com/v1/forecast",
        params,
        open_meteo_cache_key("forecast", lat, lon, timezone, day, hourly_fields),
    )


def query_marine(lat, lon, timezone, day):
    hourly_fields = [
        "sea_surface_temperature",
        "wave_height",
        "wave_direction",
        "wave_period",
        "wind_wave_height",
        "swell_wave_height",
        "swell_wave_period",
    ]
    params = {
        "latitude": lat,
        "longitude": lon,
        "timezone": timezone,
        "start_date": day,
        "end_date": day,
        "hourly": ",".join(hourly_fields),
    }
    return cached_open_meteo_get(
        "https://marine-api.open-meteo.com/v1/marine",
        params,
        open_meteo_cache_key("marine", lat, lon, timezone, day, hourly_fields),
    )


def query_world_weather_online(lat, lon, day):
    api_key = os.environ.get("WWO_API_KEY")

    if not api_key:
        raise RuntimeError("WWO_API_KEY is not configured.")

    response = requests.get(
        "https://api.worldweatheronline.com/premium/v1/marine.ashx",
        params={
            "key": api_key,
            "q": f"{lat},{lon}",
            "format": "json",
            "tp": 1,
            "date": day,
        },
        timeout=20,
    )
    response.raise_for_status()
    data = response.json()

    errors = data.get("data", {}).get("error")
    if errors:
        message = errors[0].get("msg", errors) if isinstance(errors, list) else errors
        raise RuntimeError(f"WorldWeatherOnline API error: {message}")

    return data


def parse_wwo_hour(day, hour_value, timezone):
    hour_number = int(hour_value or 0) // 100
    return datetime.fromisoformat(day).replace(
        hour=hour_number,
        minute=0,
        second=0,
        microsecond=0,
        tzinfo=ZoneInfo(timezone),
    )


def find_nearest_wwo_hour_index(hourly, day, target_dt, timezone):
    parsed = [parse_wwo_hour(day, row.get("time"), timezone) for row in hourly]
    differences = [abs((dt - target_dt).total_seconds()) for dt in parsed]
    return differences.index(min(differences))


def extract_conditions_from_world_weather_online(data, sun, timezone, target_dt):
    day = target_dt.date().isoformat()
    weather_days = data.get("data", {}).get("weather") or []

    if not weather_days:
        raise RuntimeError("WorldWeatherOnline returned no marine forecast days.")

    weather_day = next((item for item in weather_days if item.get("date") == day), weather_days[0])
    hourly = weather_day.get("hourly") or []

    if not hourly:
        raise RuntimeError("WorldWeatherOnline returned no hourly marine forecast.")

    forecast_day = weather_day.get("date", day)
    index = find_nearest_wwo_hour_index(hourly, forecast_day, target_dt, timezone)
    row = hourly[index]
    matched_time = parse_wwo_hour(forecast_day, row.get("time"), timezone)

    pressure_now = safe_float(row.get("pressure"))
    pressure_before = safe_float(hourly[index - 3].get("pressure")) if index >= 3 else None
    delta_pressure = None

    if pressure_now is not None and pressure_before is not None:
        delta_pressure = pressure_now - pressure_before

    wind_speed = safe_float(row.get("windspeedKmph"))
    wind_direction = safe_float(row.get("winddirDegree"))
    beaufort_force, beaufort_description = wind_speed_to_beaufort(wind_speed)

    conditions = {
        "weather_source": "worldweatheronline_marine",
        "matched_weather_time": matched_time.isoformat(timespec="minutes"),
        "matched_marine_time": matched_time.isoformat(timespec="minutes"),
        "sunrise": sun["sunrise"].isoformat(timespec="minutes"),
        "sunset": sun["sunset"].isoformat(timespec="minutes"),
        "civil_twilight_begin": sun["civil_twilight_begin"].isoformat(timespec="minutes"),
        "civil_twilight_end": sun["civil_twilight_end"].isoformat(timespec="minutes"),
        "temperature_c": safe_float(row.get("tempC")),
        "relative_humidity_percent": safe_float(row.get("humidity")),
        "pressure_msl_hpa": pressure_now,
        "surface_pressure_hpa": None,
        "pressure_state": pressure_state(delta_pressure),
        "delta_pressure_3h_hpa": delta_pressure,
        "wind_speed_kmh": wind_speed,
        "wind_direction_deg": wind_direction,
        "wind_direction_cardinal": row.get("winddir16Point") or wind_direction_cardinal(wind_direction),
        "beaufort_force": beaufort_force,
        "beaufort_description": beaufort_description,
        "cloud_cover_percent": safe_float(row.get("cloudcover")),
        "precipitation_mm": safe_float(row.get("precipMM")),
        "wave_height_m": safe_float(row.get("sigHeight_m")),
        "wave_direction_deg": safe_float(row.get("swellDir")),
        "wave_period_s": safe_float(row.get("swellPeriod_secs")),
        "wind_wave_height_m": None,
        "swell_wave_height_m": safe_float(row.get("swellHeight_m")),
        "swell_wave_period_s": safe_float(row.get("swellPeriod_secs")),
        "sea_surface_temperature_c": safe_float(row.get("waterTemp_C")),
    }
    conditions.update(moon_conditions(target_dt))

    return conditions


def fetch_conditions(lat, lon, timezone, target_dt):
    day = target_dt.date().isoformat()
    sun = query_sun(lat, lon, day, timezone)

    try:
        weather = query_weather(lat, lon, timezone, day)
        marine = query_marine(lat, lon, timezone, day)
    except Exception as open_meteo_error:
        if not os.environ.get("WWO_API_KEY"):
            raise

        try:
            fallback = query_world_weather_online(lat, lon, day)
            return extract_conditions_from_world_weather_online(
                data=fallback,
                sun=sun,
                timezone=timezone,
                target_dt=target_dt,
            )
        except Exception as fallback_error:
            raise RuntimeError(
                f"Open-Meteo failed: {open_meteo_error}; "
                f"WorldWeatherOnline fallback failed: {fallback_error}"
            ) from fallback_error

    weather_index = find_nearest_hour_index(weather["hourly"]["time"], target_dt, timezone)
    marine_index = find_nearest_hour_index(marine["hourly"]["time"], target_dt, timezone)

    w = weather["hourly"]
    m = marine["hourly"]
    pressure_now = w["pressure_msl"][weather_index]
    pressure_before = w["pressure_msl"][weather_index - 3] if weather_index >= 3 else None
    delta_pressure = None

    if pressure_now is not None and pressure_before is not None:
        delta_pressure = pressure_now - pressure_before

    wind_speed = w["wind_speed_10m"][weather_index]
    wind_direction = w["wind_direction_10m"][weather_index]
    beaufort_force, beaufort_description = wind_speed_to_beaufort(wind_speed)

    conditions = {
        "weather_source": "openmeteo_forecast",
        "matched_weather_time": w["time"][weather_index],
        "matched_marine_time": m["time"][marine_index],
        "sunrise": sun["sunrise"].isoformat(timespec="minutes"),
        "sunset": sun["sunset"].isoformat(timespec="minutes"),
        "civil_twilight_begin": sun["civil_twilight_begin"].isoformat(timespec="minutes"),
        "civil_twilight_end": sun["civil_twilight_end"].isoformat(timespec="minutes"),
        "temperature_c": w["temperature_2m"][weather_index],
        "relative_humidity_percent": w["relative_humidity_2m"][weather_index],
        "pressure_msl_hpa": pressure_now,
        "surface_pressure_hpa": w["surface_pressure"][weather_index],
        "pressure_state": pressure_state(delta_pressure),
        "delta_pressure_3h_hpa": delta_pressure,
        "wind_speed_kmh": wind_speed,
        "wind_direction_deg": wind_direction,
        "wind_direction_cardinal": wind_direction_cardinal(wind_direction),
        "beaufort_force": beaufort_force,
        "beaufort_description": beaufort_description,
        "cloud_cover_percent": w["cloud_cover"][weather_index],
        "precipitation_mm": w["precipitation"][weather_index],
        "wave_height_m": m["wave_height"][marine_index],
        "wave_direction_deg": m["wave_direction"][marine_index],
        "wave_period_s": m["wave_period"][marine_index],
        "wind_wave_height_m": m["wind_wave_height"][marine_index],
        "swell_wave_height_m": m["swell_wave_height"][marine_index],
        "swell_wave_period_s": m["swell_wave_period"][marine_index],
        "sea_surface_temperature_c": m.get("sea_surface_temperature", [None])[marine_index],
    }
    conditions.update(moon_conditions(target_dt))

    return conditions


def active_session():
    return get_db().execute(
        "SELECT * FROM sessions WHERE ended_at IS NULL ORDER BY started_at DESC LIMIT 1"
    ).fetchone()


def session_catches(session_id):
    return get_db().execute(
        "SELECT * FROM catches WHERE session_id = ? ORDER BY caught_at DESC",
        (session_id,),
    ).fetchall()


@app.route("/")
@login_required
def index():
    active = active_session()
    catches = session_catches(active["id"]) if active else []
    return render_template_string(
        PAGE,
        active=active,
        catches=catches,
        fish_options=DEFAULT_FISH,
        default_timezone=DEFAULT_TIMEZONE,
        message=request.args.get("message", ""),
    )


@app.route("/manifest.webmanifest")
def manifest():
    return jsonify({
        "name": "Fishing Logger",
        "short_name": "Fishing",
        "description": "Log fishing sessions, catches, and conditions from your phone.",
        "start_url": url_for("index"),
        "scope": "/",
        "display": "standalone",
        "background_color": "#f4f7f6",
        "theme_color": "#146c5c",
        "icons": [
            {
                "src": url_for("app_icon"),
                "sizes": "any",
                "type": "image/svg+xml",
                "purpose": "any maskable",
            }
        ],
    })


@app.route("/icon.svg")
def app_icon():
    svg = """<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 512 512">
  <rect width="512" height="512" rx="96" fill="#89cff0"/>
  <path d="M112 286c76-92 188-92 308 0-120 92-232 92-308 0Z" fill="#ffffff" stroke="#111111" stroke-width="22" stroke-linejoin="round"/>
  <path d="M118 286 72 236v100l46-50Z" fill="#ffffff" stroke="#111111" stroke-width="22" stroke-linejoin="round"/>
  <circle cx="356" cy="270" r="17" fill="#111111"/>
</svg>"""
    return Response(svg, mimetype="image/svg+xml")


@app.route("/service-worker.js")
def service_worker():
    script = """
self.addEventListener("install", function(event) {
  self.skipWaiting();
});

self.addEventListener("activate", function(event) {
  event.waitUntil(self.clients.claim());
});
"""
    return Response(script, mimetype="application/javascript")


@app.route("/login", methods=["GET", "POST"])
def login():
    if request.method == "POST":
        username = request.form.get("username", "")
        password = request.form.get("password", "")

        if username == configured_username() and password == configured_password():
            session.clear()
            session["authenticated"] = True
            return redirect(url_for("index"))

        return render_template_string(LOGIN_PAGE, error="Invalid username or password."), 401

    return render_template_string(LOGIN_PAGE, error="")


@app.route("/logout")
def logout():
    session.clear()
    return redirect(url_for("login"))


@app.post("/start")
@login_required
def start_session():
    if active_session():
        return redirect(url_for("index", message="A session is already active."))

    lat = float(request.form["lat"])
    lon = float(request.form["lon"])
    requested_timezone = request.form.get("timezone")
    timezone = resolve_timezone(lat, lon, requested_timezone)
    started_at = now_local(timezone).isoformat(timespec="minutes")

    get_db().execute(
        """
        INSERT INTO sessions (
            spot_name,
            lat,
            lon,
            timezone,
            started_at,
            actual_water_movement,
            water_clarity,
            current_strength
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            request.form.get("spot_name", "").strip(),
            lat,
            lon,
            timezone,
            started_at,
            request.form.get("actual_water_movement", "").strip(),
            request.form.get("water_clarity", "").strip(),
            request.form.get("current_strength", "").strip(),
        ),
    )
    get_db().commit()

    if requested_timezone and requested_timezone.strip() and requested_timezone.strip() != timezone:
        return redirect(url_for("index", message=f"Session started. Timezone set to {timezone}."))

    return redirect(url_for("index", message="Session started."))


@app.post("/fish-event")
@login_required
def log_fish_event():
    session = active_session()

    if not session:
        return redirect(url_for("index", message="Start a session first."))

    outcome = request.form.get("outcome", "caught").strip().lower()

    if outcome not in {"caught", "missed"}:
        return redirect(url_for("index", message="Invalid fish event."))

    custom_fish = request.form.get("custom_fish", "").strip()
    fish = custom_fish or request.form.get("fish", "none")
    caught_at = now_local(session["timezone"])

    try:
        conditions = fetch_conditions(
            lat=session["lat"],
            lon=session["lon"],
            timezone=session["timezone"],
            target_dt=caught_at,
        )
    except Exception as error:
        return redirect(url_for("index", message=f"Could not fetch weather: {error}"))

    columns = [
        "session_id",
        "outcome",
        "fish",
        "caught_at",
        "notes",
        *CONDITION_COLUMNS,
    ]
    values = [
        session["id"],
        outcome,
        fish,
        caught_at.isoformat(timespec="minutes"),
        request.form.get("notes", "").strip(),
        *[conditions[column] for column in CONDITION_COLUMNS],
    ]
    placeholders = ", ".join(["?"] * len(columns))

    get_db().execute(
        f"INSERT INTO catches ({', '.join(columns)}) VALUES ({placeholders})",
        values,
    )
    get_db().commit()

    if outcome == "caught":
        message = f"Logged caught {fish}."
    elif outcome == "missed":
        message = f"Logged missed {fish}."

    return redirect(url_for("index", message=message))


@app.post("/finish")
@login_required
def finish_session():
    session = active_session()

    if not session:
        return redirect(url_for("index", message="No active session."))

    ended_at = now_local(session["timezone"]).isoformat(timespec="minutes")
    notes = request.form.get("notes", "").strip()

    get_db().execute(
        "UPDATE sessions SET ended_at = ?, notes = ? WHERE id = ?",
        (ended_at, notes, session["id"]),
    )
    get_db().commit()
    return redirect(url_for("index", message="Session finished. Blank sessions are kept as effort data."))


@app.route("/sessions")
@login_required
def sessions():
    rows = get_db().execute(
        """
        SELECT
            sessions.*,
            SUM(CASE WHEN catches.outcome = 'caught' THEN 1 ELSE 0 END) AS catch_count,
            SUM(CASE WHEN catches.outcome = 'missed' THEN 1 ELSE 0 END) AS missed_count
        FROM sessions
        LEFT JOIN catches ON catches.session_id = sessions.id
        GROUP BY sessions.id
        ORDER BY sessions.started_at DESC
        """
    ).fetchall()
    return render_template_string(SESSIONS_PAGE, sessions=rows)


@app.route("/export.csv")
@login_required
def export_csv():
    rows = get_db().execute(
        """
        SELECT
            'session' AS record_type,
            sessions.id AS session_id,
            sessions.spot_name,
            sessions.lat,
            sessions.lon,
            sessions.timezone,
            sessions.started_at AS session_start_time,
            sessions.ended_at AS session_end_time,
            sessions.actual_water_movement,
            sessions.water_clarity,
            sessions.current_strength,
            CASE
                WHEN catches.id IS NULL THEN 'blank'
                ELSE catches.outcome
            END AS outcome,
            CASE
                WHEN catches.id IS NULL THEN 'none'
                ELSE catches.fish
            END AS fish,
            CASE
                WHEN catches.outcome = 'caught' THEN 1
                ELSE 0
            END AS catch_count,
            CASE
                WHEN catches.outcome = 'missed' THEN 1
                ELSE 0
            END AS missed_count,
            CASE
                WHEN catches.outcome = 'caught' THEN 'yes'
                ELSE 'no'
            END AS has_catch,
            catches.caught_at AS condition_time,
            catches.caught_at AS catch_time,
            catches.notes AS catch_notes,
            catches.weather_source,
            catches.matched_weather_time,
            catches.matched_marine_time,
            catches.sunrise,
            catches.sunset,
            catches.civil_twilight_begin,
            catches.civil_twilight_end,
            catches.temperature_c,
            catches.relative_humidity_percent,
            catches.pressure_msl_hpa,
            catches.surface_pressure_hpa,
            catches.pressure_state,
            catches.delta_pressure_3h_hpa,
            catches.wind_speed_kmh,
            catches.wind_direction_deg,
            catches.wind_direction_cardinal,
            catches.beaufort_force,
            catches.beaufort_description,
            catches.cloud_cover_percent,
            catches.precipitation_mm,
            catches.wave_height_m,
            catches.wave_direction_deg,
            catches.wave_period_s,
            catches.wind_wave_height_m,
            catches.swell_wave_height_m,
            catches.swell_wave_period_s,
            catches.sea_surface_temperature_c,
            catches.moon_phase_name,
            catches.moon_age_days,
            catches.moon_illumination_percent,
            sessions.notes AS session_notes
        FROM sessions
        LEFT JOIN catches ON catches.session_id = sessions.id
        ORDER BY sessions.started_at, catches.caught_at
        """
    ).fetchall()

    output = io.StringIO()
    writer = csv.writer(output)

    if rows:
        writer.writerow(rows[0].keys())
        for row in rows:
            writer.writerow([row[key] for key in row.keys()])

    return Response(
        output.getvalue(),
        mimetype="text/csv",
        headers={"Content-Disposition": "attachment; filename=fishing_log.csv"},
    )


def main():
    parser = argparse.ArgumentParser(description="Mobile web logger for fishing sessions and catches.")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=5000)
    parser.add_argument("--db", default="fishing_log.db")
    args = parser.parse_args()

    app.config["DATABASE"] = args.db
    init_db()
    app.run(host=args.host, port=args.port, debug=False)


if __name__ == "__main__":
    main()
