# PTSL Exploration Scripts

This directory contains standalone, ad-hoc Python scripts meant for interactive debugging with a live Avid Pro Tools session.

Unlike the automated `unit` or `integration` test suites, these scripts are built to perform very specific actions (like slamming all faders down to verify the Mix window reacts) and print verbose feedback directly to stdout. They are typically run one-at-a-time.

## Preconditions

For almost all scripts here:

1. Pro Tools must be running.
2. A session must be actively open in Pro Tools.
3. The PTSL gRPC connection must be enabled in Pro Tools (`Setup -> Preferences -> Operation -> Enable Server`).
4. You must have run `uv sync --all-extras` to ensure `py-ptsl` is installed.

## Available Scripts

### `probe_faders.py`
Connects to the current session, iterates sequentially over all `Audio` tracks, and attempts to set their Volume faders to cascading dB levels (starting at -6.0 dB and dropping by 0.5 per track). Useful for validating if fader breakpoints are being correctly acknowledged by the PTSL SDK version.

**Usage:**
```bash
uv run python tests/exploration/probe_faders.py
```
