"""Tests for wybot.http_client.WyBotHTTPClient (async aiohttp API)."""

import time
from unittest.mock import AsyncMock, MagicMock

import aiohttp
import pytest

from wybot import http_client
from wybot.exceptions import WybotAuthError, WybotConnectionError
from wybot.http_client import TOKEN_REFRESH_INTERVAL, WyBotHTTPClient


# --------------------------- JSON payload builders ---------------------------


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


# --------------------------- aiohttp mocking helpers ---------------------------


def _resp(status=200, json_data=None, text=""):
    """Build a fake aiohttp response object."""
    r = MagicMock()
    r.status = status
    r.json = AsyncMock(return_value=json_data if json_data is not None else {})
    r.text = AsyncMock(return_value=text)
    return r


def _ctx(resp=None, exc=None):
    """Build a fake ``async with`` context manager around a response."""
    cm = MagicMock()
    cm.__aenter__ = (
        AsyncMock(return_value=resp) if exc is None else AsyncMock(side_effect=exc)
    )
    cm.__aexit__ = AsyncMock(return_value=False)
    return cm


def _make_session():
    """Return a MagicMock aiohttp session with post/get as plain MagicMocks."""
    s = MagicMock()
    s.post = MagicMock()
    s.get = MagicMock()
    return s


@pytest.fixture(autouse=True)
def _no_sleep(monkeypatch):
    """Patch asyncio.sleep so retries do not actually wait."""
    monkeypatch.setattr(http_client.asyncio, "sleep", AsyncMock())


@pytest.fixture
def session():
    return _make_session()


@pytest.fixture
def client(session):
    return WyBotHTTPClient("user", "pass", session=session)


def _authed(client):
    client._token = "tok"
    client._user_id = "u-1"
    client._token_obtained_at = time.monotonic()
    return client


# --------------------------- authenticate / login ---------------------------


async def test_authenticate_success(client, session):
    session.post.return_value = _ctx(_resp(200, _login_json("u-1", "tok")))
    assert await client.authenticate() is True
    assert client.user_id == "u-1"
    assert client._token == "tok"
    assert client._token_obtained_at > 0


async def test_authenticate_metadata_none_raises(client, session):
    session.post.return_value = _ctx(
        _resp(200, {"code": 1, "reason": "bad", "message": "", "metadata": None})
    )
    with pytest.raises(WybotAuthError):
        await client.authenticate()


async def test_authenticate_401_raises(client, session):
    session.post.return_value = _ctx(_resp(401, text="nope"))
    with pytest.raises(WybotAuthError):
        await client.authenticate()


async def test_authenticate_403_raises(client, session):
    session.post.return_value = _ctx(_resp(403, text="forbidden"))
    with pytest.raises(WybotAuthError):
        await client.authenticate()


async def test_authenticate_missing_password_raises(session):
    c = WyBotHTTPClient("user", "", session=session)
    with pytest.raises(WybotAuthError):
        await c.authenticate()


async def test_login_500_all_retries_raises_connection(client, session):
    session.post.return_value = _ctx(_resp(500, text="server error"))
    with pytest.raises(WybotConnectionError):
        await client.login()
    assert session.post.call_count == http_client.MAX_RETRIES


async def test_login_client_error_all_retries_raises_connection(client, session):
    session.post.return_value = _ctx(exc=aiohttp.ClientError("boom"))
    with pytest.raises(WybotConnectionError):
        await client.login()
    assert session.post.call_count == http_client.MAX_RETRIES


async def test_login_timeout_all_retries_raises_connection(client, session):
    session.post.return_value = _ctx(exc=TimeoutError("slow"))
    with pytest.raises(WybotConnectionError):
        await client.login()
    assert session.post.call_count == http_client.MAX_RETRIES


async def test_login_unexpected_error_wrapped(client, session):
    # A non-request exception is wrapped into WybotConnectionError.
    session.post.side_effect = ValueError("weird")
    with pytest.raises(WybotConnectionError):
        await client.login()


async def test_login_500_then_success_retries(client, session):
    session.post.side_effect = [
        _ctx(_resp(500, text="err")),
        _ctx(_resp(200, _login_json("u-1", "tok"))),
    ]
    result = await client.login()
    assert result.metadata.token == "tok"
    assert session.post.call_count == 2


# --------------------------- get_devices_and_status ---------------------------


async def test_get_devices_success(client, session):
    _authed(client)
    session.get.return_value = _ctx(_resp(200, _devices_json()))
    result = await client.get_devices_and_status()
    assert result.metadata.groups == []


