"""
Sensor signals for the fake T7. Every sensor sits at its resting value. Values
are turned into volts with the config's own calibration, so the backend's
conversion runs on realistic input and gives the resting value back.
"""
import random
from configparser import ConfigParser

NOISE_VOLTS = 0.0005
AMBIENT_C = 22.0


def calibration_keys(sensor: str):
    """Offset and scale keys in [conversion], chosen as _voltages_to_values does."""
    prefix = sensor[:6]
    if prefix == "thermo":
        return "thermo_offset", "thermo_scale"
    if prefix == "b_load":
        return "big_lc_offset", "big_lc_scale"
    if prefix == "k_load":
        return "k_lc_offset", "k_lc_scale"
    if prefix == "s_load":
        return "small_lc_offset", "small_lc_scale"
    if prefix == "strain":
        return "strain_offset", "strain_scale"
    if prefix[:4] == "pres":
        return prefix + "_offset", prefix + "_scale"
    return None


class RestingSensors:
    def __init__(self, config: ConfigParser):
        self.resting_volts = {}
        for sensor, channel in config["sensor_channel_mapping"].items():
            keys = calibration_keys(sensor)
            value = AMBIENT_C if sensor.startswith("thermo") else 0.0
            if keys:
                offset = float(config["conversion"][keys[0]])
                scale = float(config["conversion"][keys[1]])
                self.resting_volts[channel] = value * scale + offset
            else:
                self.resting_volts[channel] = value

    def voltage(self, channel: str, t: float) -> float:
        return self.resting_volts.get(channel, 0.0) + random.gauss(0, NOISE_VOLTS)

    def rtd_temperature(self) -> float:
        return AMBIENT_C + random.gauss(0, 0.05)
