"""Checks the team's mapping sheet against what the code and dashboards actually use."""
import json
from configparser import ConfigParser
from pathlib import Path

import pytest

from mctester.wiring import OUTPUTS, board_order, output_for

GITHUB = Path(__file__).resolve().parent.parent.parent
CONFIG = GITHUB / "labjack" / "src" / "config.ini"
DASHBOARD_CONFIGS = GITHUB / "quonkboard" / "src" / "configs"

# (box label, dashboard driver name, volts), one row per line of the sheet
SHEET = {
    "sphinx": [
        ("FUEL REM PURGE", "nitrous_run", 24),
        ("OX REM PURGE", "remote_purge", 12),
        ("BPS", "fuel_run", 24),
        ("OX GND VNT", "ground_vent", 12),
        ("OX FILL", "ox_fill", 12),
        ("TC1", "heating", 12),
    ],
    "prometheus": [
        ("FUEL REM PURGE", "bps", 24),
        ("OX REM PURGE", "fuel_ground_vent", 12),
        ("BPS", "fuel_remote_purge", 24),
        ("OX GND VNT", "ox_ground_vent", 12),
        ("OX FILL", "ox_fill", 12),
        ("TC1", "ox_remote_purge", 12),
        ("TC2", "fuel_fill", 12),
    ],
}
SHEET_ROWS = [(engine, *row) for engine, rows in SHEET.items() for row in rows]


def pin_of(engine, driver_name):
    with open(DASHBOARD_CONFIGS / f"{engine}_configs.json") as config_file:
        drivers = json.load(config_file)["sensor_ids"]["drivers"]
    driver_id = next(key for key, name in drivers.items() if name == driver_name)
    config = ConfigParser()
    config.read(CONFIG)
    return config["driver_mapping"][driver_id]


@pytest.mark.parametrize("engine, box_label, driver_name, volts", SHEET_ROWS)
def test_sheet_row_matches_dashboard_config_and_wiring(engine, box_label, driver_name, volts):
    output = output_for(pin_of(engine, driver_name))
    assert (output.box_label, output.rail_volts) == (box_label, volts)


def test_ignition_is_the_last_output_on_the_board():
    config = ConfigParser()
    config.read(CONFIG)
    assert config["driver_mapping"]["6"] == list(OUTPUTS)[-1]


def test_each_power_input_has_one_voltage():
    rails = {}
    for output in OUTPUTS.values():
        if output.rail_volts:
            rails.setdefault(output.power, set()).add(output.rail_volts)
    assert all(len(volts) == 1 for volts in rails.values())


def test_unknown_pin_has_no_wiring_and_sorts_last():
    assert output_for("FIO3").power == "-"
    assert board_order("FIO3") == len(OUTPUTS)
