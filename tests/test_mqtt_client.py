"""Tests for wybot.mqtt_client.WyBotMQTTClient (async aiomqtt API)."""

import asyncio
import json
from unittest.mock import AsyncMock, MagicMock

import aiomqtt
import pytest

from wybot import mqtt_client
from wybot.models import MQTTMessageKind
from wybot.mqtt_client import WyBotMQTTClient


# --------------------------- _parse_message ---------------------------


def test_parse_will_online(client):
    msg = client._parse_message("/will/dev1", {"online": "1"})
    assert msg.kind is MQTTMessageKind.WILL
    assert msg.device_id == "dev1"
    assert msg.online is True


def test_parse_will_offline(client):
    msg = client._parse_message("/will/dev1", {"online": "0"})
    assert msg.kind is MQTTMessageKind.WILL
    assert msg.online is False


def test_parse_data_report_parses_command(client):
    payload = {"cmd": 4, "ts": 1, "dp": [{"id": 11, "type": 4, "len": 1, "data": "00"}]}
    msg = client._parse_message(
        "/device/DATA/send_transparent_data/dev1", payload
    )
    assert msg.kind is MQTTMessageKind.DATA_REPORT
    assert msg.device_id == "dev1"
    assert msg.command is not None
    assert msg.command.cmd == 4


def test_parse_command_response(client):
    payload = {"cmd": 4, "ts": 1, "dp": []}
    msg = client._parse_message(
        "/device/DATA/recv_transparent_cmd_data/dev1", payload
    )
    assert msg.kind is MQTTMessageKind.COMMAND_RESPONSE
    assert msg.command is not None


def test_parse_query_response(client):
    msg = client._parse_message(
        "/device/DATA/recv_transparent_query_data/dev1",
        {"cmd": 9, "ts": 1, "dp": []},
    )
    assert msg.kind is MQTTMessageKind.QUERY_RESPONSE


def test_parse_malformed_command_is_none(client):
    # Not a valid Command payload -> command stays None, no raise.
    msg = client._parse_message(
        "/device/DATA/send_transparent_data/dev1", {"bogus": True}
    )
    assert msg.command is None


def test_parse_non_dict_payload(client):
    # Raw bytes forwarded when JSON decoding failed.
    msg = client._parse_message("/will/dev1", b"not json")
    assert msg.kind is MQTTMessageKind.WILL
    assert msg.online is None


def test_parse_other_topic(client):
    msg = client._parse_message("/device/OTA/post_update_progress/dev1", {})
    assert msg.kind is MQTTMessageKind.OTHER
    assert msg.device_id is None


# --------------------------- fake aiomqtt helpers ---------------------------


class _Msg:
    """A minimal stand-in for an aiomqtt.Message."""

    def __init__(self, topic, payload):
        self.topic = topic
        self.payload = payload


class _FakeCM:
    """Fake ``async with aiomqtt.Client(...)`` context manager."""

    def __init__(self, client=None, exc=None):
        self._client = client
        self._exc = exc

    async def __aenter__(self):
        if self._exc is not None:
            raise self._exc
        return self._client

    async def __aexit__(self, *args):
        return False


def _async_iter(messages):
    async def _gen():
        for m in messages:
            yield m

    return _gen()


def _fake_client(messages=()):
    c = MagicMock()
    c.subscribe = AsyncMock()
    c.publish = AsyncMock()
    c.messages = _async_iter(messages)
    return c


@pytest.fixture(autouse=True)
def _no_sleep(monkeypatch):
    """Patch asyncio.sleep so reconnect backoff does not actually wait."""
    sleep = AsyncMock()
    monkeypatch.setattr(mqtt_client.asyncio, "sleep", sleep)
    return sleep


@pytest.fixture
def on_message():
    return MagicMock()


@pytest.fixture
def client(on_message):
    return WyBotMQTTClient(on_message)


# --------------------------- _run background loop ---------------------------


