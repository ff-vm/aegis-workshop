#!/usr/bin/env bash
# Run a skill on the REAL Aegis, with the simulator's live view and rule checks
# on this laptop. Commands go to FF's control gateway on the robot through an
# ssh tunnel (the gateway only listens on the robot's 127.0.0.1:8768).
#
#   ./robot/run_real.sh firefly spin_and_wave.py
#
# Skill files are at the repo root; the script runs from there.
#
# Join the robot's Wi-Fi hotspot first (AEGIS-2A-...). ssh asks for the
# robot's password once. 1 m of clear floor all round, someone on the e-stop.
#
# Env: ROBOT_IP (default 192.168.234.1), GW_LOCAL_PORT (default 18768),
#      FF_GW_YAW_SIGN=-1 if the dog turns the wrong way.
set -euo pipefail

USER_NAME="${1:?usage: ./robot/run_real.sh firefly <skill.py>}"
SKILL="${2:?usage: ./robot/run_real.sh firefly <skill.py>}"
ROBOT_IP="${ROBOT_IP:-192.168.234.1}"
LOCAL_PORT="${GW_LOCAL_PORT:-18768}"
HOST="$USER_NAME@$ROBOT_IP"

cd "$(dirname "$0")/.."
SKILL="$(basename "$SKILL")"
[ -f "$SKILL" ] || { echo "No such skill file: $SKILL"; exit 1; }

echo "==> checking the robot at $ROBOT_IP"
if ! python3 -c "import socket,sys; socket.create_connection((sys.argv[1], 22), 3)" "$ROBOT_IP" 2>/dev/null; then
  echo "Robot not reachable at $ROBOT_IP. Are you on its hotspot (AEGIS-2A-...)?"
  echo "Your gateway is: $(route -n get default 2>/dev/null | awk '/gateway/{print $2}')"
  echo "If that's the robot, rerun with ROBOT_IP=<that address>."
  exit 1
fi

SOCK="$(mktemp -u /tmp/aegis-gw.XXXXXX)"
echo "==> opening a tunnel to FF's gateway (ssh $HOST)"
ssh -f -N -M -S "$SOCK" -o ExitOnForwardFailure=yes -o ServerAliveInterval=2 \
    -L "$LOCAL_PORT:127.0.0.1:8768" "$HOST"
trap 'ssh -S "$SOCK" -O exit "$HOST" 2>/dev/null || true' EXIT

# Use the project venv (OpenCV + MediaPipe for camera skills) when there is one.
PY=python3
[ -x .venv/bin/python ] && PY=.venv/bin/python
FF_SIM_OPEN="${FF_SIM_OPEN:-1}" FF_TARGET=gateway AEGIS_GATEWAY="http://127.0.0.1:$LOCAL_PORT" "$PY" "$SKILL"
