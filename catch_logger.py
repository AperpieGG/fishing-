#!/usr/bin/env python3

import argparse
import csv
import os
import requests

from datetime import datetime, timedelta
from zoneinfo import ZoneInfo
from dateutil import parser as date_parser

# ============================================================
# Wind utilities
# ============================================================

def wind_direction_cardinal(degrees):
    """
    Convert wind direction in degrees to cardinal direction.

    Meteorological convention:
    0°   = wind coming from North
    90°  = wind coming from East
    180° = wind coming from South
    270° = wind coming from West
    """
    if degrees is None:
        return "N/A"

    directions = ["N", "NE", "E", "SE", "S", "SW", "W", "NW"]
    index = round(degrees / 45) % 8
    return directions[index]


def wind_speed_to_beaufort(speed_kmh):
    """
    Convert wind speed from km/h to Beaufort force.
    """
    if speed_kmh is None:
        return None, "N/A"

    if speed_kmh < 1:
        return 0, "calm"
    elif speed_kmh <= 5:
        return 1, "light air"
    elif speed_kmh <= 11:
        return 2, "light breeze"
    elif speed_kmh <= 19:
        return 3, "gentle breeze"
    elif speed_kmh <= 28:
        return 4, "moderate breeze"
    elif speed_kmh <= 38:
        return 5, "fresh breeze"
    elif speed_kmh <= 49:
        return 6, "strong breeze"
    elif speed_kmh <= 61:
        return 7, "near gale"
    elif speed_kmh <= 74:
        return 8, "gale"
    elif speed_kmh <= 88:
        return 9, "strong gale"
    elif speed_kmh <= 102:
        return 10, "storm"
    elif speed_kmh <= 117:
        return 11, "violent storm"
    else:
        return 12, "hurricane force"


def safe_float(value):
    """
    Convert pandas/numpy values to normal Python float or None.
    """
    try:
        if value != value:  # NaN check
            return None
        return float(value)
    except Exception:
        return None


# ============================================================
# API queries
# ============================================================

def query_sunrise_sunset(lat, lon, day, timezone):
    """
    Query sunrise/sunset for the catch date.
    """
    url = "https://api.sunrise-sunset.org/json"

    params = {
        "lat": lat,
        "lng": lon,
        "date": day,
        "formatted": 0,
    }

    response = requests.get(url, params=params, timeout=20)
    response.raise_for_status()
    data = response.json()

    if data.get("status") != "OK":
        raise RuntimeError(f"Sunrise/sunset API error: {data}")

    tz = ZoneInfo(timezone)
    results = data["results"]

    return {
        "sunrise": date_parser.isoparse(results["sunrise"]).astimezone(tz),
        "sunset": date_parser.isoparse(results["sunset"]).astimezone(tz),
        "civil_twilight_begin": date_parser.isoparse(results["civil_twilight_begin"]).astimezone(tz),
        "civil_twilight_end": date_parser.isoparse(results["civil_twilight_end"]).astimezone(tz),
    }


