"""
The sim's window: a live panel of what the box is doing, in place of the
backend's scrolling log. Keys turn each section on and off. The backend still
writes its full log file to the run folder.
"""
import _thread
import collections
import json
import os
import shutil
import sys
import threading
import time
from pathlib import Path

from mctester.wiring import board_order, output_for

ENGINES = ("prometheus", "sphinx")
IGNITION_DRIVER = "6"
EVENT_ROWS = 6
NOISY_LOG_TEXT = (" - DEBUG - ", "Sending data to", "samples obtained")
SECTIONS = [
    ("i", "ignition", "Ignition"),
    ("o", "outputs", "Outputs"),
    ("e", "events", "Pin changes"),
    ("l", "log", "Log"),
]
QUIT_KEYS = ("q", "\x03")
GREEN = "\x1b[32m"
RED = "\x1b[1;31m"
DIM = "\x1b[2m"
BOLD = "\x1b[1m"
RESET = "\x1b[0m"


class LogBuffer:
    """Stands in for sys.stdout, so backend logging lands here instead of on screen."""

    def __init__(self, keep=500):
        self.lines = collections.deque(maxlen=keep)
        self.partial = ""

    def write(self, text):
        self.partial += text
        *finished, self.partial = self.partial.split("\n")
        self.lines.extend(finished)
        return len(text)

    def flush(self):
        pass

    def isatty(self):
        return False

    def tail(self, count, verbose=False):
        lines = [line for line in self.lines
                 if verbose or not any(text in line for text in NOISY_LOG_TEXT)]
        return lines[-count:] if count > 0 else []


def driver_names(configs_dir: Path, engine: str):
    """Driver id -> name from the dashboard's config, so names match what the operator sees."""
    try:
        with open(configs_dir / f"{engine}_configs.json") as config_file:
            return json.load(config_file)["sensor_ids"]["drivers"]
    except (OSError, KeyError, ValueError):
        return {}


def pin_changes(pin_log):
    """Writes that changed a pin, leaving out repeats like the clear at startup."""
    pins = {}
    changes = []
    for entry in pin_log:
        if pins.get(entry["pin"], 0) != entry["value"]:
            changes.append(entry)
        pins[entry["pin"]] = entry["value"]
    return changes


class SimStatus:
    """Everything the panel shows."""

    def __init__(self, device, config, names_by_engine, engine, address, run_dir, log):
        self.device = device
        self.names_by_engine = names_by_engine
        self.engine = engine
        self.address = address
        self.run_dir = run_dir
        self.log = log
        self.last_message = None
        self.sender = None
        mapping = config["driver_mapping"]
        self.ignition_pin = mapping.get(IGNITION_DRIVER)
        self.driver_pins = {driver_id: pin for driver_id, pin in mapping.items()
                            if driver_id != IGNITION_DRIVER}

    def next_engine(self):
        self.engine = ENGINES[(ENGINES.index(self.engine) + 1) % len(ENGINES)]

    def driver_rows(self):
        """(pin, name) for this engine's outputs, or for all of them if names are unavailable."""
        names = self.names_by_engine.get(self.engine, {})
        return [(pin, names.get(driver_id, "-")) for driver_id, pin in self.driver_pins.items()
                if driver_id in names or not names]

    def name_of(self, pin):
        if pin == self.ignition_pin:
            return "ignition"
        return dict(self.driver_rows()).get(pin, "-")

    def dashboards(self):
        return len(self.sender.clients) if self.sender else 0

    def elapsed(self):
        return self.device.clock() - self.device.started_at


def header_lines(status):
    return [
        (f"SIMULATOR - no hardware attached   {status.address}   mode: {status.engine.upper()}   "
         f"dashboards: {status.dashboards()}   scans: {status.device.scans_read}", BOLD),
        ("", None),
    ]


def ignition_lines(status):
    pin = status.ignition_pin
    if not pin:
        return [("IGNITION: no driver 6 in this config", DIM), ("", None)]
    title = f"IGNITION  {pin}"
    if status.device.pins.get(pin, 0):
        turned_on = pin_changes(status.device.pin_log)
        since = next(e["t"] for e in reversed(turned_on) if e["pin"] == pin)
        state = (f"{title}   ON for {status.elapsed() - since:.1f} s", RED)
    else:
        state = (f"{title}   off", GREEN)
    return [state, (f"  Last backend message: {status.last_message or '-'}", None), ("", None)]


def output_lines(status):
    lines = [("PS12DC OUTPUTS", BOLD),
             (f"  {'Pin':<7}{'Box label':<17}{'Dashboard':<20}{'In':<6}{'Rail':<6}State", DIM)]
    pins = status.device.pins
    rows = status.driver_rows()
    listed = {pin for pin, _ in rows} | {status.ignition_pin}
    configured = set(status.driver_pins.values())
    # A pin this engine doesn't use is still shown if it is on: nothing should drive one
    extras = [(pin, "-" if pin in configured else "NOT IN CONFIG") for pin, value in pins.items()
              if value and pin not in listed]
    for pin, name in sorted(rows + extras, key=lambda row: board_order(row[0])):
        output = output_for(pin)
        rail = f"{output.rail_volts}V" if output.rail_volts else "-"
        row = f"  {pin:<7}{output.box_label:<17}{name:<20}{output.power:<6}{rail:<6}"
        if pins.get(pin, 0):
            lines.append((row + "ON", RED))
        else:
            lines.append((row + "off", DIM))
    lines.append(("", None))
    return lines


