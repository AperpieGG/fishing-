#!/usr/bin/env python3

import argparse
import csv
import math
from collections import Counter, defaultdict
from datetime import datetime, timedelta
from statistics import median
from zoneinfo import ZoneInfo

import requests


NUMERIC_FIELDS = [
    "temperature_c",
    "relative_humidity_percent",
    "pressure_msl_hpa",
    "delta_pressure_3h_hpa",
    "wind_speed_kmh",
    "wave_height_m",
    "wave_period_s",
    "swell_wave_height_m",
    "swell_wave_period_s",
]


def parse_float(value):
    if value in ("", None):
        return None

    try:
        value = float(value)
    except ValueError:
        return None

    if math.isnan(value):
        return None

    return value


def parse_int(value, default=0):
    if value in ("", None):
        return default

    try:
        return int(float(value))
    except ValueError:
        return default


def parse_datetime(value):
    if not value:
        return None

    return datetime.fromisoformat(value)


def load_session_records(csv_path, fish, spot_name=None):
    fish = fish.strip().lower()
    spot_name = spot_name.strip().lower() if spot_name else None

    with open(csv_path, newline="", encoding="utf-8") as f:
        rows = list(csv.DictReader(f))

    records = []

    for row in rows:
        if spot_name and row.get("spot_name", "").strip().lower() != spot_name:
            continue

        if not row.get("session_start_time") or not row.get("session_end_time"):
            continue

        row["_session_start"] = parse_datetime(row["session_start_time"])
        row["_session_end"] = parse_datetime(row["session_end_time"])
        row["_catch_time"] = parse_datetime(row.get("catch_time"))
        row_fish = row.get("fish", "").strip().lower()

        if row_fish == fish:
            row["_catch_count"] = parse_int(row.get("catch_count"), 1 if row.get("catch_time") else 0)
        else:
            row["_catch_count"] = 0

        if row["_session_start"] and row["_session_end"] and row["_session_end"] > row["_session_start"]:
            records.append(row)

    if not records:
        raise RuntimeError(
            f"No usable session records found for {fish}. "
            "Log real sessions with --session-start, --session-end, and --catch-count first. "
            "Old catch-only/random rows are intentionally ignored."
        )

    return records


def time_bucket(dt):
    hour = dt.hour + dt.minute / 60

    if 5 <= hour < 8.5:
        return "early morning 05:00-08:30"
    if 8.5 <= hour < 16:
        return "midday 08:30-16:00"
    if 16 <= hour < 19:
        return "late afternoon 16:00-19:00"
    if 19 <= hour < 22.75:
        return "evening/sunset 19:00-22:45"

    return "night 22:45-05:00"


def light_phase(dt, sunrise, sunset):
    if sunset - timedelta(minutes=90) <= dt <= sunset + timedelta(minutes=120):
        return "sunset window"
    if sunrise - timedelta(minutes=90) <= dt <= sunrise + timedelta(minutes=90):
        return "sunrise window"
    if dt > sunset:
        return "after sunset/night"
    if dt < sunrise:
        return "pre-sunrise/night"

    return "daylight outside windows"


def wind_direction_cardinal(degrees):
    if degrees is None:
        return "N/A"

    directions = ["N", "NE", "E", "SE", "S", "SW", "W", "NW"]
    return directions[round(degrees / 45) % 8]


def wind_speed_to_beaufort(speed_kmh):
    if speed_kmh is None:
        return None
    if speed_kmh < 1:
        return 0
    if speed_kmh <= 5:
        return 1
    if speed_kmh <= 11:
        return 2
    if speed_kmh <= 19:
        return 3
    if speed_kmh <= 28:
        return 4
    if speed_kmh <= 38:
        return 5
    if speed_kmh <= 49:
        return 6
    if speed_kmh <= 61:
        return 7
    if speed_kmh <= 74:
        return 8
    if speed_kmh <= 88:
        return 9
    if speed_kmh <= 102:
        return 10
    if speed_kmh <= 117:
        return 11

    return 12


