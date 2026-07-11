"""Library for interacting with the WyBot API."""

import hashlib
import logging
import time

import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

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


# Get all pools on the account
POOLS_URL = "https://api.wybotpool.com/api/env/pool"

# Given a Pool ID, get all the devices and the status
# The end should append the user id
DEVICES_URL = "https://api.wybotpool.com/api/group/"

# Send commands
COMMAND_URL = "https://api.wybotpool.com/api/device/ao"

# User notification endpoint - may be used for presence registration
NOTIFICATION_URL = "https://api.wybotpool.com/api/user/notification"

DEFAULT_HEADER = {
    "Content-Type": "application/json",
    "User-Agent": "WYBOT/13 CFNetwork/1498.700.2 Darwin/23.6.0",
}


TOKEN_REFRESH_INTERVAL = 50 * 60  # Proactively refresh token after 50 minutes


class WyBotHTTPClient:
    """Client for interacting with the WyBot API."""

    _token: str | None = None
    _user_id: str | None = None
    _password: str
    _username: str
    _session: requests.Session | None = None
    _token_obtained_at: float = 0.0

    def __init__(self, username: str, password: str) -> None:
        """Init the wybot api."""
        self._username = username
        self._password = password
        self._setup_session()

    @property
    def user_id(self) -> str | None:
        """Return the authenticated account's user id, if available."""
        return self._user_id

    def _setup_session(self) -> None:
        """Set up HTTP session with retry strategy."""
        self._session = requests.Session()
        retry_strategy = Retry(
            total=MAX_RETRIES,
            backoff_factor=1,
            status_forcelist=[500, 502, 503, 504],
            allowed_methods=["GET", "POST"],
        )
        adapter = HTTPAdapter(max_retries=retry_strategy)
        self._session.mount("http://", adapter)
        self._session.mount("https://", adapter)

    def authenticate(self) -> bool:
        """Authenticate with the host.

        Returns True on success. Raises WybotAuthError if the credentials are
        rejected and WybotConnectionError if the API cannot be reached.
        """
        login_response = self.login()
        token = (
            login_response.metadata.token if login_response.metadata else None
        )
        user_id = (
            login_response.metadata.user_id if login_response.metadata else None
        )
        if token is None or user_id is None:
            raise WybotAuthError("Login succeeded but no token was returned")
        self._token = token
        self._user_id = user_id
        self._token_obtained_at = time.time()
        return True

    def _refresh_token_if_needed(self) -> bool:
        """Refresh token proactively before expiry or if missing.

        Raises WybotAuthError / WybotConnectionError on failure.
        """
        if self._token is None or self._user_id is None:
            _LOGGER.debug("Token missing, re-authenticating")
            return self.authenticate()
        # Proactively refresh before token expires
        token_age = time.time() - self._token_obtained_at
        if token_age > TOKEN_REFRESH_INTERVAL:
            _LOGGER.info(
                "Token age %.0fs exceeds %ds, proactively refreshing",
                token_age,
                TOKEN_REFRESH_INTERVAL,
            )
            return self.authenticate()
        return True

    def login(self) -> LoginResponse:
        """Authenticate the user and retrieve a token with retry logic.

        Raises:
            WybotAuthError: the credentials were rejected by the API.
            WybotConnectionError: the API could not be reached after retries.
        """
        _LOGGER.debug("Grabbing a token with a user and password")
        if not self._password:
            raise WybotAuthError("Password is not set")
        md5_hash = hashlib.md5()
        md5_hash.update(self._password.encode("utf-8"))
        md5_hex = md5_hash.hexdigest()
        auth_data = {
            "username": self._username,
            "password": md5_hex,
        }

        delay = INITIAL_RETRY_DELAY
        for attempt in range(MAX_RETRIES):
            try:
                response = self._session.post(
                    AUTH_URL,
                    json=auth_data,
                    headers=DEFAULT_HEADER,
                    allow_redirects=False,
                    timeout=TIMEOUT,
                )
                if response.status_code == 200:
                    json_response = response.json()
                    response.close()
                    return LoginResponse(**json_response)
                if response.status_code in (401, 403):
                    status = response.status_code
                    response.close()
                    raise WybotAuthError(
                        f"Authentication rejected with status {status}"
                    )
                status = response.status_code
                text = response.text
                response.close()
                _LOGGER.warning(
                    "Login attempt %d failed with status %d: %s",
                    attempt + 1,
                    status,
                    text,
                )
                if attempt < MAX_RETRIES - 1:
                    time.sleep(delay)
                    delay = min(delay * 2, MAX_RETRY_DELAY)
                else:
                    raise WybotConnectionError(
                        f"Login failed after {MAX_RETRIES} attempts: HTTP {status}"
                    )
            except (requests.exceptions.Timeout, requests.exceptions.RequestException) as err:
                _LOGGER.warning("Login request error on attempt %d: %s", attempt + 1, err)
                if attempt < MAX_RETRIES - 1:
                    time.sleep(delay)
                    delay = min(delay * 2, MAX_RETRY_DELAY)
                else:
                    raise WybotConnectionError(
                        f"Could not reach WyBot API after {MAX_RETRIES} attempts: {err}"
                    ) from err
            except WybotError:
                raise
            except Exception as err:
                raise WybotConnectionError(
                    f"Unexpected error during login: {err}"
                ) from err

        raise WybotConnectionError("Login failed")

    def get_devices_and_status(self) -> DevicesResponse:
        """Grab all devices and statuses with retry logic and token refresh.

        Raises:
            WybotAuthError: the stored credentials are no longer valid.
            WybotConnectionError: the API could not be reached after retries.
        """
        self._refresh_token_if_needed()

        if self._user_id is None:
            raise WybotAuthError("User ID is not set")

        device_url = DEVICES_URL + str(self._user_id)
        _LOGGER.debug("Grabbing devices and statuses: %s", device_url)

        delay = INITIAL_RETRY_DELAY
        for attempt in range(MAX_RETRIES):
            try:
                response = self._session.get(
                    device_url,
                    headers={**DEFAULT_HEADER, "Authorization": f"token {self._token}"},
                    allow_redirects=False,
                    timeout=TIMEOUT,
                )

                if response.status_code == 200:
                    json_response = response.json()
                    response.close()
                    return DevicesResponse(**json_response)
                if response.status_code == 401:
                    # Token expired, try to refresh (raises WybotAuthError if the
                    # stored credentials are no longer valid).
                    _LOGGER.info("Token expired, refreshing authentication")
                    response.close()
                    self.authenticate()
                    # Retry immediately after re-auth
                    continue
                status = response.status_code
                text = response.text
                response.close()
                _LOGGER.warning(
                    "Get devices attempt %d failed with status %d: %s",
                    attempt + 1,
                    status,
                    text,
                )
                if attempt < MAX_RETRIES - 1:
                    time.sleep(delay)
                    delay = min(delay * 2, MAX_RETRY_DELAY)
                else:
                    raise WybotConnectionError(
                        f"Error getting devices after {MAX_RETRIES} attempts: HTTP {status}"
                    )
            except (requests.exceptions.Timeout, requests.exceptions.RequestException) as err:
                _LOGGER.warning(
                    "Get devices request error on attempt %d: %s", attempt + 1, err
                )
                if attempt < MAX_RETRIES - 1:
                    time.sleep(delay)
                    delay = min(delay * 2, MAX_RETRY_DELAY)
                else:
                    raise WybotConnectionError(
                        f"Could not reach WyBot API after {MAX_RETRIES} attempts: {err}"
                    ) from err
            except WybotError:
                raise
            except Exception as err:
                raise WybotConnectionError(
                    f"Unexpected error getting devices: {err}"
                ) from err

        raise WybotConnectionError("Failed to get devices after retries")

    def get_indexed_current_grouped_devices(self) -> dict[str, Group]:
        """Return a dictionary of devices indexed by the grouped device_id.

        Raises WybotAuthError / WybotConnectionError on failure.
        """
        response = self.get_devices_and_status()
        return {group.id: group for group in response.metadata.groups}

    def register_presence(self) -> bool:
        """Register presence with the cloud server.

        This signals to the WyBot cloud that we're actively listening,
        which may help ensure MQTT messages are relayed when devices come online.
        Note: Devices still need to be woken up via the mobile app's BLE connection.
        """
        try:
            self._refresh_token_if_needed()
        except WybotError as err:
            _LOGGER.debug("Failed to refresh token for presence registration: %s", err)
            return False

        if self._user_id is None:
            _LOGGER.debug("User ID not set for presence registration")
            return False

        # POST to notification endpoint with userId to register presence (with 1 retry)
        for attempt in range(2):
            try:
                response = self._session.post(
                    NOTIFICATION_URL,
                    headers={**DEFAULT_HEADER, "Authorization": f"token {self._token}"},
                    json={"userId": self._user_id},
                    allow_redirects=False,
                    timeout=TIMEOUT,
                )
                success = response.status_code == 200
                response.close()
                if success:
                    _LOGGER.debug("Presence registered successfully")
                    return True
                _LOGGER.debug(
                    "Presence registration failed (attempt %d): status=%d",
                    attempt + 1,
                    response.status_code,
                )
            except Exception as err:
                _LOGGER.debug(
                    "Presence registration error (attempt %d): %s", attempt + 1, err
                )
            if attempt == 0:
                time.sleep(2)
        return False
