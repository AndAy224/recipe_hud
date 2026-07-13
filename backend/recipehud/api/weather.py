"""Weather for the idle clock via Open-Meteo (free, no API key).
Cached in memory for 15 minutes; stale data is served if the API is down."""

import datetime
import logging
import time

import httpx
from fastapi import APIRouter, Query, Request

log = logging.getLogger(__name__)

router = APIRouter(prefix="/api/weather", tags=["weather"])

FORECAST_URL = "https://api.open-meteo.com/v1/forecast"
GEOCODE_URL = "https://geocoding-api.open-meteo.com/v1/search"
CACHE_TTL_S = 900

# key: weather_location string -> (payload, monotonic timestamp)
_cache: dict[str, tuple[dict, float]] = {}

# WMO weather codes -> (day emoji, night emoji, description)
WMO = {
    0: ("☀️", "🌙", "Clear"),
    1: ("🌤", "🌙", "Mainly clear"),
    2: ("⛅", "☁️", "Partly cloudy"),
    3: ("☁️", "☁️", "Overcast"),
    45: ("🌫", "🌫", "Fog"),
    48: ("🌫", "🌫", "Rime fog"),
    51: ("🌦", "🌧", "Light drizzle"),
    53: ("🌦", "🌧", "Drizzle"),
    55: ("🌦", "🌧", "Heavy drizzle"),
    56: ("🌦", "🌧", "Freezing drizzle"),
    57: ("🌦", "🌧", "Freezing drizzle"),
    61: ("🌧", "🌧", "Light rain"),
    63: ("🌧", "🌧", "Rain"),
    65: ("🌧", "🌧", "Heavy rain"),
    66: ("🌧", "🌧", "Freezing rain"),
    67: ("🌧", "🌧", "Freezing rain"),
    71: ("🌨", "🌨", "Light snow"),
    73: ("🌨", "🌨", "Snow"),
    75: ("🌨", "🌨", "Heavy snow"),
    77: ("🌨", "🌨", "Snow grains"),
    80: ("🌦", "🌧", "Rain showers"),
    81: ("🌦", "🌧", "Rain showers"),
    82: ("🌧", "🌧", "Heavy showers"),
    85: ("🌨", "🌨", "Snow showers"),
    86: ("🌨", "🌨", "Snow showers"),
    95: ("⛈", "⛈", "Thunderstorm"),
    96: ("⛈", "⛈", "Thunderstorm, hail"),
    99: ("⛈", "⛈", "Thunderstorm, hail"),
}


@router.get("")
async def get_weather(request: Request):
    location = str(request.app.state.store.get("weather_location")).strip()
    if "," not in location:
        return {"configured": False}
    try:
        lat, lon = (float(part) for part in location.split(",", 1))
    except ValueError:
        log.warning("weather_location %r is not 'lat,lon'", location)
        return {"configured": False}

    cached = _cache.get(location)
    if cached and time.monotonic() - cached[1] < CACHE_TTL_S:
        return cached[0]
    try:
        async with httpx.AsyncClient(timeout=10) as client:
            resp = await client.get(FORECAST_URL, params={
                "latitude": lat,
                "longitude": lon,
                "current": "temperature_2m,weather_code,is_day",
                "daily": "temperature_2m_max,temperature_2m_min,weather_code,"
                         "precipitation_probability_max",
                "forecast_days": 4,
                "timezone": "auto",
                "temperature_unit": "fahrenheit",
            })
            resp.raise_for_status()
            raw = resp.json()
    except Exception as exc:
        log.warning("weather fetch failed: %s", exc)
        if cached:
            return cached[0]  # stale beats nothing on the clock screen
        return {"configured": True, "error": "unavailable"}

    current = raw["current"]
    is_day = bool(current.get("is_day", 1))
    day_emoji, night_emoji, description = WMO.get(current["weather_code"], ("🌡", "🌡", ""))
    daily = raw["daily"]
    forecast = []
    for i, date in enumerate(daily["time"]):
        d_emoji, _, d_description = WMO.get(daily["weather_code"][i], ("🌡", "🌡", ""))
        forecast.append({
            "date": date,
            "dow": datetime.date.fromisoformat(date).strftime("%a"),
            "high": daily["temperature_2m_max"][i],
            "low": daily["temperature_2m_min"][i],
            "code": daily["weather_code"][i],
            "emoji": d_emoji,
            "description": d_description,
            "precip_pct": daily["precipitation_probability_max"][i],
        })
    payload = {
        "configured": True,
        "temp": current["temperature_2m"],
        "high": raw["daily"]["temperature_2m_max"][0],
        "low": raw["daily"]["temperature_2m_min"][0],
        "unit": "°F",
        "is_day": is_day,
        "code": current["weather_code"],
        "emoji": day_emoji if is_day else night_emoji,
        "description": description,
        "daily": forecast,
        "fetched_at": datetime.datetime.now(datetime.timezone.utc).isoformat(),
    }
    _cache[location] = (payload, time.monotonic())
    return payload


@router.get("/geocode")
async def geocode(q: str = Query(min_length=2)):
    try:
        async with httpx.AsyncClient(timeout=10) as client:
            resp = await client.get(GEOCODE_URL, params={
                "name": q, "count": 5, "language": "en", "format": "json",
            })
            resp.raise_for_status()
            raw = resp.json()
    except Exception as exc:
        log.warning("geocode failed: %s", exc)
        return []
    # Open-Meteo omits "results" entirely when nothing matches.
    return [
        {
            "name": r["name"],
            "admin1": r.get("admin1", ""),
            "country": r.get("country", ""),
            "latitude": r["latitude"],
            "longitude": r["longitude"],
        }
        for r in raw.get("results", [])
    ]