def pressure_state(delta_pressure_3h):
    if delta_pressure_3h is None:
        return "unknown"
    if delta_pressure_3h > 1.0:
        return "rising"
    if delta_pressure_3h < -1.0:
        return "falling"
    return "stable"


def robust_profile(values):
    values = sorted(v for v in values if v is not None)

    if not values:
        return None

    center = median(values)
    distances = [abs(v - center) for v in values]
    spread = median(distances)

    if spread == 0:
        spread = max((max(values) - min(values)) / 4, 0.1)

    return {
        "center": center,
        "spread": spread,
        "min": min(values),
        "max": max(values),
    }


def numeric_similarity(value, profile):
    if value is None or profile is None:
        return None

    distance = abs(value - profile["center"])
    scale = max(profile["spread"] * 2.5, 0.1)

    return max(0.0, 1.0 - distance / scale)


def rate_score(catches, effort):
    if effort <= 0:
        return 0.0

    # Light smoothing prevents one tiny session from dominating too early.
    return (catches + 0.25) / (effort + 1.0)


def normalized_rate_score(rates, key):
    value = rates.get(key, 0.0)
    best = max(rates.values()) if rates else 0.0

    if best <= 0:
        return None

    return value / best


def next_hour_boundary(dt):
    return (dt.replace(minute=0, second=0, microsecond=0) + timedelta(hours=1))


def effort_slots(start, end):
    current = start

    while current < end:
        slot_end = min(next_hour_boundary(current), end)
        minutes = (slot_end - current).total_seconds() / 60
        midpoint = current + ((slot_end - current) / 2)

        yield midpoint, minutes / 60

        current = slot_end


def learn_model(records):
    effort_bucket = defaultdict(float)
    catch_bucket = defaultdict(float)
    effort_phase = defaultdict(float)
    catch_phase = defaultdict(float)

    effort_pressure = Counter()
    catch_pressure = Counter()
    effort_beaufort = Counter()
    catch_beaufort = Counter()
    effort_wind = Counter()
    catch_wind = Counter()

    numeric_success = defaultdict(list)
    lats = []
    lons = []
    timezones = Counter()
    total_effort_hours = 0.0
    total_catches = 0
    seen_sessions = set()

    for row in records:
        session_start = row["_session_start"]
        session_end = row["_session_end"]
        catch_time = row["_catch_time"]
        catch_count = row["_catch_count"]
        sunrise = parse_datetime(row.get("sunrise"))
        sunset = parse_datetime(row.get("sunset"))

        lat = parse_float(row.get("lat"))
        lon = parse_float(row.get("lon"))

        if lat is not None:
            lats.append(lat)
        if lon is not None:
            lons.append(lon)

        timezones[row.get("timezone", "Europe/Athens")] += 1

        session_key = (
            row.get("session_id") or "",
            row.get("spot_name") or "",
            row.get("lat") or "",
            row.get("lon") or "",
            row.get("session_start_time") or "",
            row.get("session_end_time") or "",
        )

        if session_key not in seen_sessions:
            seen_sessions.add(session_key)

            for slot_time, hours in effort_slots(session_start, session_end):
                bucket = time_bucket(slot_time)
                phase = light_phase(slot_time, sunrise, sunset) if sunrise and sunset else bucket

                effort_bucket[bucket] += hours
                effort_phase[phase] += hours
                total_effort_hours += hours

        if catch_count > 0 and catch_time:
            bucket = time_bucket(catch_time)
            phase = light_phase(catch_time, sunrise, sunset) if sunrise and sunset else bucket

            catch_bucket[bucket] += catch_count
            catch_phase[phase] += catch_count
            total_catches += catch_count

            for field in NUMERIC_FIELDS:
                numeric_success[field].append(parse_float(row.get(field)))

        pressure = row.get("pressure_state", "unknown")
        beaufort = row.get("beaufort_force", "")
        wind = row.get("wind_direction_cardinal", "N/A")

        effort_pressure[pressure] += 1
        effort_beaufort[beaufort] += 1
        effort_wind[wind] += 1

        if catch_count > 0:
            catch_pressure[pressure] += catch_count
            catch_beaufort[beaufort] += catch_count
            catch_wind[wind] += catch_count

    bucket_rates = {
        key: rate_score(catch_bucket[key], effort_bucket[key])
        for key in effort_bucket
    }
    phase_rates = {
        key: rate_score(catch_phase[key], effort_phase[key])
        for key in effort_phase
    }
    pressure_rates = {
        key: rate_score(catch_pressure[key], effort_pressure[key])
        for key in effort_pressure
    }
    beaufort_rates = {
        key: rate_score(catch_beaufort[key], effort_beaufort[key])
        for key in effort_beaufort
    }
    wind_rates = {
        key: rate_score(catch_wind[key], effort_wind[key])
        for key in effort_wind
    }

    return {
        "session_count": len(records),
        "total_catches": total_catches,
        "total_effort_hours": total_effort_hours,
        "lat": median(lats) if lats else None,
        "lon": median(lons) if lons else None,
        "timezone": timezones.most_common(1)[0][0],
        "bucket_rates": bucket_rates,
        "phase_rates": phase_rates,
        "pressure_rates": pressure_rates,
        "beaufort_rates": beaufort_rates,
        "wind_rates": wind_rates,
        "numeric_success": {
            field: robust_profile(values)
            for field, values in numeric_success.items()
        },
    }


