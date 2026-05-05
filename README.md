# annealr

Annealing and drying temperature controller for [Kalico](https://github.com/KalicoCrew/kalico) (Klipper fork).

Turns a cheap airfryer or convection oven into a PID-controlled annealing chamber for high-performance 3D printing filaments (PET-CF, PPS-CF, PPA-CF, etc.) and filament drying.

## Features

- **Profile-based ramp-and-soak control** - define temperature profiles with ramp segments (target + rate or duration) and soak segments (duration at temperature)
- **Pause/resume with time accounting** - pause mid-run without losing soak progress
- **Safety watchdogs** - ramp timeout, soak drift, and cooling stall detection
- **Multiple profiles** - store profiles in `printer.cfg`, define interactively via GCode
- **Moonraker status integration** - exposes state for dashboards (KlipperScreen, Mainsail, custom UI)

## Requirements

- Kalico or Klipper installation
- Python 3.9+
- A `[heater_generic]` configured for your chamber heater

## Installation

```bash
cd ~
git clone https://github.com/YOUR_USERNAME/annealr.git
cd annealr
chmod +x scripts/install.sh
./scripts/install.sh
```

To uninstall:

```bash
./scripts/install.sh --uninstall
```

Add to your `printer.cfg`:

```ini
[annealr]
heater: annealer
```

For automatic updates, add to `moonraker.conf`:

```ini
[update_manager annealr]
type: git_repo
path: ~/annealr
origin: https://github.com/YOUR_USERNAME/annealr.git
managed_services: klipper
primary_branch: main
install_script: scripts/install.sh
```

## Defining profiles

### Via GCode commands (interactive)

```gcode
ANNEALR_PROFILE_BEGIN NAME=pet_cf DESCRIPTION="PET-CF stress relief"
ANNEALR_PROFILE_RAMP TARGET=85 RATE=15
ANNEALR_PROFILE_SOAK DURATION=60
ANNEALR_PROFILE_RAMP TARGET=45
ANNEALR_PROFILE_END
```

Profile is immediately available. Run `SAVE_CONFIG` to persist to `printer.cfg`.

### Via printer.cfg (version-controllable)

```ini
[annealr_profile pps_cf]
description: PPS-CF crystallization
segments:
    ramp,  80, rate=15
    soak,  30
    ramp, 130, rate=8
    soak,  45
    ramp, 200, rate=6
    soak, 120
    ramp, 120, rate=3
    ramp,  45
```

Segment format: `kind, value [, rate=X] [, duration=X]`
- **ramp**: value is target °C. Specify `rate` (°C/min), `duration` (minutes), or neither (unconstrained).
- **soak**: value is duration in minutes.

## Running a profile

```gcode
ANNEALR_START PROFILE=pps_cf
ANNEALR_STATUS
ANNEALR_PAUSE
ANNEALR_RESUME
ANNEALR_CANCEL
```

## GCode command reference

| Command | Parameters | Description |
|---------|-----------|-------------|
| `ANNEALR_PROFILE_BEGIN` | `NAME=` `DESCRIPTION=` | Start defining a profile |
| `ANNEALR_PROFILE_RAMP` | `TARGET=` `RATE=` or `DURATION=` | Add ramp segment |
| `ANNEALR_PROFILE_SOAK` | `DURATION=` | Add soak segment |
| `ANNEALR_PROFILE_END` | | Commit profile |
| `ANNEALR_PROFILE_ABORT` | | Discard in-progress profile |
| `ANNEALR_PROFILES` | | List all profiles |
| `ANNEALR_PROFILE_SHOW` | `NAME=` | Show profile details |
| `ANNEALR_PROFILE_DELETE` | `NAME=` | Delete a profile |
| `ANNEALR_START` | `PROFILE=` | Start annealing |
| `ANNEALR_PAUSE` | | Pause (heater holds) |
| `ANNEALR_RESUME` | | Resume |
| `ANNEALR_CANCEL` | | Cancel and turn off heater |
| `ANNEALR_STATUS` | | Report current state |

## KlipperScreen integration

For touchscreen controls on KlipperScreen:

```bash
./scripts/install.sh --klipperscreen
```

This adds an "Annealing" menu to KlipperScreen's home screen with
Start, Pause, Resume, Cancel, and Status buttons for each profile.

To set up manually, copy the menu entries from
`klipperscreen/KlipperScreen_anneal_menu.conf` into your
`~/printer_data/config/KlipperScreen.conf`.

To show the chamber temperature in KlipperScreen's title bar, add:

```ini
[printer MyPrinter]
titlebar_items: annealer
```

## Development

Cross-platform development setup (Linux, macOS, Windows):

```bash
python setup_dev_env.py              # create venv and install deps
python setup_dev_env.py --run-tests  # also run the test suite
```

Or manually:

```bash
python -m venv dev-env
# Linux/macOS:
source dev-env/bin/activate
# Windows:
dev-env\Scripts\activate

pip install pytest
pytest tests/unit/ -v
```

## Project structure

```
annealr/
├── src/annealr/           # Klippy extra (symlinked into klipper/klippy/extras/)
│   ├── __init__.py       # AnnealrManager - entry point, GCode commands, tick loop
│   ├── profile.py        # Segment, Profile, config parsing, validation
│   ├── executor.py       # SegmentExecutor - setpoint trajectory computation
│   ├── state_machine.py  # State transitions, pause/resume time accounting
│   └── watchdogs.py      # Ramp timeout, soak drift, cool stall detection
├── scripts/              # Installation scripts (Linux only)
│   ├── install.sh
│   
├── klipperscreen/        # KlipperScreen integration
│   ├── KlipperScreen_anneal_menu.conf
│   └── panels/           # Custom panel (future)
├── tests/
│   ├── unit/             # pytest unit tests (cross-platform)
│   └── integration/      # Kalico integration tests
├── docs/examples/        # Example profile configs
├── setup_dev_env.py      # Cross-platform dev environment setup
├── moonraker.conf        # Moonraker update_manager snippet
└── README.md
```

## License

MIT
