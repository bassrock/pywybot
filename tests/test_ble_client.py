"""Unit tests for wybot.ble_client.WyBotBLEClient."""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from bleak import BleakError

from wybot import ble_client as bc
from wybot.ble_client import WyBotBLEClient
from wybot.dp_models import DP, GenericDP

# A representative status broadcast: aa55 + ver(00) + cmd(05) + len(0004,big) +
# dp[id=0,type=4,len=1,data=03] + checksum(00)
STATUS_BROADCAST = bytes.fromhex("aa550005000400040103") + b"\x00"


# ---------------------------------------------------------------------------
# Helpers for building mock BLE service/characteristic trees
# ---------------------------------------------------------------------------
def make_char(uuid: str, properties: list[str]):
    return SimpleNamespace(uuid=uuid, properties=properties)


def make_service(uuid: str, chars: list):
    return SimpleNamespace(uuid=uuid, characteristics=chars)


def default_services():
    """A realistic DS20 service tree: EE service (write) + FF service (notify)."""
    ee01 = make_char(bc.CHAR_UUID_EE01, ["write"])
    ff01 = make_char(bc.CHAR_UUID_FF01, ["notify", "write"])
    return [
        make_service(bc.SERVICE_UUID_EE, [ee01]),
        make_service(bc.SERVICE_UUID_FF, [ff01]),
    ]


def make_client(connected: bool = True, services=None, write_side_effect=None):
    """Build an AsyncMock BleakClient-like object."""
    client = MagicMock()
    client.is_connected = connected
    client.services = services if services is not None else default_services()
    client.start_notify = AsyncMock()
    client.write_gatt_char = AsyncMock(side_effect=write_side_effect)
    client.disconnect = AsyncMock()
    return client


def make_adapter():
    adapter = MagicMock()
    adapter.scanner_count.return_value = 1
    adapter.discovered_devices.return_value = []
    adapter.device_from_address.return_value = SimpleNamespace(
        name="WyBot", address="CC:BA:97:93:2A:96"
    )
    return adapter


@pytest.fixture(autouse=True)
def _no_sleep():
    """Make asyncio.sleep instantaneous everywhere in the module under test."""
    with patch.object(bc.asyncio, "sleep", new=AsyncMock()):
        yield


# ===========================================================================
# Pure command builders
# ===========================================================================
def test_build_binary_command_matches_known_constants():
    client = WyBotBLEClient(make_adapter())
    assert client._build_binary_command(4, 0, 4, 1, b"\x03") == bc.CMD_START_CLEANING
    assert client._build_binary_command(4, 0, 4, 1, b"\x01") == bc.CMD_STOP_CLEANING
    assert client._build_binary_command(4, 11, 4, 1, b"\x01") == bc.CMD_RETURN_TO_DOCK
    assert client._build_binary_command(4, 1, 4, 1, b"\x02") == bc.CMD_MODE_WALL_THEN_FLOOR


def test_build_binary_command_structure():
    client = WyBotBLEClient(make_adapter())
    out = client._build_binary_command(4, 0, 4, 1, b"\x03")
    assert out[:2] == b"\xaa\x55"
    assert out[2:4] == (4).to_bytes(2, "big")
    # payload len little-endian == len(dp_data) == 3 + len(value)
    assert out[4:6] == (4).to_bytes(2, "little")
    checksum = (sum(out[2:-1]) - 1) & 0xFF
    assert out[-1] == checksum


def test_build_binary_query():
    client = WyBotBLEClient(make_adapter())
    out = client._build_binary_query(1)
    assert out == bytes.fromhex("aa5500090100010a")
    assert out[:2] == b"\xaa\x55"
    assert out[-1] == ((sum(out[2:-1]) - 1) & 0xFF)


def test_build_wifi_ssid_command():
    client = WyBotBLEClient(make_adapter())
    out = client._build_wifi_ssid_command("MyNet")
    assert out[:2] == b"\xaa\x55"
    assert out[2:4] == (0x2A).to_bytes(2, "big")
    # padded SSID is 34 bytes
    assert out[4:6] == (34).to_bytes(2, "big")
    assert len(out) == 2 + 2 + 2 + 34 + 1
    assert out[6:11] == b"MyNet"
    assert out[-1] == (sum(out[2:-1]) & 0xFF)


def test_build_wifi_password_command():
    client = WyBotBLEClient(make_adapter())
    out = client._build_wifi_password_command("secret")
    assert out[:2] == b"\xaa\x55"
    assert out[2:4] == (0x2B).to_bytes(2, "big")
    assert out[4:6] == (len(b"secret")).to_bytes(2, "big")
    assert out.endswith(bytes([sum(out[2:-1]) & 0xFF]))
    assert b"secret" in out


