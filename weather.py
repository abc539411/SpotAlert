from __future__ import annotations

import logging
import time
from typing import Optional

import requests

log = logging.getLogger(__name__)

_WMO = {
    0: "Clear sky", 1: "Mainly clear", 2: "Partly cloudy", 3: "Overcast",
    45: "Fog", 48: "Icy fog",
    51: "Light drizzle", 53: "Drizzle", 55: "Heavy drizzle",
    61: "Light rain", 63: "Rain", 65: "Heavy rain",
    71: "Light snow", 73: "Snow", 75: "Heavy snow", 77: "Snow grains",
    80: "Light showers", 81: "Showers", 82: "Heavy showers",
    85: "Light snow showers", 86: "Heavy snow showers",
    95: "Thunderstorm", 96: "Thunderstorm with hail", 99: "Thunderstorm with heavy hail",
}

_WMO_EMOJI = {
    0: "☀️", 1: "🌤", 2: "⛅", 3: "☁️",
    45: "🌫", 48: "🌫",
    51: "🌦", 53: "🌦", 55: "🌧",
    61: "🌧", 63: "🌧", 65: "🌧",
    71: "🌨", 73: "❄️", 75: "❄️", 77: "🌨",
    80: "🌦", 81: "🌧", 82: "🌧",
    85: "🌨", 86: "🌨",
    95: "⛈", 96: "⛈", 99: "⛈",
}

# Conditions where you physically can't stand outdoors
_SEVERE = {75, 82, 86, 95, 96, 99}


class WeatherResult:
    def __init__(self, temp: float, code: int):
        self.temp = temp
        self.code = code
        self.description = _WMO.get(int(code), f"Code {code}")
        self.is_severe = int(code) in _SEVERE

    def __str__(self) -> str:
        emoji = _WMO_EMOJI.get(int(self.code), "")
        prefix = f"{emoji} " if emoji else ""
        return f"{prefix}{self.description}, {self.temp:.0f}°C"


def get_current_weather(lat: float, lon: float, tz_name: str) -> Optional[WeatherResult]:
    params = {
        "latitude": lat, "longitude": lon,
        "current": "temperature_2m,weather_code",
        "timezone": tz_name, "forecast_days": 1,
    }
    for attempt in range(1, 3):
        try:
            r = requests.get("https://api.open-meteo.com/v1/forecast", params=params, timeout=10)
            r.raise_for_status()
            d = r.json()["current"]
            return WeatherResult(float(d["temperature_2m"]), int(d["weather_code"]))
        except Exception as exc:
            log.warning("Current weather fetch failed (attempt %d/2): %s", attempt, exc)
            if attempt < 2:
                time.sleep(5)
    return None


def get_forecast_weather(lat: float, lon: float, tz_name: str, day_offset: int = 1) -> Optional[WeatherResult]:
    """Fetch daily forecast. day_offset=0 is today, 1 is tomorrow."""
    params = {
        "latitude": lat, "longitude": lon,
        "daily": "weather_code,temperature_2m_max",
        "timezone": tz_name, "forecast_days": day_offset + 1,
    }
    for attempt in range(1, 3):
        try:
            r = requests.get("https://api.open-meteo.com/v1/forecast", params=params, timeout=10)
            r.raise_for_status()
            d = r.json()["daily"]
            return WeatherResult(float(d["temperature_2m_max"][day_offset]), int(d["weather_code"][day_offset]))
        except Exception as exc:
            log.warning("Forecast weather fetch failed (attempt %d/2): %s", attempt, exc)
            if attempt < 2:
                time.sleep(5)
    return None
