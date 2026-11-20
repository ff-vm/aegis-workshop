#!/usr/bin/env bash
# Copy the skill pack to the Aegis, install the SDK there (offline), and run a skill.
#
#   ./run_on_robot.sh <user> [skill] [extra args...]
#
#   ./run_on_robot.sh myuser              # copy + install + status (doesn't move)
#   ./run_on_robot.sh myuser stand        # through FF's gateway (gateway.py):
#   ./run_on_robot.sh myuser greet        #   stand lie limp greet sitdown shakehand
#   ./run_on_robot.sh myuser square 2     #   stretchleg square wiggle, forward 1.5 ...
#   ./run_on_robot.sh myuser sdk-check    # old ff_sdk path (aegis_skills.py); can't
#                                         # connect while FF's gateway holds the link
#   ./run_on_robot.sh myuser panel           # web panel at http://192.168.234.1:8000, Ctrl+C quits
#   ./run_on_robot.sh myuser panel-install   # start the panel on every boot
#   ./run_on_robot.sh myuser panel-uninstall
#
# Env: ROBOT_IP (default 192.168.234.1). FF_SDK_AEGIS_HOST and
#      FF_SDK_AEGIS_VARIANT override what aegis-run picks on the robot
#      (target_ip from sdk_config.yaml, and footed).
# Join the robot's Wi-Fi hotspot first. ssh asks for the password once.
set -euo pipefail

USER_NAME="${1:?usage: ./run_on_robot.sh <user> [skill] [args...]}"
SKILL="${2:-status}"
shift $(( $# >= 2 ? 2 : 1 ))
ROBOT_IP="${ROBOT_IP:-192.168.234.1}"
HOST="$USER_NAME@$ROBOT_IP"
APP_DIR='~/ff_app'

cd "$(dirname "$0")"
# The FF devkit is handed out separately: unzip it at the repo root (../aegis_devkit).
WHEELS=(../aegis_devkit/wheels/ff_sdk-*-linux_aarch64.whl ../aegis_devkit/wheels/typing_extensions-*.whl)
[ -f "${WHEELS[0]}" ] || { echo "FF SDK not found: unzip ff_sdk_aegis_devkit.zip at the repo root first (aegis_devkit/)."; exit 1; }

echo "==> pinging $ROBOT_IP"
if ! ping -c 2 -t 3 "$ROBOT_IP" >/dev/null; then
  echo "Robot not reachable at $ROBOT_IP. Are you on its hotspot?"
  echo "Your gateway is: $(route -n get default 2>/dev/null | awk '/gateway/{print $2}')"
  echo "If that's the robot, rerun with ROBOT_IP=<that address>."
  exit 1
fi

# One shared ssh connection, so the password is asked only once.
SOCK="$(mktemp -u /tmp/aegis-ssh.XXXXXX)"
SSH=(ssh -o ControlMaster=auto -o ControlPath="$SOCK" -o ControlPersist=60)
trap '"${SSH[@]}" -O exit "$HOST" 2>/dev/null || true' EXIT

echo "==> copying files"
"${SSH[@]}" "$HOST" "mkdir -p $APP_DIR/wheels"
scp -o ControlPath="$SOCK" "${WHEELS[@]}" "$HOST:$APP_DIR/wheels/"
scp -o ControlPath="$SOCK" aegis_skills.py gateway.py panel.py aegis-run aegis-panel.service "$HOST:$APP_DIR/"

echo "==> installing SDK (skipped if already installed)"
"${SSH[@]}" "$HOST" "
  set -e
  [ -x $APP_DIR/venv/bin/python ] || python3 -m venv $APP_DIR/venv
  $APP_DIR/venv/bin/pip install -q --no-index --find-links $APP_DIR/wheels ff_sdk==0.1.0a10
  $APP_DIR/venv/bin/python -c 'import ff_sdk; print(\"ff_sdk\", ff_sdk.__version__)'
"

case "$SKILL" in
  panel-install)
    echo "==> installing the panel as a boot service"
    "${SSH[@]}" "$HOST" "
      set -e
      mkdir -p ~/.config/systemd/user
      cp $APP_DIR/aegis-panel.service ~/.config/systemd/user/
      systemctl --user daemon-reload
      systemctl --user enable aegis-panel
      systemctl --user restart aegis-panel
      loginctl enable-linger \$USER || echo 'enable-linger failed: run  sudo loginctl enable-linger \$USER  on the robot, or the panel only starts after you log in'
      sleep 3
      systemctl --user --no-pager status aegis-panel | head -12
    "
    echo "Panel: http://$ROBOT_IP:8000  (logs: ssh in, then journalctl --user -u aegis-panel -f)"
    exit 0 ;;
  panel-uninstall)
    "${SSH[@]}" "$HOST" "systemctl --user disable --now aegis-panel; rm -f ~/.config/systemd/user/aegis-panel.service; systemctl --user daemon-reload"
    echo "Panel service removed."
    exit 0 ;;
  status|check|limp|lie|sdk-check) ;;
  panel)
    echo
    echo "The panel doesn't move the dog by itself, but its buttons do."
    echo "Open http://$ROBOT_IP:8000 on a device on the hotspot. Ctrl+C here quits (dog goes limp)." ;;
  *)
    echo
    echo "!! '$SKILL' will MOVE the dog. 1 m clear floor all round, someone on the e-stop."
    read -r -p "Type yes to go: " ok
    [ "$ok" = "yes" ] || { echo "Aborted."; exit 1; } ;;
esac

echo "==> running $SKILL"
# aegis-run sets FF_SDK_AEGIS_HOST from the robot's sdk_config.yaml and the
# variant to footed; anything set here is passed through and wins.
REMOTE_ENV=""
[ -n "${FF_SDK_AEGIS_HOST:-}" ] && REMOTE_ENV="FF_SDK_AEGIS_HOST=$FF_SDK_AEGIS_HOST"
[ -n "${FF_SDK_AEGIS_VARIANT:-}" ] && REMOTE_ENV="$REMOTE_ENV FF_SDK_AEGIS_VARIANT=$FF_SDK_AEGIS_VARIANT"
case "$SKILL" in
  panel)
    # A panel left running by a dropped ssh session keeps port 8000. Stop it
    # first ('[p]' so pkill doesn't match this command line itself).
    "${SSH[@]}" -t "$HOST" "systemctl --user stop aegis-panel 2>/dev/null; pkill -f 'python -u [p]anel.py' && echo 'stopped an old panel' && sleep 1; cd $APP_DIR && chmod +x aegis-run && $REMOTE_ENV ./aegis-run panel.py" ;;
  check)
    "${SSH[@]}" -t "$HOST" "cd $APP_DIR && python3 gateway.py status" ;;
  sdk-*)
    "${SSH[@]}" -t "$HOST" "cd $APP_DIR && chmod +x aegis-run && $REMOTE_ENV ./aegis-run aegis_skills.py ${SKILL#sdk-} $*" ;;
  *)
    "${SSH[@]}" -t "$HOST" "cd $APP_DIR && python3 gateway.py $SKILL $*" ;;
esac
