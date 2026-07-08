#!/usr/bin/env python3

import argparse
import requests
from datetime import datetime, date, timedelta
from zoneinfo import ZoneInfo
from dateutil import parser as date_parser


def query_sunrise_sunset(lat, lon, day, timezone):
    """
    Query sunrise and sunset times from sunrise-sunset.org.
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

    results = data["results"]

    tz = ZoneInfo(timezone)

    sunrise = date_parser.isoparse(results["sunrise"]).astimezone(tz)
    sunset = date_parser.isoparse(results["sunset"]).astimezone(tz)
    civil_twilight_begin = date_parser.isoparse(results["civil_twilight_begin"]).astimezone(tz)
    civil_twilight_end = date_parser.isoparse(results["civil_twilight_end"]).astimezone(tz)

    return {
        "sunrise": sunrise,
        "sunset": sunset,
        "civil_twilight_begin": civil_twilight_begin,
        "civil_twilight_end": civil_twilight_end,
    }


def query_weather(lat, lon, timezone):
    """
    Query pressure, wind, and temperature from Open-Meteo.
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
        ]),
        "timezone": timezone,
        "forecast_days": 2,
    }

    response = requests.get(url, params=params, timeout=20)
    response.raise_for_status()

    return response.json()


def query_marine(lat, lon, timezone):
    """
    Query wave conditions from Open-Meteo Marine API.
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
        "forecast_days": 2,
    }

    response = requests.get(url, params=params, timeout=20)
    response.raise_for_status()

    return response.json()


def find_nearest_hour_index(times, target_dt):
    """
    Find the index of the hourly time closest to target_dt.
    """
    parsed_times = [
        datetime.fromisoformat(t).replace(tzinfo=target_dt.tzinfo)
        for t in times
    ]

    differences = [abs((t - target_dt).total_seconds()) for t in parsed_times]

    return differences.index(min(differences))


def pressure_trend(weather, now_index):
    """
    Estimate pressure trend using pressure_msl over approximately 3 hours.
    """
    pressure = weather["hourly"]["pressure_msl"]

    if now_index < 3:
        return "not enough data"

    p_now = pressure[now_index]
    p_before = pressure[now_index - 3]

    delta_p = p_now - p_before

    if delta_p > 1.0:
        trend = "rising"
    elif delta_p < -1.0:
        trend = "falling"
    else:
        trend = "stable"

    return trend, delta_p


def lrf_score(

    now,

    sunrise,

    sunset,

    pressure_state,

    beaufort_force,

    wave_height,

):

    """

    Experimental LRF score out of 10 for Greek shore fishing.

    """

    score = 0

    sunrise_window_start = sunrise - timedelta(minutes=90)

    sunrise_window_end = sunrise + timedelta(minutes=90)

    sunset_window_start = sunset - timedelta(minutes=90)

    sunset_window_end = sunset + timedelta(minutes=120)

    # Light windows

    if sunrise_window_start <= now <= sunrise_window_end:

        score += 3

    if sunset_window_start <= now <= sunset_window_end:

        score += 3

    # Pressure trend

    if pressure_state == "stable":

        score += 2

    elif pressure_state == "falling":

        score += 2

    elif pressure_state == "rising":

        score += 1

    # Wind in Beaufort

    if beaufort_force in [2, 3]:

        score += 2

    elif beaufort_force in [1, 4]:

        score += 1

    # Wave height

    if 0.1 <= wave_height <= 0.7:

        score += 2

    elif 0.7 < wave_height <= 1.0:

        score += 1

    return min(score, 10)


def wind_direction_cardinal(degrees):
    """
    Convert wind direction in degrees to cardinal direction.

    Meteorological convention:
    0°   = wind coming from North
    90°  = wind coming from East
    180° = wind coming from South
    270° = wind coming from West
    """
    directions = [
        "N", "NE", "E", "SE",
        "S", "SW", "W", "NW"
    ]

    index = round(degrees / 45) % 8

    return directions[index]


def wind_speed_to_beaufort(speed_kmh):
    """
    Convert wind speed from km/h to Beaufort force.
    """
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


def print_report(args, sun, weather, marine):
    tz = ZoneInfo(args.timezone)
    now = datetime.now(tz)

    weather_times = weather["hourly"]["time"]
    weather_index = find_nearest_hour_index(weather_times, now)

    marine_times = marine["hourly"]["time"]
    marine_index = find_nearest_hour_index(marine_times, now)

    hourly_weather = weather["hourly"]
    hourly_marine = marine["hourly"]

    temperature = hourly_weather["temperature_2m"][weather_index]
    pressure_msl = hourly_weather["pressure_msl"][weather_index]
    surface_pressure = hourly_weather["surface_pressure"][weather_index]
    wind_speed = hourly_weather["wind_speed_10m"][weather_index]
    wind_direction = hourly_weather["wind_direction_10m"][weather_index]
    wind_cardinal = wind_direction_cardinal(wind_direction)
    beaufort_force, beaufort_description = wind_speed_to_beaufort(wind_speed)
    cloud_cover = hourly_weather["cloud_cover"][weather_index]

    wave_height = hourly_marine["wave_height"][marine_index]
    wave_direction = hourly_marine["wave_direction"][marine_index]
    wave_period = hourly_marine["wave_period"][marine_index]
    wind_wave_height = hourly_marine["wind_wave_height"][marine_index]
    swell_wave_height = hourly_marine["swell_wave_height"][marine_index]
    swell_wave_period = hourly_marine["swell_wave_period"][marine_index]

    p_trend = pressure_trend(weather, weather_index)

    if isinstance(p_trend, tuple):
        pressure_state, delta_p = p_trend
        pressure_text = f"{pressure_state} ({delta_p:+.1f} hPa over ~3 h)"
    else:
        pressure_state = "unknown"
        pressure_text = p_trend

    score = lrf_score(
        now=now,
        sunrise=sun["sunrise"],
        sunset=sun["sunset"],
        pressure_state=pressure_state,
        beaufort_force=beaufort_force,
        wave_height=wave_height,
    )

    sunrise_start = sun["sunrise"] - timedelta(minutes=90)
    sunrise_end = sun["sunrise"] + timedelta(minutes=90)

    sunset_start = sun["sunset"] - timedelta(minutes=90)
    sunset_end = sun["sunset"] + timedelta(minutes=120)

    print("=" * 60)
    print("LRF CONDITIONS REPORT")
    print("=" * 60)

    print(f"Location: {args.name}")
    print(f"Latitude:  {args.lat}")
    print(f"Longitude: {args.lon}")
    print(f"Timezone:  {args.timezone}")
    print(f"Date/time: {now.strftime('%Y-%m-%d %H:%M')}")
    print()

    print("SUN")
    print("-" * 60)
    print(f"Civil twilight begins: {sun['civil_twilight_begin'].strftime('%H:%M')}")
    print(f"Sunrise:               {sun['sunrise'].strftime('%H:%M')}")
    print(f"Sunset:                {sun['sunset'].strftime('%H:%M')}")
    print(f"Civil twilight ends:   {sun['civil_twilight_end'].strftime('%H:%M')}")
    print()

    print("BEST LIGHT WINDOWS")
    print("-" * 60)
    print(f"Sunrise window: {sunrise_start.strftime('%H:%M')} - {sunrise_end.strftime('%H:%M')}")
    print(f"Sunset window:  {sunset_start.strftime('%H:%M')} - {sunset_end.strftime('%H:%M')}")
    print()

    print("WEATHER")
    print("-" * 60)
    print(f"Temperature:      {temperature:.1f} °C")
    print(f"Pressure MSL:     {pressure_msl:.1f} hPa")
    print(f"Surface pressure: {surface_pressure:.1f} hPa")
    print(f"Pressure trend:   {pressure_text}")
    print(f"Wind speed:       {wind_speed:.1f} km/h")
    print(f"Wind force:       {beaufort_force} Bf, {beaufort_description}")
    print(f"Wind direction:   {wind_direction:.0f}° ({wind_cardinal})")
    print(f"Cloud cover:      {cloud_cover:.0f} %")
    print()

    print("MARINE")
    print("-" * 60)
    print(f"Wave height:       {wave_height:.2f} m")
    print(f"Wave direction:    {wave_direction:.0f}°")
    print(f"Wave period:       {wave_period:.1f} s")
    print(f"Wind-wave height:  {wind_wave_height:.2f} m")
    print(f"Swell height:      {swell_wave_height:.2f} m")
    print(f"Swell period:      {swell_wave_period:.1f} s")
    print()

    print("LRF INTERPRETATION")
    print("-" * 60)
    print(f"Experimental LRF score: {score}/10")

    if score >= 7:
        print("Conditions look good. Prioritise rocks, harbour lights, and moving water.")
    elif score >= 4:
        print("Conditions are moderate. You can fish, but choose the spot carefully.")
    else:
        print("Conditions look weak. Fish sunrise/sunset, deeper structure, or harbour lights.")

    print()
    print("Note: This score is a simple heuristic, not a biological model.")
    print("For Greece, wind/wave movement and light windows are usually more useful than tide height.")
    print("=" * 60)

    next_24h = score_next_24_hours(
        sun=sun,
        weather=weather,
        marine=marine,
        timezone=args.timezone,
    )

    print_next_24h_scores(next_24h)


def score_next_24_hours(sun, weather, marine, timezone):
    tz = ZoneInfo(timezone)
    now = datetime.now(tz)
    end_time = now + timedelta(hours=24)

    results = []

    weather_times = weather["hourly"]["time"]
    marine_times = marine["hourly"]["time"]

    for i, t_str in enumerate(weather_times):
        t = datetime.fromisoformat(t_str).replace(tzinfo=tz)

        if not (now <= t <= end_time):
            continue

        marine_i = find_nearest_hour_index(marine_times, t)

        wind_speed = weather["hourly"]["wind_speed_10m"][i]
        wind_direction = weather["hourly"]["wind_direction_10m"][i]
        pressure = weather["hourly"]["pressure_msl"][i]
        wave_height = marine["hourly"]["wave_height"][marine_i]

        beaufort_force, beaufort_description = wind_speed_to_beaufort(wind_speed)
        wind_cardinal = wind_direction_cardinal(wind_direction)

        if i >= 3:
            delta_p = pressure - weather["hourly"]["pressure_msl"][i - 3]
            if delta_p > 1.0:
                pressure_state = "rising"
            elif delta_p < -1.0:
                pressure_state = "falling"
            else:
                pressure_state = "stable"
        else:
            delta_p = 0.0
            pressure_state = "unknown"

        score = lrf_score(
            now=t,
            sunrise=sun["sunrise"],
            sunset=sun["sunset"],
            pressure_state=pressure_state,
            beaufort_force=beaufort_force,
            wave_height=wave_height,
        )

        results.append({
            "time": t,
            "score": score,
            "wind_speed": wind_speed,
            "wind_direction_deg": wind_direction,
            "wind_direction_cardinal": wind_cardinal,
            "beaufort": beaufort_force,
            "beaufort_description": beaufort_description,
            "pressure": pressure,
            "pressure_state": pressure_state,
            "delta_p": delta_p,
            "wave_height": wave_height,
        })

    return results


def print_next_24h_scores(results, top_n=8):
    print()
    print("NEXT 24 HOURS — BEST LRF WINDOWS")
    print("-" * 60)

    sorted_results = sorted(results, key=lambda x: x["score"], reverse=True)

    for r in sorted_results[:top_n]:
        print(
            f"{r['time'].strftime('%Y-%m-%d %H:%M')} | "
            f"Score: {r['score']}/10 | "
            f"Wind: {r['wind_speed']:.1f} km/h, "
            f"{r['beaufort']} Bf, "
            f"{r['wind_direction_cardinal']} | "
            f"Wave: {r['wave_height']:.2f} m | "
            f"Pressure: {r['pressure_state']} "
            f"({r['delta_p']:+.1f} hPa/3h)"
        )


def main():
    parser = argparse.ArgumentParser(
        description="Query LRF fishing conditions: sunrise/sunset, pressure, wind, and marine waves."
    )

    parser.add_argument("--name", default="Athens, Greece")
    parser.add_argument("--lat", type=float, default=37.9838)
    parser.add_argument("--lon", type=float, default=23.7275)
    parser.add_argument("--timezone", default="Europe/Athens")

    args = parser.parse_args()

    today = date.today().isoformat()

    sun = query_sunrise_sunset(args.lat, args.lon, today, args.timezone)
    weather = query_weather(args.lat, args.lon, args.timezone)
    marine = query_marine(args.lat, args.lon, args.timezone)

    print_report(args, sun, weather, marine)


if __name__ == "__main__":
    main()
