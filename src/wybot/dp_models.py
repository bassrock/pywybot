from enum import Enum
import logging
from typing import Any

from pydantic import BaseModel

_LOGGER = logging.getLogger(__name__)

# F1 cleaning mode constants (outside DS20's 0-6 range)
F1_CLEANING_MODE_SMART = 14
F1_CLEANING_MODE_STANDARD = 15


class DP(BaseModel):
    """Represents the response for a device command operation."""

    # Represents a data point for a command.
    # 0 - Cleaning Start/Stop (03 - cleaning, 01 - stopped, 02 returning - to dock)
    # 1 - Cleaning Mode
    # 50 - Charge status (First 2, 01 charging, 02 - charged, second 2 digits = charge level)
    id: int

    # All our none if we are requesting data
    type: int | None = None
    len: int | None = None
    data: str | None = None


class GenericDP:
    id: int

    # Type of data
    # 0, len =2, take value of length as hex
    # 4 = 00, 01, 02...  basically convert to simple int
    # 5 = string that looks like hex
    type: int
    len: int
    data: str | None = None

    def __init__(self, data: DP) -> None:
        self.id = data.id
        if data.type is not None:
            self.type = data.type
        if data.len is not None:
            self.len = data.len
        self.data = data.data

    def dict(self) -> dict[str, Any]:
        return {"id": self.id, "type": self.type, "len": self.len, "data": self.data}

    def __str__(self) -> str:
        return f"({type(self).__name__}, value={self.dict()})"

    def __repr__(self) -> str:
        return f"({type(self).__name__}, value={self.dict()})"


class CleaningStatusMode(Enum):
    STOPPED = 1
    RETURNING = 2  # Legacy/intermediate returning state
    CLEANING = 3
    RETURNING_TO_DOCK = 4  # Returning after "return to dock" command
    UNKNOWN = 15
    STARTING = 255


# Send 03 to start
# Send 01 to stop
class CleaningStatus(GenericDP):
    id = 0
    type = 4
    len = 1

    def __init__(self, data: DP | None = None, status: CleaningStatusMode | None = None) -> None:
        if data is not None:
            super().__init__(data)
        if status is not None:
            self.status = status

    @property
    def status(self) -> CleaningStatusMode:
        if self.data is None:
            return CleaningStatusMode.UNKNOWN
        return CleaningStatusMode(int(self.data, 16))

    @status.setter
    def status(self, data: CleaningStatusMode) -> None:
        self.data = f"{int(data.value):02x}"

    def __str__(self) -> str:
        return f"({type(self).__name__}, status={self.status})"

    def __repr__(self) -> str:
        return f"({type(self).__name__}, status={self.status})"


class DockStatus(Enum):
    DOCKED = 0  # Robot is docked/idle
    RETURNING = 1
    GENERAL = 3


#  Send 01 to go back to dock
class Dock(GenericDP):
    id = 11
    type = 4
    len = 1  # can be 2 when recieving, no idea what the first characters represent

    def __init__(self, data: DP | None = None, status: DockStatus | None = None) -> None:
        if data is not None:
            super().__init__(data)
        if status is not None:
            self.status = status

    @property
    def status(self) -> DockStatus:
        """Return the status of the dock. Note, not really sure how to read this, other then send it comamnd 01 to return to dock."""
        if self.data is None:
            return DockStatus.GENERAL  # Default to general status if no data
        return DockStatus(int(self.data[-2:], 16))

    @status.setter
    def status(self, data: DockStatus) -> None:
        self.data = f"{int(data.value):02x}"

    def __str__(self) -> str:
        return f"({type(self).__name__}, status={self.data})"

    def __repr__(self) -> str:
        return f"({type(self).__name__}, status={self.data})"


