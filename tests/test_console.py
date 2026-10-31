from configparser import ConfigParser

import pytest

from mctester.console import (
    LogBuffer, Panel, SimStatus, driver_names, pin_changes, render,
)
from mctester.fake_t7 import FakeT7

ALL_SECTIONS = {"ignition", "outputs", "events", "log"}


class FlatSensors:
    def voltage(self, channel, t):
        return 0.0


class ManualClock:
    def __init__(self):
        self.now = 100.0

    def __call__(self):
        return self.now


@pytest.fixture
def clock():
    return ManualClock()


@pytest.fixture
def status(clock):
    config = ConfigParser()
    config.read_dict({"driver_mapping": {"0": "EIO0", "2": "EIO2", "6": "CIO2"}})
    device = FakeT7(FlatSensors(), clock=clock, sleep=lambda seconds: None)
    names = {"prometheus": {"0": "ox_fill", "6": "ignition"}, "sphinx": {"2": "fuel_run", "6": "ignition"}}
    return SimStatus(device, config, names, "prometheus", "127.0.0.1:2707", "runs/x", LogBuffer())


def status_for(clock, mapping, names):
    config = ConfigParser()
    config.read_dict({"driver_mapping": mapping})
    device = FakeT7(FlatSensors(), clock=clock, sleep=lambda seconds: None)
    return SimStatus(device, config, {"prometheus": names}, "prometheus", "127.0.0.1:2707", "runs/x", LogBuffer())


def text_of(status, shown, verbose=False, width=120, height=40):
    return "\n".join(text for text, _ in render(status, shown, verbose, width, height))


def test_output_shows_wiring_name_and_state(status):
    status.device.write("EIO0", 1)
    row = next(line for line in text_of(status, {"outputs"}).splitlines() if "EIO0" in line)
    for part in ("OX FILL", "ox_fill", "VS1", "12V", "ON"):
        assert part in row


def test_switch_labels_are_gone(status):
    text = text_of(status, {"ignition", "outputs", "events"})
    assert "S0" not in text and "S10" not in text and "Sw " not in text


def test_outputs_use_each_pins_own_rail_voltage(clock):
    status = status_for(clock, {"5": "EIO5"}, {"5": "bps"})
    row = next(line for line in text_of(status, {"outputs"}).splitlines() if "EIO5" in line)
    assert "24V" in row and "FUEL REM PURGE" in row


def test_outputs_are_listed_in_board_order(clock):
    status = status_for(clock, {"7": "CIO1", "8": "CIO0", "0": "EIO0"}, {})
    text = text_of(status, {"outputs"})
    assert text.index("EIO0") < text.index("CIO0") < text.index("CIO1")


def test_each_mode_lists_only_its_engines_outputs(status):
    assert "EIO0" in text_of(status, {"outputs"}) and "EIO2" not in text_of(status, {"outputs"})
    Panel(status, None).press("m")
    assert status.engine == "sphinx"
    assert "EIO2" in text_of(status, {"outputs"}) and "EIO0" not in text_of(status, {"outputs"})
    assert "fuel_run" in text_of(status, {"outputs"})


def test_mode_key_wraps_back_and_shows_in_header_and_footer(status):
    panel = Panel(status, None)
    panel.press("m")
    text = text_of(status, set())
    assert "mode: SPHINX" in text and "[m] Mode: sphinx" in text
    panel.press("M")
    assert status.engine == "prometheus"


def test_driver_this_engine_does_not_use_appears_when_on(status):
    status.device.write("EIO2", 1)
    assert "EIO2" in text_of(status, {"outputs"})
    assert "NOT IN CONFIG" not in text_of(status, {"outputs"})


def test_all_drivers_appear_if_dashboard_names_are_missing(clock):
    assert "EIO2" in text_of(status_for(clock, {"2": "EIO2"}, {}), {"outputs"})


def test_ignition_stays_out_of_outputs_when_on(status):
    status.device.write("CIO2", 1)
    assert "CIO2" not in text_of(status, {"outputs"})


def test_pin_outside_the_config_appears_when_on(status):
    assert "EIO7" not in text_of(status, {"outputs"})
    status.device.write("EIO7", 1)
    assert "NOT IN CONFIG" in text_of(status, {"outputs"})


def test_ignition_has_its_own_section_not_a_row_in_outputs(status):
    assert "CIO2" not in text_of(status, {"outputs"})
    assert "CIO2   off" in text_of(status, {"ignition"})


def test_ignition_shows_how_long_it_has_been_on(status, clock):
    status.device.write("CIO2", 1)
    clock.now += 3.2
    assert "ON for 3.2 s" in text_of(status, {"ignition"})


def test_ignition_shows_the_last_backend_message(status):
    status.last_message = "Ignition in 7..."
    assert "Ignition in 7..." in text_of(status, {"ignition"})


def test_pin_changes_skip_the_startup_clear(status):
    for pin in ("EIO0", "CIO2"):
        status.device.write(pin, 0)
    status.device.write("EIO0", 1)
    status.device.write("EIO0", 1)
    assert [(e["pin"], e["value"]) for e in pin_changes(status.device.pin_log)] == [("EIO0", 1)]


def test_events_use_driver_names(status):
    status.device.write("EIO0", 1)
    assert "ox_fill" in text_of(status, {"events"})


@pytest.mark.parametrize("key, section, marker", [
    ("i", "ignition", "IGNITION"),
    ("o", "outputs", "PS12DC OUTPUTS"),
    ("e", "events", "PIN CHANGES"),
    ("l", "log", "LOG"),
])
def test_each_key_toggles_its_section(status, key, section, marker):
    panel = Panel(status, None)
    panel.shown = set(ALL_SECTIONS)
    assert marker in text_of(status, panel.shown)
    panel.press(key)
    assert section not in panel.shown
    assert f"{marker}\n" not in text_of(status, panel.shown) + "\n"
    panel.press(key.upper())
    assert section in panel.shown


def test_log_is_off_at_start(status):
    assert "log" not in Panel(status, None).shown


def test_log_hides_noise_until_verbose(status):
    status.log.write("x - DEBUG - > TEXT\nx - INFO - Sending data to abc\nx - INFO - Setting driver EIO0\n")
    assert status.log.tail(10) == ["x - INFO - Setting driver EIO0"]
    assert len(status.log.tail(10, verbose=True)) == 3


def test_log_buffer_joins_partial_writes(status):
    status.log.write("half")
    assert status.log.tail(5, verbose=True) == []
    status.log.write(" line\n")
    assert status.log.tail(5, verbose=True) == ["half line"]


def test_log_only_uses_the_room_left_on_screen(status):
    status.log.write("".join(f"line {i}\n" for i in range(100)))
    lines = render(status, {"ignition", "outputs", "log"}, False, 80, 20)
    assert len(lines) <= 20
    assert "line 99" in "\n".join(text for text, _ in lines)


def test_small_window_keeps_the_key_hints(status):
    assert "[q] Quit" in render(status, ALL_SECTIONS, False, 200, 5)[-1][0]


def test_lines_fit_the_window_width(status):
    assert all(len(text) <= 30 for text, _ in render(status, ALL_SECTIONS, False, 30, 40))


def test_driver_names_come_from_the_dashboard_config(tmp_path):
    (tmp_path / "prometheus_configs.json").write_text('{"sensor_ids": {"drivers": {"0": "ox_fill"}}}')
    assert driver_names(tmp_path, "prometheus") == {"0": "ox_fill"}
    assert driver_names(tmp_path, "missing") == {}
