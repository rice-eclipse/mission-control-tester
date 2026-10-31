"""Stands in for the RTD amplifier driver."""
from mctester import runtime


class MAX31865:
    def __init__(self, spi, cs, wires=2):
        self.spi = spi
        self.cs = cs
        self.wires = wires

    @property
    def temperature(self):
        return runtime.sensors.rtd_temperature()
