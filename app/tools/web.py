"""External API tools (free, keyless services): Open-Meteo weather and a dictionary."""

from __future__ import annotations

import re
from typing import Literal

import httpx
from pydantic import BaseModel, ConfigDict, Field, field_validator

from .base import ToolContext, ToolFailure, ToolResult, register

WEATHER_CODES = {
    0: "clear sky", 1: "mainly clear", 2: "partly cloudy", 3: "overcast",
    45: "fog", 48: "freezing fog", 51: "light drizzle", 53: "drizzle", 55: "heavy drizzle",
    61: "light rain", 63: "rain", 65: "heavy rain", 66: "freezing rain", 67: "heavy freezing rain",
    71: "light snow", 73: "snow", 75: "heavy snow", 77: "snow grains",
    80: "light showers", 81: "showers", 82: "violent showers", 85: "snow showers", 86: "heavy snow showers",
    95: "thunderstorm", 96: "thunderstorm with hail", 99: "severe thunderstorm with hail",
}


class _Args(BaseModel):
    model_config = ConfigDict(extra="forbid")


class WeatherArgs(_Args):
    city: str = Field(min_length=2, max_length=80)
    days: int = Field(default=1, ge=1, le=3)
    units: Literal["fahrenheit", "celsius"] = "fahrenheit"


class DefineArgs(_Args):
    word: str = Field(min_length=1, max_length=40)

    @field_validator("word")
    @classmethod
    def _letters(cls, v: str) -> str:
        v = v.strip().lower()
        if not re.fullmatch(r"[a-z][a-z' -]*", v):
            raise ValueError("must be a single English word or short phrase")
        return v


async def _get_json(ctx: ToolContext, url: str, params: dict | None = None, service: str = "service"):
    if ctx.http is None:
        raise ToolFailure("Web access isn't available in this environment.")
    try:
        resp = await ctx.http.get(url, params=params, timeout=10)
    except httpx.TimeoutException:
        raise ToolFailure(f"The {service} timed out. Try again in a moment.") from None
    except httpx.HTTPError:
        raise ToolFailure(f"Couldn't reach the {service}.") from None
    return resp


@register(
    "get_weather",
    "web",
    "Current weather and a 1-3 day forecast for a city (Open-Meteo API).",
    WeatherArgs,
)
async def get_weather(ctx: ToolContext, args: WeatherArgs) -> ToolResult:
    # Geocoding works best with just the city name ("Pomona, CA" -> "Pomona").
    name = args.city.split(",")[0].strip()
    geo = await _get_json(
        ctx,
        "https://geocoding-api.open-meteo.com/v1/search",
        {"name": name, "count": 1, "language": "en", "format": "json"},
        "geocoding service",
    )
    if geo.status_code != 200:
        raise ToolFailure(f"The geocoding service returned an error ({geo.status_code}).")
    results = geo.json().get("results") or []
    if not results:
        raise ToolFailure(f"Couldn't find a place called '{args.city}'.")
    place = results[0]

    temp_unit = args.units
    wind_unit = "mph" if args.units == "fahrenheit" else "kmh"
    fc = await _get_json(
        ctx,
        "https://api.open-meteo.com/v1/forecast",
        {
            "latitude": place["latitude"],
            "longitude": place["longitude"],
            "current": "temperature_2m,apparent_temperature,weather_code,wind_speed_10m,relative_humidity_2m",
            "daily": "weather_code,temperature_2m_max,temperature_2m_min,precipitation_probability_max",
            "temperature_unit": temp_unit,
            "wind_speed_unit": wind_unit,
            "timezone": "auto",
            "forecast_days": args.days,
        },
        "weather service",
    )
    if fc.status_code != 200:
        raise ToolFailure(f"The weather service returned an error ({fc.status_code}).")
    data = fc.json()
    cur = data.get("current", {})
    daily = data.get("daily", {})
    deg = "°F" if args.units == "fahrenheit" else "°C"
    location = ", ".join(x for x in (place.get("name"), place.get("admin1"), place.get("country")) if x)

    forecast = []
    for i, day in enumerate(daily.get("time", [])):
        forecast.append(
            {
                "date": day,
                "conditions": WEATHER_CODES.get(daily["weather_code"][i], "unknown"),
                "high": f"{daily['temperature_2m_max'][i]}{deg}",
                "low": f"{daily['temperature_2m_min'][i]}{deg}",
                "chance_of_rain": f"{daily['precipitation_probability_max'][i]}%",
            }
        )
    result = {
        "location": location,
        "now": {
            "temperature": f"{cur.get('temperature_2m')}{deg}",
            "feels_like": f"{cur.get('apparent_temperature')}{deg}",
            "conditions": WEATHER_CODES.get(cur.get("weather_code"), "unknown"),
            "humidity": f"{cur.get('relative_humidity_2m')}%",
            "wind": f"{cur.get('wind_speed_10m')} {wind_unit}",
        },
        "forecast": forecast,
    }
    summary = f"{location}: {result['now']['temperature']}, {result['now']['conditions']}."
    return ToolResult(True, summary, result)


@register("define_word", "web", "Look up dictionary definitions of an English word.", DefineArgs)
async def define_word(ctx: ToolContext, args: DefineArgs) -> ToolResult:
    resp = await _get_json(
        ctx, f"https://api.dictionaryapi.dev/api/v2/entries/en/{args.word}", service="dictionary service"
    )
    if resp.status_code == 404:
        raise ToolFailure(f"No dictionary entry for '{args.word}'.")
    if resp.status_code != 200:
        raise ToolFailure(f"The dictionary service returned an error ({resp.status_code}).")
    entries = resp.json()
    meanings = []
    for entry in entries[:2]:
        for meaning in entry.get("meanings", []):
            for d in meaning.get("definitions", [])[:2]:
                meanings.append({"part_of_speech": meaning.get("partOfSpeech"), "definition": d.get("definition")})
                if len(meanings) >= 4:
                    break
            if len(meanings) >= 4:
                break
    if not meanings:
        raise ToolFailure(f"No definitions found for '{args.word}'.")
    return ToolResult(True, f"Found {len(meanings)} definition(s) of '{args.word}'.", {"word": args.word, "meanings": meanings})