def query_forecast(lat, lon, timezone, days):
    url = "https://api.open-meteo.com/v1/forecast"
    params = {
        "latitude": lat,
        "longitude": lon,
        "timezone": timezone,
        "forecast_days": days,
        "hourly": ",".join([
            "temperature_2m",
            "relative_humidity_2m",
            "pressure_msl",
            "wind_speed_10m",
            "wind_direction_10m",
            "cloud_cover",
            "precipitation",
        ]),
        "daily": "sunrise,sunset",
    }

    response = requests.get(url, params=params, timeout=20)
    response.raise_for_status()
    return response.json()


def query_marine(lat, lon, timezone, days):
    url = "https://marine-api.open-meteo.com/v1/marine"
    params = {
        "latitude": lat,
        "longitude": lon,
        "timezone": timezone,
        "forecast_days": days,
        "hourly": ",".join([
            "wave_height",
            "wave_direction",
            "wave_period",
            "wind_wave_height",
            "swell_wave_height",
            "swell_wave_period",
        ]),
    }

    response = requests.get(url, params=params, timeout=20)
    response.raise_for_status()
    return response.json()


def index_by_time(hourly):
    return {
        item_time: i
        for i, item_time in enumerate(hourly.get("time", []))
    }


def local_datetime(value, timezone):
    dt = parse_datetime(value)

    if dt.tzinfo is None:
        return dt.replace(tzinfo=ZoneInfo(timezone))

    return dt.astimezone(ZoneInfo(timezone))


def daily_sun_times(forecast, timezone):
    daily = forecast["daily"]
    result = {}

    for i, day in enumerate(daily["time"]):
        result[day] = {
            "sunrise": local_datetime(daily["sunrise"][i], timezone),
            "sunset": local_datetime(daily["sunset"][i], timezone),
        }

    return result