def test_build_command_payload_binary_with_and_without_data():
    client = WyBotBLEClient(make_adapter())
    dp = GenericDP(DP(id=0, type=4, len=1, data="03"))
    assert client._build_command_payload_binary(dp) == bc.CMD_START_CLEANING

    dp_empty = GenericDP(DP(id=9, type=0, len=0, data=None))
    out = client._build_command_payload_binary(dp_empty)
    assert out[:2] == b"\xaa\x55"


# ===========================================================================
# Bluetooth availability + warning-logged flags
# ===========================================================================
def test_is_bluetooth_available_true():
    adapter = make_adapter()
    adapter.scanner_count.return_value = 2
    assert WyBotBLEClient(adapter)._is_bluetooth_available() is True


def test_is_bluetooth_available_zero():
    adapter = make_adapter()
    adapter.scanner_count.return_value = 0
    assert WyBotBLEClient(adapter)._is_bluetooth_available() is False


def test_is_bluetooth_available_exception():
    adapter = make_adapter()
    adapter.scanner_count.side_effect = RuntimeError("boom")
    assert WyBotBLEClient(adapter)._is_bluetooth_available() is False


def test_missing_warning_flag_helpers():
    client = WyBotBLEClient(make_adapter())
    assert client._bluetooth_missing_warning_logged() is False
    client._mark_bluetooth_missing_warning_logged()
    assert client._bluetooth_missing_warning_logged() is True


# ===========================================================================
# Notification handler
# ===========================================================================
def test_notification_handler_status_broadcast():
    client = WyBotBLEClient(make_adapter())
    client._notification_handler(None, bytearray(STATUS_BROADCAST))
    assert client._last_notification_data == STATUS_BROADCAST
    assert client._last_status_data == STATUS_BROADCAST


def test_notification_handler_non_status_with_uuid_sender():
    client = WyBotBLEClient(make_adapter())
    sender = SimpleNamespace(uuid="0000ff01-0000-1000-8000-00805f9b34fb")
    data = bytes.fromhex("aa5500120001ff00")  # cmd=12, not a status
    client._notification_handler(sender, bytearray(data))
    assert client._last_notification_data == data
    assert client._last_status_data is None


def test_notification_handler_non_status_int_sender_and_empty():
    client = WyBotBLEClient(make_adapter())
    client._notification_handler(5, bytearray(b""))
    assert client._last_notification_data == b""
    assert client._last_status_data is None


# ===========================================================================
# Status detection + response parsing
# ===========================================================================
def test_is_status_response():
    client = WyBotBLEClient(make_adapter())
    assert client._is_status_response(STATUS_BROADCAST) is True
    assert client._is_status_response(b"\x00") is False  # too short
    assert client._is_status_response(bytes.fromhex("aa5500120001ff00")) is False


def test_parse_ble_response_status():
    client = WyBotBLEClient(make_adapter())
    dps = client._parse_ble_response(STATUS_BROADCAST)
    assert dps == [{"id": 0, "type": 4, "len": 1, "data": "03"}]


def test_parse_ble_response_too_short():
    client = WyBotBLEClient(make_adapter())
    assert client._parse_ble_response(b"\xaa\x55\x00") == []


def test_parse_ble_response_non_status_cmd_skipped():
    client = WyBotBLEClient(make_adapter())
    # aa55 header but cmd=0x12 -> non-status, no DP parse
    data = bytes.fromhex("aa550012000401030304")
    assert client._parse_ble_response(data) == []


def test_parse_ble_response_tuya_header():
    client = WyBotBLEClient(make_adapter())
    # 55aa header + version + cmd + 2 len + dp[id=0,type=4,len=1,data=03] + checksum
    data = bytes.fromhex("55aa0007000400040103" + "00")
    dps = client._parse_ble_response(data)
    assert dps == [{"id": 0, "type": 4, "len": 1, "data": "03"}]


def test_parse_ble_response_raw_no_header():
    client = WyBotBLEClient(make_adapter())
    # No aa55/55aa header: parsed raw from offset 0
    # dp[id=1,type=4,len=1,data=02] then padding (>=7 bytes to pass length guard)
    data = bytes.fromhex("0104010200000000")
    dps = client._parse_ble_response(data)
    assert {"id": 1, "type": 4, "len": 1, "data": "02"} in dps


def test_parse_ble_response_not_enough_data_breaks():
    client = WyBotBLEClient(make_adapter())
    # status header, declares dp_len bigger than remaining data -> break
    # dp claims len=10 but no data follows
    data = bytes.fromhex("aa550005000400040a11") + b"\x00"
    dps = client._parse_ble_response(data)
    assert dps == []