def event_lines(status):
    lines = [("PIN CHANGES", BOLD)]
    changes = pin_changes(status.device.pin_log)[-EVENT_ROWS:]
    if not changes:
        lines.append(("  none yet", DIM))
    for entry in changes:
        name = status.name_of(entry["pin"])
        box_label = output_for(entry["pin"]).box_label
        state = "ON" if entry["value"] else "off"
        lines.append((f"  {entry['t']:>9.2f} s  {entry['pin']:<7}{box_label:<17}{name:<20}{state}", None))
    lines.append(("", None))
    return lines


def footer_lines(status, shown, verbose):
    toggles = "  ".join(f"[{key}] {label} {'on' if name in shown else 'off'}"
                        for key, name, label in SECTIONS)
    log_detail = "everything" if verbose else "important"
    return [(f"[m] Mode: {status.engine}  {toggles}  [v] Log detail: {log_detail}  [q] Quit", DIM)]


def render(status, shown, verbose, width, height):
    """The panel's lines as (text, color). The log gets whatever height is left."""
    lines = header_lines(status)
    if "ignition" in shown:
        lines += ignition_lines(status)
    if "outputs" in shown:
        lines += output_lines(status)
    if "events" in shown:
        lines += event_lines(status)
    footer = footer_lines(status, shown, verbose)
    footer.insert(0, (f"Run folder: {status.run_dir}", DIM))
    if "log" in shown:
        room = height - len(lines) - len(footer) - 2
        lines.append(("LOG", BOLD))
        lines += [(line, None) for line in status.log.tail(room, verbose)]
        lines.append(("", None))
    lines = lines[:max(height - len(footer), 0)] + footer
    return [(text[:width], color) for text, color in lines]


class KeyReader:
    """Non-blocking key presses."""

    def __enter__(self):
        if os.name != "nt":
            import termios
            import tty
            self.saved = termios.tcgetattr(sys.stdin)
            tty.setcbreak(sys.stdin)
        return self

    def __exit__(self, *exc):
        if os.name != "nt":
            import termios
            termios.tcsetattr(sys.stdin, termios.TCSADRAIN, self.saved)

    def poll(self):
        keys = []
        if os.name == "nt":
            import msvcrt
            while msvcrt.kbhit():
                key = msvcrt.getwch()
                if key in ("\x00", "\xe0"):  # Arrow and function keys arrive as two characters
                    msvcrt.getwch()
                else:
                    keys.append(key)
        else:
            import select
            while select.select([sys.stdin], [], [], 0)[0]:
                keys.append(sys.stdin.read(1))
        return keys


def enable_ansi():
    if os.name == "nt":
        import ctypes
        kernel32 = ctypes.windll.kernel32
        handle = kernel32.GetStdHandle(-11)
        mode = ctypes.c_ulong()
        kernel32.GetConsoleMode(handle, ctypes.byref(mode))
        kernel32.SetConsoleMode(handle, mode.value | 4)


class Panel:
    """Draws on the terminal's alternate screen, so the window's history survives."""

    REFRESH_SECONDS = 0.25
    POLL_SECONDS = 0.05

    def __init__(self, status, out):
        self.status = status
        self.out = out
        self.shown = {"ignition", "outputs", "events"}
        self.verbose = False
        self.stopping = threading.Event()
        self.thread = threading.Thread(target=self._run, daemon=True)

    def __enter__(self):
        enable_ansi()
        self.out.write("\x1b[?1049h\x1b[?25l")
        self.out.flush()
        self.thread.start()
        return self

    def __exit__(self, *exc):
        self.stopping.set()
        self.thread.join(timeout=2)
        self.out.write("\x1b[?25h\x1b[?1049l")
        self.out.flush()

    def press(self, key):
        key = key.lower()
        for section_key, name, _ in SECTIONS:
            if key == section_key:
                self.shown.symmetric_difference_update({name})
        if key == "v":
            self.verbose = not self.verbose
        if key == "m":
            self.status.next_engine()
        if key in QUIT_KEYS:
            _thread.interrupt_main()

    def draw(self):
        size = shutil.get_terminal_size()
        lines = render(self.status, self.shown, self.verbose, size.columns - 1, size.lines)
        painted = [f"{color}{text}{RESET}" if color else text for text, color in lines]
        self.out.write("\x1b[H" + "\x1b[K\n".join(painted) + "\x1b[K\x1b[J")
        self.out.flush()

    def _run(self):
        next_draw = 0.0
        with KeyReader() as keys:
            while not self.stopping.is_set():
                pressed = keys.poll()
                for key in pressed:
                    self.press(key)
                if pressed or time.monotonic() >= next_draw:
                    self.draw()
                    next_draw = time.monotonic() + self.REFRESH_SECONDS
                time.sleep(self.POLL_SECONDS)
