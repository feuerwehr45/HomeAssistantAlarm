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