class CleaningMode(GenericDP):
    id = 1
    type = 4
    len = 1
    CLEANING_MODES = [
        "Floor",
        "Wall",
        "Wall Then Floor",
        "Advanced Full Pool",
        "Water Line",
        "Turbo Floor",
        "Eco Floor",
    ]
    # F1-specific modes (values 14-15, outside DS20's 0-6 range)
    F1_CLEANING_MODES = {
        14: "Smart",
        15: "Standard",
    }

    def __init__(self, data: DP | None = None, mode: str | None = None) -> None:
        if data is not None:
            super().__init__(data)
        if mode is not None:
            self.cleaning_mode = mode

    @property
    def cleaning_mode(self) -> str:
        if self.data is None:
            return self.CLEANING_MODES[0]  # Default to first mode if no data
        mode_val = int(self.data, 16)
        # Check F1-specific modes first
        if mode_val in self.F1_CLEANING_MODES:
            return self.F1_CLEANING_MODES[mode_val]
        # DS20 modes (0-6)
        if mode_val < len(self.CLEANING_MODES):
            return self.CLEANING_MODES[mode_val]
        return f"Unknown ({mode_val})"

    @cleaning_mode.setter
    def cleaning_mode(self, data: str) -> None:
        # Check F1 modes
        for hex_val, name in self.F1_CLEANING_MODES.items():
            if data == name:
                self.data = f"{hex_val:02x}"
                return
        self.data = f"{self.CLEANING_MODES.index(data):02x}"

    def __str__(self) -> str:
        return f"({type(self).__name__}, mode={self.cleaning_mode})"

    def __repr__(self) -> str:
        return f"({type(self).__name__}, mode={self.cleaning_mode})"


class BatteryState(Enum):
    NOT_PLUGGED_IN = 0
    CHARGING = 1
    CHARGED = 2


class Battery(GenericDP):
    def __init__(self, data: DP) -> None:
        super().__init__(data)

    @property
    def battery_level(self) -> int:
        """Return the robot battery level as percentage (0-100).

        On DS20 (2-byte format): last byte = robot battery %
        On F1 (3-byte format):   last byte = robot battery %
        """
        if self.data is None:
            return 0
        return int(self.data[-2:], 16)

    @property
    def charge_state(self) -> BatteryState:
        """Return the charging state (first byte)."""
        if self.data is None:
            return BatteryState.NOT_PLUGGED_IN
        return BatteryState(int(self.data[:2], 16))

    @property
    def solar_battery_level(self) -> int | None:
        """Return the solar/integrated battery percentage (F1 only).

        The F1 sends 3 bytes for DP 50: [charge_state][solar_battery%][robot_battery%]
        The DS20 sends 2 bytes: [charge_state][robot_battery%]

        Returns None when the data is 2-byte (DS20) and the solar byte is absent.
        """
        if self.data is None or len(self.data) < 6:  # 3 bytes = 6 hex chars
            return None
        try:
            return int(self.data[2:4], 16)
        except (ValueError, IndexError):
            return None

    @property
    def robot_battery_level(self) -> int | None:
        """Return the robot battery level, or None when stale (F1 unplugged).

        On the F1, the robot battery byte (byte 2) only updates when the device
        is physically plugged in (charge_state CHARGING or CHARGED). When running
        on solar alone (NOT_PLUGGED_IN), the value is frozen/stale and does not
        reflect actual battery state.

        - DS20 (2-byte): always returns the robot battery % (no stale issue)
        - F1 (3-byte) + plugged in: returns the robot battery %
        - F1 (3-byte) + unplugged: returns None (stale data, not reliable)
        """
        if self.data is None:
            return None
        # DS20 2-byte format — no staleness issue
        if len(self.data) < 6:
            return int(self.data[-2:], 16)
        # F1 3-byte format — check charge_state
        if self.charge_state == BatteryState.NOT_PLUGGED_IN:
            return None  # stale, not reporting
        return int(self.data[-2:], 16)

    def __str__(self) -> str:
        return f"({type(self).__name__}, charge_state={self.charge_state}, battery_level={self.battery_level}, robot_battery_level={self.robot_battery_level})"

    def __repr__(self) -> str:
        return f"({type(self).__name__}, charge_state={self.charge_state}, battery_level={self.battery_level}, robot_battery_level={self.robot_battery_level})"


