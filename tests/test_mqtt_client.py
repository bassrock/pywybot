"""Tests for wybot.mqtt_client.WyBotMQTTClient."""

import json
from unittest.mock import MagicMock

import pytest

from wybot import mqtt_client
from wybot.mqtt_client import WyBotMQTTClient


@pytest.fixture
def mock_mqtt(monkeypatch):
    """Patch paho's mqtt.Client with a MagicMock factory."""
    instance = MagicMock()
    instance.is_connected.return_value = True
    instance.publish.return_value = MagicMock(rc=mqtt_client.mqtt.MQTT_ERR_SUCCESS, mid=1)
    factory = MagicMock(return_value=instance)
    monkeypatch.setattr(mqtt_client.mqtt, "Client", factory)
    return instance


@pytest.fixture
def on_message():
    return MagicMock()


@pytest.fixture
def client(mock_mqtt, on_message):
    return WyBotMQTTClient(on_message)


# --------------------------- construction ---------------------------


def test_construction_sets_callbacks(client, mock_mqtt):
    mock_mqtt.username_pw_set.assert_called_once_with(
        mqtt_client.USERNAME, mqtt_client.PASWORD
    )
    assert mock_mqtt.on_connect == client._on_connect
    assert mock_mqtt.on_message == client._on_message_handler
    assert mock_mqtt.on_disconnect == client._on_disconnect
    assert client._connected is False


# --------------------------- connect ---------------------------


def test_connect_normal(client, mock_mqtt):
    client.connect()
    mock_mqtt.connect.assert_called_once_with(mqtt_client.MQTT_URL)
    mock_mqtt.loop_start.assert_called_once()
    assert client._connecting is True
    assert client._loop_started is True


def test_connect_already_connecting_noop(client, mock_mqtt):
    client._connecting = True
    client.connect()
    mock_mqtt.connect.assert_not_called()


def test_connect_already_connected_noop(client, mock_mqtt):
    client._connected = True
    mock_mqtt.is_connected.return_value = True
    client.connect()
    mock_mqtt.connect.assert_not_called()


def test_connect_flag_drift_forces_reconnect(client, mock_mqtt):
    client._connected = True
    mock_mqtt.is_connected.return_value = False
    client.connect()
    mock_mqtt.reconnect.assert_called_once()
    assert client._connected is False
    assert client._connecting is True


def test_connect_flag_drift_reconnect_fails(client, mock_mqtt):
    client._connected = True
    mock_mqtt.is_connected.return_value = False
    mock_mqtt.reconnect.side_effect = Exception("boom")
    client.connect()
    assert client._connecting is False


def test_connect_connect_raises(client, mock_mqtt):
    mock_mqtt.connect.side_effect = Exception("no network")
    client.connect()
    assert client._connecting is False
    assert client._connected is False


def test_connect_loop_already_started(client, mock_mqtt):
    client._loop_started = True
    client.connect()
    mock_mqtt.loop_start.assert_not_called()


# --------------------------- is_connected / disconnect ---------------------------


def test_is_connected_true(client, mock_mqtt):
    client._connected = True
    mock_mqtt.is_connected.return_value = True
    assert client.is_connected() is True


def test_is_connected_false(client, mock_mqtt):
    client._connected = False
    assert client.is_connected() is False


def test_disconnect(client, mock_mqtt):
    client._loop_started = True
    client.disconnect()
    mock_mqtt.loop_stop.assert_called_once()
    mock_mqtt.disconnect.assert_called_once()
    assert client._connected is False
    assert client._loop_started is False


def test_disconnect_swallows_error(client, mock_mqtt):
    client._loop_started = False
    mock_mqtt.disconnect.side_effect = Exception("boom")
    client.disconnect()  # should not raise
    mock_mqtt.loop_stop.assert_not_called()


# --------------------------- paho callbacks ---------------------------


def test_on_connect_success_resubscribes(client, mock_mqtt):
    client._subscriptions = {"/will/dev1"}
    client._devices = {"dev1"}
    client._connected = True
    mock_mqtt.is_connected.return_value = True
    cb_client = MagicMock()
    client._on_connect(cb_client, None, None, 0)
    assert client._connected is True
    assert client._connecting is False
    cb_client.subscribe.assert_called_once_with("/will/dev1")
    # ensure_device_sends_statuses publishes query commands for the device
    assert mock_mqtt.publish.called


def test_on_connect_failure(client, mock_mqtt):
    client._on_connect(MagicMock(), None, None, 5)
    assert client._connected is False
    assert client._connecting is False


def test_on_connect_fail_callback(client):
    client._connecting = True
    client._on_connect_fail(MagicMock(), None)
    assert client._connected is False
    assert client._connecting is False


