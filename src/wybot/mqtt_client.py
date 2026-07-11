"""Async library for interacting with the WyBot MQTT API."""

from __future__ import annotations

import asyncio
from collections.abc import Callable
import json
import logging
import time
from typing import Any
import uuid

import aiomqtt

_LOGGER = logging.getLogger(__name__)

MQTT_URL = "mqtt.wybotpool.com"

# User/Password to authenticate from the iOS/Android app to the MQTT server.
# These are hardcoded to the same value for every user in the mobile apps.
USERNAME = "wyindustry"
PASWORD = "nwe_GTG4faf2qyx8ugx"

# Reconnect backoff bounds (seconds).
INITIAL_RECONNECT_DELAY = 1.0
MAX_RECONNECT_DELAY = 60.0

# Flag to disable MQTT command sending (useful for recording app traffic).
DISABLE_MQTT_COMMANDS = False


class WyBotMQTTClient:
    """Async client for interacting with the WyBot MQTT API."""

    def __init__(self, on_message: Callable[[str, Any], None]) -> None:
        """Initialize the WyBot MQTT client.

        Args:
            on_message: Callback invoked with ``(topic, payload)`` for each
                message received (payload is a parsed dict or the raw bytes).
        """
        self._on_message = on_message
        self._subscriptions: set[str] = set()
        self._devices: set[str] = set()
        self._client: aiomqtt.Client | None = None
        self._task: asyncio.Task[None] | None = None
        self._connected: bool = False
        self._stop: bool = False
        self._identifier = f"wybot-{uuid.uuid4()}"

    async def connect(self) -> None:
        """Start the background connection/reconnection task (idempotent)."""
        if self._task is not None and not self._task.done():
            return
        self._stop = False
        self._task = asyncio.create_task(self._run())

    async def _run(self) -> None:
        """Maintain the MQTT connection, reconnecting with backoff."""
        delay = INITIAL_RECONNECT_DELAY
        while not self._stop:
            try:
                async with aiomqtt.Client(
                    hostname=MQTT_URL,
                    username=USERNAME,
                    password=PASWORD,
                    identifier=self._identifier,
                    clean_session=True,
                ) as client:
                    self._client = client
                    self._connected = True
                    delay = INITIAL_RECONNECT_DELAY
                    _LOGGER.info("MQTT connected successfully")
                    # Re-subscribe and re-request statuses on (re)connect.
                    for topic in self._subscriptions:
                        await client.subscribe(topic)
                    for device in list(self._devices):
                        await self.ensure_device_sends_statuses(device)
                    async for message in client.messages:
                        self._handle_message(message)
            except aiomqtt.MqttError as err:
                _LOGGER.warning(
                    "MQTT connection error: %s; reconnecting in %.0fs", err, delay
                )
            except asyncio.CancelledError:
                raise
            except Exception as err:  # noqa: BLE001
                _LOGGER.error("Unexpected MQTT error: %s; reconnecting", err)
            finally:
                self._connected = False
                self._client = None
            if self._stop:
                break
            await asyncio.sleep(delay)
            delay = min(delay * 2, MAX_RECONNECT_DELAY)

    def is_connected(self) -> bool:
        """Return whether the client currently has a live connection."""
        return self._connected

    async def disconnect(self) -> None:
        """Stop the background task and disconnect."""
        _LOGGER.info("Stopping MQTT client")
        self._stop = True
        if self._task is not None:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
            self._task = None
        self._connected = False
        self._client = None

    async def subscribe_for_device(self, device_id: str) -> None:
        """Subscribe to a device (idempotent — safe to call multiple times)."""
        if device_id in self._devices:
            _LOGGER.debug("Already subscribed to device %s", device_id)
            return
        _LOGGER.debug("Subscribing to wybot mqtt for device %s", device_id)
        topics = [
            f"/will/{device_id}",
            f"/device/DATA/send_transparent_data/{device_id}",
            f"/device/DATA/recv_transparent_query_data/{device_id}",
            f"/device/DATA/recv_transparent_cmd_data/{device_id}",
            f"/device/OTA/post_update_progress/{device_id}",
            f"/device/OTA/notify_ready_to_update/{device_id}",
        ]
        for topic in topics:
            self._subscriptions.add(topic)
            if self._client is not None:
                await self._client.subscribe(topic)
        self._devices.add(device_id)
        await self.ensure_device_sends_statuses(device_id)

    async def ensure_device_sends_statuses(self, device_id: str) -> None:
        """Ask a device to publish its current data points."""
        _LOGGER.debug("Ensuring device sends statuses %s", device_id)
        # Query DPs individually in the order the iOS app uses (DP 1 twice),
        # plus battery/dock/solar DPs and S2 Pro extras.
        query_dps = [0, 1, 79, 1, 0, 77, 50, 11, 131, 209, 212, 213, 214, 221, 222]
        for dp_id in query_dps:
            await self.send_query_command_for_device(
                device_id,
                {"ts": int(time.time()), "cmd": 9, "dp": [{"id": dp_id}]},
            )

    async def send_query_command_for_device(
        self, device_id: str, command: dict[str, Any]
    ) -> None:
        """Send a query command to a device."""
        await self._publish(
            f"/device/DATA/recv_transparent_query_data/{device_id}", command, "query"
        )

    async def send_write_command_for_device(
        self, device_id: str, command: dict[str, Any]
    ) -> None:
        """Send a write command to a device."""
        await self._publish(
            f"/device/DATA/recv_transparent_cmd_data/{device_id}", command, "write"
        )

    async def _publish(self, topic: str, command: dict[str, Any], kind: str) -> None:
        """Publish a JSON command to a topic if connected."""
        if DISABLE_MQTT_COMMANDS:
            _LOGGER.debug("MQTT commands disabled, skipping %s: %s", kind, command)
            return
        if not self.is_connected() or self._client is None:
            _LOGGER.debug("Not connected, cannot send %s command", kind)
            return
        try:
            await self._client.publish(topic, json.dumps(command))
        except aiomqtt.MqttError as err:
            _LOGGER.error("Failed to publish %s command: %s", kind, err)

    def _handle_message(self, message: aiomqtt.Message) -> None:
        """Handle an incoming message from the MQTT server."""
        try:
            payload: Any = json.loads(message.payload)
        except (json.JSONDecodeError, UnicodeDecodeError, TypeError):
            payload = message.payload
        self._on_message(str(message.topic), payload)