# ===========================================================================
# scan_for_device
# ===========================================================================
@pytest.mark.asyncio
async def test_scan_no_bluetooth_logs_once():
    adapter = make_adapter()
    adapter.scanner_count.return_value = 0
    client = WyBotBLEClient(adapter)
    assert await client.scan_for_device("CCBA97932A96") is None
    assert client._bluetooth_missing_warning_logged() is True
    # second call: warning already logged branch
    assert await client.scan_for_device("CCBA97932A96") is None


@pytest.mark.asyncio
async def test_scan_matches_by_name():
    adapter = make_adapter()
    dev = SimpleNamespace(name="WyBot-CCBA97932A96", address="CC:BA:97:93:2A:96")
    adapter.discovered_devices.return_value = [dev]
    client = WyBotBLEClient(adapter)
    assert await client.scan_for_device("CCBA97932A96") is dev


@pytest.mark.asyncio
async def test_scan_matches_by_mac():
    adapter = make_adapter()
    dev = SimpleNamespace(name=None, address="CC:BA:97:93:2A:96")
    adapter.discovered_devices.return_value = [dev]
    client = WyBotBLEClient(adapter)
    assert await client.scan_for_device("CCBA97932A96") is dev


@pytest.mark.asyncio
async def test_scan_not_found():
    adapter = make_adapter()
    dev = SimpleNamespace(name="Other", address="11:22:33:44:55:66")
    adapter.discovered_devices.return_value = [dev]
    client = WyBotBLEClient(adapter)
    assert await client.scan_for_device("CCBA97932A96") is None


@pytest.mark.asyncio
async def test_scan_exception():
    adapter = make_adapter()
    adapter.discovered_devices.side_effect = RuntimeError("scan fail")
    client = WyBotBLEClient(adapter)
    assert await client.scan_for_device("CCBA97932A96") is None


# ===========================================================================
# wake_device
# ===========================================================================
@pytest.mark.asyncio
async def test_wake_device_already_in_progress():
    client = WyBotBLEClient(make_adapter())
    client._wake_in_progress = True
    assert await client.wake_device("CCBA97932A96") is False


@pytest.mark.asyncio
async def test_wake_device_success_via_scan():
    adapter = make_adapter()
    dev = SimpleNamespace(name="WyBot-CCBA97932A96", address="CC:BA:97:93:2A:96")
    adapter.discovered_devices.return_value = [dev]
    client = WyBotBLEClient(adapter)
    mock_client = make_client()
    with patch.object(bc, "establish_connection", AsyncMock(return_value=mock_client)):
        assert await client.wake_device("CCBA97932A96") is True
    mock_client.disconnect.assert_awaited()
    assert client._wake_in_progress is False


@pytest.mark.asyncio
async def test_wake_device_direct_mac_success():
    adapter = make_adapter()
    adapter.discovered_devices.return_value = []  # scan finds nothing
    client = WyBotBLEClient(adapter)
    mock_client = make_client()
    with patch.object(bc, "establish_connection", AsyncMock(return_value=mock_client)):
        assert await client.wake_device("CCBA97932A96") is True


@pytest.mark.asyncio
async def test_wake_device_direct_mac_not_found():
    adapter = make_adapter()
    adapter.discovered_devices.return_value = []
    adapter.device_from_address.return_value = None
    client = WyBotBLEClient(adapter)
    assert await client.wake_device("CCBA97932A96") is False


@pytest.mark.asyncio
async def test_wake_device_non_mac_name_direct_lookup():
    # ble_name not 12 hex chars -> used as-is
    adapter = make_adapter()
    adapter.discovered_devices.return_value = []
    adapter.device_from_address.return_value = None
    client = WyBotBLEClient(adapter)
    assert await client.wake_device("not-a-mac") is False


@pytest.mark.asyncio
async def test_wake_device_ble_device_none_after_found():
    adapter = make_adapter()
    dev = SimpleNamespace(name="WyBot-CCBA97932A96", address="CC:BA:97:93:2A:96")
    adapter.discovered_devices.return_value = [dev]
    # first device_from_address (in wake, after scan) returns None
    adapter.device_from_address.return_value = None
    client = WyBotBLEClient(adapter)
    assert await client.wake_device("CCBA97932A96") is False


@pytest.mark.asyncio
async def test_wake_device_not_connected():
    adapter = make_adapter()
    dev = SimpleNamespace(name="WyBot-CCBA97932A96", address="CC:BA:97:93:2A:96")
    adapter.discovered_devices.return_value = [dev]
    client = WyBotBLEClient(adapter)
    mock_client = make_client(connected=False)
    with patch.object(bc, "establish_connection", AsyncMock(return_value=mock_client)):
        assert await client.wake_device("CCBA97932A96") is False


