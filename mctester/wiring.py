"""
How the mission control box is wired, from the team's mapping sheet, in board
order. Each label and voltage belongs to a pin, not an engine: Sphinx and
Prometheus use the same box. The sheet lists one power input per two outputs, so
the pairs here are read from its layout, and they agree with EIO4 and EIO5 both
being 24 V.
"""
from typing import NamedTuple, Optional


class Output(NamedTuple):
    box_label: str
    rail_volts: Optional[int]
    power: str


OUTPUTS = {
    "EIO0": Output("OX FILL", 12, "VS1"),
    "EIO1": Output("OX GND VNT", 12, "VS1"),
    "EIO2": Output("-", None, "VS2"),
    "EIO3": Output("OX REM PURGE", 12, "VS2"),
    "EIO4": Output("BPS", 24, "VS3"),
    "EIO5": Output("FUEL REM PURGE", 24, "VS3"),
    "EIO6": Output("-", None, "VS4"),
    "EIO7": Output("-", None, "VS4"),
    "CIO0": Output("TC2", 12, "VS5"),
    "CIO1": Output("TC1", 12, "VS5"),
    "CIO2": Output("-", None, "VS6"),
}
UNKNOWN = Output("-", None, "-")


def output_for(pin: str) -> Output:
    return OUTPUTS.get(pin, UNKNOWN)


def board_order(pin: str) -> int:
    pins = list(OUTPUTS)
    return pins.index(pin) if pin in OUTPUTS else len(pins)
