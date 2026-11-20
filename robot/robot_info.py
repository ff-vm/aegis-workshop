"""
Print what the Aegis says about itself (dev_info, version, fault_info, ...).

Listens on UDP 8080, where the robot pushes its state, for a few seconds and
prints one sample of each message type. Sends nothing, moves nothing, needs
no SDK. Run it on the robot straight from the Mac:

    ssh <user>@192.168.234.1 python3 - < robot_info.py

Useful when asking FF which SDK build matches this unit.
"""

import json
import socket
import sys
import time

PORT = 8080
SECONDS = float(sys.argv[1]) if len(sys.argv) > 1 else 6.0
WANT = ("dev_info", "version", "fault_info", "dog_state")

sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
if hasattr(socket, "SO_REUSEPORT"):
    sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEPORT, 1)
sock.bind(("0.0.0.0", PORT))
sock.settimeout(0.5)

seen = {}
end = time.time() + SECONDS
print(f"listening on UDP {PORT} for {SECONDS:.0f} s ...", file=sys.stderr)
while time.time() < end:
    try:
        data, addr = sock.recvfrom(65535)
    except socket.timeout:
        continue
    text = data.decode("utf-8", "replace")
    try:
        msg = json.loads(text)
    except ValueError:
        kind = "non-json"
    else:
        kind = next((k for k in WANT if k in text), None) or str(
            msg.get("type") or msg.get("cmd") or msg.get("name") or "other"
            if isinstance(msg, dict) else "other")
    seen.setdefault(kind, (addr, text))

if not seen:
    print("no packets - the robot may only push state after a client says hello.\n"
          "Run './robot/run_on_robot.sh <user>' (check) in another terminal while this listens.")
for kind in sorted(seen, key=lambda k: (k not in WANT, k)):
    addr, text = seen[kind]
    print(f"\n== {kind}  (from {addr[0]})\n{text[:1500]}")
