"""
The sim's window: a live panel of what the box is doing, in place of the
backend's scrolling log. Keys turn each section on and off. The backend still
writes its full log file to the run folder.

A line on screen is a list of (text, style) spans, so the layout can measure
and pad it without counting escape codes.
"""
import _thread
import collections
import json
import os
import re
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
LOG_LINE = re.compile(r"^\d{4}-\d\d-\d\d (\d\d:\d\d:\d\d) - (\w+) - (.*)$")
SECTIONS = [
    ("i", "ignition", "Ignition"),
    ("o", "outputs", "Outputs"),
    ("e", "events", "Pin changes"),
    ("l", "log", "Log"),
]
QUIT_KEYS = ("q", "\x03")

MAX_WIDTH = 132
OUTPUT_CARD_WIDTH = 62
SIDE_BY_SIDE_WIDTH = 107

TEXT = "\x1b[38;5;252m"
MUTED = "\x1b[38;5;245m"
BORDER = "\x1b[38;5;240m"
ACCENT = "\x1b[1;38;5;75m"
GREEN = "\x1b[1;38;5;78m"
AMBER = "\x1b[1;38;5;214m"
RED = "\x1b[1;38;5;203m"
FIRING = "\x1b[1;97;41m"
SIM_CHIP = "\x1b[1;30;48;5;214m"
MODE_CHIP = "\x1b[1;30;48;5;75m"
KEY_ON = "\x1b[1;30;48;5;75m"
KEY_OFF = "\x1b[38;5;250;48;5;238m"
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


def fit(spans, width):
    """Cut or pad a line to exactly this width."""
    fitted = []
    room = width
    for text, style in spans:
        piece = text[:room]
        fitted.append((piece, style))
        room -= len(piece)
    if room > 0:
        fitted.append((" " * room, None))
    return fitted


def card(title, rows, width, border=BORDER):
    inner = max(width - 4, 0)
    title = title[:max(inner - 2, 0)]
    top = [("╭─ ", border), (title, ACCENT), (" " + "─" * max(width - 5 - len(title), 0) + "╮", border)]
    bottom = [("╰" + "─" * max(width - 2, 0) + "╯", border)]
    middle = [[("│ ", border)] + fit(row, inner) + [(" │", border)] for row in rows]
    return [fit(top, width)] + [fit(line, width) for line in middle] + [fit(bottom, width)]


def beside(left, right, left_width, right_width):
    lines = []
    for index in range(max(len(left), len(right))):
        left_line = left[index] if index < len(left) else [(" " * left_width, None)]
        right_line = right[index] if index < len(right) else [(" " * right_width, None)]
        lines.append(left_line + [(" ", None)] + right_line)
    return lines


def header_line(status):
    count = status.dashboards()
    if count:
        link = [("● ", GREEN), (f"{count} dashboard{'s' if count != 1 else ''}", TEXT)]
    else:
        link = [("○ ", MUTED), ("no dashboard", MUTED)]
    minutes, seconds = divmod(int(status.elapsed()), 60)
    return ([(" SIM ", SIM_CHIP), (f" {status.engine.upper()} ", MODE_CHIP),
             ("  no hardware", TEXT), (f"  {status.address}  ", MUTED)]
            + link + [(f"  up {minutes}:{seconds:02}", MUTED)])


def ignition_card(status, width):
    pin = status.ignition_pin
    if not pin:
        return card("IGNITION", [[("No driver 6 in this config", MUTED)]], width)
    message = [("Last message  ", MUTED), (status.last_message or "-", TEXT)]
    if status.device.pins.get(pin, 0):
        turned_on = pin_changes(status.device.pin_log)
        since = next(e["t"] for e in reversed(turned_on) if e["pin"] == pin)
        banner = f" ● {pin}  ON for {status.elapsed() - since:.1f} s"
        return card("IGNITION", [[(banner.ljust(max(width - 4, 0)), FIRING)], message], width, RED)
    return card("IGNITION", [[("○ ", MUTED), (f"{pin}  ", MUTED), ("off", TEXT)], message], width)