def query_weather_meteostat(lat, lon, catch_time):
    """
    Query observed/historical hourly weather from Meteostat.

    Meteostat columns commonly used here:
    temp = temperature in °C
    rhum = relative humidity in %
    prcp = precipitation in mm
    wdir = wind direction in degrees
    wspd = wind speed in km/h
    pres = sea-level air pressure in hPa
    coco = weather condition code
    """
    from meteostat import Point, Hourly

    # Meteostat expects naive datetime values.
    catch_time_naive = catch_time.replace(tzinfo=None)

    start = catch_time_naive.replace(minute=0, second=0, microsecond=0) - timedelta(hours=3)
    end = catch_time_naive.replace(minute=0, second=0, microsecond=0) + timedelta(hours=1)

    location = Point(lat, lon)
    data = Hourly(location, start, end)
    df = data.fetch()

    if df.empty:
        raise RuntimeError("No Meteostat hourly weather data found for this location/time.")

    target_hour = catch_time_naive.replace(minute=0, second=0, microsecond=0)

    if target_hour in df.index:
        matched_index = target_hour
    else:
        differences = abs(df.index - target_hour)
        matched_index = df.index[differences.argmin()]

    row = df.loc[matched_index]

    pressure_series = df["pres"].dropna() if "pres" in df.columns else None

    pressure_now = safe_float(row.get("pres"))

    if pressure_series is not None and len(pressure_series) >= 2:
        previous_target = matched_index - timedelta(hours=3)
        if previous_target in pressure_series.index:
            pressure_before = safe_float(pressure_series.loc[previous_target])
        else:
            previous_diffs = abs(pressure_series.index - previous_target)
            pressure_before = safe_float(pressure_series.iloc[previous_diffs.argmin()])

        if pressure_now is not None and pressure_before is not None:
            delta_pressure_3h = pressure_now - pressure_before

            if delta_pressure_3h > 1.0:
                pressure_state = "rising"
            elif delta_pressure_3h < -1.0:
                pressure_state = "falling"
            else:
                pressure_state = "stable"
        else:
            delta_pressure_3h = None
            pressure_state = "unknown"
    else:
        delta_pressure_3h = None
        pressure_state = "unknown"

    return {
        "weather_source": "meteostat",
        "matched_weather_time": matched_index.isoformat(timespec="minutes"),
        "temperature_c": safe_float(row.get("temp")),
        "relative_humidity_percent": safe_float(row.get("rhum")),
        "precipitation_mm": safe_float(row.get("prcp")),
        "wind_direction_deg": safe_float(row.get("wdir")),
        "wind_speed_kmh": safe_float(row.get("wspd")),
        "pressure_msl_hpa": pressure_now,
        "surface_pressure_hpa": None,
        "cloud_cover_percent": None,
        "weather_condition_code": safe_float(row.get("coco")),
        "pressure_state": pressure_state,
        "delta_pressure_3h_hpa": delta_pressure_3h,
    }


def query_weather_openmeteo(lat, lon, timezone, start_date, end_date):
    """
    Fallback query from Open-Meteo forecast endpoint.

    This is less ideal for old catches, but useful when Meteostat has no nearby data.
    """
    url = "https://api.open-meteo.com/v1/forecast"

    params = {
        "latitude": lat,
        "longitude": lon,
        "hourly": ",".join([
            "temperature_2m",
            "pressure_msl",
            "surface_pressure",
            "wind_speed_10m",
            "wind_direction_10m",
            "cloud_cover",
            "precipitation",
        ]),
        "timezone": timezone,
        "start_date": start_date,
        "end_date": end_date,
    }

    response = requests.get(url, params=params, timeout=20)

    if response.status_code != 200:
        print("Open-Meteo weather API error:")
        print(response.url)
        print(response.text)

    response.raise_for_status()
    return response.json()


def query_marine(lat, lon, timezone, start_date, end_date):
    """
    Query marine wave conditions from Open-Meteo Marine API for a specific date range.
    """
    url = "https://marine-api.open-meteo.com/v1/marine"

    params = {
        "latitude": lat,
        "longitude": lon,
        "hourly": ",".join([
            "wave_height",
            "wave_direction",
            "wave_period",
            "wind_wave_height",
            "swell_wave_height",
            "swell_wave_period",
        ]),
        "timezone": timezone,
        "start_date": start_date,
        "end_date": end_date,
    }

    response = requests.get(url, params=params, timeout=20)

    if response.status_code != 200:
        print("Open-Meteo marine API error:")
        print(response.url)
        print(response.text)

    response.raise_for_status()
    return response.json()


# ============================================================
# Time handling
# ============================================================