@pytest.mark.asyncio
async def test_wake_device_timeout_error_returns_true():
    adapter = make_adapter()
    dev = SimpleNamespace(name="WyBot-CCBA97932A96", address="CC:BA:97:93:2A:96")
    adapter.discovered_devices.return_value = [dev]
    client = WyBotBLEClient(adapter)
    with patch.object(
        bc, "establish_connection", AsyncMock(side_effect=TimeoutError())
    ):
        assert await client.wake_device("CCBA97932A96") is True


@pytest.mark.asyncio
async def test_wake_device_bleak_timeout_returns_true():
    adapter = make_adapter()
    dev = SimpleNamespace(name="WyBot-CCBA97932A96", address="CC:BA:97:93:2A:96")
    adapter.discovered_devices.return_value = [dev]
    client = WyBotBLEClient(adapter)
    with patch.object(
        bc, "establish_connection", AsyncMock(side_effect=BleakError("Timeout occurred"))
    ):
        assert await client.wake_device("CCBA97932A96") is True


@pytest.mark.asyncio
async def test_wake_device_bleak_error_returns_false():
    adapter = make_adapter()
    dev = SimpleNamespace(name="WyBot-CCBA97932A96", address="CC:BA:97:93:2A:96")
    adapter.discovered_devices.return_value = [dev]
    client = WyBotBLEClient(adapter)
    with patch.object(
        bc, "establish_connection", AsyncMock(side_effect=BleakError("disconnected"))
    ):
        assert await client.wake_device("CCBA97932A96") is False


@pytest.mark.asyncio
async def test_wake_device_unexpected_exception_returns_false():
    adapter = make_adapter()
    client = WyBotBLEClient(adapter)
    # scan_for_device raises unexpectedly (outer except)
    with patch.object(
        client, "scan_for_device", AsyncMock(side_effect=ValueError("weird"))
    ):
        assert await client.wake_device("CCBA97932A96") is False
    assert client._wake_in_progress is False


@pytest.mark.asyncio
async def test_wake_device_notification_data_logged_branch():
    adapter = make_adapter()
    dev = SimpleNamespace(name="WyBot-CCBA97932A96", address="CC:BA:97:93:2A:96")
    adapter.discovered_devices.return_value = [dev]
    client = WyBotBLEClient(adapter)

    def set_notif(*_a, **_k):
        client._last_notification_data = STATUS_BROADCAST

    mock_client = make_client(write_side_effect=set_notif)
    with patch.object(bc, "establish_connection", AsyncMock(return_value=mock_client)):
        assert await client.wake_device("CCBA97932A96") is True


# ===========================================================================
# wake_devices
# ===========================================================================
@pytest.mark.asyncio
async def test_wake_devices_multiple():
    client = WyBotBLEClient(make_adapter())
    with patch.object(client, "wake_device", AsyncMock(side_effect=[True, False])):
        results = await client.wake_devices(["AAA", "BBB"])
    assert results == {"AAA": True, "BBB": False}


# ===========================================================================
# configure_wifi
# ===========================================================================
@pytest.mark.asyncio
async def test_configure_wifi_in_progress():
    client = WyBotBLEClient(make_adapter())
    client._wake_in_progress = True
    assert await client.configure_wifi("CCBA97932A96", "ssid", "pw") is False


@pytest.mark.asyncio
async def test_configure_wifi_success():
    adapter = make_adapter()
    dev = SimpleNamespace(name="WyBot-CCBA97932A96", address="CC:BA:97:93:2A:96")
    adapter.discovered_devices.return_value = [dev]
    client = WyBotBLEClient(adapter)
    mock_client = make_client()
    with patch.object(bc, "establish_connection", AsyncMock(return_value=mock_client)):
        assert await client.configure_wifi("CCBA97932A96", "ssid", "pw") is True
    # 4 wifi writes (init, ssid, pw, apply) to EE01
    assert mock_client.write_gatt_char.await_count == 4


@pytest.mark.asyncio
async def test_configure_wifi_direct_mac_and_notif_response():
    adapter = make_adapter()
    adapter.discovered_devices.return_value = []
    client = WyBotBLEClient(adapter)

    def set_notif(*_a, **_k):
        client._last_notification_data = STATUS_BROADCAST

    mock_client = make_client(write_side_effect=set_notif)
    with patch.object(bc, "establish_connection", AsyncMock(return_value=mock_client)):
        assert await client.configure_wifi("CCBA97932A96", "ssid", "pw") is True


@pytest.mark.asyncio
async def test_configure_wifi_not_found():
    adapter = make_adapter()
    adapter.discovered_devices.return_value = []
    adapter.device_from_address.return_value = None
    client = WyBotBLEClient(adapter)
    assert await client.configure_wifi("not-a-mac", "ssid", "pw") is False


