"""
Run the real labjack backend on this machine against fake hardware, then point
Quonkboard at it. The backend's code is not modified or copied: the fake modules
are simply found first on the import path.

    python run_sim.py [--config config.ini] [--port 2707]
"""
import argparse
import datetime as dt
import os
import runpy
import socket
import subprocess
import sys
import traceback
from configparser import ConfigParser
from pathlib import Path

# Keeps __pycache__ folders out of the labjack checkout
sys.dont_write_bytecode = True

ROOT = Path(__file__).resolve().parent
FAKES = ROOT / "mctester" / "fakes"
VENV_PYTHON = ROOT / ".venv" / ("Scripts/python.exe" if os.name == "nt" else "bin/python")
SIM_HOST = "127.0.0.1"
BANNER = "SIMULATOR - no hardware attached"
DASHBOARD_CONFIGS = ROOT.parent / "quonkboard" / "src" / "configs"

sys.path.insert(0, str(ROOT))
from mctester import runtime
from mctester.console import ENGINES, LogBuffer, Panel, SimStatus, driver_names
from mctester.fake_t7 import FakeT7
from mctester.sensors import RestingSensors


def relaunch_in_venv():
    """Double-clicking uses the system Python, which lacks the dependencies."""
    if sys.prefix != sys.base_prefix:
        return
    if not VENV_PYTHON.exists():
        sys.exit(f"No virtual environment at {VENV_PYTHON.parent.parent}. See Setup in README.md.")
    try:
        sys.exit(subprocess.call([str(VENV_PYTHON), __file__, *sys.argv[1:]]))
    except KeyboardInterrupt:
        sys.exit(0)


def check_port_free(port: int):
    with socket.socket() as s:
        try:
            s.bind((SIM_HOST, port))
        except OSError:
            sys.exit(f"Port {port} is already in use. Is another sim running? Stop it or use --port.")


def build_config(source: Path, port: int) -> ConfigParser:
    """The real config, changed only so the server stays on this machine."""
    config = ConfigParser()
    if not config.read(source):
        sys.exit(f"Could not read config: {source}")
    config["general"]["host"] = SIM_HOST
    config["general"]["port"] = str(port)
    return config


def prepare_run_folder(runs_dir: Path, config: ConfigParser) -> Path:
    """The backend reads ./config.ini and writes ../logs and ../data, so give it
    a folder laid out like the Pi's. Returns the folder to run in."""
    stamp = dt.datetime.now().strftime("%Y-%m-%d_%H-%M-%S")
    run_dir = runs_dir / stamp
    attempt = 1
    while run_dir.exists():
        attempt += 1
        run_dir = runs_dir / f"{stamp}_{attempt}"
    work_dir = run_dir / "work"
    work_dir.mkdir(parents=True)
    (run_dir / "logs").mkdir()
    (run_dir / "data").mkdir()
    with open(work_dir / "config.ini", "w") as config_file:
        config.write(config_file)
    return work_dir


def install_fakes(backend_src: Path):
    sys.path[:0] = [str(FAKES), str(backend_src)]
    from labjack import ljm
    if FAKES not in Path(ljm.__file__).resolve().parents:
        sys.exit(f"Refusing to run: labjack.ljm resolved to the real library at {ljm.__file__}")


def hook_backend(status: SimStatus):
    """Tell each dashboard that connects that this is not the real box, and
    keep the panel up to date on clients, broadcasts and the engine in use."""
    from cmd_from_dash import CmdListener
    from data_to_dash import DataSender
    add_client = DataSender.add_client
    broadcast_message = DataSender.broadcast_message
    process_command = CmdListener.process_command

    async def add_client_with_banner(self, client):
        status.sender = self
        await add_client(self, client)
        await self.send_message(client, BANNER)

    async def broadcast_and_record(self, message):
        status.last_message = message
        await broadcast_message(self, message)

    async def follow_engine_and_process(self, cmd, websocket):
        # Ignition is the only command that says which engine page sent it
        if isinstance(cmd, dict) and cmd.get("engine") in ENGINES:
            status.engine = cmd["engine"]
        await process_command(self, cmd, websocket)

    DataSender.add_client = add_client_with_banner
    DataSender.broadcast_message = broadcast_and_record
    CmdListener.process_command = follow_engine_and_process


def run_backend(backend_src: Path, status: SimStatus, plain: bool):
    main_py = str(backend_src / "main.py")
    if plain or not sys.stdout.isatty():
        runpy.run_path(main_py, run_name="__main__")
        return
    real_stdout = sys.stdout
    sys.stdout = status.log
    try:
        with Panel(status, real_stdout):
            runpy.run_path(main_py, run_name="__main__")
    finally:
        sys.stdout = real_stdout


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--backend", type=Path, default=ROOT.parent / "labjack" / "src",
                        help="labjack src folder (default: ../labjack/src)")
    parser.add_argument("--config", default="config.ini",
                        help="config file in the backend folder (default: config.ini)")
    parser.add_argument("--port", type=int, default=2707)
    parser.add_argument("--runs-dir", type=Path, default=ROOT / "runs",
                        help="where each run's logs, CSV and pin log go")
    parser.add_argument("--engine", choices=ENGINES,
                        help=f"mode the panel starts in (default: the config's engine, or {ENGINES[0]})")
    parser.add_argument("--plain", action="store_true",
                        help="scroll the backend's log instead of showing the live panel")
    args = parser.parse_args()

    relaunch_in_venv()
    check_port_free(args.port)
    backend_src = args.backend.resolve()
    config = build_config(backend_src / args.config, args.port)
    work_dir = prepare_run_folder(args.runs_dir.resolve(), config)
    run_dir = work_dir.parent

    runtime_sensors = RestingSensors(config)
    device = FakeT7(runtime_sensors, run_dir / "pins.jsonl")
    runtime.configure(device, runtime_sensors)
    install_fakes(backend_src)
    config_engine = config["general"].get("engine")
    engine = args.engine or (config_engine if config_engine in ENGINES else ENGINES[0])
    names_by_engine = {name: driver_names(DASHBOARD_CONFIGS, name) for name in ENGINES}
    status = SimStatus(device, config, names_by_engine, engine,
                       f"{SIM_HOST}:{args.port}", run_dir, LogBuffer())
    hook_backend(status)

    print(f"[sim] {BANNER}")
    print(f"[sim] Connect Quonkboard to {status.address}")
    print(f"[sim] Run folder: {run_dir}", flush=True)

    os.chdir(work_dir)
    try:
        run_backend(backend_src, status, args.plain)
    except KeyboardInterrupt:
        print("[sim] Stopped")


def hold_window_open():
    """A double-clicked script's window closes on exit, taking the error with it."""
    if sys.stdin.isatty() and sys.stdout.isatty():
        input("\nThe simulator did not start. Press Enter to close...")


if __name__ == "__main__":
    try:
        main()
    except SystemExit as exit_request:
        if exit_request.code:
            if isinstance(exit_request.code, str):
                print(exit_request.code, file=sys.stderr)
            hold_window_open()
            sys.exit(1)
    except Exception:
        traceback.print_exc()
        hold_window_open()
        sys.exit(1)