class WorkingTime(GenericDP):
    """DP 131: Total working time in seconds (little-endian 4-byte value).

    Originally labeled 'SolarEnergyHarvested' (Wh) — APK analysis confirmed
    the correct meaning is working time in seconds.
    Backward-compatible alias ``SolarEnergyHarvested`` is kept below.
    """

    id = 131
    type = 2
    len = 4

    def __init__(self, data: DP) -> None:
        super().__init__(data)

    @property
    def energy_wh(self) -> int:
        """Return the raw little-endian value (backward compat).

        Historically exposed as energy_wh; actually seconds of working time.
        Consumers that need the true value should use ``seconds``.
        """
        if self.data is None or len(self.data) < 8:
            return 0
        return int.from_bytes(bytes.fromhex(self.data), byteorder="little")

    @property
    def seconds(self) -> int:
        """Return total working time in seconds."""
        return self.energy_wh

    @property
    def energy_kwh(self) -> float:
        """Return the raw value divided by 1000 (backward compat)."""
        return self.energy_wh / 1000.0

    def __str__(self) -> str:
        return f"({type(self).__name__}, seconds={self.seconds})"

    def __repr__(self) -> str:
        return f"({type(self).__name__}, seconds={self.seconds})"


# Backward-compatible alias
SolarEnergyHarvested = WorkingTime


class SolarDockBattery(GenericDP):
    """DP 221: Solar dock battery level (3-byte format).

    Data format: XXYYZZ (3 bytes / 6 hex chars)
    - XX: Status/flags byte (typically 01)
    - YY: Battery percentage (0-100)
    - ZZ: Unknown (possibly voltage related)

    Example: "01480a" = 72% battery (0x48 = 72)
    """

    id = 221
    type = 0
    len = 3

    def __init__(self, data: DP) -> None:
        super().__init__(data)

    @property
    def battery_level(self) -> int:
        """Return solar dock battery level as percentage (0-100)."""
        if self.data is None or len(self.data) < 4:
            return 0
        # Battery percentage is in byte 1 (hex chars 2-3)
        # Example: "01480a" -> "48" -> 72%
        try:
            return int(self.data[2:4], 16)
        except (ValueError, IndexError):
            return 0

    def __str__(self) -> str:
        return f"({type(self).__name__}, battery_level={self.battery_level}%)"

    def __repr__(self) -> str:
        return f"({type(self).__name__}, battery_level={self.battery_level}%)"


class SolarStatusMode(Enum):
    """Solar charging status modes."""

    NOT_CHARGING = 0
    CHARGING = 1


class SolarStatus(GenericDP):
    """DP 222: Solar charging status (1-byte boolean)."""

    id = 222
    type = 0
    len = 1

    def __init__(self, data: DP) -> None:
        super().__init__(data)

    @property
    def is_charging(self) -> bool:
        """Return True if solar panel is actively charging."""
        if self.data is None:
            return False
        return int(self.data, 16) == 1

    @property
    def status(self) -> SolarStatusMode:
        """Return the solar charging status mode."""
        if self.data is None:
            return SolarStatusMode.NOT_CHARGING
        return SolarStatusMode(int(self.data, 16))

    def __str__(self) -> str:
        return f"({type(self).__name__}, is_charging={self.is_charging})"

    def __repr__(self) -> str:
        return f"({type(self).__name__}, is_charging={self.is_charging})"


class DockType(Enum):
    """Dock type based on DP 214 values."""

    UNKNOWN = 0
    STANDARD = 1
    SOLAR = 5  # S2 Pro solar dock appears to report 5


class DockInfo(GenericDP):
    """DP 214: Dock type information."""

    id = 214
    type = 4
    len = 1

    def __init__(self, data: DP) -> None:
        super().__init__(data)

    @property
    def dock_type(self) -> DockType:
        """Return the dock type."""
        if self.data is None:
            return DockType.UNKNOWN
        try:
            return DockType(int(self.data, 16))
        except ValueError:
            return DockType.UNKNOWN

    @property
    def is_solar_dock(self) -> bool:
        """Return True if this is a solar dock."""
        return self.dock_type == DockType.SOLAR

    def __str__(self) -> str:
        return f"({type(self).__name__}, dock_type={self.dock_type}, is_solar_dock={self.is_solar_dock})"

    def __repr__(self) -> str:
        return f"({type(self).__name__}, dock_type={self.dock_type}, is_solar_dock={self.is_solar_dock})"