@pytest.mark.asyncio
async def test_configure_wifi_ble_device_none():
    adapter = make_adapter()
    dev = SimpleNamespace(name="WyBot-CCBA97932A96", address="CC:BA:97:93:2A:96")
    adapter.discovered_devices.return_value = [dev]
    adapter.device_from_address.return_value = None
    client = WyBotBLEClient(adapter)
    assert await client.configure_wifi("CCBA97932A96", "ssid", "pw") is False


@pytest.mark.asyncio
async def test_configure_wifi_not_connected():
    adapter = make_adapter()
    dev = SimpleNamespace(name="WyBot-CCBA97932A96", address="CC:BA:97:93:2A:96")
    adapter.discovered_devices.return_value = [dev]
    client = WyBotBLEClient(adapter)
    mock_client = make_client(connected=False)
    with patch.object(bc, "establish_connection", AsyncMock(return_value=mock_client)):
        assert await client.configure_wifi("CCBA97932A96", "ssid", "pw") is False


@pytest.mark.asyncio
async def test_configure_wifi_write_fails_returns_false():
    adapter = make_adapter()
    dev = SimpleNamespace(name="WyBot-CCBA97932A96", address="CC:BA:97:93:2A:96")
    adapter.discovered_devices.return_value = [dev]
    client = WyBotBLEClient(adapter)
    mock_client = make_client()
    # EE01 write raises BleakError -> _write_binary_command_to_ee01 returns False
    mock_client.write_gatt_char = AsyncMock(side_effect=BleakError("nope"))
    with patch.object(bc, "establish_connection", AsyncMock(return_value=mock_client)):
        assert await client.configure_wifi("CCBA97932A96", "ssid", "pw") is False


@pytest.mark.asyncio
async def test_configure_wifi_timeout():
    adapter = make_adapter()
    dev = SimpleNamespace(name="WyBot-CCBA97932A96", address="CC:BA:97:93:2A:96")
    adapter.discovered_devices.return_value = [dev]
    client = WyBotBLEClient(adapter)
    with patch.object(
        bc, "establish_connection", AsyncMock(side_effect=TimeoutError())
    ):
        assert await client.configure_wifi("CCBA97932A96", "ssid", "pw") is False


@pytest.mark.asyncio
async def test_configure_wifi_bleak_error():
    adapter = make_adapter()
    dev = SimpleNamespace(name="WyBot-CCBA97932A96", address="CC:BA:97:93:2A:96")
    adapter.discovered_devices.return_value = [dev]
    client = WyBotBLEClient(adapter)
    with patch.object(
        bc, "establish_connection", AsyncMock(side_effect=BleakError("err"))
    ):
        assert await client.configure_wifi("CCBA97932A96", "ssid", "pw") is False


@pytest.mark.asyncio
async def test_configure_wifi_outer_oserror():
    adapter = make_adapter()
    client = WyBotBLEClient(adapter)
    with patch.object(client, "scan_for_device", AsyncMock(side_effect=OSError("boom"))):
        assert await client.configure_wifi("CCBA97932A96", "ssid", "pw") is False


# ===========================================================================
# send_command
# ===========================================================================
def _dp():
    return GenericDP(DP(id=0, type=4, len=1, data="03"))


@pytest.mark.asyncio
async def test_send_command_in_progress():
    client = WyBotBLEClient(make_adapter())
    client._wake_in_progress = True
    assert await client.send_command("CCBA97932A96", _dp()) == (False, None)


@pytest.mark.asyncio
async def test_send_command_success_with_parsed_response():
    adapter = make_adapter()
    dev = SimpleNamespace(name="WyBot-CCBA97932A96", address="CC:BA:97:93:2A:96")
    adapter.discovered_devices.return_value = [dev]
    client = WyBotBLEClient(adapter)

    def set_notif(*_a, **_k):
        client._last_notification_data = STATUS_BROADCAST

    mock_client = make_client(write_side_effect=set_notif)
    with patch.object(bc, "establish_connection", AsyncMock(return_value=mock_client)):
        ok, dps = await client.send_command("CCBA97932A96", _dp())
    assert ok is True
    assert dps == [{"id": 0, "type": 4, "len": 1, "data": "03"}]


@pytest.mark.asyncio
async def test_send_command_success_no_response():
    adapter = make_adapter()
    dev = SimpleNamespace(name="WyBot-CCBA97932A96", address="CC:BA:97:93:2A:96")
    adapter.discovered_devices.return_value = [dev]
    client = WyBotBLEClient(adapter)
    mock_client = make_client()  # write doesn't set notification data
    with patch.object(bc, "establish_connection", AsyncMock(return_value=mock_client)):
        ok, dps = await client.send_command("CCBA97932A96", _dp())
    assert ok is True
    assert dps is None


