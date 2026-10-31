# Mission Control Tester

Runs the real [labjack](https://github.com/rice-eclipse/labjack) backend and the real
[Quonkboard](https://github.com/rice-eclipse/quonkboard) on one computer, with no mission control
box or Raspberry Pi. Only the hardware libraries are faked.

```
Quonkboard (real)
   | WebSocket on 127.0.0.1:2707
labjack/src: main.py and the rest of the backend (real, unmodified)
   | labjack.ljm, board, digitalio, adafruit_max31865
mctester/fakes -> FakeT7 and RestingSensors
```

## Setup

Keep `labjack`, `quonkboard` and this folder side by side. From this folder:

```bash
py -m venv .venv
.venv/Scripts/python -m pip install -r requirements.txt
```

## Running it

Start the sim by double-clicking `run_sim.py`, or from a terminal:

```bash
py run_sim.py
```

It switches to the `.venv` Python on its own. Leave its window open while you use the sim, and press
`q` or `Ctrl + C` to stop it. If it can't start, the window stays open and shows why.

## The sim window

The window is a live panel of what the box is doing. Press a key to show or hide a section:

| Key | Section | Starts |
| --- | --- | --- |
| `i` | Ignition: the output, how long it has been on, and the last backend message | on |
| `m` | Mode: switch between Prometheus and Sphinx | Prometheus |
| `o` | PS12DC outputs: pin, box label, dashboard name, power input, rail voltage, state | on |
| `e` | Pin changes: the last few pins that changed, with times | on |
| `l` | Log: the backend's log, without the repeating telemetry lines | off |
| `v` | Log detail: show every log line in the Log section | off |

- The mode sets which outputs are listed and their dashboard names, which come from Quonkboard's
  config for that engine. It does not change what the backend does: the engine page you fire from
  in Quonkboard picks the ignition sequence. Quonkboard doesn't report which page is open, so
  the panel can't follow it until an Ignition command names the engine. The mode switches to match
  then. `--engine` sets the starting mode.
- An output the current engine doesn't use is hidden unless it is on. Anything without a name or
  known wiring shows `-`.
- Box labels, power inputs and rail voltages come from the team's mapping sheet, kept in
  `mctester/wiring.py`. They belong to pins, so they are the same for every engine. If the box is
  rewired, update that file and the sheet rows in `tests/test_wiring.py`, which checks them against
  `config.ini` and Quonkboard.
- A pin that is on but isn't in the config appears as `NOT IN CONFIG`.
- `Rail` is the voltage the sheet lists for that output, whether or not it is on. `ON` means the
  software has turned the output on. The sim has no real board, and it does not model the key
  switch, so it can't show whether power would reach a valve or the igniters.
- The full log always goes to the run folder, whatever is on screen. `--plain` scrolls the log in
  the window instead of showing the panel.

In the `quonkboard` folder, run `npm start`. Open an engine page, set `Connection IP` to
`127.0.0.1:2707`, and click `Connect`. The telemetry log shows `SIMULATOR - no hardware attached`
when you are connected to the sim. Commands need the password from the config, as on the real box.

Options: `--config sphinx_config.ini` picks another config from the backend folder, `--port` changes
the port, and `--backend` points at a different labjack `src` folder.

## What each run leaves behind

Each run gets its own folder in `runs/`, which git ignores. A run writes about 150 MB of CSV an hour.

- `pins.jsonl`: every digital pin write, with seconds since start. This is the record of what an
  ignition sequence actually did.
- `data/` and `logs/`: the CSV and log the backend would write on the Pi.
- `work/config.ini`: the config it ran with. This is the real config with only `host` and `port`
  changed, so there is no separate sim config to keep up to date.

## Tests

```bash
.venv/Scripts/python -m pytest
```

`tests/test_end_to_end.py` starts the backend through `run_sim.py` and sends commands over WebSocket
the way Quonkboard does. It takes about 30 seconds.

## What is simulated

- Pin state, which reads back through `EIO_STATE` and `CIO_STATE`
- The 300 Hz stream, on a real clock, with an accurate backlog
- Sensors at rest, plus noise, converted to volts with the config's calibration
- Errors for unknown registers, or for starting a stream twice

## What is not simulated yet

- Sensors do not react to valves. Pressures stay near zero until the plant model exists.
- There is no key switch, and no way to inject faults such as an unplugged PT or a USB drop.

## What this can never test

Relays, the PS12DC, wiring, the mux, the radios, and timing on the Pi itself. The fake T7 is also
written from how the backend uses the LJM library, so behavior the real device has but the backend
has not hit yet will not appear here. Do a hardware checkout before any fire.

Check that `labjack/src` matches the code on the Pi before trusting a result. Tests here have only
run on Windows with Python 3.14, while the Pi runs an older Python.