class Schedule(GenericDP):
    """DP 79: Schedule data (12-byte value containing schedule configuration)."""

    id = 79
    type = 2
    len = 12

    def __init__(self, data: DP) -> None:
        super().__init__(data)

    @property
    def raw_schedule(self) -> str:
        """Return raw schedule data as hex string."""
        return self.data if self.data else ""

    def __str__(self) -> str:
        return f"({type(self).__name__}, raw_schedule={self.raw_schedule})"

    def __repr__(self) -> str:
        return f"({type(self).__name__}, raw_schedule={self.raw_schedule})"


class DeviceStatus(GenericDP):
    """DP 209: Device status flag."""

    id = 209
    type = 4
    len = 1

    def __init__(self, data: DP) -> None:
        super().__init__(data)

    @property
    def status_value(self) -> int:
        """Return the raw status value."""
        if self.data is None:
            return 0
        return int(self.data, 16)

    def __str__(self) -> str:
        return f"({type(self).__name__}, status_value={self.status_value})"

    def __repr__(self) -> str:
        return f"({type(self).__name__}, status_value={self.status_value})"


class ConnectionStatus(GenericDP):
    """DP 212: Connection/communication status."""

    id = 212
    type = 4
    len = 1

    def __init__(self, data: DP) -> None:
        super().__init__(data)

    @property
    def is_connected(self) -> bool:
        """Return True if device is connected."""
        if self.data is None:
            return False
        return int(self.data, 16) == 1

    def __str__(self) -> str:
        return f"({type(self).__name__}, is_connected={self.is_connected})"

    def __repr__(self) -> str:
        return f"({type(self).__name__}, is_connected={self.is_connected})"


class DockConnectionStatus(GenericDP):
    """DP 213: Dock connection status."""

    id = 213
    type = 4
    len = 1

    def __init__(self, data: DP) -> None:
        super().__init__(data)

    @property
    def is_docked(self) -> bool:
        """Return True if device is docked."""
        if self.data is None:
            return False
        return int(self.data, 16) == 1

    def __str__(self) -> str:
        return f"({type(self).__name__}, is_docked={self.is_docked})"

    def __repr__(self) -> str:
        return f"({type(self).__name__}, is_docked={self.is_docked})"


# =============================================================================
# F1-Specific DP Classes (confirmed via APK reverse engineering)
# Source: WYBOT.apk Flutter AOT-compiled libapp.so log strings
# =============================================================================


class AutoRunMode(GenericDP):
    """DP 207 (0xCF): Auto-run mode toggle (F1).

    APK: "parseBLEData: getAutoRunMode DP_ID_AUTO_RUN 0XCF mode: "
    When enabled, the robot starts skimming automatically when battery is sufficient.
    """

    id = 207
    type = 4
    len = 1

    def __init__(self, data: DP) -> None:
        super().__init__(data)

    @property
    def is_enabled(self) -> bool:
        """Return True if auto-run is enabled."""
        if self.data is None:
            return False
        return int(self.data, 16) > 0

    def __str__(self) -> str:
        return f"({type(self).__name__}, is_enabled={self.is_enabled})"

    def __repr__(self) -> str:
        return f"({type(self).__name__}, is_enabled={self.is_enabled})"


class HeavyDirtMode(GenericDP):
    """DP 145 (0x91): Heavy dirt mode (F1).

    APK: "check mqtt delay DP_ID:OX91 heavyDirt mode: "
    When enabled, the robot runs extra cleaning cycles for dirty pools.
    """

    id = 145
    type = 4
    len = 1

    def __init__(self, data: DP) -> None:
        super().__init__(data)

    @property
    def is_enabled(self) -> bool:
        """Return True if heavy dirt mode is enabled."""
        if self.data is None:
            return False
        return int(self.data, 16) > 0

    def __str__(self) -> str:
        return f"({type(self).__name__}, is_enabled={self.is_enabled})"

    def __repr__(self) -> str:
        return f"({type(self).__name__}, is_enabled={self.is_enabled})"