async def test_run_connects_subscribes_and_handles_message(monkeypatch):
    received = []
    holder = {}

    def on_message(msg):
        received.append(msg)
        holder["client"]._stop = True

    c = WyBotMQTTClient(on_message)
    holder["client"] = c
    c._subscriptions = {"/will/dev1"}
    c._devices = {"dev1"}
    fake = _fake_client([_Msg("/will/dev1", json.dumps({"a": 1}).encode())])
    monkeypatch.setattr(
        mqtt_client.aiomqtt, "Client", MagicMock(return_value=_FakeCM(fake))
    )

    await c.connect()
    await asyncio.wait_for(c._task, timeout=5)

    assert len(received) == 1
    assert received[0].kind is MQTTMessageKind.WILL
    assert received[0].device_id == "dev1"
    assert received[0].payload == {"a": 1}
    # Re-subscribed to the one stored subscription on (re)connect.
    assert fake.subscribe.await_count == 1
    # ensure_device_sends_statuses publishes 15 query DPs for the device.
    assert fake.publish.await_count == 15
    assert c.is_connected() is False


async def test_run_reconnects_on_mqtt_error(monkeypatch, _no_sleep):
    received = []
    holder = {}

    def on_message(msg):
        received.append(msg)
        holder["client"]._stop = True

    c = WyBotMQTTClient(on_message)
    holder["client"] = c
    good = _fake_client([_Msg("/will/dev1", b"{}")])
    monkeypatch.setattr(
        mqtt_client.aiomqtt,
        "Client",
        MagicMock(
            side_effect=[
                _FakeCM(exc=aiomqtt.MqttError("down")),
                _FakeCM(good),
            ]
        ),
    )

    await c.connect()
    await asyncio.wait_for(c._task, timeout=5)

    assert len(received) == 1
    assert received[0].kind is MQTTMessageKind.WILL
    assert received[0].device_id == "dev1"
    _no_sleep.assert_awaited()  # backoff sleep happened between attempts


async def test_run_reconnects_on_generic_error(monkeypatch, _no_sleep):
    received = []
    holder = {}

    def on_message(msg):
        received.append(msg)
        holder["client"]._stop = True

    c = WyBotMQTTClient(on_message)
    holder["client"] = c
    good = _fake_client([_Msg("/t", b"{}")])
    monkeypatch.setattr(
        mqtt_client.aiomqtt,
        "Client",
        MagicMock(
            side_effect=[
                _FakeCM(exc=RuntimeError("boom")),
                _FakeCM(good),
            ]
        ),
    )

    await c.connect()
    await asyncio.wait_for(c._task, timeout=5)

    assert len(received) == 1
    assert received[0].kind is MQTTMessageKind.OTHER
    _no_sleep.assert_awaited()


# --------------------------- connect / disconnect ---------------------------


async def test_connect_idempotent_when_task_running(client):
    fake_task = MagicMock()
    fake_task.done.return_value = False
    client._task = fake_task
    client._connected = True  # already up: connect returns immediately
    assert await client.connect() is True
    assert client._task is fake_task  # no new task created


async def test_connect_waits_for_connection(client, monkeypatch):
    # _run sets the event once "connected"; connect() must wait for it.
    async def _fake_run():
        client._connected = True
        client._connected_event.set()

    monkeypatch.setattr(client, "_run", _fake_run)
    assert await client.connect(timeout=5) is True
    assert client.is_connected() is True


async def test_connect_times_out_when_never_connects(client, monkeypatch):
    async def _never():
        await asyncio.Event().wait()

    monkeypatch.setattr(client, "_run", _never)
    assert await client.connect(timeout=0.05) is False
    await client.disconnect()


async def test_disconnect_cancels_task(client):
    async def _forever():
        await asyncio.Event().wait()

    client._task = asyncio.create_task(_forever())
    client._connected = True
    await client.disconnect()
    assert client._task is None
    assert client._stop is True
    assert client._connected is False


async def test_disconnect_no_task(client):
    await client.disconnect()
    assert client._stop is True
    assert client._connected is False


# --------------------------- is_connected ---------------------------


def test_is_connected_true(client):
    client._connected = True
    assert client.is_connected() is True


def test_is_connected_false(client):
    client._connected = False
    assert client.is_connected() is False


# --------------------------- subscribe_for_device ---------------------------


async def test_subscribe_for_device_when_connected(client):
    fake = _fake_client()
    client._client = fake
    client._connected = True
    await client.subscribe_for_device("dev1")
    assert "dev1" in client._devices
    assert len(client._subscriptions) == 6
    assert fake.subscribe.await_count == 6
    # ensure_device_sends_statuses fires 15 query publishes.
    assert fake.publish.await_count == 15


