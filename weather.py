from __future__ import annotations

import logging
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

# Conditions where you physically can't stand outdoors
_SEVERE = {75, 82, 86, 95, 96, 99}


class WeatherResult:
    def __init__(self, temp: float, code: int):
        self.temp = temp
        self.code = code
        self.description = _WMO.get(int(code), f"Code {code}")
        self.is_severe = int(code) in _SEVERE

    def __str__(self) -> str:
        return f"{self.description}, {self.temp:.0f}°C"


def get_current_weather(lat: float, lon: float, tz_name: str) -> Optional[WeatherResult]:
    try:
        r = requests.get(
            "https://api.open-meteo.com/v1/forecast",
            params={
                "latitude": lat, "longitude": lon,
                "current": "temperature_2m,weather_code",
                "timezone": tz_name, "forecast_days": 1,
            },
            timeout=10,
        )
        d = r.json()["current"]
        return WeatherResult(float(d["temperature_2m"]), int(d["weather_code"]))
    except Exception as exc:
        log.warning("Current weather fetch failed: %s", exc)
        return None


def get_forecast_weather(lat: float, lon: float, tz_name: str, day_offset: int = 1) -> Optional[WeatherResult]:
    """Fetch daily forecast. day_offset=0 is today, 1 is tomorrow."""
    try:
        r = requests.get(
            "https://api.open-meteo.com/v1/forecast",
            params={
                "latitude": lat, "longitude": lon,
                "daily": "weather_code,temperature_2m_max",
                "timezone": tz_name, "forecast_days": day_offset + 1,
            },
            timeout=10,
        )
        d = r.json()["daily"]
        return WeatherResult(float(d["temperature_2m_max"][day_offset]), int(d["weather_code"][day_offset]))
    except Exception as exc:
        log.warning("Forecast weather fetch failed: %s", exc)
        return None
