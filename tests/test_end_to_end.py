"""Runs the real backend through run_sim.py and talks to it the way Quonkboard does."""
import json
import socket
import subprocess
import sys
import time
from configparser import ConfigParser
from pathlib import Path

import pytest
from websockets.sync.client import connect

ROOT = Path(__file__).resolve().parent.parent
BACKEND_CONFIG = ROOT.parent / "labjack" / "src" / "config.ini"
IGNITION_PIN = "CIO2"
OX_FILL_PIN = "EIO0"
FUEL_FILL_PIN = "CIO0"


def free_port():
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


class Sim:
    def __init__(self, port, runs_dir):
        self.port = port
        self.runs_dir = runs_dir
        config = ConfigParser()
        config.read(BACKEND_CONFIG)
        self.password = config["general"]["password"]

    def client(self):
        return connect(f"ws://127.0.0.1:{self.port}")

    def pin_log(self):
        path = next(self.runs_dir.glob("*/pins.jsonl"))
        return [json.loads(line) for line in path.read_text().splitlines()]


@pytest.fixture(scope="module")
def sim(tmp_path_factory):
    runs_dir = tmp_path_factory.mktemp("runs")
    port = free_port()
    with open(runs_dir / "stdout.txt", "w") as out:
        process = subprocess.Popen(
            [sys.executable, str(ROOT / "run_sim.py"), "--port", str(port), "--runs-dir", str(runs_dir)],
            stdout=out, stderr=subprocess.STDOUT,
        )
    try:
        deadline = time.monotonic() + 20
        while True:
            try:
                socket.create_connection(("127.0.0.1", port), timeout=0.2).close()
                break
            except OSError:
                if process.poll() is not None or time.monotonic() > deadline:
                    pytest.fail((runs_dir / "stdout.txt").read_text())
                time.sleep(0.1)
        yield Sim(port, runs_dir)
    finally:
        process.terminate()
        process.wait(timeout=10)


def wait_for_port(port, process, timeout=20):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline and process.poll() is None:
        try:
            socket.create_connection(("127.0.0.1", port), timeout=0.2).close()
            return True
        except OSError:
            time.sleep(0.1)
    return False


def test_starts_from_system_python_as_a_double_click_would(tmp_path):
    port = free_port()
    process = subprocess.Popen(
        [sys._base_executable, str(ROOT / "run_sim.py"), "--port", str(port), "--runs-dir", str(tmp_path)],
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
    )
    assert wait_for_port(port, process)
    with connect(f"ws://127.0.0.1:{port}") as client:
        client.send(json.dumps({"type": "close"}))
    assert process.wait(timeout=10) == 0


def test_busy_port_gives_a_clear_error(tmp_path):
    with socket.socket() as blocker:
        blocker.bind(("127.0.0.1", 0))
        blocker.listen()
        port = blocker.getsockname()[1]
        result = subprocess.run(
            [sys.executable, str(ROOT / "run_sim.py"), "--port", str(port), "--runs-dir", str(tmp_path)],
            capture_output=True, text=True, timeout=20,
        )
    assert result.returncode == 1
    assert f"Port {port} is already in use" in result.stderr


def messages(client, seconds):
    deadline = time.monotonic() + seconds
    while True:
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            return
        try:
            yield json.loads(client.recv(timeout=remaining))
        except TimeoutError:
            return


def next_telemetry(client):
    for message in messages(client, 5):
        if "pts" in message:
            return message
    pytest.fail("No telemetry within 5 seconds")


def wait_for_driver(client, driver_id, expected):
    for message in messages(client, 5):
        if "driver" in message and message["driver"]["values"][driver_id] == expected:
            return message["driver"]["values"]
    pytest.fail(f"Driver {driver_id} never reached {expected}")


def test_dashboard_is_told_this_is_a_simulator(sim):
    with sim.client() as client:
        consoles = [m.get("console") for m in messages(client, 1)]
    assert any("SIMULATOR" in (text or "") for text in consoles)


def test_telemetry_arrives_in_the_shape_quonkboard_reads(sim):
    with sim.client() as client:
        telemetry = next_telemetry(client)
    assert len(telemetry["pts"]["readings"]) == 6
    assert len(telemetry["driver"]["values"]) == 9
    assert telemetry["rtds"]["readings"][0]["reading"] == pytest.approx(22, abs=1)
    # Resting voltages convert back to about zero through the real calibration
    for reading in telemetry["pts"]["readings"]:
        assert reading["reading"] == pytest.approx(0, abs=5)


@pytest.mark.parametrize("driver_id", range(9))
def test_each_driver_round_trips_to_its_own_index(sim, driver_id):
    with sim.client() as client:
        actuate = {"type": "Actuate", "driver_id": str(driver_id), "password": sim.password}
        client.send(json.dumps({**actuate, "value": True}))
        values = wait_for_driver(client, driver_id, True)
        assert [i for i, on in enumerate(values) if on] == [driver_id]
        client.send(json.dumps({**actuate, "value": False}))
        wait_for_driver(client, driver_id, False)


def test_wrong_password_moves_nothing(sim):
    with sim.client() as client:
        writes_before = len(sim.pin_log())
        client.send(json.dumps({"type": "Actuate", "driver_id": "0", "value": True, "password": "wrong"}))
        consoles = [m.get("console") for m in messages(client, 1)]
    assert "Actuate failed: Invalid password" in consoles
    assert len(sim.pin_log()) == writes_before


def test_prometheus_ignition_timeline(sim):
    with sim.client() as client:
        start = len(sim.pin_log())
        client.send(json.dumps({"type": "Ignition", "engine": "prometheus", "password": sim.password}))
        list(messages(client, 19))
    events = sim.pin_log()[start:]
    assert [(e["pin"], e["value"]) for e in events[:6]] == [
        (IGNITION_PIN, 1),
        (OX_FILL_PIN, 1),
        (FUEL_FILL_PIN, 1),
        (OX_FILL_PIN, 0),
        (FUEL_FILL_PIN, 0),
        (IGNITION_PIN, 0),
    ]
    assert events[1]["t"] - events[0]["t"] == pytest.approx(1, abs=0.2)
    assert events[3]["t"] - events[1]["t"] == pytest.approx(5, abs=0.2)