async def test_get_devices_401_reauth_then_success(client, session):
    _authed(client)
    session.get.side_effect = [
        _ctx(_resp(401, text="expired")),
        _ctx(_resp(200, _devices_json())),
    ]
    session.post.return_value = _ctx(_resp(200, _login_json("u-1", "newtok")))
    result = await client.get_devices_and_status()
    assert result.metadata.groups == []
    assert client._token == "newtok"


async def test_get_devices_connection_error(client, session):
    _authed(client)
    session.get.return_value = _ctx(exc=TimeoutError("slow"))
    with pytest.raises(WybotConnectionError):
        await client.get_devices_and_status()
    assert session.get.call_count == http_client.MAX_RETRIES


async def test_get_devices_500_all_retries(client, session):
    _authed(client)
    session.get.return_value = _ctx(_resp(500, text="oops"))
    with pytest.raises(WybotConnectionError):
        await client.get_devices_and_status()


async def test_get_devices_unexpected_error_wrapped(client, session):
    _authed(client)
    session.get.side_effect = ValueError("weird")
    with pytest.raises(WybotConnectionError):
        await client.get_devices_and_status()


async def test_get_devices_user_id_none_raises(client):
    client._refresh_token_if_needed = AsyncMock(return_value=True)
    client._token = "tok"
    client._user_id = None
    with pytest.raises(WybotAuthError):
        await client.get_devices_and_status()


# --------------------------- indexed grouped devices ---------------------------


async def test_get_indexed_current_grouped_devices(client, session, sample_api_group):
    _authed(client)
    session.get.return_value = _ctx(_resp(200, _devices_json([sample_api_group])))
    indexed = await client.get_indexed_current_grouped_devices()
    assert set(indexed.keys()) == {"group1"}
    assert indexed["group1"].name == "My Pool"


# --------------------------- register_presence ---------------------------


async def test_register_presence_success(client, session):
    _authed(client)
    session.post.return_value = _ctx(_resp(200))
    assert await client.register_presence() is True


async def test_register_presence_swallows_wybot_error(client):
    # No token and no password -> refresh triggers authenticate -> WybotAuthError.
    client._password = ""
    client._token = None
    client._user_id = None
    assert await client.register_presence() is False


async def test_register_presence_user_id_none(client, session):
    client._refresh_token_if_needed = AsyncMock(return_value=True)
    client._token = "tok"
    client._user_id = None
    assert await client.register_presence() is False
    session.post.assert_not_called()


async def test_register_presence_failure_then_false(client, session):
    _authed(client)
    session.post.return_value = _ctx(_resp(500))
    assert await client.register_presence() is False
    assert session.post.call_count == 2


async def test_register_presence_exception_returns_false(client, session):
    _authed(client)
    session.post.side_effect = Exception("boom")
    assert await client.register_presence() is False


# --------------------------- _refresh_token_if_needed ---------------------------


async def test_refresh_token_missing_reauthenticates(client, session):
    client._token = None
    client._user_id = None
    session.post.return_value = _ctx(_resp(200, _login_json("u-9", "freshtok")))
    assert await client._refresh_token_if_needed() is True
    assert client._token == "freshtok"


async def test_refresh_token_proactive_refresh(client, session):
    client._token = "old"
    client._user_id = "u-1"
    client._token_obtained_at = time.monotonic() - (TOKEN_REFRESH_INTERVAL + 100)
    session.post.return_value = _ctx(_resp(200, _login_json("u-1", "rotated")))
    assert await client._refresh_token_if_needed() is True
    assert client._token == "rotated"


async def test_refresh_token_still_valid_noop(client, session):
    _authed(client)
    assert await client._refresh_token_if_needed() is True
    session.post.assert_not_called()


# --------------------------- session lifecycle ---------------------------


async def test_get_session_creates_owned(monkeypatch):
    c = WyBotHTTPClient("user", "pass")
    fake = MagicMock()
    monkeypatch.setattr(
        http_client.aiohttp, "ClientSession", MagicMock(return_value=fake)
    )
    assert c._get_session() is fake
    assert c._owns_session is True
    # Subsequent calls return the same session.
    assert c._get_session() is fake


async def test_close_owned_session():
    c = WyBotHTTPClient("user", "pass")
    mock_sess = MagicMock()
    mock_sess.close = AsyncMock()
    c._session = mock_sess
    c._owns_session = True
    await c.close()
    mock_sess.close.assert_awaited_once()
    assert c._session is None


async def test_close_injected_session():
    mock_sess = MagicMock()
    mock_sess.close = AsyncMock()
    c = WyBotHTTPClient("user", "pass", session=mock_sess)
    await c.close()
    mock_sess.close.assert_not_called()
    assert c._session is mock_sess