@pytest.mark.asyncio
async def test_send_command_direct_mac():
    adapter = make_adapter()
    adapter.discovered_devices.return_value = []
    client = WyBotBLEClient(adapter)
    mock_client = make_client()
    with patch.object(bc, "establish_connection", AsyncMock(return_value=mock_client)):
        ok, _ = await client.send_command("CCBA97932A96", _dp())
    assert ok is True


@pytest.mark.asyncio
async def test_send_command_not_found():
    adapter = make_adapter()
    adapter.discovered_devices.return_value = []
    adapter.device_from_address.return_value = None
    client = WyBotBLEClient(adapter)
    assert await client.send_command("not-a-mac", _dp()) == (False, None)


@pytest.mark.asyncio
async def test_send_command_ble_device_none():
    adapter = make_adapter()
    dev = SimpleNamespace(name="WyBot-CCBA97932A96", address="CC:BA:97:93:2A:96")
    adapter.discovered_devices.return_value = [dev]
    adapter.device_from_address.return_value = None
    client = WyBotBLEClient(adapter)
    assert await client.send_command("CCBA97932A96", _dp()) == (False, None)


@pytest.mark.asyncio
async def test_send_command_not_connected():
    adapter = make_adapter()
    dev = SimpleNamespace(name="WyBot-CCBA97932A96", address="CC:BA:97:93:2A:96")
    adapter.discovered_devices.return_value = [dev]
    client = WyBotBLEClient(adapter)
    mock_client = make_client(connected=False)
    with patch.object(bc, "establish_connection", AsyncMock(return_value=mock_client)):
        assert await client.send_command("CCBA97932A96", _dp()) == (False, None)


@pytest.mark.asyncio
async def test_send_command_write_failed():
    adapter = make_adapter()
    dev = SimpleNamespace(name="WyBot-CCBA97932A96", address="CC:BA:97:93:2A:96")
    adapter.discovered_devices.return_value = [dev]
    client = WyBotBLEClient(adapter)
    mock_client = make_client()
    mock_client.write_gatt_char = AsyncMock(side_effect=OSError("write fail"))
    with patch.object(bc, "establish_connection", AsyncMock(return_value=mock_client)):
        assert await client.send_command("CCBA97932A96", _dp()) == (False, None)


@pytest.mark.asyncio
async def test_send_command_timeout():
    adapter = make_adapter()
    dev = SimpleNamespace(name="WyBot-CCBA97932A96", address="CC:BA:97:93:2A:96")
    adapter.discovered_devices.return_value = [dev]
    client = WyBotBLEClient(adapter)
    with patch.object(
        bc, "establish_connection", AsyncMock(side_effect=TimeoutError())
    ):
        assert await client.send_command("CCBA97932A96", _dp()) == (False, None)


@pytest.mark.asyncio
async def test_send_command_bleak_error():
    adapter = make_adapter()
    dev = SimpleNamespace(name="WyBot-CCBA97932A96", address="CC:BA:97:93:2A:96")
    adapter.discovered_devices.return_value = [dev]
    client = WyBotBLEClient(adapter)
    with patch.object(
        bc, "establish_connection", AsyncMock(side_effect=BleakError("err"))
    ):
        assert await client.send_command("CCBA97932A96", _dp()) == (False, None)


@pytest.mark.asyncio
async def test_send_command_outer_oserror():
    adapter = make_adapter()
    client = WyBotBLEClient(adapter)
    with patch.object(client, "scan_for_device", AsyncMock(side_effect=OSError("boom"))):
        assert await client.send_command("CCBA97932A96", _dp()) == (False, None)


# ===========================================================================
# query_status
# ===========================================================================
@pytest.mark.asyncio
async def test_query_status_in_progress():
    client = WyBotBLEClient(make_adapter())
    client._wake_in_progress = True
    assert await client.query_status("CCBA97932A96") is None


@pytest.mark.asyncio
async def test_query_status_success():
    adapter = make_adapter()
    dev = SimpleNamespace(name="WyBot-CCBA97932A96", address="CC:BA:97:93:2A:96")
    adapter.discovered_devices.return_value = [dev]
    client = WyBotBLEClient(adapter)

    def set_status(*_a, **_k):
        client._last_status_data = STATUS_BROADCAST

    mock_client = make_client(write_side_effect=set_status)
    with patch.object(bc, "establish_connection", AsyncMock(return_value=mock_client)):
        dps = await client.query_status("CCBA97932A96")
    # first status parse + cleaning-mode query parse -> two DP entries
    assert dps == [
        {"id": 0, "type": 4, "len": 1, "data": "03"},
        {"id": 0, "type": 4, "len": 1, "data": "03"},
    ]


