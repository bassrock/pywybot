"""Tests for F1-specific DP classes added via APK reverse engineering."""

import pytest

from wybot.dp_models import (
    DP,
    AutoRunMode,
    Battery,
    BatteryState,
    CleaningDepthRange,
    CleaningMode,
    HeavyDirtMode,
    PhData,
    SolarEnergyHarvested,
    SystemTime,
    WorkingTime,
    wybot_dp_id,
)


# =============================================================================
# Battery — F1 3-byte format
# =============================================================================


class TestBatteryF1:
    """Tests for Battery with F1's 3-byte data format."""

    def test_f1_solar_battery_level(self, sample_dp_data):
        """F1 sends 3 bytes: [charge_state][solar%][robot%]."""
        dp = DP(**sample_dp_data["f1_battery_charging"])
        bat = Battery(dp)
        assert bat.solar_battery_level == 99  # 0x63 = 99

    def test_f1_robot_battery_level(self, sample_dp_data):
        dp = DP(**sample_dp_data["f1_battery_charging"])
        bat = Battery(dp)
        assert bat.battery_level == 2  # 0x02 = 2

    def test_f1_charge_state(self, sample_dp_data):
        dp = DP(**sample_dp_data["f1_battery_charging"])
        bat = Battery(dp)
        assert bat.charge_state == BatteryState.CHARGING

    def test_f1_charged_state(self, sample_dp_data):
        dp = DP(**sample_dp_data["f1_battery_charged"])
        bat = Battery(dp)
        assert bat.charge_state == BatteryState.CHARGED
        assert bat.solar_battery_level == 100  # 0x64
        assert bat.battery_level == 2

    def test_f1_not_plugged(self, sample_dp_data):
        dp = DP(**sample_dp_data["f1_battery_not_plugged"])
        bat = Battery(dp)
        assert bat.charge_state == BatteryState.NOT_PLUGGED_IN
        assert bat.solar_battery_level == 14  # 0x0e
        assert bat.battery_level == 1

    def test_ds20_2byte_returns_none_solar(self, sample_dp_data):
        """DS20's 2-byte battery format should return None for solar_battery_level."""
        dp = DP(**sample_dp_data["battery_charging_50"])
        bat = Battery(dp)
        assert bat.solar_battery_level is None

    def test_ds20_2byte_robot_battery_works(self, sample_dp_data):
        """DS20's 2-byte format should still work for robot battery."""
        dp = DP(**sample_dp_data["battery_charging_50"])
        bat = Battery(dp)
        assert bat.battery_level == 50

    def test_no_data_returns_none_solar(self):
        dp = DP(id=50, type=0, len=3, data=None)
        bat = Battery(dp)
        assert bat.solar_battery_level is None

    def test_str_includes_battery_level(self, sample_dp_data):
        dp = DP(**sample_dp_data["f1_battery_charging"])
        bat = Battery(dp)
        assert "battery_level=2" in str(bat)


# =============================================================================
# CleaningMode — F1 modes
# =============================================================================


class TestCleaningModeF1:
    """Tests for CleaningMode with F1-specific mode values."""

    def test_f1_smart_mode(self, sample_dp_data):
        dp = DP(**sample_dp_data["f1_cleaning_mode_smart"])
        cm = CleaningMode(dp)
        assert cm.cleaning_mode == "Smart"

    def test_f1_standard_mode(self, sample_dp_data):
        dp = DP(**sample_dp_data["f1_cleaning_mode_standard"])
        cm = CleaningMode(dp)
        assert cm.cleaning_mode == "Standard"

    def test_f1_smart_roundtrip(self):
        cm = CleaningMode(mode="Smart")
        assert cm.cleaning_mode == "Smart"
        assert cm.data == "0e"

    def test_f1_standard_roundtrip(self):
        cm = CleaningMode(mode="Standard")
        assert cm.cleaning_mode == "Standard"
        assert cm.data == "0f"

    def test_ds20_modes_still_work(self):
        """DS20 modes (0-6) should still work alongside F1 modes."""
        cm = CleaningMode(mode="Floor")
        assert cm.cleaning_mode == "Floor"
        assert cm.data == "00"

    def test_all_ds20_modes_roundtrip(self):
        for mode_name in CleaningMode.CLEANING_MODES:
            cm = CleaningMode(mode=mode_name)
            assert cm.cleaning_mode == mode_name

    def test_unknown_mode_value(self):
        """Values outside both DS20 (0-6) and F1 (14-15) ranges."""
        cm = CleaningMode(DP(id=1, type=4, len=1, data="99"))
        result = cm.cleaning_mode
        assert "Unknown" in result
        assert "153" in result  # 0x99 = 153