def build_catch_datetime(date_str, time_str, timezone):
    """
    Build timezone-aware catch datetime from separate date and time arguments.
    """
    tz = ZoneInfo(timezone)
    dt = date_parser.parse(f"{date_str} {time_str}")

    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=tz)
    else:
        dt = dt.astimezone(tz)

    return dt


def build_session_datetime(date_str, value, timezone):
    """
    Build timezone-aware session datetime.

    If value is only a time, use the catch date. If value includes a date,
    respect that date.
    """
    tz = ZoneInfo(timezone)

    if not value:
        return None

    if not any(ch in value for ch in ["-", "/", "T"]):
        dt = date_parser.parse(f"{date_str} {value}")
    else:
        dt = date_parser.parse(value)

    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=tz)
    else:
        dt = dt.astimezone(tz)

    return dt


def build_session_times(args, catch_time):
    if bool(args.session_start) != bool(args.session_end):
        raise ValueError("Provide both --session-start and --session-end, or neither.")

    if not args.session_start:
        if catch_time is None:
            raise ValueError("Provide --time, or provide --session-start and --session-end.")

        return None, None, None, None

    session_start = build_session_datetime(
        date_str=args.date,
        value=args.session_start,
        timezone=args.timezone,
    )
    session_end = build_session_datetime(
        date_str=args.date,
        value=args.session_end,
        timezone=args.timezone,
    )

    if session_end <= session_start:
        session_end += timedelta(days=1)

    session_duration_minutes = int((session_end - session_start).total_seconds() // 60)

    if catch_time is None:
        catch_minutes_after_start = None
    else:
        if catch_time < session_start:
            catch_time_for_offset = catch_time + timedelta(days=1)
        else:
            catch_time_for_offset = catch_time

        catch_minutes_after_start = int((catch_time_for_offset - session_start).total_seconds() // 60)

        if catch_time_for_offset > session_end:
            raise ValueError("Catch time must be inside the fishing session start/end window.")

    return (
        session_start,
        session_end,
        session_duration_minutes,
        catch_minutes_after_start,
    )


def session_midpoint(session_start, session_end):
    if session_start is None or session_end is None:
        return None

    return session_start + ((session_end - session_start) / 2)


def find_nearest_hour_index(times, target_dt):
    """
    Find the hourly API value closest to the catch time.
    """
    tz = target_dt.tzinfo

    parsed_times = [
        datetime.fromisoformat(t).replace(tzinfo=tz)
        for t in times
    ]

    differences = [
        abs((t - target_dt).total_seconds())
        for t in parsed_times
    ]

    return differences.index(min(differences))


def pressure_trend_at_index_openmeteo(weather, index):
    """
    Estimate pressure trend from pressure_msl over the previous 3 hours.
    Used only for Open-Meteo fallback.
    """
    pressure = weather["hourly"]["pressure_msl"]

    if index < 3:
        return "unknown", None

    p_now = pressure[index]
    p_before = pressure[index - 3]

    if p_now is None or p_before is None:
        return "unknown", None

    delta_p = p_now - p_before

    if delta_p > 1.0:
        state = "rising"
    elif delta_p < -1.0:
        state = "falling"
    else:
        state = "stable"

    return state, delta_p


# ============================================================
# LRF score
# ============================================================

def lrf_score(catch_time, sunrise, sunset, pressure_state, beaufort_force, wave_height):
    """
    Experimental LRF score out of 10.

    This is a practical heuristic, not a biological model.
    """
    score = 0

    sunrise_start = sunrise - timedelta(minutes=90)
    sunrise_end = sunrise + timedelta(minutes=90)

    sunset_start = sunset - timedelta(minutes=90)
    sunset_end = sunset + timedelta(minutes=120)

    # Light window
    if sunrise_start <= catch_time <= sunrise_end:
        score += 3

    if sunset_start <= catch_time <= sunset_end:
        score += 3

    # Pressure
    if pressure_state in ["stable", "falling"]:
        score += 2
    elif pressure_state == "rising":
        score += 1

    # Wind
    if beaufort_force in [2, 3]:
        score += 2
    elif beaufort_force in [1, 4]:
        score += 1

    # Wave movement
    if wave_height is not None:
        if 0.1 <= wave_height <= 0.7:
            score += 2
        elif 0.7 < wave_height <= 1.0:
            score += 1

    return min(score, 10)


# ============================================================
# Condition extraction
# ============================================================

def extract_weather_from_openmeteo(weather, catch_time):
    """
    Convert Open-Meteo weather response into the same dict style as Meteostat.
    """
    weather_index = find_nearest_hour_index(
        weather["hourly"]["time"],
        catch_time,
    )

    w = weather["hourly"]

    pressure_state, delta_pressure_3h = pressure_trend_at_index_openmeteo(weather, weather_index)

    return {
        "weather_source": "openmeteo_fallback",
        "matched_weather_time": w["time"][weather_index],
        "temperature_c": w["temperature_2m"][weather_index],
        "relative_humidity_percent": None,
        "precipitation_mm": w["precipitation"][weather_index],
        "wind_direction_deg": w["wind_direction_10m"][weather_index],
        "wind_speed_kmh": w["wind_speed_10m"][weather_index],
        "pressure_msl_hpa": w["pressure_msl"][weather_index],
        "surface_pressure_hpa": w["surface_pressure"][weather_index],
        "cloud_cover_percent": w["cloud_cover"][weather_index],
        "weather_condition_code": None,
        "pressure_state": pressure_state,
        "delta_pressure_3h_hpa": delta_pressure_3h,
    }


def extract_marine_conditions(marine, catch_time):
    """
    Extract marine conditions nearest to catch time.
    """
    if marine is None:
        return {
            "matched_marine_time": None,
            "wave_height_m": None,
            "wave_direction_deg": None,
            "wave_period_s": None,
            "wind_wave_height_m": None,
            "swell_wave_height_m": None,
            "swell_wave_period_s": None,
        }

    marine_index = find_nearest_hour_index(
        marine["hourly"]["time"],
        catch_time,
    )

    m = marine["hourly"]

    return {
        "matched_marine_time": m["time"][marine_index],
        "wave_height_m": m["wave_height"][marine_index],
        "wave_direction_deg": m["wave_direction"][marine_index],
        "wave_period_s": m["wave_period"][marine_index],
        "wind_wave_height_m": m["wind_wave_height"][marine_index],
        "swell_wave_height_m": m["swell_wave_height"][marine_index],
        "swell_wave_period_s": m["swell_wave_period"][marine_index],
    }


def extract_conditions(args, condition_time, catch_time, session, sun, weather_conditions, marine_conditions):
    wind_speed = weather_conditions["wind_speed_kmh"]
    wind_direction_deg = weather_conditions["wind_direction_deg"]

    beaufort_force, beaufort_description = wind_speed_to_beaufort(wind_speed)
    wind_cardinal = wind_direction_cardinal(wind_direction_deg)

    score = lrf_score(
        catch_time=condition_time,
        sunrise=sun["sunrise"],
        sunset=sun["sunset"],
        pressure_state=weather_conditions["pressure_state"],
        beaufort_force=beaufort_force,
        wave_height=marine_conditions["wave_height_m"],
    )

    row = {
        "record_type": "session",
        "fish": args.fish,
        "spot_name": args.spot_name or "",
        "lat": args.lat,
        "lon": args.lon,
        "timezone": args.timezone,

        "catch_count": args.catch_count,
        "has_catch": "yes" if args.catch_count > 0 else "no",
        "condition_time": condition_time.isoformat(timespec="minutes"),
        "catch_time": catch_time.isoformat(timespec="minutes") if catch_time else "",
        "session_start_time": session["start"],
        "session_end_time": session["end"],
        "session_duration_minutes": session["duration_minutes"],
        "catch_minutes_after_session_start": session["catch_minutes_after_start"],

        "weather_source": weather_conditions["weather_source"],
        "matched_weather_time": weather_conditions["matched_weather_time"],
        "matched_marine_time": marine_conditions["matched_marine_time"],

        "sunrise": sun["sunrise"].isoformat(timespec="minutes"),
        "sunset": sun["sunset"].isoformat(timespec="minutes"),
        "civil_twilight_begin": sun["civil_twilight_begin"].isoformat(timespec="minutes"),
        "civil_twilight_end": sun["civil_twilight_end"].isoformat(timespec="minutes"),

        "temperature_c": weather_conditions["temperature_c"],
        "relative_humidity_percent": weather_conditions["relative_humidity_percent"],
        "pressure_msl_hpa": weather_conditions["pressure_msl_hpa"],
        "surface_pressure_hpa": weather_conditions["surface_pressure_hpa"],
        "pressure_state": weather_conditions["pressure_state"],
        "delta_pressure_3h_hpa": weather_conditions["delta_pressure_3h_hpa"],

        "wind_speed_kmh": wind_speed,
        "wind_direction_deg": wind_direction_deg,
        "wind_direction_cardinal": wind_cardinal,
        "beaufort_force": beaufort_force,
        "beaufort_description": beaufort_description,

        "cloud_cover_percent": weather_conditions["cloud_cover_percent"],
        "precipitation_mm": weather_conditions["precipitation_mm"],
        "weather_condition_code": weather_conditions["weather_condition_code"],

        "wave_height_m": marine_conditions["wave_height_m"],
        "wave_direction_deg": marine_conditions["wave_direction_deg"],
        "wave_period_s": marine_conditions["wave_period_s"],
        "wind_wave_height_m": marine_conditions["wind_wave_height_m"],
        "swell_wave_height_m": marine_conditions["swell_wave_height_m"],
        "swell_wave_period_s": marine_conditions["swell_wave_period_s"],

        "lrf_score": score,
        "lure": args.lure or "",
        "technique": args.technique or "",
        "water_clarity": args.water_clarity or "",
        "notes": args.notes or "",
    }

    return row


# ============================================================
# CSV + printing
# ============================================================

def append_to_csv(csv_path, row):
    file_exists = os.path.exists(csv_path)

    if file_exists:
        with open(csv_path, newline="", encoding="utf-8") as f:
            reader = csv.DictReader(f)
            existing_rows = list(reader)
            existing_fields = reader.fieldnames or []

        fieldnames = list(existing_fields)

        for field in row.keys():
            if field not in fieldnames:
                fieldnames.append(field)

        if fieldnames != existing_fields:
            existing_rows.append(row)

            with open(csv_path, "w", newline="", encoding="utf-8") as f:
                writer = csv.DictWriter(f, fieldnames=fieldnames)
                writer.writeheader()
                writer.writerows(existing_rows)

            return
    else:
        fieldnames = list(row.keys())

    with open(csv_path, "a", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)

        if not file_exists:
            writer.writeheader()

        writer.writerow(row)


def fmt(value, precision=1):
    if value is None:
        return "N/A"

    if isinstance(value, float):
        return f"{value:.{precision}f}"

    return str(value)


def print_conditions(row):
    print("=" * 72)
    print("LRF CATCH CONDITIONS")
    print("=" * 72)

    print(f"Fish:       {row['fish']}")
    print(f"Catch count:{row['catch_count']}")
    print(f"Location:   {row['lat']}, {row['lon']}")
    if row["spot_name"]:
        print(f"Spot:       {row['spot_name']}")
    print(f"Condition time: {row['condition_time']}")
    if row["catch_time"]:
        print(f"Catch time: {row['catch_time']}")
    if row["session_start_time"]:
        print(f"Session:    {row['session_start_time']} - {row['session_end_time']}")
        print(f"Duration:   {row['session_duration_minutes']} min")
        if row["catch_minutes_after_session_start"] != "":
            print(f"Catch at:   {row['catch_minutes_after_session_start']} min after session start")
    print(f"Weather source:     {row['weather_source']}")
    print(f"Weather matched to: {row['matched_weather_time']}")
    print(f"Marine matched to:  {row['matched_marine_time']}")
    print()

    print("SUN")
    print("-" * 72)
    print(f"Sunrise:              {row['sunrise']}")
    print(f"Sunset:               {row['sunset']}")
    print(f"Civil twilight start: {row['civil_twilight_begin']}")
    print(f"Civil twilight end:   {row['civil_twilight_end']}")
    print()

    print("WEATHER CONDITIONS")
    print("-" * 72)
    print(f"Temperature:       {fmt(row['temperature_c'])} °C")
    print(f"Humidity:          {fmt(row['relative_humidity_percent'], 0)} %")
    print(f"Pressure MSL:      {fmt(row['pressure_msl_hpa'])} hPa")
    print(f"Surface pressure:  {fmt(row['surface_pressure_hpa'])} hPa")
    print(
        f"Pressure trend:    {row['pressure_state']} "
        f"({fmt(row['delta_pressure_3h_hpa'])} hPa / 3 h)"
    )
    print(
        f"Wind:              {fmt(row['wind_speed_kmh'])} km/h, "
        f"{row['beaufort_force']} Bf, "
        f"{row['wind_direction_cardinal']} "
        f"({fmt(row['wind_direction_deg'], 0)}°)"
    )
    print(f"Cloud cover:       {fmt(row['cloud_cover_percent'], 0)} %")
    print(f"Precipitation:     {fmt(row['precipitation_mm'])} mm")
    print(f"Weather code:      {fmt(row['weather_condition_code'], 0)}")
    print()

    print("MARINE CONDITIONS")
    print("-" * 72)
    print(f"Wave height:       {fmt(row['wave_height_m'], 2)} m")
    print(f"Wave direction:    {fmt(row['wave_direction_deg'], 0)}°")
    print(f"Wave period:       {fmt(row['wave_period_s'])} s")
    print(f"Wind-wave height:  {fmt(row['wind_wave_height_m'], 2)} m")
    print(f"Swell height:      {fmt(row['swell_wave_height_m'], 2)} m")
    print(f"Swell period:      {fmt(row['swell_wave_period_s'])} s")
    print()

    print("LRF SCORE")
    print("-" * 72)
    print(f"Experimental LRF score: {row['lrf_score']}/10")

    if row["notes"]:
        print()
        print("NOTES")
        print("-" * 72)
        print(row["notes"])

    print("=" * 72)


# ============================================================
# Main
# ============================================================

def validate_coordinates(lat, lon):
    if not (-90 <= lat <= 90):
        raise ValueError("Latitude must be between -90 and 90.")

    if not (-180 <= lon <= 180):
        raise ValueError("Longitude must be between -180 and 180.")


def main():
    parser = argparse.ArgumentParser(
        description="Log a fishing session with catch count, effort time, and environmental conditions."
    )

    parser.add_argument(
        "--fish",
        help='Fish name/species, e.g. melanouri. Optional for blank sessions; saved as "none".',
    )
    parser.add_argument("--date", required=True, help='Catch date, e.g. "2026-07-08".')
    parser.add_argument(
        "--time",
        help='Catch time, e.g. "20:00". Omit this for blank sessions with --catch-count 0.',
    )
    parser.add_argument("--lat", type=float, required=True, help="Latitude in decimal degrees.")
    parser.add_argument("--lon", type=float, required=True, help="Longitude in decimal degrees.")

    parser.add_argument("--timezone", default="Europe/Athens")
    parser.add_argument("--csv", default="lrf_catches.csv")
    parser.add_argument("--notes", default="")
    parser.add_argument("--spot-name", default="", help="Human name for this fishing spot.")
    parser.add_argument("--catch-count", type=int, default=None, help="Number of this fish caught in the session.")
    parser.add_argument("--lure", default="", help="Lure/bait used.")
    parser.add_argument("--technique", default="", help="Technique used, e.g. jighead, micro jig, dropshot.")
    parser.add_argument("--water-clarity", default="", help="Manual water clarity note, e.g. clear, stained, dirty.")
    parser.add_argument(
        "--session-start",
        help='Fishing session start time, e.g. "18:30" or "2026-07-08 18:30".',
    )
    parser.add_argument(
        "--session-end",
        help='Fishing session end time, e.g. "22:15" or "2026-07-08 22:15".',
    )

    parser.add_argument(
        "--fallback-openmeteo",
        action="store_true",
        help="Use Open-Meteo weather forecast endpoint if Meteostat has no data.",
    )

    args = parser.parse_args()

    validate_coordinates(args.lat, args.lon)

    if args.catch_count is None:
        args.catch_count = 1 if args.time else 0

    if args.catch_count < 0:
        raise ValueError("--catch-count must be zero or greater.")

    if args.catch_count > 0 and not args.time:
        raise ValueError("Provide --time when --catch-count is greater than zero.")

    if args.catch_count > 0 and not args.fish:
        raise ValueError("Provide --fish when --catch-count is greater than zero.")

    if not args.fish:
        args.fish = "none"

    catch_time = (
        build_catch_datetime(
            date_str=args.date,
            time_str=args.time,
            timezone=args.timezone,
        )
        if args.time
        else None
    )
    session_start, session_end, session_duration, catch_offset = build_session_times(
        args=args,
        catch_time=catch_time,
    )
    session = {
        "start": session_start.isoformat(timespec="minutes") if session_start else "",
        "end": session_end.isoformat(timespec="minutes") if session_end else "",
        "duration_minutes": session_duration if session_duration is not None else "",
        "catch_minutes_after_start": catch_offset if catch_offset is not None else "",
    }
    condition_time = catch_time or session_midpoint(session_start, session_end)

    start_date = condition_time.date().isoformat()
    end_date = condition_time.date().isoformat()

    sun = query_sunrise_sunset(
        lat=args.lat,
        lon=args.lon,
        day=start_date,
        timezone=args.timezone,
    )

    try:
        weather_conditions = query_weather_meteostat(
            lat=args.lat,
            lon=args.lon,
            catch_time=condition_time,
        )
    except Exception as error:
        if not args.fallback_openmeteo:
            raise RuntimeError(
                f"Meteostat weather query failed: {error}\n"
                f"Run again with --fallback-openmeteo if you want to use Open-Meteo instead."
            )

        print(f"Warning: Meteostat query failed: {error}")
        print("Using Open-Meteo weather fallback instead.")

        weather = query_weather_openmeteo(
            lat=args.lat,
            lon=args.lon,
            timezone=args.timezone,
            start_date=start_date,
            end_date=end_date,
        )

        weather_conditions = extract_weather_from_openmeteo(
            weather=weather,
            catch_time=condition_time,
        )

    try:
        marine = query_marine(
            lat=args.lat,
            lon=args.lon,
            timezone=args.timezone,
            start_date=start_date,
            end_date=end_date,
        )
    except Exception as error:
        print(f"Warning: marine data unavailable for this location/time: {error}")
        marine = None

    marine_conditions = extract_marine_conditions(
        marine=marine,
        catch_time=condition_time,
    )

    row = extract_conditions(
        args=args,
        condition_time=condition_time,
        catch_time=catch_time,
        session=session,
        sun=sun,
        weather_conditions=weather_conditions,
        marine_conditions=marine_conditions,
    )

    print_conditions(row)
    append_to_csv(args.csv, row)

    print()
    print(f"Saved to CSV: {args.csv}")


if __name__ == "__main__":
    main()
