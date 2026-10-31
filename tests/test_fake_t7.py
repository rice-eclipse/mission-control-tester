import pytest

from mctester.fake_t7 import FakeT7, LJMError, name_to_address


class FlatSensors:
    def voltage(self, channel, t):
        return float(channel[3:])


class ManualClock:
    def __init__(self):
        self.now = 100.0

    def __call__(self):
        return self.now

    def sleep(self, seconds):
        self.now += seconds


@pytest.fixture
def clock():
    return ManualClock()


@pytest.fixture
def t7(clock):
    return FakeT7(FlatSensors(), clock=clock, sleep=clock.sleep)


def test_written_pins_read_back_through_state_registers(t7):
    t7.write("EIO0", True)
    t7.write("EIO5", 1)
    t7.write("CIO2", 1)
    assert t7.read("EIO_STATE") == 0b100001
    assert t7.read("CIO_STATE") == 0b0100
    t7.write("EIO5", False)
    assert t7.read("EIO_STATE") == 0b000001


def test_pin_writes_are_logged_with_time(t7, clock):
    clock.now += 2.5
    t7.write("CIO2", True)
    assert t7.pin_log == [{"t": 2.5, "pin": "CIO2", "value": 1}]


def test_pin_log_file(clock, tmp_path):
    path = tmp_path / "pins.jsonl"
    FakeT7(FlatSensors(), path, clock=clock, sleep=clock.sleep).write("EIO1", 1)
    assert '"pin": "EIO1"' in path.read_text()


@pytest.mark.parametrize("name", ["EIO9", "CIO4", "NOT_A_REGISTER", "AIN1_TYPO"])
def test_unknown_registers_are_rejected(t7, name):
    with pytest.raises(LJMError):
        t7.write(name, 1)


def test_addresses_match_the_t7_register_map():
    assert name_to_address("AIN48")[0] == 96
    assert name_to_address("EIO0")[0] == 2008
    assert name_to_address("CIO2")[0] == 2018


def test_stream_returns_channels_in_scan_order(t7):
    t7.stream_start(1, [96, 98], 300)
    assert t7.stream_read()[0] == [48.0, 49.0]


def test_stream_read_waits_for_the_next_scan(t7, clock):
    t7.stream_start(1, [96], 100)
    t7.stream_read()
    assert clock.now == pytest.approx(100.01)


def test_stream_reports_backlog_when_the_reader_is_slow(t7, clock):
    t7.stream_start(1, [96], 100)
    clock.now += 1
    assert t7.stream_read()[2] == 99


def test_stream_cannot_start_twice_or_stop_when_stopped(t7):
    t7.stream_start(1, [96], 100)
    with pytest.raises(LJMError):
        t7.stream_start(1, [96], 100)
    t7.stream_stop()
    with pytest.raises(LJMError):
        t7.stream_stop()
