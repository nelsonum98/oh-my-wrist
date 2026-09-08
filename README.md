<p align="center">
  <img src="static/ohw_logo_transparent.png" alt="oh-my-wrist logo" width="240"/>
</p>

<h1 align="center">oh-my-wrist</h1>

![oh-my-wrist](https://img.shields.io/badge/Oh--My--Wrist-blue)
![GitHub stars](https://img.shields.io/github/stars/yazon/oh-my-wrist?style=social)
![GitHub top language](https://img.shields.io/github/languages/top/yazon/oh-my-wrist)
![GitHub repo size](https://img.shields.io/github/repo-size/yazon/oh-my-wrist)
![GitHub last commit](https://img.shields.io/github/last-commit/yazon/oh-my-wrist?color=red)
![GitHub License](https://img.shields.io/github/license/yazon/oh-my-wrist)

**oh-my-wrist displays [Claude Code](https://docs.anthropic.com/en/docs/agents-and-tools/claude-code/overview), Codex, and [OpenCode](https://opencode.ai) activity on your Garmin smartwatch over Bluetooth Low Energy. See weekly Claude/Codex usage and job-completion alerts right on your wrist.**

## Key Features

- **Real-time BLE updates** — tool calls, file edits, and session state streamed to your watch
- **Haptic alerts** — vibration patterns for idle, session done, destructive commands, and agent completion
- **CLI-styled watch UI** — terminal aesthetic with animated amber spinner, event stack, and per-provider stats
- **Multi-provider** — supports Claude Code, Codex, and OpenCode simultaneously
- **Cross-platform** — runs on Linux, macOS, and Windows
- **Quiet hours** — suppress vibrations during configurable time windows
- **Connection ID filter** — optional 0–255 ID keeps nearby users' watches from pairing with each other

<p align="center">
  <img src="static/demo.gif" alt="oh-my-wrist Watch Demo"/>
</p>

## Quick Start

### 1. Install the Python daemon

```bash
pip install oh-my-wrist
```

Or with [uv](https://docs.astral.sh/uv/):

```bash
uv pip install oh-my-wrist
```

### 2. Configure providers and system service

```bash
oh-my-wrist install
```

This automatically:
- Patches **Claude Code** hooks in `~/.claude/settings.json`
- Configures a **Claude Code statusLine** that preserves and chains the full existing statusLine configuration
- Adds **Codex** lifecycle hooks in `~/.codex/hooks.json` without changing Codex's existing `notify` command
- Installs the **OpenCode** TypeScript plugin (when `opencode` is on PATH)
- Registers a background **system service** (systemd / launchd / Task Scheduler)

To install a single provider only:

```bash
oh-my-wrist install --provider claude
oh-my-wrist install --provider codex
oh-my-wrist install --provider opencode
oh-my-wrist install --provider agents  # Claude Code + Codex
```

### 3. Install the Garmin watch app

Install it one of two ways:

1. GitHub Releases: download `oh-my-wrist-prg-<version>.zip` from the latest release, unzip it, connect your Garmin device over USB, then copy the matching `oh-my-wrist-<device-id>.prg` file to `/GARMIN/Apps/`.
2. Connect IQ Store: [DOWNLOAD](https://apps.garmin.com/en-US/apps/1fff0ec7-4813-4f94-8d24-3dc9fef0b9df)
3. Build from source: install Connect IQ SDK 9.1+, then run `tools/build_garmin.sh release` and copy the generated device-specific `.prg` file from `build/garmin/` to `/GARMIN/Apps/`. VS Code single-device builds from `garmin/` write `bin/oh-my-wrist.prg`.

### 4. Start coding

```bash
oh-my-wrist start
```

Open the **oh-my-wrist** app on your watch — it connects automatically and updates in real time.

## Platform Guides

| Platform | Guide |
|----------|-------|
| 🐧 Linux | [docs/INSTALL_LINUX.md](docs/INSTALL_LINUX.md) |
| 🍎 macOS | [docs/INSTALL_MACOS.md](docs/INSTALL_MACOS.md) |
| 🪟 Windows | [docs/INSTALL_WINDOWS.md](docs/INSTALL_WINDOWS.md) |

## Usage

Start the daemon in the background (or foreground for debugging):

```bash
oh-my-wrist start
oh-my-wrist start --foreground
```

Start a Claude Code, Codex, or OpenCode session — the watch updates automatically.

### Connection Check

From a source checkout, run the one-minute diagnostic stream to verify the
daemon-to-watch link without starting a real Claude Code or OpenCode session:

```bash
python tools/check_connection.py
```

On Linux, the daemon's verbose BlueZ diagnostics (connection parameters, device info, dmesg disconnect reasons) are off by default; set `OHM_BLE_DIAG=1` in the daemon's environment to enable them.

Keep the daemon running and the watch app open. You should see history rows
change, Claude/OpenCode stats increment, and Claude usage bars move. Useful
options:

```bash
python tools/check_connection.py --duration 30
python tools/check_connection.py --dry-run
python tools/check_connection.py --provider claude
```

### Watch Navigation

Four swipeable views (left/right or UP/DOWN on button watches). History is the
initial view — swipe/press **UP** for the weekly usage screen, **DOWN** for the
per-provider stats screens:

| View | Direction from History | Content |
|------|------------------------|---------|
| Weekly Usage | UP | Weekly Claude and Codex usage bars |
| History | — (initial) | CLI-style event stack (3 visible rows with animated spinner) |
| Claude Stats | DOWN | Session duration, tool calls, files edited, bash count, idle time |
| OpenCode Stats | DOWN ×2 | Same metrics, isolated from Claude |

The usage screen shows an empty bar with no percentage when a provider's quota
data is unavailable. Claude reuses the same private cache as the user's normal
status line; Codex reads its local app-server rate-limit API.

Press **SELECT/START** on any view to open the app menu. Use **Set id** to
save the same 0–255 connection ID configured on your desktop daemon, then
restart the watch app so the new BLE service UUID is registered cleanly.

### CLI Commands

| Command | Purpose |
|---------|---------|
| `oh-my-wrist start [-f\|--foreground]` | Start the BLE daemon |
| `oh-my-wrist stop` | Stop the daemon |
| `oh-my-wrist status` | Show daemon PID, service status, hook/plugin state |
| `oh-my-wrist install [--provider claude\|codex\|opencode\|agents\|both\|all]` | Configure hooks, plugin, and OS service |
| `oh-my-wrist uninstall [--provider …]` | Remove hooks, plugin, and service |
| `oh-my-wrist test [message] [--provider claude\|codex\|opencode]` | Send a test message to the daemon |
| `oh-my-wrist config [--show\|--haptic on\|off\|--quiet-start HH:MM\|--quiet-end HH:MM]` | View or update configuration |
| `oh-my-wrist set-id ID` | Set BLE connection ID (`0`–`255`) and queue an update for a running daemon |
| `oh-my-wrist logs [-n LINES]` | Tail the daemon log |

## Configuration

Settings are stored at `~/.oh-my-wrist/config.json` and take effect immediately (no restart needed).

```bash
# View current config
oh-my-wrist config --show

# Toggle haptic alerts
oh-my-wrist config --haptic on
oh-my-wrist config --haptic off

# Set quiet hours (vibrations suppressed)
oh-my-wrist config --quiet-start 22:00 --quiet-end 08:00

# Set BLE connection ID to match the watch menu's Set id value
oh-my-wrist set-id 42
```

| Setting | Default | Description |
|---------|---------|-------------|
| `haptic_enabled` | `true` | Enable/disable all vibration alerts |
| `quiet_start` | `22:00` | Start of quiet window (HH:MM) |
| `quiet_end` | `08:00` | End of quiet window (HH:MM) |
| `connection_id` | `0` | BLE scan filter ID. The watch only connects to daemons with the same ID. |

The connection ID is not authentication; it is a collision reducer for rooms
where multiple people run oh-my-wrist. ID `0` is the default and preserves the
original BLE service UUID.

## Supported Devices

Requires a Garmin watch with Connect IQ Generic BLE support (API level alone is not enough):

Manifest product IDs: `approachs50`, `approachs7042mm`, `approachs7047mm`, `d2air`, `d2airx10`, `d2mach1`, `d2mach2`, `d2mach2pro`, `descentg1`, `descentg2`, `descentmk2`, `descentmk2s`, `descentmk343mm`, `descentmk351mm`, `edge1030`, `edge1030plus`, `edge1040`, `edge1050`, `edge530`, `edge540`, `edge550`, `edge830`, `edge840`, `edge850`, `edgeexplore`, `edgeexplore2`, `edgemtb`, `enduro`, `enduro3`, `epix2`, `epix2pro42mm`, `epix2pro47mm`, `epix2pro51mm`, `etrextouch`, `fenix5plus`, `fenix5splus`, `fenix5xplus`, `fenix6`, `fenix6pro`, `fenix6s`, `fenix6spro`, `fenix6xpro`, `fenix7`, `fenix7pro`, `fenix7pronowifi`, `fenix7s`, `fenix7spro`, `fenix7x`, `fenix7xpro`, `fenix7xpronowifi`, `fenix843mm`, `fenix847mm`, `fenix8pro47mm`, `fenix8solar47mm`, `fenix8solar51mm`, `fenixe`, `fr165`, `fr165m`, `fr170`, `fr170m`, `fr245`, `fr245m`, `fr255`, `fr255m`, `fr255s`, `fr255sm`, `fr265`, `fr265s`, `fr55`, `fr57042mm`, `fr57047mm`, `fr645m`, `fr70`, `fr745`, `fr945`, `fr945lte`, `fr955`, `fr965`, `fr970`, `gpsmap66`, `gpsmap67`, `gpsmaph1`, `instinct2`, `instinct2s`, `instinct2x`, `instinct3amoled45mm`, `instinct3amoled50mm`, `instinct3solar45mm`, `instinctcrossover`, `instinctcrossoveramoled`, `instincte40mm`, `instincte45mm`, `legacyherocaptainmarvel`, `legacyherofirstavenger`, `legacysagadarthvader`, `legacysagarey`, `marq2`, `marq2aviator`, `marqadventurer`, `marqathlete`, `marqaviator`, `marqcaptain`, `marqcommander`, `marqdriver`, `marqexpedition`, `marqgolfer`, `montana7xx`, `venu`, `venu2`, `venu2plus`, `venu2s`, `venu3`, `venu3s`, `venu441mm`, `venu445mm`, `venud`, `venusq2m`, `venusqm`, `venux1`, `vivoactive3m`, `vivoactive3mlte`, `vivoactive4`, `vivoactive4s`, `vivoactive5`, `vivoactive6`.

## Troubleshooting

| Problem | Solution |
|---------|----------|
| Watch can't find daemon | Run `oh-my-wrist status` — verify daemon is advertising and that desktop/watch connection IDs match. Restart with `oh-my-wrist stop && oh-my-wrist start`. From a source checkout, run `python tools/check_connection.py` with the watch app open to exercise live HISTORY, stats, and usage updates. |
| Garmin app does not connect to daemon on PC | Make sure your Bluetooth adapter is supported. Some adapters or OS settings, such as Windows random MAC behavior, can prevent stable BLE connections. If problems persist, macOS or Linux is preferred. |
| Garmin app does not start on watch | Create an empty `/GARMIN/Apps/Logs/oh-my-wrist-YOUR_WATCH_ID.log (e.g oh-my-wrist-fenix7x.log)` file on the device, run the app, wait for the issue to occur, reconnect the device to your PC, download log file, then create a GitHub issue with the log for debugging. |
| Hook not firing | Run `oh-my-wrist status` — confirm hooks in `~/.claude/settings.json`. Re-run `oh-my-wrist install`. |
| OpenCode not updating | Check plugin: `oh-my-wrist opencode status`. Re-install: `oh-my-wrist opencode install`. |
| BLE permission errors | See your platform guide: [Linux](docs/INSTALL_LINUX.md) · [macOS](docs/INSTALL_MACOS.md) · [Windows](docs/INSTALL_WINDOWS.md) |
| Daemon crashes on start | Run `oh-my-wrist start --foreground` to see the error. Check `oh-my-wrist logs`. |

## Uninstall

```bash
oh-my-wrist uninstall
pip uninstall oh-my-wrist
```

To remove the watch app, delete it via Garmin Express or the Connect IQ app on your phone.

## Contributing

Contributions are more than welcome. Please contribute! See [CONTRIBUTING.md](docs/CONTRIBUTING.md) for guidelines.

## Development

```bash
# pip
pip install -e ".[dev]"
pytest tests/

# or uv
uv venv && uv pip install -e ".[dev]"
uv run pytest

oh-my-wrist start --foreground
```

## License

This project is licensed under the BSD 3-Clause License. See the [LICENSE](LICENSE) file for details.