def test_on_disconnect_clean(client):
    client._connected = True
    client._on_disconnect(MagicMock(), None, 0)
    assert client._connected is False


def test_on_disconnect_unexpected(client):
    client._connected = True
    client._on_disconnect(MagicMock(), None, 1)
    assert client._connected is False


# --------------------------- subscribe_for_device ---------------------------


def test_subscribe_for_device(client, mock_mqtt):
    client._connected = True
    mock_mqtt.is_connected.return_value = True
    client.subscribe_for_device("dev1")
    assert "dev1" in client._devices
    # six topics subscribed
    assert mock_mqtt.subscribe.call_count == 6
    assert len(client._subscriptions) == 6


def test_subscribe_for_device_idempotent(client, mock_mqtt):
    client._devices.add("dev1")
    client.subscribe_for_device("dev1")
    mock_mqtt.subscribe.assert_not_called()


# --------------------------- ensure_device_sends_statuses ---------------------------


def test_ensure_device_sends_statuses(client, mock_mqtt):
    client._connected = True
    mock_mqtt.is_connected.return_value = True
    client.ensure_device_sends_statuses("dev1")
    # 15 DPs queried
    assert mock_mqtt.publish.call_count == 15


# --------------------------- send_query_command_for_device ---------------------------


def test_send_query_connected_success(client, mock_mqtt):
    client._connected = True
    mock_mqtt.is_connected.return_value = True
    client.send_query_command_for_device("dev1", {"cmd": 9})
    mock_mqtt.publish.assert_called_once()


def test_send_query_not_connected(client, mock_mqtt):
    client._connected = False
    client.send_query_command_for_device("dev1", {"cmd": 9})
    mock_mqtt.publish.assert_not_called()


def test_send_query_disabled(client, mock_mqtt, monkeypatch):
    monkeypatch.setattr(mqtt_client, "DISABLE_MQTT_COMMANDS", True)
    client._connected = True
    client.send_query_command_for_device("dev1", {"cmd": 9})
    mock_mqtt.publish.assert_not_called()


def test_send_query_publish_failure(client, mock_mqtt):
    client._connected = True
    mock_mqtt.is_connected.return_value = True
    mock_mqtt.publish.return_value = MagicMock(rc=1)
    client.send_query_command_for_device("dev1", {"cmd": 9})
    mock_mqtt.publish.assert_called_once()


def test_send_query_exception(client, mock_mqtt):
    client._connected = True
    mock_mqtt.is_connected.return_value = True
    mock_mqtt.publish.side_effect = Exception("boom")
    client.send_query_command_for_device("dev1", {"cmd": 9})  # swallowed


# --------------------------- send_write_command_for_device ---------------------------


def test_send_write_connected_success(client, mock_mqtt):
    client._connected = True
    mock_mqtt.is_connected.return_value = True
    client.send_write_command_for_device("dev1", {"cmd": 4})
    mock_mqtt.publish.assert_called_once()


def test_send_write_not_connected(client, mock_mqtt):
    client._connected = False
    client.send_write_command_for_device("dev1", {"cmd": 4})
    mock_mqtt.publish.assert_not_called()


def test_send_write_disabled(client, mock_mqtt, monkeypatch):
    monkeypatch.setattr(mqtt_client, "DISABLE_MQTT_COMMANDS", True)
    client._connected = True
    client.send_write_command_for_device("dev1", {"cmd": 4})
    mock_mqtt.publish.assert_not_called()


def test_send_write_publish_failure(client, mock_mqtt):
    client._connected = True
    mock_mqtt.is_connected.return_value = True
    mock_mqtt.publish.return_value = MagicMock(rc=1)
    client.send_write_command_for_device("dev1", {"cmd": 4})
    mock_mqtt.publish.assert_called_once()


def test_send_write_exception(client, mock_mqtt):
    client._connected = True
    mock_mqtt.is_connected.return_value = True
    mock_mqtt.publish.side_effect = Exception("boom")
    client.send_write_command_for_device("dev1", {"cmd": 4})  # swallowed


# --------------------------- _on_message_handler ---------------------------


def test_on_message_valid_json(client, on_message):
    msg = MagicMock()
    msg.topic = "/will/dev1"
    msg.payload = json.dumps({"a": 1}).encode()
    client._on_message_handler(MagicMock(), None, msg)
    on_message.assert_called_once_with("/will/dev1", {"a": 1})


def test_on_message_invalid_json_raw(client, on_message):
    msg = MagicMock()
    msg.topic = "/will/dev1"
    msg.payload = b"\xff\xfenot json"
    client._on_message_handler(MagicMock(), None, msg)
    on_message.assert_called_once()
    topic, payload = on_message.call_args[0]
    assert topic == "/will/dev1"
    assert payload == msg.payload