def score_hour(row, model):
    parts = []

    def add(name, value, weight):
        if value is None:
            return
        parts.append((name, value, weight))

    add("hour catch rate", normalized_rate_score(model["bucket_rates"], row["time_bucket"]), 1.8)
    add("light-phase catch rate", normalized_rate_score(model["phase_rates"], row["light_phase"]), 1.8)
    add("pressure catch rate", normalized_rate_score(model["pressure_rates"], row["pressure_state"]), 1.0)
    add("Beaufort catch rate", normalized_rate_score(model["beaufort_rates"], str(row["beaufort_force"])), 1.0)
    add("wind-direction catch rate", normalized_rate_score(model["wind_rates"], row["wind_direction_cardinal"]), 0.8)

    numeric_weights = {
        "temperature_c": 1.0,
        "relative_humidity_percent": 0.4,
        "pressure_msl_hpa": 0.5,
        "delta_pressure_3h_hpa": 0.7,
        "wind_speed_kmh": 1.0,
        "wave_height_m": 1.2,
        "wave_period_s": 0.4,
        "swell_wave_height_m": 0.4,
        "swell_wave_period_s": 0.3,
    }

    for field, weight in numeric_weights.items():
        add(field, numeric_similarity(row.get(field), model["numeric_success"].get(field)), weight)

    weighted = sum(value * weight for _, value, weight in parts)
    total_weight = sum(weight for _, _, weight in parts)
    score = 100 * weighted / total_weight if total_weight else 0
    reasons = sorted(parts, key=lambda item: item[1] * item[2], reverse=True)

    return score, reasons


def build_candidates(forecast, marine, model, timezone):
    tz = ZoneInfo(timezone)
    weather = forecast["hourly"]
    marine_hourly = marine["hourly"]
    marine_index = index_by_time(marine_hourly)
    sun_by_day = daily_sun_times(forecast, timezone)
    candidates = []

    for i, time_text in enumerate(weather["time"]):
        dt = datetime.fromisoformat(time_text).replace(tzinfo=tz)
        day = dt.date().isoformat()
        sun = sun_by_day.get(day)

        if not sun:
            continue

        pressure_now = weather["pressure_msl"][i]
        pressure_before = weather["pressure_msl"][i - 3] if i >= 3 else None
        delta_pressure = None

        if pressure_now is not None and pressure_before is not None:
            delta_pressure = pressure_now - pressure_before

        m_index = marine_index.get(time_text)
        row = {
            "time": dt,
            "time_bucket": time_bucket(dt),
            "light_phase": light_phase(dt, sun["sunrise"], sun["sunset"]),
            "temperature_c": weather["temperature_2m"][i],
            "relative_humidity_percent": weather["relative_humidity_2m"][i],
            "pressure_msl_hpa": pressure_now,
            "delta_pressure_3h_hpa": delta_pressure,
            "pressure_state": pressure_state(delta_pressure),
            "wind_speed_kmh": weather["wind_speed_10m"][i],
            "wind_direction_deg": weather["wind_direction_10m"][i],
            "cloud_cover_percent": weather["cloud_cover"][i],
            "precipitation_mm": weather["precipitation"][i],
        }

        row["wind_direction_cardinal"] = wind_direction_cardinal(row["wind_direction_deg"])
        row["beaufort_force"] = wind_speed_to_beaufort(row["wind_speed_kmh"])

        if m_index is not None:
            row.update({
                "wave_height_m": marine_hourly["wave_height"][m_index],
                "wave_period_s": marine_hourly["wave_period"][m_index],
                "swell_wave_height_m": marine_hourly["swell_wave_height"][m_index],
                "swell_wave_period_s": marine_hourly["swell_wave_period"][m_index],
            })
        else:
            row.update({
                "wave_height_m": None,
                "wave_period_s": None,
                "swell_wave_height_m": None,
                "swell_wave_period_s": None,
            })

        score, reasons = score_hour(row, model)
        row["fit_score"] = score
        row["reasons"] = reasons
        candidates.append(row)

    return candidates


def best_by_day(candidates):
    grouped = defaultdict(list)

    for item in candidates:
        grouped[item["time"].date()].append(item)

    return [
        max(items, key=lambda item: item["fit_score"])
        for _, items in sorted(grouped.items())
    ]


def fmt(value, precision=1):
    if value is None:
        return "N/A"
    if isinstance(value, float):
        return f"{value:.{precision}f}"
    return str(value)


def print_rates(title, rates):
    print(title)
    for key, value in sorted(rates.items(), key=lambda item: item[1], reverse=True):
        print(f"  {key}: {value:.2f} catches/effort-unit")
    print()


