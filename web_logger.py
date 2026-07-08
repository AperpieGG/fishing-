#!/usr/bin/env python3

import argparse
import csv
import io
import os
import sqlite3
from functools import wraps
from secrets import token_hex
from datetime import datetime
from zoneinfo import ZoneInfo

import requests
from dateutil import parser as date_parser
from flask import Flask, Response, g, redirect, render_template_string, request, session, url_for


DEFAULT_TIMEZONE = "Europe/Athens"
DEFAULT_FISH = ["melanouri", "sargos", "lavraki", "other"]

app = Flask(__name__)
app.config["DATABASE"] = os.environ.get("DATABASE_PATH", "fishing_log.db")
app.secret_key = os.environ.get("SECRET_KEY") or token_hex(32)

DB_INITIALIZED = False


SCHEMA = """
CREATE TABLE IF NOT EXISTS sessions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    spot_name TEXT,
    lat REAL NOT NULL,
    lon REAL NOT NULL,
    timezone TEXT NOT NULL,
    started_at TEXT NOT NULL,
    ended_at TEXT,
    notes TEXT
);

CREATE TABLE IF NOT EXISTS catches (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    session_id INTEGER NOT NULL,
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
    FOREIGN KEY(session_id) REFERENCES sessions(id)
);
"""


PAGE = """
<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
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
    </section>

    <section class="panel">
      <h2>Log Catch</h2>
      <form method="post" action="{{ url_for('log_catch') }}">
        <label for="fish">Fish</label>
        <select id="fish" name="fish">
          {% for fish in fish_options %}
            <option value="{{ fish }}">{{ fish }}</option>
          {% endfor %}
        </select>

        <label for="custom_fish">Custom fish</label>
        <input id="custom_fish" name="custom_fish" placeholder="Use only if not in list">

        <label for="catch_notes">Catch notes</label>
        <textarea id="catch_notes" name="notes" placeholder="Lure, depth, current, lights, baitfish"></textarea>

        <button type="submit">I Caught One</button>
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
          <thead><tr><th>Time</th><th>Fish</th><th>Conditions</th></tr></thead>
          <tbody>
          {% for catch in catches %}
            <tr>
              <td>{{ catch["caught_at"][11:16] }}</td>
              <td>{{ catch["fish"] }}</td>
              <td>{{ catch["wind_speed_kmh"] }} km/h, {{ catch["beaufort_force"] }} Bf, wave {{ catch["wave_height_m"] }} m</td>
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
        <input id="timezone" name="timezone" value="{{ default_timezone }}">

        <button type="submit">Start Fishing</button>
      </form>
    </section>
  {% endif %}

  <section class="panel">
    <h2>Data</h2>
    <div class="grid">
      <a class="button secondary" href="{{ url_for('sessions') }}">Sessions</a>
      <a class="button secondary" href="{{ url_for('export_csv') }}">Export CSV</a>
    </div>
    <p class="muted">Weather is stored when each catch is logged. Blank sessions are stored when you finish without catches.</p>
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
  }, function(err) {
    alert("Could not get location: " + err.message);
  });
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
      <tr><th>ID</th><th>Spot</th><th>Started</th><th>Ended</th><th>Catches</th></tr>
    </thead>
    <tbody>
    {% for session in sessions %}
      <tr>
        <td>{{ session["id"] }}</td>
        <td>{{ session["spot_name"] }}</td>
        <td>{{ session["started_at"] }}</td>
        <td>{{ session["ended_at"] or "active" }}</td>
        <td>{{ session["catch_count"] }}</td>
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
    with sqlite3.connect(app.config["DATABASE"]) as db:
        db.executescript(SCHEMA)


def now_local(timezone):
    return datetime.now(ZoneInfo(timezone))


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


def query_weather(lat, lon, timezone, day):
    response = requests.get(
        "https://api.open-meteo.com/v1/forecast",
        params={
            "latitude": lat,
            "longitude": lon,
            "timezone": timezone,
            "start_date": day,
            "end_date": day,
            "hourly": ",".join([
                "temperature_2m",
                "relative_humidity_2m",
                "pressure_msl",
                "surface_pressure",
                "wind_speed_10m",
                "wind_direction_10m",
                "cloud_cover",
                "precipitation",
            ]),
        },
        timeout=20,
    )
    response.raise_for_status()
    return response.json()


def query_marine(lat, lon, timezone, day):
    response = requests.get(
        "https://marine-api.open-meteo.com/v1/marine",
        params={
            "latitude": lat,
            "longitude": lon,
            "timezone": timezone,
            "start_date": day,
            "end_date": day,
            "hourly": ",".join([
                "wave_height",
                "wave_direction",
                "wave_period",
                "wind_wave_height",
                "swell_wave_height",
                "swell_wave_period",
            ]),
        },
        timeout=20,
    )
    response.raise_for_status()
    return response.json()


def fetch_conditions(lat, lon, timezone, target_dt):
    day = target_dt.date().isoformat()
    sun = query_sun(lat, lon, day, timezone)
    weather = query_weather(lat, lon, timezone, day)
    marine = query_marine(lat, lon, timezone, day)

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

    return {
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
    }


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
    timezone = request.form.get("timezone") or DEFAULT_TIMEZONE
    started_at = now_local(timezone).isoformat(timespec="minutes")

    get_db().execute(
        """
        INSERT INTO sessions (spot_name, lat, lon, timezone, started_at)
        VALUES (?, ?, ?, ?, ?)
        """,
        (
            request.form.get("spot_name", "").strip(),
            lat,
            lon,
            timezone,
            started_at,
        ),
    )
    get_db().commit()
    return redirect(url_for("index", message="Session started."))


@app.post("/catch")
@login_required
def log_catch():
    session = active_session()

    if not session:
        return redirect(url_for("index", message="Start a session first."))

    custom_fish = request.form.get("custom_fish", "").strip()
    fish = custom_fish or request.form["fish"]
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
        "fish",
        "caught_at",
        "notes",
        *conditions.keys(),
    ]
    values = [
        session["id"],
        fish,
        caught_at.isoformat(timespec="minutes"),
        request.form.get("notes", "").strip(),
        *conditions.values(),
    ]
    placeholders = ", ".join(["?"] * len(columns))

    get_db().execute(
        f"INSERT INTO catches ({', '.join(columns)}) VALUES ({placeholders})",
        values,
    )
    get_db().commit()
    return redirect(url_for("index", message=f"Logged {fish}."))


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
        SELECT sessions.*, COUNT(catches.id) AS catch_count
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
            CASE
                WHEN catches.id IS NULL THEN 'none'
                ELSE catches.fish
            END AS fish,
            CASE
                WHEN catches.id IS NULL THEN 0
                ELSE 1
            END AS catch_count,
            CASE
                WHEN catches.id IS NULL THEN 'no'
                ELSE 'yes'
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
