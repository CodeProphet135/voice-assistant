"""Tests for the concrete tools (Phase 4). Weather is fully mocked with respx;
notes need Postgres (skip if unreachable); timers use a real Session with fake
WS/TTS and sub-tick durations."""

import httpx
import pytest
import respx

from voice_assistant.agent.tools.weather import get_weather

_GEOCODE = "https://geocoding-api.open-meteo.com/v1/search"
_FORECAST = "https://api.open-meteo.com/v1/forecast"


@respx.mock
async def test_get_weather_returns_spoken_summary():
    respx.get(_GEOCODE).mock(
        return_value=httpx.Response(
            200,
            json={"results": [{"latitude": 35.68, "longitude": 139.69, "name": "Tokyo"}]},
        )
    )
    respx.get(_FORECAST).mock(
        return_value=httpx.Response(
            200, json={"current": {"temperature_2m": 18.2, "weather_code": 2}}
        )
    )
    out = await get_weather(None, city="Tokyo")
    assert "Tokyo" in out
    assert "18" in out
    assert "partly cloudy" in out


@respx.mock
async def test_get_weather_unknown_city_is_graceful():
    respx.get(_GEOCODE).mock(return_value=httpx.Response(200, json={}))
    out = await get_weather(None, city="Nowhereville")
    assert "couldn't find" in out.lower()
    assert "Nowhereville" in out


@respx.mock
async def test_get_weather_http_error_raises():
    respx.get(_GEOCODE).mock(return_value=httpx.Response(500))
    with pytest.raises(httpx.HTTPStatusError):
        await get_weather(None, city="Tokyo")
