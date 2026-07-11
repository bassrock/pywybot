"""pywybot — a Python client library for WyBot pool robots.

Provides HTTP (cloud), MQTT, and Bluetooth (BLE) clients plus the data models
used to talk to WyBot devices. The BLE client is transport-agnostic: inject a
:class:`~wybot.bluetooth.BluetoothAdapter` to supply device discovery.
"""

from __future__ import annotations

from . import dp_models, models
from .ble_client import WyBotBLEClient
from .bluetooth import BluetoothAdapter
from .exceptions import WybotAuthError, WybotConnectionError, WybotError
from .http_client import WyBotHTTPClient
from .mqtt_client import WyBotMQTTClient

__version__ = "0.1.0"

__all__ = [
    "BluetoothAdapter",
    "WyBotBLEClient",
    "WyBotHTTPClient",
    "WyBotMQTTClient",
    "WybotAuthError",
    "WybotConnectionError",
    "WybotError",
    "dp_models",
    "models",
]