async def test_subscribe_for_device_no_client(client):
    # No live client: topics are recorded but nothing is published/subscribed.
    client._client = None
    client._connected = False
    await client.subscribe_for_device("dev1")
    assert "dev1" in client._devices
    assert len(client._subscriptions) == 6


async def test_subscribe_for_device_idempotent(client):
    fake = _fake_client()
    client._client = fake
    client._connected = True
    client._devices.add("dev1")
    await client.subscribe_for_device("dev1")
    fake.subscribe.assert_not_called()


# --------------------------- ensure_device_sends_statuses ---------------------------


async def test_ensure_device_sends_statuses(client):
    fake = _fake_client()
    client._client = fake
    client._connected = True
    await client.ensure_device_sends_statuses("dev1")
    assert fake.publish.await_count == 15


# --------------------------- send query / write ---------------------------


async def test_send_query_connected_success(client):
    fake = _fake_client()
    client._client = fake
    client._connected = True
    assert await client.send_query_command_for_device("dev1", {"cmd": 9}) is True
    fake.publish.assert_awaited_once()
    topic, _payload = fake.publish.await_args[0]
    assert topic == "/device/DATA/recv_transparent_query_data/dev1"


async def test_send_write_connected_success(client):
    fake = _fake_client()
    client._client = fake
    client._connected = True
    assert await client.send_write_command_for_device("dev1", {"cmd": 4}) is True
    fake.publish.assert_awaited_once()
    topic, _payload = fake.publish.await_args[0]
    assert topic == "/device/DATA/recv_transparent_cmd_data/dev1"


async def test_send_write_disconnected_reports_failure(client):
    # Disconnected: nothing published and the caller learns the command dropped.
    client._client = None
    client._connected = False
    assert await client.send_write_command_for_device("dev1", {"cmd": 4}) is False


# --------------------------- _publish ---------------------------


async def test_publish_disabled(client, monkeypatch):
    monkeypatch.setattr(mqtt_client, "DISABLE_MQTT_COMMANDS", True)
    fake = _fake_client()
    client._client = fake
    client._connected = True
    assert await client._publish("/topic", {"cmd": 9}, "query") is False
    fake.publish.assert_not_called()


async def test_publish_not_connected(client):
    fake = _fake_client()
    client._client = fake
    client._connected = False
    assert await client._publish("/topic", {"cmd": 9}, "query") is False
    fake.publish.assert_not_called()


async def test_publish_no_client(client):
    client._client = None
    client._connected = True
    # No client -> nothing to publish, no error.
    assert await client._publish("/topic", {"cmd": 9}, "query") is False


async def test_publish_connected_success(client):
    fake = _fake_client()
    client._client = fake
    client._connected = True
    assert await client._publish("/topic", {"cmd": 9}, "query") is True
    fake.publish.assert_awaited_once_with("/topic", json.dumps({"cmd": 9}))


async def test_publish_mqtt_error_swallowed(client):
    fake = _fake_client()
    fake.publish = AsyncMock(side_effect=aiomqtt.MqttError("nope"))
    client._client = fake
    client._connected = True
    # Should not raise, and reports failure.
    assert await client._publish("/topic", {"cmd": 9}, "query") is False


# --------------------------- _handle_message ---------------------------


def test_handle_message_valid_json(client, on_message):
    msg = _Msg("/will/dev1", json.dumps({"online": "1"}).encode())
    client._handle_message(msg)
    on_message.assert_called_once()
    parsed = on_message.call_args[0][0]
    assert parsed.kind is MQTTMessageKind.WILL
    assert parsed.device_id == "dev1"
    assert parsed.online is True
    assert parsed.payload == {"online": "1"}


def test_handle_message_raw_bytes_fallback(client, on_message):
    msg = _Msg("/will/dev1", b"\xff\xfenot json")
    client._handle_message(msg)
    on_message.assert_called_once()
    parsed = on_message.call_args[0][0]
    assert parsed.kind is MQTTMessageKind.WILL
    assert parsed.online is None
    assert parsed.payload == msg.payload
