"""Classification of SSE error events (task 7)."""
from __future__ import annotations

from custom_components.homeassistantalarm.api import (
    GroupAlarmAuthError,
    GroupAlarmNotFoundError,
    GroupAlarmRateLimitError,
    GroupAlarmStreamRejectedError,
    classify_stream_error,
)

from .conftest import RATE_LIMIT_TEXT


def test_classify_stream_error() -> None:
    # texts as sent by HomeAssistantAccess.authenticate on the AlarmServer
    assert isinstance(classify_stream_error(RATE_LIMIT_TEXT), GroupAlarmRateLimitError)
    assert isinstance(classify_stream_error("Ungültiger API-Key"), GroupAlarmAuthError)
    assert isinstance(
        classify_stream_error("Authorization: Bearer <api_key> Header fehlt"),
        GroupAlarmAuthError,
    )
    assert isinstance(
        classify_stream_error("Verbindung nicht gefunden"), GroupAlarmNotFoundError
    )
    assert isinstance(
        classify_stream_error("Ungültige UUID im Pfad"), GroupAlarmNotFoundError
    )
    # unknown wording: no reauth, the next /poll classifies by HTTP status
    assert isinstance(classify_stream_error("irgendwas"), GroupAlarmStreamRejectedError)
    assert not isinstance(classify_stream_error("irgendwas"), GroupAlarmAuthError)


def test_normalize_base_url() -> None:
    from custom_components.homeassistantalarm.api import normalize_base_url

    root = "https://alarm.feuerwehr45.de"
    uuid = "3f9c2b1a-4e2d-4a3b-9c1a-1234567890ab"
    # what the AlarmServer profile shows as "Basis-URL"
    assert normalize_base_url(f"{root}/api/homeassistant/{uuid}") == root
    assert normalize_base_url(f" {root}/api/homeassistant/{uuid}/status ") == root
    assert normalize_base_url(f"{root}/api/") == root
    assert normalize_base_url(f"{root}/") == root
    assert normalize_base_url(root) == root
    assert normalize_base_url("alarm.feuerwehr45.de") == root
    assert normalize_base_url("http://192.168.1.10:8080") == "http://192.168.1.10:8080"
    # reverse proxy with a path prefix stays intact
    assert normalize_base_url("https://host/alarm/api/homeassistant/x") == "https://host/alarm"
