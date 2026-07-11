"""Bluetooth adapter interface for the WyBot BLE client.

The library is transport-agnostic: callers inject an adapter that knows how to
discover and resolve BLE devices. Home Assistant supplies an adapter backed by
its Bluetooth stack; other callers can back one with bleak directly.
"""

from __future__ import annotations

from typing import Protocol

from bleak.backends.device import BLEDevice


class BluetoothAdapter(Protocol):
    """Provides BLE device discovery/resolution to :class:`WyBotBLEClient`."""

    def scanner_count(self) -> int:
        """Return the number of active connectable BLE scanners."""

    def discovered_devices(self) -> list[BLEDevice]:
        """Return the currently discovered connectable BLE devices."""

    def device_from_address(self, address: str) -> BLEDevice | None:
        """Resolve a connectable BLEDevice for ``address``, or None."""
