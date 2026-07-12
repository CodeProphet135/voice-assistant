"""``get_weather`` — Open-Meteo geocoding + current forecast (keyless, no auth).

Two GETs (a fresh ``httpx.AsyncClient`` per call so nothing is left open across
requests): geocode the city to lat/lon, then fetch the current temperature and
WMO weather code. Returns a short spoken-style summary the model relays. A
missing geocoding match returns a normal string (the model should relay it
conversationally); an HTTP failure raises so ``agent/agent.py`` maps it to an
``Error: ...`` output like any other tool failure.
"""

import httpx

from voice_assistant.agent.tools.registry import tool

_GEOCODE_URL = "https://geocoding-api.open-meteo.com/v1/search"
_FORECAST_URL = "https://api.open-meteo.com/v1/forecast"

# WMO weather codes collapsed to ~spoken buckets (not all 27 need distinct copy).
_WMO: dict[int, str] = {
    0: "clear",
    1: "mainly clear",
    2: "partly cloudy",
    3: "overcast",
    45: "foggy",
    48: "foggy",
    51: "drizzly",
    53: "drizzly",
    55: "drizzly",
    61: "rainy",
    63: "rainy",
    65: "raining heavily",
    66: "sleeting",
    67: "sleeting",
    71: "snowy",
    73: "snowy",
    75: "snowing heavily",
    77: "snowy",
    80: "showery",
    81: "showery",
    82: "stormy with heavy showers",
    85: "snow-showery",
    86: "snow-showery",
    95: "thundery",
    96: "thundery with hail",
    99: "thundery with hail",
}


@tool(
    name="get_weather",
    description="Get the current weather for a city by name.",
    parameters={
        "type": "object",
        "properties": {
            "city": {"type": "string", "description": "City name, e.g. 'Tokyo'."}
        },
        "required": ["city"],
        "additionalProperties": False,
    },
)
async def get_weather(ctx, *, city: str) -> str:
    async with httpx.AsyncClient(timeout=10.0) as client:
        geo = await client.get(_GEOCODE_URL, params={"name": city, "count": 1})
        geo.raise_for_status()
        results = geo.json().get("results")
        if not results:
            return f"I couldn't find a city called {city}."

        place = results[0]
        forecast = await client.get(
            _FORECAST_URL,
            params={
                "latitude": place["latitude"],
                "longitude": place["longitude"],
                "current": "temperature_2m,weather_code",
            },
        )
        forecast.raise_for_status()
        current = forecast.json()["current"]

    temp = round(current["temperature_2m"])
    desc = _WMO.get(current["weather_code"], "hard to describe")
    return f"It's currently {temp} degrees Celsius and {desc} in {place['name']}."
