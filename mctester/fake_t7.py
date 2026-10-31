"""
In-memory stand-in for a LabJack T7. Holds real register state, so a pin that
is written reads back through EIO_STATE/CIO_STATE, and streams scans at the
configured rate on a real clock. Unknown registers raise instead of being
ignored, so a typo in a config fails here the way it would on the device.
"""
import json
import re
import time

PIN = re.compile(r"^(FIO|EIO|CIO)(\d+)$")
AIN = re.compile(r"^AIN(\d+)$")
AIN_SETTING = re.compile(r"^AIN\d+_(NEGATIVE_CH|RANGE)$")
STREAM_SETTINGS = {
    "STREAM_TRIGGER_INDEX",
    "STREAM_CLOCK_SOURCE",
    "STREAM_RESOLUTION_INDEX",
    "STREAM_SETTLING_US",
}
PIN_COUNT = {"FIO": 8, "EIO": 8, "CIO": 4}
PIN_BASE_ADDRESS = {"FIO": 2000, "EIO": 2008, "CIO": 2016}
FLOAT32 = 3
UINT16 = 0


class LJMError(Exception):
    pass


def name_to_address(name: str):
    """Modbus address and data type of a register, as namesToAddresses gives them."""
    pin = PIN.match(name)
    if pin and int(pin.group(2)) < PIN_COUNT[pin.group(1)]:
        return PIN_BASE_ADDRESS[pin.group(1)] + int(pin.group(2)), UINT16
    ain = AIN.match(name)
    if ain:
        return int(ain.group(1)) * 2, FLOAT32
    raise LJMError(f"LJME_INVALID_NAME: {name}")


class FakeT7:
    def __init__(self, sensors, pin_log_path=None, clock=time.monotonic, sleep=time.sleep):
        self.sensors = sensors
        self.clock = clock
        self.sleep = sleep
        self.pins = {}
        self.settings = {}
        self.pin_log = []
        self.pin_log_fd = open(pin_log_path, "a") if pin_log_path else None
        self.started_at = clock()
        self.stream_active = False
        self.stream_started_at = 0.0
        self.scans_per_read = 0
        self.scan_rate = 0
        self.scan_channels = []
        self.scans_read = 0

    def write(self, name: str, value):
        if PIN.match(name):
            name_to_address(name)
            self._set_pin(name, 1 if float(value) else 0)
        elif name in STREAM_SETTINGS or AIN_SETTING.match(name):
            self.settings[name] = float(value)
        else:
            raise LJMError(f"LJME_INVALID_NAME: {name}")

    def read(self, name: str) -> float:
        if name in ("FIO_STATE", "EIO_STATE", "CIO_STATE"):
            bank = name[:3]
            return float(sum(self.pins.get(f"{bank}{i}", 0) << i for i in range(PIN_COUNT[bank])))
        if PIN.match(name):
            name_to_address(name)
            return float(self.pins.get(name, 0))
        if AIN.match(name):
            return self.sensors.voltage(name, self.clock())
        if name in self.settings:
            return self.settings[name]
        raise LJMError(f"LJME_INVALID_NAME: {name}")

    def stream_start(self, scans_per_read: int, addresses, scan_rate: float) -> float:
        if self.stream_active:
            raise LJMError("STREAM_IS_ACTIVE")
        self.scan_channels = [f"AIN{address // 2}" for address in addresses]
        self.scans_per_read = scans_per_read
        self.scan_rate = scan_rate
        self.scans_read = 0
        self.stream_started_at = self.clock()
        self.stream_active = True
        return scan_rate

    def stream_read(self):
        """Block until a read's worth of scans exists, like eStreamRead. The
        backlog is how far behind the reader is, so a slow backend sees it."""
        if not self.stream_active:
            raise LJMError("LJME_NO_SCANS_RETURNED: stream is not running")
        ready_at = self.stream_started_at + (self.scans_read + self.scans_per_read) / self.scan_rate
        wait = ready_at - self.clock()
        if wait > 0:
            self.sleep(wait)
        data = []
        for _ in range(self.scans_per_read):
            scan_time = self.stream_started_at + self.scans_read / self.scan_rate
            data += [self.sensors.voltage(channel, scan_time) for channel in self.scan_channels]
            self.scans_read += 1
        scans_made = int((self.clock() - self.stream_started_at) * self.scan_rate)
        return data, 0, max(scans_made - self.scans_read, 0)

    def stream_stop(self):
        if not self.stream_active:
            raise LJMError("STREAM_NOT_RUNNING")
        self.stream_active = False

    def _set_pin(self, name: str, value: int):
        self.pins[name] = value
        entry = {"t": round(self.clock() - self.started_at, 4), "pin": name, "value": value}
        self.pin_log.append(entry)
        if self.pin_log_fd:
            self.pin_log_fd.write(json.dumps(entry) + "\n")
            self.pin_log_fd.flush()