# =============================================================================
# WorkingTime / SolarEnergyHarvested (DP 131)
# =============================================================================


class TestWorkingTime:
    """Tests for WorkingTime (formerly SolarEnergyHarvested)."""

    def test_seconds(self, sample_dp_data):
        dp = DP(**sample_dp_data["f1_working_time"])
        wt = WorkingTime(dp)
        assert wt.seconds == 1000

    def test_energy_wh_backward_compat(self, sample_dp_data):
        """energy_wh property still returns the raw LE value for backward compat."""
        dp = DP(**sample_dp_data["f1_working_time"])
        wt = WorkingTime(dp)
        assert wt.energy_wh == 1000  # same raw value, just misnamed

    def test_energy_kwh_backward_compat(self, sample_dp_data):
        dp = DP(**sample_dp_data["f1_working_time"])
        wt = WorkingTime(dp)
        assert wt.energy_kwh == 1.0

    def test_str_shows_seconds(self, sample_dp_data):
        dp = DP(**sample_dp_data["f1_working_time"])
        wt = WorkingTime(dp)
        assert "seconds=1000" in str(wt)
        assert "seconds=1000" in repr(wt)

    def test_no_data(self):
        dp = DP(id=131, type=2, len=4, data=None)
        wt = WorkingTime(dp)
        assert wt.seconds == 0
        assert wt.energy_wh == 0

    def test_alias_is_same_class(self):
        """SolarEnergyHarvested should be the same class as WorkingTime."""
        assert SolarEnergyHarvested is WorkingTime

    def test_dp_id_maps_to_working_time(self):
        assert wybot_dp_id[131] is WorkingTime


# =============================================================================
# AutoRunMode (DP 207)
# =============================================================================


class TestAutoRunMode:
    """Tests for AutoRunMode DP."""

    def test_enabled(self, sample_dp_data):
        dp = DP(**sample_dp_data["f1_auto_run_enabled"])
        ar = AutoRunMode(dp)
        assert ar.is_enabled is True

    def test_disabled(self, sample_dp_data):
        dp = DP(**sample_dp_data["f1_auto_run_disabled"])
        ar = AutoRunMode(dp)
        assert ar.is_enabled is False

    def test_no_data(self):
        dp = DP(id=207, type=4, len=1, data=None)
        ar = AutoRunMode(dp)
        assert ar.is_enabled is False

    def test_str_repr(self, sample_dp_data):
        dp = DP(**sample_dp_data["f1_auto_run_enabled"])
        ar = AutoRunMode(dp)
        assert "AutoRunMode" in str(ar)
        assert "is_enabled=True" in str(ar)
        assert "is_enabled=True" in repr(ar)


# =============================================================================
# HeavyDirtMode (DP 145)
# =============================================================================


class TestHeavyDirtMode:
    """Tests for HeavyDirtMode DP."""

    def test_enabled(self, sample_dp_data):
        dp = DP(**sample_dp_data["f1_heavy_dirt_enabled"])
        hd = HeavyDirtMode(dp)
        assert hd.is_enabled is True

    def test_disabled(self, sample_dp_data):
        dp = DP(**sample_dp_data["f1_heavy_dirt_disabled"])
        hd = HeavyDirtMode(dp)
        assert hd.is_enabled is False

    def test_no_data(self):
        dp = DP(id=145, type=4, len=1, data=None)
        hd = HeavyDirtMode(dp)
        assert hd.is_enabled is False

    def test_str_repr(self, sample_dp_data):
        dp = DP(**sample_dp_data["f1_heavy_dirt_enabled"])
        hd = HeavyDirtMode(dp)
        assert "HeavyDirtMode" in str(hd)
        assert "is_enabled=True" in str(hd)


# =============================================================================
# PhData (DP 142)
# =============================================================================


