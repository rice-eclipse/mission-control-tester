# Mission Control Tester

Runs the labjack backend and Quonkboard on one computer without the box or the Pi. The backend code
in `labjack/src` runs as is, and only the hardware libraries are faked.

## Setup

Keep `labjack`, `quonkboard` and this folder side by side, then run this here:

```bash
py -m venv .venv
.venv/Scripts/python -m pip install -r requirements.txt
```

## Use

1. Double-click `run_sim.py`, or run `py run_sim.py`.
2. Run `npm start` in `quonkboard` and open an engine page.
3. Set `Connection IP` to `127.0.0.1:2707` and click `Connect`.

Commands use the password in `config.ini`. Press `q` in the sim window to stop.

## Sim window

| Key | Action |
| --- | --- |
| `m` | Switch between Prometheus and Sphinx |
| `i` | Show or hide ignition |
| `o` | Show or hide PS12DC outputs |
| `e` | Show or hide pin changes |
| `l` | Show or hide the log |
| `v` | Show every log line |

The mode only changes what the panel lists. The ignition sequence comes from the Quonkboard page you
fire from, and the panel switches to that engine when you do.

`Rail` is the voltage from the mapping sheet, and `ON` means the software turned that output on. The
sim doesn't model the key switch, so it can't tell you whether power would actually reach anything.

## Options

`--config` picks a config in `labjack/src`, `--engine` sets the starting mode, `--port` changes the
port, `--backend` points at another `src` folder, and `--plain` shows the scrolling log instead of
the panel.

## Run folders

Each run gets a folder in `runs/` with the backend's CSV and logs, the config it used, and
`pins.jsonl`, a record of every pin write. CSVs grow by about 150 MB an hour.

## Wiring

Box labels, power inputs and voltages are in `mctester/wiring.py`, copied from the mapping sheet. If
the box changes, update that file and the rows in `tests/test_wiring.py`.

## Tests

```bash
.venv/Scripts/python -m pytest
```

## Limits

Sensors sit at rest and don't react to valves, and there's no key switch or fault injection yet. The
sim can't test wiring, relays, the PS12DC, the mux, radios or timing on the Pi, so still do a hardware
checkout before any fire. Make sure `labjack/src` matches the Pi before trusting a result.
