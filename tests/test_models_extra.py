"""Extra tests for models.py to close coverage gaps in the get_dp methods."""

import pytest

from wybot.dp_models import DP, CleaningStatus, GenericDP
from wybot.models import Device, Docker, Group


def _cleaning_status_dp():
    return CleaningStatus(DP(id=0, type=4, len=1, data="03"))


class TestDeviceGetDp:
    """Cover the match branch of Device.get_dp."""

    def test_returns_matching_dp(self, sample_api_device):
        dev = Device(**sample_api_device)
        cs = _cleaning_status_dp()
        dev.dps = {"0": cs}
        assert dev.get_dp(CleaningStatus) is cs


class TestDockerGetDp:
    """Cover Docker.get_dp: TypeError guard, match branch, and None return."""

    def test_raises_for_non_generic(self, sample_api_docker):
        dock = Docker(**sample_api_docker)
        with pytest.raises(TypeError):
            dock.get_dp(str)  # type: ignore[arg-type]

    def test_returns_none_when_empty(self, sample_api_docker):
        dock = Docker(**sample_api_docker)
        assert dock.get_dp(CleaningStatus) is None

    def test_returns_matching_dp(self, sample_api_docker):
        dock = Docker(**sample_api_docker)
        cs = _cleaning_status_dp()
        dock.dps = {"0": cs}
        assert dock.get_dp(CleaningStatus) is cs


class TestGroupGetDp:
    """Cover Group.get_dp: TypeError guard and device-first match branch."""

    def test_raises_for_non_generic(self, sample_api_group):
        group = Group(**sample_api_group)
        with pytest.raises(TypeError):
            group.get_dp(str)  # type: ignore[arg-type]

    def test_finds_dp_on_device(self, sample_api_group):
        group = Group(**sample_api_group)
        cs = _cleaning_status_dp()
        group.device.dps = {"0": cs}
        found = group.get_dp(CleaningStatus)
        assert found is cs

    def test_returns_none_without_docker(self, sample_api_device, sample_api_vision):
        data = {
            "device": sample_api_device,
            "docker": None,
            "vision": sample_api_vision,
            "name": "Solo Pool",
            "id": "group2",
            "autoUpdate": "0",
        }
        group = Group(**data)
        assert group.get_dp(CleaningStatus) is None
