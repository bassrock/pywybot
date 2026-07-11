"""Constants for the WyBot client library."""

# HTTP request timeout in seconds.
TIMEOUT = 30

# BLE command timing (seconds).
BLE_COMMAND_TIMEOUT = 25.0  # connection + status wait + CleaningMode query
BLE_COMMAND_HOLD_TIME = 2.0  # wait after write for acknowledgment