def output_card(status, width):
    heading = f"  {'Pin':<6}{'Box label':<16}{'Dashboard':<19}{'In':<5}{'Rail':<5}State"
    rows = [[(heading, MUTED)]]
    pins = status.device.pins
    drivers = status.driver_rows()
    listed = {pin for pin, _ in drivers} | {status.ignition_pin}
    configured = set(status.driver_pins.values())
    # A pin this engine doesn't use is still shown if it is on: nothing should drive one
    extras = [(pin, "-" if pin in configured else "NOT IN CONFIG") for pin, value in pins.items()
              if value and pin not in listed]
    for pin, name in sorted(drivers + extras, key=lambda row: board_order(row[0])):
        output = output_for(pin)
        rail = f"{output.rail_volts}V" if output.rail_volts else "-"
        detail = f"{pin:<6}{output.box_label:<16}{name:<19}{output.power:<5}{rail:<5}"
        if pin not in listed:
            rows.append([("● ", AMBER), (detail, AMBER), ("ON", AMBER)])
        elif pins.get(pin, 0):
            rows.append([("● ", GREEN), (detail, TEXT), ("ON", GREEN)])
        else:
            rows.append([("○ ", MUTED), (detail, MUTED), ("off", MUTED)])
    return card("PS12DC OUTPUTS", rows, width)


def event_card(status, width):
    changes = pin_changes(status.device.pin_log)[-EVENT_ROWS:]
    rows = []
    for entry in changes:
        on = entry["value"]
        rows.append([(f"{entry['t']:>8.2f} s  ", MUTED), ("● " if on else "○ ", GREEN if on else MUTED),
                     (f"{entry['pin']:<6}{status.name_of(entry['pin']):<19}", TEXT if on else MUTED),
                     ("ON" if on else "off", GREEN if on else MUTED)])
    return card("PIN CHANGES", rows or [[("None yet", MUTED)]], width)


def log_row(line):
    match = LOG_LINE.match(line)
    if not match:
        return [(line, TEXT)]
    clock_time, level, message = match.groups()
    style = {"ERROR": RED, "CRITICAL": RED, "WARNING": AMBER}.get(level, TEXT)
    return [(f"{clock_time}  ", MUTED), (message, style)]


def body_lines(status, shown, width):
    show_outputs = "outputs" in shown
    side_cards = "ignition" in shown or "events" in shown
    if show_outputs and side_cards and width >= SIDE_BY_SIDE_WIDTH:
        side_width = width - OUTPUT_CARD_WIDTH - 1
        side = []
        if "ignition" in shown:
            side += ignition_card(status, side_width)
        if "events" in shown:
            side += event_card(status, side_width)
        return beside(output_card(status, OUTPUT_CARD_WIDTH), side, OUTPUT_CARD_WIDTH, side_width)
    lines = []
    if "ignition" in shown:
        lines += ignition_card(status, width)
    if show_outputs:
        lines += output_card(status, width)
    if "events" in shown:
        lines += event_card(status, width)
    return lines


def footer_lines(status, shown, verbose, width):
    chips = [("m", "Mode", True)]
    chips += [(key, label, name in shown) for key, name, label in SECTIONS]
    chips += [("v", "All log lines", verbose), ("q", "Quit", True)]
    run_folder = f"Run folder: {status.run_dir}"
    if len(run_folder) > width:
        run_folder = "…" + run_folder[-(width - 1):]
    lines = [[(run_folder, MUTED)]]
    line = []
    used = 0
    for key, label, active in chips:
        size = len(key) + len(label) + 6
        if line and used + size > width:
            lines.append(line)
            line = []
            used = 0
        line += [(f" {key} ", KEY_ON if active else KEY_OFF), (f" {label}  ", TEXT if active else MUTED)]
        used += size
    lines.append(line)
    return lines


def render(status, shown, verbose, width, height):
    """The panel's lines as lists of spans. The log gets whatever height is left."""
    width = min(width, MAX_WIDTH)
    lines = [header_line(status), []] + body_lines(status, shown, width)
    footer = footer_lines(status, shown, verbose, width)
    if "log" in shown:
        room = height - len(lines) - len(footer) - 2
        rows = [log_row(line) for line in status.log.tail(room, verbose)]
        lines += card("LOG", rows or [[("Nothing to show yet", MUTED)]], width)
    lines = lines[:max(height - len(footer), 0)] + footer
    return [fit(line, width) for line in lines]


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
        # A terminal without these characters should show "?", not stop the panel
        self.out.reconfigure(errors="replace")
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
        painted = ["".join(f"{style}{text}{RESET}" if style else text for text, style in line)
                   for line in lines]
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
