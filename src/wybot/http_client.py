"""Async library for interacting with the WyBot HTTP API."""

from __future__ import annotations

import asyncio
import hashlib
import logging
import time

import aiohttp

from .const import TIMEOUT
from .exceptions import WybotAuthError, WybotConnectionError, WybotError
from .models import DevicesResponse, Group, LoginResponse

_LOGGER = logging.getLogger(__name__)

# Retry configuration
MAX_RETRIES = 3
INITIAL_RETRY_DELAY = 1.0
MAX_RETRY_DELAY = 10.0

# Send the user/password to get the Token
AUTH_URL = "https://api.wybotpool.com/api/user/login"

# Given a Pool ID, get all the devices and the status (append the user id)
DEVICES_URL = "https://api.wybotpool.com/api/group/"

# User notification endpoint - may be used for presence registration
NOTIFICATION_URL = "https://api.wybotpool.com/api/user/notification"

DEFAULT_HEADER = {
    "Content-Type": "application/json",
    "User-Agent": "WYBOT/13 CFNetwork/1498.700.2 Darwin/23.6.0",
}

TOKEN_REFRESH_INTERVAL = 50 * 60  # Proactively refresh token after 50 minutes


class WyBotHTTPClient:
    """Async client for interacting with the WyBot API."""

    def __init__(
        self,
        username: str,
        password: str,
        session: aiohttp.ClientSession | None = None,
    ) -> None:
        """Initialize the WyBot API client.

        Args:
            username: WyBot account email.
            password: WyBot account password.
            session: Optional aiohttp session to use. When provided (e.g. Home
                Assistant's shared session) it is not closed by this client.
        """
        self._username = username
        self._password = password
        self._token: str | None = None
        self._user_id: str | None = None
        self._token_obtained_at: float = 0.0
        self._session = session
        self._owns_session = session is None

    @property
    def user_id(self) -> str | None:
        """Return the authenticated account's user id, if available."""
        return self._user_id

    def _get_session(self) -> aiohttp.ClientSession:
        """Return the aiohttp session, creating an owned one if needed."""
        if self._session is None:
            self._session = aiohttp.ClientSession()
            self._owns_session = True
        return self._session

    async def close(self) -> None:
        """Close the owned aiohttp session (no-op for an injected session)."""
        if self._owns_session and self._session is not None:
            await self._session.close()
            self._session = None

    async def authenticate(self) -> bool:
        """Authenticate with the host.

        Returns True on success. Raises WybotAuthError if the credentials are
        rejected and WybotConnectionError if the API cannot be reached.
        """
        login_response = await self.login()
        token = login_response.metadata.token if login_response.metadata else None
        user_id = (
            login_response.metadata.user_id if login_response.metadata else None
        )
        if token is None or user_id is None:
            raise WybotAuthError("Login succeeded but no token was returned")
        self._token = token
        self._user_id = user_id
        self._token_obtained_at = time.monotonic()
        return True

    async def _refresh_token_if_needed(self) -> bool:
        """Refresh token proactively before expiry or if missing.

        Raises WybotAuthError / WybotConnectionError on failure.
        """
        if self._token is None or self._user_id is None:
            _LOGGER.debug("Token missing, re-authenticating")
            return await self.authenticate()
        token_age = time.monotonic() - self._token_obtained_at
        if token_age > TOKEN_REFRESH_INTERVAL:
            _LOGGER.info(
                "Token age %.0fs exceeds %ds, proactively refreshing",
                token_age,
                TOKEN_REFRESH_INTERVAL,
            )
            return await self.authenticate()
        return True

    async def login(self) -> LoginResponse:
        """Authenticate the user and retrieve a token with retry logic.

        Raises:
            WybotAuthError: the credentials were rejected by the API.
            WybotConnectionError: the API could not be reached after retries.
        """
        _LOGGER.debug("Grabbing a token with a user and password")
        if not self._password:
            raise WybotAuthError("Password is not set")
        md5_hex = hashlib.md5(self._password.encode("utf-8")).hexdigest()
        auth_data = {"username": self._username, "password": md5_hex}
        session = self._get_session()

        delay = INITIAL_RETRY_DELAY
        for attempt in range(MAX_RETRIES):
            try:
                async with session.post(
                    AUTH_URL,
                    json=auth_data,
                    headers=DEFAULT_HEADER,
                    allow_redirects=False,
                    timeout=aiohttp.ClientTimeout(total=TIMEOUT),
                ) as response:
                    if response.status == 200:
                        return LoginResponse(**await response.json(content_type=None))
                    if response.status in (401, 403):
                        raise WybotAuthError(
                            f"Authentication rejected with status {response.status}"
                        )
                    status = response.status
                    text = await response.text()
                _LOGGER.warning(
                    "Login attempt %d failed with status %d: %s",
                    attempt + 1,
                    status,
                    text,
                )
                if attempt < MAX_RETRIES - 1:
                    await asyncio.sleep(delay)
                    delay = min(delay * 2, MAX_RETRY_DELAY)
                else:
                    raise WybotConnectionError(
                        f"Login failed after {MAX_RETRIES} attempts: HTTP {status}"
                    )
            except (aiohttp.ClientError, TimeoutError) as err:
                _LOGGER.warning("Login request error on attempt %d: %s", attempt + 1, err)
                if attempt < MAX_RETRIES - 1:
                    await asyncio.sleep(delay)
                    delay = min(delay * 2, MAX_RETRY_DELAY)
                else:
                    raise WybotConnectionError(
                        f"Could not reach WyBot API after {MAX_RETRIES} attempts: {err}"
                    ) from err
            except WybotError:
                raise
            except Exception as err:  # noqa: BLE001
                raise WybotConnectionError(
                    f"Unexpected error during login: {err}"
                ) from err

        raise WybotConnectionError("Login failed")

    async def get_devices_and_status(self) -> DevicesResponse:
        """Grab all devices and statuses with retry logic and token refresh.

        Raises:
            WybotAuthError: the stored credentials are no longer valid.
            WybotConnectionError: the API could not be reached after retries.
        """
        await self._refresh_token_if_needed()

        if self._user_id is None:
            raise WybotAuthError("User ID is not set")

        device_url = DEVICES_URL + str(self._user_id)
        _LOGGER.debug("Grabbing devices and statuses: %s", device_url)
        session = self._get_session()

        delay = INITIAL_RETRY_DELAY
        for attempt in range(MAX_RETRIES):
            try:
                async with session.get(
                    device_url,
                    headers={**DEFAULT_HEADER, "Authorization": f"token {self._token}"},
                    allow_redirects=False,
                    timeout=aiohttp.ClientTimeout(total=TIMEOUT),
                ) as response:
                    if response.status == 200:
                        return DevicesResponse(
                            **await response.json(content_type=None)
                        )
                    if response.status == 401:
                        # Token expired; re-auth (raises WybotAuthError if the
                        # stored credentials are no longer valid) then retry.
                        _LOGGER.info("Token expired, refreshing authentication")
                        await self.authenticate()
                        continue
                    status = response.status
                    text = await response.text()
                _LOGGER.warning(
                    "Get devices attempt %d failed with status %d: %s",
                    attempt + 1,
                    status,
                    text,
                )
                if attempt < MAX_RETRIES - 1:
                    await asyncio.sleep(delay)
                    delay = min(delay * 2, MAX_RETRY_DELAY)
                else:
                    raise WybotConnectionError(
                        f"Error getting devices after {MAX_RETRIES} attempts: HTTP {status}"
                    )
            except (aiohttp.ClientError, TimeoutError) as err:
                _LOGGER.warning(
                    "Get devices request error on attempt %d: %s", attempt + 1, err
                )
                if attempt < MAX_RETRIES - 1:
                    await asyncio.sleep(delay)
                    delay = min(delay * 2, MAX_RETRY_DELAY)
                else:
                    raise WybotConnectionError(
                        f"Could not reach WyBot API after {MAX_RETRIES} attempts: {err}"
                    ) from err
            except WybotError:
                raise
            except Exception as err:  # noqa: BLE001
                raise WybotConnectionError(
                    f"Unexpected error getting devices: {err}"
                ) from err

        raise WybotConnectionError("Failed to get devices after retries")

    async def get_indexed_current_grouped_devices(self) -> dict[str, Group]:
        """Return a dictionary of devices indexed by the grouped device_id.

        Raises WybotAuthError / WybotConnectionError on failure.
        """
        response = await self.get_devices_and_status()
        return {group.id: group for group in response.metadata.groups}

    async def register_presence(self) -> bool:
        """Register presence with the cloud server.

        Signals to the WyBot cloud that we're actively listening, which may help
        ensure MQTT messages are relayed when devices come online. Best-effort:
        returns False instead of raising.
        """
        try:
            await self._refresh_token_if_needed()
        except WybotError as err:
            _LOGGER.debug("Failed to refresh token for presence registration: %s", err)
            return False

        if self._user_id is None:
            _LOGGER.debug("User ID not set for presence registration")
            return False

        session = self._get_session()
        for attempt in range(2):
            try:
                async with session.post(
                    NOTIFICATION_URL,
                    headers={**DEFAULT_HEADER, "Authorization": f"token {self._token}"},
                    json={"userId": self._user_id},
                    allow_redirects=False,
                    timeout=aiohttp.ClientTimeout(total=TIMEOUT),
                ) as response:
                    if response.status == 200:
                        _LOGGER.debug("Presence registered successfully")
                        return True
                    _LOGGER.debug(
                        "Presence registration failed (attempt %d): status=%d",
                        attempt + 1,
                        response.status,
                    )
            except Exception as err:  # noqa: BLE001
                _LOGGER.debug(
                    "Presence registration error (attempt %d): %s", attempt + 1, err
                )
            if attempt == 0:
                await asyncio.sleep(2)
        return False
