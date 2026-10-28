import pytest
from pydantic import ValidationError

from invinv.settings import MIN_REQUEST_INTERVAL_SECONDS, Settings


def test_request_interval_cannot_go_below_floor() -> None:
    with pytest.raises(ValidationError):
        Settings(request_interval_seconds=MIN_REQUEST_INTERVAL_SECONDS - 1)


def test_request_interval_default_is_above_floor() -> None:
    assert Settings().request_interval_seconds >= MIN_REQUEST_INTERVAL_SECONDS
