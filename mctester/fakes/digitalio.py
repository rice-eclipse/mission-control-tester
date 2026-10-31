"""Stands in for the Pi's Adafruit `digitalio` module."""
from enum import Enum


class Direction(Enum):
    INPUT = 0
    OUTPUT = 1


class DigitalInOut:
    def __init__(self, pin):
        self.pin = pin