def print_model(model, fish):
    print(f"LEARNED {fish.upper()} SESSION MODEL")
    print("-" * 72)
    print(f"Real sessions: {model['session_count']}")
    print(f"Total catches: {model['total_catches']}")
    print(f"Total effort:  {model['total_effort_hours']:.1f} hours")
    print()
    print_rates("Catch rate by hour bucket", model["bucket_rates"])
    print_rates("Catch rate by light phase", model["phase_rates"])
    print_rates("Catch rate by pressure", model["pressure_rates"])
    print_rates("Catch rate by Beaufort", model["beaufort_rates"])
    print_rates("Catch rate by wind direction", model["wind_rates"])


def print_candidate(item, rank=None):
    prefix = f"{rank}. " if rank is not None else ""
    print(
        f"{prefix}{item['time'].strftime('%Y-%m-%d %H:%M')} | "
        f"fit {item['fit_score']:.0f}/100 | "
        f"{item['light_phase']} | {item['time_bucket']}"
    )
    print(
        "   "
        f"Temp {fmt(item['temperature_c'])} C, "
        f"wind {fmt(item['wind_speed_kmh'])} km/h "
        f"({item['beaufort_force']} Bf {item['wind_direction_cardinal']}), "
        f"pressure {item['pressure_state']} "
        f"({fmt(item['delta_pressure_3h_hpa'])} hPa/3h), "
        f"wave {fmt(item['wave_height_m'], 2)} m"
    )

    reason_text = ", ".join(
        name for name, value, _ in item["reasons"][:4]
        if value >= 0.35
    )
    print(f"   Best matches: {reason_text}")
    print()


def main():
    parser = argparse.ArgumentParser(
        description="Rank upcoming fishing windows from real session logs, including blanks."
    )
    parser.add_argument(
        "fish_arg",
        nargs="?",
        default=None,
        help="Fish name to analyse. Defaults to melanouri.",
    )
    parser.add_argument("--csv", default="lrf_catches.csv")
    parser.add_argument("--fish", default=None, help="Overrides the positional fish argument.")
    parser.add_argument("--lat", type=float)
    parser.add_argument("--lon", type=float)
    parser.add_argument("--timezone")
    parser.add_argument("--spot-name", default="", help="Only learn from this spot_name.")
    parser.add_argument("--days", type=int, default=7)
    parser.add_argument("--top", type=int, default=10)
    parser.add_argument("--show-model", action="store_true")

    args = parser.parse_args()
    fish = args.fish or args.fish_arg or "melanouri"

    try:
        records = load_session_records(args.csv, fish, args.spot_name)
    except RuntimeError as error:
        parser.error(str(error))

    model = learn_model(records)

    lat = args.lat if args.lat is not None else model["lat"]
    lon = args.lon if args.lon is not None else model["lon"]
    timezone = args.timezone or model["timezone"]

    if lat is None or lon is None:
        raise RuntimeError("Latitude/longitude were not supplied and could not be learned from the CSV.")

    forecast = query_forecast(lat, lon, timezone, args.days)
    marine = query_marine(lat, lon, timezone, args.days)
    candidates = build_candidates(forecast, marine, model, timezone)
    candidates = sorted(candidates, key=lambda item: item["fit_score"], reverse=True)

    print("=" * 72)
    print(f"{fish.upper()} UPCOMING WINDOWS: {lat:.5f}, {lon:.5f}, {timezone}")
    print("Scoring uses real session effort and catch counts. It ignores lrf_score.")
    print("=" * 72)
    print()

    if args.show_model:
        print_model(model, fish)

    print("BEST WINDOW EACH DAY")
    print("-" * 72)
    for item in best_by_day(candidates):
        print_candidate(item)

    print(f"TOP {args.top} WINDOWS")
    print("-" * 72)
    for rank, item in enumerate(candidates[:args.top], start=1):
        print_candidate(item, rank)

    print("Note: predictions become useful only after you log real blank and successful sessions.")


if __name__ == "__main__":
    main()