@pytest.mark.asyncio
async def test_query_status_no_status_broadcast():
    adapter = make_adapter()
    dev = SimpleNamespace(name="WyBot-CCBA97932A96", address="CC:BA:97:93:2A:96")
    adapter.discovered_devices.return_value = [dev]
    client = WyBotBLEClient(adapter)
    mock_client = make_client()  # never sets _last_status_data
    with patch.object(bc, "establish_connection", AsyncMock(return_value=mock_client)):
        assert await client.query_status("CCBA97932A96") is None


@pytest.mark.asyncio
async def test_query_status_direct_mac_not_found_with_bt():
    adapter = make_adapter()
    adapter.discovered_devices.return_value = []
    adapter.device_from_address.return_value = None
    client = WyBotBLEClient(adapter)
    assert await client.query_status("CCBA97932A96") is None


@pytest.mark.asyncio
async def test_query_status_non_mac_name():
    adapter = make_adapter()
    adapter.discovered_devices.return_value = []
    adapter.device_from_address.return_value = None
    client = WyBotBLEClient(adapter)
    assert await client.query_status("not-a-mac") is None


@pytest.mark.asyncio
async def test_query_status_ble_device_none():
    adapter = make_adapter()
    dev = SimpleNamespace(name="WyBot-CCBA97932A96", address="CC:BA:97:93:2A:96")
    adapter.discovered_devices.return_value = [dev]
    adapter.device_from_address.return_value = None
    client = WyBotBLEClient(adapter)
    assert await client.query_status("CCBA97932A96") is None


@pytest.mark.asyncio
async def test_query_status_not_connected():
    adapter = make_adapter()
    dev = SimpleNamespace(name="WyBot-CCBA97932A96", address="CC:BA:97:93:2A:96")
    adapter.discovered_devices.return_value = [dev]
    client = WyBotBLEClient(adapter)
    mock_client = make_client(connected=False)
    with patch.object(bc, "establish_connection", AsyncMock(return_value=mock_client)):
        assert await client.query_status("CCBA97932A96") is None


@pytest.mark.asyncio
async def test_query_status_timeout():
    adapter = make_adapter()
    dev = SimpleNamespace(name="WyBot-CCBA97932A96", address="CC:BA:97:93:2A:96")
    adapter.discovered_devices.return_value = [dev]
    client = WyBotBLEClient(adapter)
    with patch.object(
        bc, "establish_connection", AsyncMock(side_effect=TimeoutError())
    ):
        assert await client.query_status("CCBA97932A96") is None


@pytest.mark.asyncio
async def test_query_status_bleak_error():
    adapter = make_adapter()
    dev = SimpleNamespace(name="WyBot-CCBA97932A96", address="CC:BA:97:93:2A:96")
    adapter.discovered_devices.return_value = [dev]
    client = WyBotBLEClient(adapter)
    with patch.object(
        bc, "establish_connection", AsyncMock(side_effect=BleakError("err"))
    ):
        assert await client.query_status("CCBA97932A96") is None


@pytest.mark.asyncio
async def test_query_status_outer_oserror():
    adapter = make_adapter()
    client = WyBotBLEClient(adapter)
    with patch.object(client, "scan_for_device", AsyncMock(side_effect=OSError("boom"))):
        assert await client.query_status("CCBA97932A96") is None


# ===========================================================================
# Service-discovery / notification / write helpers (edge coverage)
# ===========================================================================
@pytest.mark.asyncio
async def test_log_device_services_unknown_and_notify_char():
    client = WyBotBLEClient(make_adapter())
    known_char = make_char(bc.CHAR_UUID_1002, ["notify"])
    unknown_char = make_char("0000abcd-0000-1000-8000-00805f9b34fb", ["read"])
    services = [
        make_service(bc.SERVICE_UUID_K1, [known_char]),
        make_service("0000dead-0000-1000-8000-00805f9b34fb", [unknown_char]),
    ]
    mock_client = make_client(services=services)
    info = await client._log_device_services(mock_client)
    assert info["found_primary_service"] is True
    assert info["found_notification_char"] is True


@pytest.mark.asyncio
async def test_enable_notifications_none_found():
    client = WyBotBLEClient(make_adapter())
    # WyBot service but no notify-capable char, plus a non-WyBot service
    ee01 = make_char(bc.CHAR_UUID_EE01, ["write"])
    other = make_service("0000dead-0000-1000-8000-00805f9b34fb", [])
    services = [make_service(bc.SERVICE_UUID_EE, [ee01]), other]
    mock_client = make_client(services=services)
    assert await client._enable_notifications(mock_client) is False


@pytest.mark.asyncio
async def test_enable_notifications_start_notify_raises():
    client = WyBotBLEClient(make_adapter())
    ff01 = make_char(bc.CHAR_UUID_FF01, ["notify"])
    services = [make_service(bc.SERVICE_UUID_FF, [ff01])]
    mock_client = make_client(services=services)
    mock_client.start_notify = AsyncMock(side_effect=BleakError("boom"))
    assert await client._enable_notifications(mock_client) is False