class PhData(GenericDP):
    """DP 142 (0x8E): pH value and temperature (F1).

    APK: "parseBLEData: PH_DATA:0X8E, pHvalue: " + "tempValue: "
    Data format: 4 bytes (8 hex chars)
    - Bytes 0-1 (hex chars 0-3): pH value * 100 (little-endian)
    - Bytes 2-3 (hex chars 4-7): temperature * 10 (little-endian)

    BLE-only — not available via MQTT cloud stream.
    """

    id = 142
    type = 2
    len = 4

    def __init__(self, data: DP) -> None:
        super().__init__(data)

    @property
    def ph_value(self) -> float | None:
        """Return the pH value (raw / 100)."""
        if self.data is None or len(self.data) < 4:
            return None
        try:
            raw = int.from_bytes(bytes.fromhex(self.data[:4]), byteorder="little")
        except ValueError:
            return None
        return raw / 100.0

    @property
    def temperature(self) -> float | None:
        """Return the temperature from pH electrode (raw / 10)."""
        if self.data is None or len(self.data) < 8:
            return None
        try:
            raw = int.from_bytes(bytes.fromhex(self.data[4:8]), byteorder="little")
        except ValueError:
            return None
        return raw / 10.0

    def __str__(self) -> str:
        return f"({type(self).__name__}, ph={self.ph_value}, temp={self.temperature})"

    def __repr__(self) -> str:
        return f"({type(self).__name__}, ph={self.ph_value}, temp={self.temperature})"


class CleaningDepthRange(GenericDP):
    """DP 206 (0xCE): Cleaning depth range (F1).

    APK: "parseBLEData: getCleaningDepth DP_ID_CLEANING_DEPTH_RANGE 0xCE min: "
    Adjustable cleaning depth for the F1 skimmer.
    """

    id = 206
    type = 2
    len = 4

    def __init__(self, data: DP) -> None:
        super().__init__(data)

    @property
    def raw_value(self) -> int:
        """Return the raw little-endian value."""
        if self.data is None or len(self.data) < 8:
            return 0
        return int.from_bytes(bytes.fromhex(self.data), byteorder="little")

    def __str__(self) -> str:
        return f"({type(self).__name__}, raw_value={self.raw_value})"

    def __repr__(self) -> str:
        return f"({type(self).__name__}, raw_value={self.raw_value})"


class SystemTime(GenericDP):
    """DP 70 (0x46): System time (F1).

    APK: "parseBLEData: DP_ID_SYSTEM_TIME 0x46 sysTime: "
    """

    id = 70
    type = 2
    len = 4

    def __init__(self, data: DP) -> None:
        super().__init__(data)

    @property
    def raw_value(self) -> int:
        """Return the raw little-endian value."""
        if self.data is None or len(self.data) < 8:
            return 0
        return int.from_bytes(bytes.fromhex(self.data), byteorder="little")

    def __str__(self) -> str:
        return f"({type(self).__name__}, raw_value={self.raw_value})"

    def __repr__(self) -> str:
        return f"({type(self).__name__}, raw_value={self.raw_value})"


# Mapping of types to classes
wybot_dp_id = {
    0: CleaningStatus,
    1: CleaningMode,
    11: Dock,  # Docking status
    13: GenericDP,  # Unknown 4-byte value
    15: GenericDP,
    50: Battery,
    70: SystemTime,  # System time (F1)
    77: GenericDP,  # Unknown 36-byte data (cleaning map/log?)
    79: Schedule,  # Schedule configuration
    131: WorkingTime,  # Working time (seconds) — was SolarEnergyHarvested
    142: PhData,  # pH value + temperature (F1)
    145: HeavyDirtMode,  # Heavy dirt mode (F1)
    206: CleaningDepthRange,  # Cleaning depth range (F1)
    207: AutoRunMode,  # Auto-run mode (F1)
    209: DeviceStatus,  # Device status flag
    212: ConnectionStatus,  # Connection status
    213: DockConnectionStatus,  # Dock connection status
    214: DockInfo,  # Dock type information
    221: SolarDockBattery,  # Solar dock battery level (%)
    222: SolarStatus,  # Solar charging status
    223: GenericDP,  # Unknown 4-byte value
    # Add more mappings as needed
}
