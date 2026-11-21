#!/bin/bash
# Double-click (macOS) or run ./run_demo.command: runs the spin-and-wave
# sequence in the simulator and opens the live view in your browser.
cd "$(dirname "$0")" || exit 1
echo "Aegis workshop folder: $(pwd)"
echo
PY=.venv/bin/python
[ -x "$PY" ] || PY=python3
FF_SIM_OPEN=1 "$PY" spin_and_wave.py
echo
echo "Replay saved: $(pwd)/run.html"
