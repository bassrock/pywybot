"""Tests for wybot.http_client.WyBotHTTPClient."""

from unittest.mock import MagicMock

import pytest
import requests

from wybot import http_client
from wybot.exceptions import WybotAuthError, WybotConnectionError
from wybot.http_client import TOKEN_REFRESH_INTERVAL, WyBotHTTPClient


def _login_json(user_id="u-1", token="tok"):
    return {
        "code": 0,
        "reason": "ok",
        "message": "",
        "metadata": {
            "userId": user_id,
            "token": token,
            "username": "u",
            "name": "n",
            "avatar": "",
            "groupid": 1,
            "regTime": 0,
            "lastLoginTime": 0,
        },
    }


def _devices_json(groups=None):
    return {
        "code": 0,
        "reason": "ok",
        "message": "",
        "metadata": {"groups": groups or []},
    }


def _resp(status_code=200, json_data=None, text=""):
    resp = MagicMock()
    resp.status_code = status_code
    resp.json.return_value = json_data
    resp.text = text
    return resp


@pytest.fixture(autouse=True)
def _no_sleep(monkeypatch):
    """Patch time.sleep so retries do not actually wait."""
    monkeypatch.setattr(http_client.time, "sleep", lambda *_a, **_k: None)


@pytest.fixture
def client():
    c = WyBotHTTPClient("user", "pass")
    c._session = MagicMock()
    return c


# --------------------------- authenticate ---------------------------


def test_authenticate_success(client):
    client._session.post.return_value = _resp(200, _login_json("u-1", "tok"))
    assert client.authenticate() is True
    assert client.user_id == "u-1"
    assert client._token == "tok"
    assert client._token_obtained_at > 0


def test_authenticate_metadata_none_raises(client):
    client._session.post.return_value = _resp(
        200, {"code": 1, "reason": "bad", "message": "", "metadata": None}
    )
    with pytest.raises(WybotAuthError):
        client.authenticate()


def test_authenticate_401_raises(client):
    client._session.post.return_value = _resp(401, text="nope")
    with pytest.raises(WybotAuthError):
        client.authenticate()


def test_authenticate_403_raises(client):
    client._session.post.return_value = _resp(403, text="forbidden")
    with pytest.raises(WybotAuthError):
        client.authenticate()


def test_authenticate_missing_password_raises():
    c = WyBotHTTPClient("user", "")
    c._session = MagicMock()
    with pytest.raises(WybotAuthError):
        c.authenticate()


def test_authenticate_timeout_all_retries_raises_connection(client):
    client._session.post.side_effect = requests.exceptions.Timeout("slow")
    with pytest.raises(WybotConnectionError):
        client.authenticate()
    assert client._session.post.call_count == http_client.MAX_RETRIES


def test_authenticate_request_exception_all_retries(client):
    client._session.post.side_effect = requests.exceptions.RequestException("boom")
    with pytest.raises(WybotConnectionError):
        client.authenticate()


def test_authenticate_500_all_retries_raises_connection(client):
    client._session.post.return_value = _resp(500, text="server error")
    with pytest.raises(WybotConnectionError):
        client.authenticate()
    assert client._session.post.call_count == http_client.MAX_RETRIES


def test_login_unexpected_error_wrapped(client):
    # A non-request exception is wrapped into WybotConnectionError.
    client._session.post.side_effect = ValueError("weird")
    with pytest.raises(WybotConnectionError):
        client.login()


# --------------------------- get_devices_and_status ---------------------------


def _authed(client):
    client._token = "tok"
    client._user_id = "u-1"
    client._token_obtained_at = http_client.time.time()
    return client


def test_get_devices_success(client):
    _authed(client)
    client._session.get.return_value = _resp(200, _devices_json())
    result = client.get_devices_and_status()
    assert result.metadata.groups == []


def test_get_devices_401_reauth_then_success(client):
    _authed(client)
    client._session.get.side_effect = [
        _resp(401, text="expired"),
        _resp(200, _devices_json()),
    ]
    client._session.post.return_value = _resp(200, _login_json("u-1", "newtok"))
    result = client.get_devices_and_status()
    assert result.metadata.groups == []
    assert client._token == "newtok"


def test_get_devices_connection_error(client):
    _authed(client)
    client._session.get.side_effect = requests.exceptions.Timeout("slow")
    with pytest.raises(WybotConnectionError):
        client.get_devices_and_status()


def test_get_devices_500_all_retries(client):
    _authed(client)
    client._session.get.return_value = _resp(500, text="oops")
    with pytest.raises(WybotConnectionError):
        client.get_devices_and_status()


def test_get_devices_unexpected_error_wrapped(client):
    _authed(client)
    client._session.get.side_effect = ValueError("weird")
    with pytest.raises(WybotConnectionError):
        client.get_devices_and_status()


def test_get_devices_user_id_none_raises(client):
    # Token present but user_id missing after refresh short-circuit is not
    # possible; simulate by leaving refresh a no-op and clearing user id.
    client._token = "tok"
    client._user_id = "u-1"
    client._token_obtained_at = http_client.time.time()
    client._refresh_token_if_needed = lambda: True
    client._user_id = None
    with pytest.raises(WybotAuthError):
        client.get_devices_and_status()


# --------------------------- indexed grouped devices ---------------------------


def test_get_indexed_current_grouped_devices(client, sample_api_group):
    _authed(client)
    client._session.get.return_value = _resp(200, _devices_json([sample_api_group]))
    indexed = client.get_indexed_current_grouped_devices()
    assert set(indexed.keys()) == {"group1"}
    assert indexed["group1"].name == "My Pool"


# --------------------------- register_presence ---------------------------


def test_register_presence_success(client):
    _authed(client)
    client._session.post.return_value = _resp(200)
    assert client.register_presence() is True


def test_register_presence_swallows_wybot_error(client):
    # No token and no password -> refresh triggers authenticate -> WybotAuthError.
    client._password = ""
    client._token = None
    client._user_id = None
    assert client.register_presence() is False


def test_register_presence_failure_then_false(client):
    _authed(client)
    client._session.post.return_value = _resp(500)
    assert client.register_presence() is False
    assert client._session.post.call_count == 2


def test_register_presence_exception_returns_false(client):
    _authed(client)
    client._session.post.side_effect = Exception("boom")
    assert client.register_presence() is False


# --------------------------- _refresh_token_if_needed ---------------------------


def test_refresh_token_missing_reauthenticates(client):
    client._token = None
    client._user_id = None
    client._session.post.return_value = _resp(200, _login_json("u-9", "freshtok"))
    assert client._refresh_token_if_needed() is True
    assert client._token == "freshtok"


def test_refresh_token_proactive_refresh(client):
    client._token = "old"
    client._user_id = "u-1"
    client._token_obtained_at = http_client.time.time() - (TOKEN_REFRESH_INTERVAL + 100)
    client._session.post.return_value = _resp(200, _login_json("u-1", "rotated"))
    assert client._refresh_token_if_needed() is True
    assert client._token == "rotated"


def test_refresh_token_still_valid_noop(client):
    _authed(client)
    assert client._refresh_token_if_needed() is True
    client._session.post.assert_not_called()
