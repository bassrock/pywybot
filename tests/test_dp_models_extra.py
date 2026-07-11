"""Extra tests for dp_models.py to close coverage gaps.

Focuses on __str__/__repr__ methods, None-data branches, and error branches
that the main test_dp_models.py does not exercise.
"""

from wybot.dp_models import (
    DP,
    Battery,
    CleaningMode,
    CleaningStatus,
    CleaningStatusMode,
    ConnectionStatus,
    DeviceStatus,
    Dock,
    DockConnectionStatus,
    DockInfo,
    DockType,
    GenericDP,
    Schedule,
    SolarDockBattery,
    SolarEnergyHarvested,
    SolarStatus,
    SolarStatusMode,
)


class TestReprMethods:
    """Exercise every __str__ and __repr__ implementation."""

    def test_generic_dp_repr(self):
        generic = GenericDP(DP(id=0, type=4, len=1, data="03"))
        assert "GenericDP" in repr(generic)
        assert "03" in repr(generic)

    def test_cleaning_status_repr(self):
        cs = CleaningStatus(status=CleaningStatusMode.CLEANING)
        assert "CleaningStatus" in repr(cs)
        assert "CLEANING" in repr(cs)

    def test_dock_str_and_repr(self):
        dock = Dock(DP(id=11, type=4, len=1, data="01"))
        assert "01" in str(dock)
        assert "01" in repr(dock)

    def test_cleaning_mode_str_and_repr(self):
        cm = CleaningMode(mode="Floor")
        assert "Floor" in str(cm)
        assert "Floor" in repr(cm)

    def test_battery_str_and_repr(self):
        bat = Battery(DP(id=50, type=0, len=2, data="0132"))
        assert "battery_level=50" in str(bat)
        assert "battery_level=50" in repr(bat)

    def test_solar_energy_str_and_repr(self):
        solar = SolarEnergyHarvested(DP(id=131, type=2, len=4, data="e8030000"))
        assert "energy_wh=1000" in str(solar)
        assert "energy_kwh=1.0" in repr(solar)

    def test_solar_dock_battery_str_and_repr(self):
        bat = SolarDockBattery(DP(id=221, type=0, len=3, data="01480a"))
        assert "battery_level=72%" in str(bat)
        assert "battery_level=72%" in repr(bat)

    def test_solar_status_str_and_repr(self):
        ss = SolarStatus(DP(id=222, type=0, len=1, data="01"))
        assert "is_charging=True" in str(ss)
        assert "is_charging=True" in repr(ss)

    def test_dock_info_str_and_repr(self):
        di = DockInfo(DP(id=214, type=4, len=1, data="05"))
        assert "is_solar_dock=True" in str(di)
        assert "SOLAR" in repr(di)

    def test_schedule_str_and_repr(self):
        sc = Schedule(DP(id=79, type=2, len=12, data="abcdef123456abcdef123456"))
        assert "abcdef" in str(sc)
        assert "abcdef" in repr(sc)

    def test_device_status_str_and_repr(self):
        ds = DeviceStatus(DP(id=209, type=4, len=1, data="03"))
        assert "status_value=3" in str(ds)
        assert "status_value=3" in repr(ds)

    def test_connection_status_str_and_repr(self):
        cs = ConnectionStatus(DP(id=212, type=4, len=1, data="01"))
        assert "is_connected=True" in str(cs)
        assert "is_connected=True" in repr(cs)

    def test_dock_connection_status_str_and_repr(self):
        dcs = DockConnectionStatus(DP(id=213, type=4, len=1, data="01"))
        assert "is_docked=True" in str(dcs)
        assert "is_docked=True" in repr(dcs)


class TestNoneDataBranches:
    """Exercise the None-data / default branches of each property."""

    def test_solar_status_none_data(self):
        ss = SolarStatus(DP(id=222, type=0, len=1, data=None))
        assert ss.is_charging is False
        assert ss.status == SolarStatusMode.NOT_CHARGING

    def test_dock_info_none_data(self):
        di = DockInfo(DP(id=214, type=4, len=1, data=None))
        assert di.dock_type == DockType.UNKNOWN

    def test_device_status_none_data(self):
        ds = DeviceStatus(DP(id=209, type=4, len=1, data=None))
        assert ds.status_value == 0

    def test_connection_status_none_data(self):
        cs = ConnectionStatus(DP(id=212, type=4, len=1, data=None))
        assert cs.is_connected is False

    def test_dock_connection_status_none_data(self):
        dcs = DockConnectionStatus(DP(id=213, type=4, len=1, data=None))
        assert dcs.is_docked is False


class TestSolarDockBatteryErrorBranch:
    """Exercise the try/except in SolarDockBattery.battery_level."""

    def test_non_hex_data_returns_zero(self):
        # Length >= 4 so the guard passes, but chars 2-4 are not valid hex,
        # forcing the ValueError branch.
        bat = SolarDockBattery(DP(id=221, type=0, len=3, data="01zz0a"))
        assert bat.battery_level == 0