class TestPhData:
    """Tests for PhData DP."""

    def test_ph_value(self, sample_dp_data):
        """pH is the first 2 bytes, little-endian, scaled by 100."""
        dp = DP(**sample_dp_data["f1_ph_data"])
        ph = PhData(dp)
        # "2c01" little-endian → 0x012c = 300 → pH 3.00
        assert ph.ph_value == 3.0

    def test_ph_value_typical_pool(self):
        """A typical pool reading of pH 7.20 encodes as 720 = 0x02d0 → "d002"."""
        dp = DP(id=142, type=2, len=4, data="d0021901")
        ph = PhData(dp)
        assert ph.ph_value == 7.2

    def test_temperature(self, sample_dp_data):
        """Temperature is the last 2 bytes, little-endian, scaled by 10."""
        dp = DP(**sample_dp_data["f1_ph_data"])
        ph = PhData(dp)
        # "1901" little-endian → 0x0119 = 281 → 28.1 °C
        assert ph.temperature == 28.1

    def test_no_data(self):
        dp = DP(id=142, type=2, len=4, data=None)
        ph = PhData(dp)
        assert ph.ph_value is None
        assert ph.temperature is None

    def test_short_data(self):
        """A truncated payload yields None rather than a bogus reading."""
        dp = DP(id=142, type=2, len=4, data="2c")
        ph = PhData(dp)
        assert ph.ph_value is None
        assert ph.temperature is None

    def test_odd_length_data(self):
        """Non-hex-decodable payloads yield None rather than raising."""
        dp = DP(id=142, type=2, len=4, data="2c0zzz01")
        ph = PhData(dp)
        assert ph.ph_value is None
        assert ph.temperature is None

    def test_str_repr(self, sample_dp_data):
        dp = DP(**sample_dp_data["f1_ph_data"])
        ph = PhData(dp)
        assert "PhData" in str(ph)
        assert "ph=" in str(ph)


# =============================================================================
# CleaningDepthRange (DP 206)
# =============================================================================


class TestCleaningDepthRange:
    """Tests for CleaningDepthRange DP."""

    def test_raw_value(self, sample_dp_data):
        dp = DP(**sample_dp_data["f1_cleaning_depth"])
        cdr = CleaningDepthRange(dp)
        # "10000000" LE = 16
        assert cdr.raw_value == 16

    def test_no_data(self):
        dp = DP(id=206, type=2, len=4, data=None)
        cdr = CleaningDepthRange(dp)
        assert cdr.raw_value == 0

    def test_str_repr(self, sample_dp_data):
        dp = DP(**sample_dp_data["f1_cleaning_depth"])
        cdr = CleaningDepthRange(dp)
        assert "CleaningDepthRange" in str(cdr)
        assert "raw_value=16" in str(cdr)


# =============================================================================
# SystemTime (DP 70)
# =============================================================================


class TestSystemTime:
    """Tests for SystemTime DP."""

    def test_raw_value(self, sample_dp_data):
        dp = DP(**sample_dp_data["f1_system_time"])
        st = SystemTime(dp)
        # "00010000" LE = 256
        assert st.raw_value == 256

    def test_no_data(self):
        dp = DP(id=70, type=2, len=4, data=None)
        st = SystemTime(dp)
        assert st.raw_value == 0

    def test_str_repr(self, sample_dp_data):
        dp = DP(**sample_dp_data["f1_system_time"])
        st = SystemTime(dp)
        assert "SystemTime" in str(st)
        assert "raw_value=256" in str(st)


# =============================================================================
# DP mapping for F1 classes
# =============================================================================


class TestF1DPMapping:
    """Tests for F1 DP class mappings in wybot_dp_id."""

    def test_auto_run_mapped(self):
        assert wybot_dp_id[207] is AutoRunMode

    def test_heavy_dirt_mapped(self):
        assert wybot_dp_id[145] is HeavyDirtMode

    def test_ph_data_mapped(self):
        assert wybot_dp_id[142] is PhData

    def test_cleaning_depth_mapped(self):
        assert wybot_dp_id[206] is CleaningDepthRange

    def test_system_time_mapped(self):
        assert wybot_dp_id[70] is SystemTime

    def test_working_time_mapped(self):
        assert wybot_dp_id[131] is WorkingTime

    def test_all_f1_classes_instantiate(self, sample_dp_data):
        """Verify all F1 mapped DP classes can be instantiated."""
        f1_fixtures = [
            ("f1_auto_run_enabled", 207),
            ("f1_heavy_dirt_enabled", 145),
            ("f1_ph_data", 142),
            ("f1_cleaning_depth", 206),
            ("f1_system_time", 70),
            ("f1_working_time", 131),
        ]
        for fixture_key, dp_id in f1_fixtures:
            dp = DP(**sample_dp_data[fixture_key])
            dp_class = wybot_dp_id[dp_id]
            instance = dp_class(dp)
            assert instance.id == dp_id