@pytest.mark.asyncio
async def test_write_binary_command_ee01_not_found():
    client = WyBotBLEClient(make_adapter())
    # EE service present but no EE01 char
    services = [make_service(bc.SERVICE_UUID_EE, [])]
    mock_client = make_client(services=services)
    assert await client._write_binary_command_to_ee01(mock_client, b"\x01") is False


@pytest.mark.asyncio
async def test_write_binary_command_ee01_not_writable():
    client = WyBotBLEClient(make_adapter())
    ee01 = make_char(bc.CHAR_UUID_EE01, ["read"])  # not writable
    services = [make_service(bc.SERVICE_UUID_EE, [ee01])]
    mock_client = make_client(services=services)
    assert await client._write_binary_command_to_ee01(mock_client, b"\x01") is False


@pytest.mark.asyncio
async def test_write_binary_command_ee01_write_without_response():
    client = WyBotBLEClient(make_adapter())
    ee01 = make_char(bc.CHAR_UUID_EE01, ["write-without-response"])
    ff01 = make_char(bc.CHAR_UUID_FF01, ["notify"])
    # Non-EE service first exercises the service-skip branch.
    services = [
        make_service(bc.SERVICE_UUID_FF, [ff01]),
        make_service(bc.SERVICE_UUID_EE, [ee01]),
    ]
    mock_client = make_client(services=services)
    assert await client._write_binary_command_to_ee01(mock_client, b"\x01") is True
    _, kwargs = mock_client.write_gatt_char.call_args
    assert kwargs["response"] is False


@pytest.mark.asyncio
async def test_wake_device_no_known_services():
    """Cover the 'primary service not found', no-notifications, no-wake-write branches."""
    adapter = make_adapter()
    dev = SimpleNamespace(name="WyBot-CCBA97932A96", address="CC:BA:97:93:2A:96")
    adapter.discovered_devices.return_value = [dev]
    client = WyBotBLEClient(adapter)
    unknown = make_service(
        "0000dead-0000-1000-8000-00805f9b34fb",
        [make_char("0000dead-0000-1000-8000-00805f9b34fb", ["write"])],
    )
    mock_client = make_client(services=[unknown])
    with patch.object(bc, "establish_connection", AsyncMock(return_value=mock_client)):
        assert await client.wake_device("CCBA97932A96") is True


@pytest.mark.asyncio
async def test_query_status_direct_mac_success():
    """Scan misses but direct MAC lookup succeeds -> DirectDevice path."""
    adapter = make_adapter()
    adapter.discovered_devices.return_value = []
    client = WyBotBLEClient(adapter)

    def set_status(*_a, **_k):
        client._last_status_data = STATUS_BROADCAST

    mock_client = make_client(write_side_effect=set_status)
    with patch.object(bc, "establish_connection", AsyncMock(return_value=mock_client)):
        dps = await client.query_status("CCBA97932A96")
    assert dps is not None


def _counted_write(fail_indices):
    """Return a write_gatt_char side effect that raises BleakError on given call indices."""
    state = {"n": 0}

    def _write(*_a, **_k):
        idx = state["n"]
        state["n"] += 1
        if idx in fail_indices:
            raise BleakError("write fail")

    return _write


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "fail_index",
    [1, 2, 3],  # ssid step, password step, apply step
)
async def test_configure_wifi_step_failures(fail_index):
    adapter = make_adapter()
    dev = SimpleNamespace(name="WyBot-CCBA97932A96", address="CC:BA:97:93:2A:96")
    adapter.discovered_devices.return_value = [dev]
    client = WyBotBLEClient(adapter)
    mock_client = make_client(write_side_effect=_counted_write({fail_index}))
    with patch.object(bc, "establish_connection", AsyncMock(return_value=mock_client)):
        assert await client.configure_wifi("CCBA97932A96", "ssid", "pw") is False


@pytest.mark.asyncio
async def test_write_wake_commands_no_writable():
    client = WyBotBLEClient(make_adapter())
    # WyBot service but EE01 not writable
    ee01 = make_char(bc.CHAR_UUID_EE01, ["read"])
    services = [make_service(bc.SERVICE_UUID_EE, [ee01])]
    mock_client = make_client(services=services)
    assert await client._write_wake_commands(mock_client) is False


@pytest.mark.asyncio
async def test_write_wake_commands_write_raises():
    client = WyBotBLEClient(make_adapter())
    ee01 = make_char(bc.CHAR_UUID_EE01, ["write"])
    services = [make_service(bc.SERVICE_UUID_EE, [ee01])]
    mock_client = make_client(services=services)
    mock_client.write_gatt_char = AsyncMock(side_effect=BleakError("fail"))
    assert await client._write_wake_commands(mock_client) is False
