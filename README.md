# pywybot

A Python client library for [WyBot](https://www.wybotpool.com/) pool robots.
It provides the transport clients and data models used to talk to WyBot devices:

- **HTTP** (`WyBotHTTPClient`) — cloud login and device/status retrieval.
- **MQTT** (`WyBotMQTTClient`) — real-time status and command relay.
- **BLE** (`WyBotBLEClient`) — local Bluetooth control using the AA55 binary protocol.

This library is transport-agnostic for Bluetooth: you inject a
`BluetoothAdapter` that knows how to discover and resolve BLE devices, so the
same client works under Home Assistant's Bluetooth stack or a bare `bleak`
setup. It was extracted from the
[hass-wybot](https://github.com/bassrock/hass-wybot) Home Assistant integration.

## Installation

```bash
pip install pywybot
```

## Usage

The client is fully async (aiohttp / aiomqtt).

### Cloud (HTTP)

```python
import aiohttp
from wybot import WyBotHTTPClient, WybotAuthError, WybotConnectionError

async with aiohttp.ClientSession() as session:
    # Pass a session to reuse it (e.g. Home Assistant's shared session);
    # omit it and the client creates and owns one (call await client.close()).
    client = WyBotHTTPClient("you@example.com", "password", session=session)
    try:
        await client.authenticate()
    except WybotAuthError:
        ...  # invalid credentials
    except WybotConnectionError:
        ...  # network/server error

    groups = await client.get_indexed_current_grouped_devices()
```

The HTTP client raises `WybotAuthError` for rejected credentials and
`WybotConnectionError` for network failures (both subclass `WybotError`).

### Bluetooth (BLE)

`WyBotBLEClient` takes a `BluetoothAdapter`:

```python
from wybot import WyBotBLEClient, BluetoothAdapter


class MyAdapter:  # implements the BluetoothAdapter protocol
    def scanner_count(self) -> int: ...
    def discovered_devices(self): ...            # -> list[bleak BLEDevice]
    def device_from_address(self, address): ...  # -> BLEDevice | None


ble = WyBotBLEClient(MyAdapter())
```

Home Assistant supplies an adapter backed by `homeassistant.components.bluetooth`.

## License

GPL-3.0-or-later. See [LICENSE](LICENSE).
