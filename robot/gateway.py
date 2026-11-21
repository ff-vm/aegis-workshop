"""
Drive the Aegis through FF's control gateway (firefly_control_gateway.py).

The robot runs FF's gateway on 127.0.0.1:8768. It owns the native control link
(UDP 43988), so the ff_sdk native backend can't connect while it runs; talking
to the gateway's HTTP API instead works and leaves FF's setup alone.

Stdlib only, plain python3. On the robot:
    python3 gateway.py status
    python3 gateway.py stand
    python3 gateway.py forward 1.5        # seconds
    python3 gateway.py square 2           # seconds per side
    python3 gateway.py limp               # motors off: the dog slumps down

From the Mac:
    ssh firefly@192.168.234.1 python3 - stand < gateway.py

Commands seen working: stand, lie, passive (limp). The tricks are forwarded by
the gateway to the motion gateway on :8765 (per FF's Aegis README).
"""

import json
import os
import sys
import time
import urllib.error
import urllib.request

URL = os.environ.get("AEGIS_GATEWAY", "http://127.0.0.1:8768")
SOURCE = os.environ.get("AEGIS_SOURCE", "aegis-skill")
TIMEOUT = 45.0  # tricks can take ~10 s, and the gateway waits them out


class GatewayError(RuntimeError):
    pass


class Stopped(Exception):
    """Raised between steps when the caller's stop() says so."""


def _check(stop):
    if stop and stop():
        raise Stopped()


def _request(path, body=None, timeout=TIMEOUT):
    data = None if body is None else json.dumps(body).encode()
    req = urllib.request.Request(URL + path, data=data, method="GET" if body is None else "POST",
                                 headers={"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return json.loads(r.read() or b"{}")
    except urllib.error.HTTPError as e:
        try:
            return json.loads(e.read() or b"{}")
        except ValueError:
            raise GatewayError(f"gateway HTTP {e.code}") from None
    except (urllib.error.URLError, OSError) as e:
        raise GatewayError(f"gateway not reachable at {URL}: {e}") from None


def status(timeout=5):
    return _request("/status", timeout=timeout)


def feedback(timeout=5):
    return _request("/feedback", timeout=timeout)


def cmd(name, **args):
    """POST /cmd and raise if the gateway says no. Blocks while the gateway runs it."""
    res = _request("/cmd", {"cmd": name, "source": SOURCE, **args})
    if not res.get("ok"):
        raise GatewayError(f"{name}: {res.get('error') or res}")
    return res


def move(vx=0.0, vy=0.0, yaw=0.0, duration=0.5, **timing):
    """The gateway fakes a gamepad: it waits `warmup`, ramps the stick over
    `ramp`, eases back over `neutral_end`, then sleeps `post_wait` (defaults
    0.15 / 0.35 / 0.25 / 0.7 s). Moves shorter than ~1 s never reach walking
    speed with those defaults; pass tighter timing for continuous driving."""
    return cmd("move", vx=vx, vy=vy, yaw=yaw, duration=duration, **timing)


# ------------------------------------------------------------------ actions

def stand(stop=None):
    return cmd("stand")


def lie(stop=None):
    return cmd("lie")


def limp(stop=None):
    """passive: motors off. The dog slumps to the floor - the safe 'off' state."""
    return cmd("passive")


def square(side_s=2.0, turn_s=2.0, stop=None):
    """Open-loop square: walk side_s seconds, turn left turn_s seconds, four times.

    vx/yaw here are stick fractions, not m/s (the gateway emulates a gamepad),
    and below ~0.75 the dog doesn't walk. turn_s for a 90 deg turn is a guess:
    time one turn on the floor and adjust.
    """
    for _ in range(4):
        _check(stop)
        move(vx=0.75, duration=side_s)
        _check(stop)
        move(yaw=0.75, duration=turn_s)


def wiggle(times=3, stop=None):
    """Small turn left / right in place."""
    for _ in range(times):
        _check(stop)
        move(yaw=0.75, duration=1.0)
        _check(stop)
        move(yaw=-0.75, duration=1.0)


# No nod on this robot: the gateway's remote_udp backend has no attitude
# command, and a right-stick (ry) push is ignored. The firmware's own dance
# (ZGWS_dance_1 in /opt/export/config/dance_config.yaml) has no gateway
# trigger. So the dance is built from moves that work: twists, steps, a paw.
DANCE = [  # (vx, yaw, beats); left/right and forward/back balance out
    (0, 0.75, 2), (0, -0.75, 2), (0, 0.75, 2), (0, -0.75, 2),
    (0.75, 0, 2), (-0.75, 0, 2),
    (0, 0.75, 1), (0, -0.75, 1), (0, 0.75, 1), (0, -0.75, 1),
    (0.75, 0, 2), (-0.75, 0, 2),
]


def dance(bpm=100, stop=None):
    """Twist and step in place on a beat, then shake hands.

    Tight gateway timing (no warm-up or pause) so moves chain on the beat;
    0.75 is the smallest stick that makes the dog move.
    """
    beat = 60.0 / bpm
    for vx, yaw, beats in DANCE:
        _check(stop)
        move(vx=vx, yaw=yaw, duration=beat * beats,
             warmup=0.0, ramp=0.15, neutral_end=0.1, post_wait=0.0)
    _check(stop)
    cmd("shakehand")


def trick(name):
    def run(stop=None):
        return cmd(name)
    run.__name__ = name
    return run


DIRECTIONS = ("forward", "backward", "left", "right", "turn_left", "turn_right")

ACTIONS = {
    "stand": stand,
    "lie": lie,
    "limp": limp,
    "greet": trick("greet"),
    "sitdown": trick("sitdown"),
    "shakehand": trick("shakehand"),
    "stretchleg": trick("stretchleg"),
    # Front jump is a point-foot stunt: needs room and a full battery (devkit).
    "frontjump": trick("frontjump"),
    "dance": dance,
    "square": square,
    "wiggle": wiggle,
    "recover": trick("recover"),   # get back up after a fall
    # Gait speed for later walking (gateway REMOTE_CMD_MAP 174 / 175 / 176).
    "low_speed": trick("low_speed"),
    "mid_speed": trick("mid_speed"),
    "high_speed": trick("high_speed"),
}


def main(argv):
    if not argv or argv[0] in ("-h", "--help", "help"):
        print("usage: gateway.py status | " + " | ".join(ACTIONS) +
              " | " + " | ".join(f"{d} [seconds]" for d in DIRECTIONS))
        return 0
    name, rest = argv[0], argv[1:]
    try:
        if name == "status":
            print(json.dumps(status(), indent=2))
        elif name == "feedback":
            print(json.dumps(feedback(), indent=2))
        elif name in DIRECTIONS:
            print(cmd(name, duration=float(rest[0]) if rest else 1.0))
        elif name == "square":
            square(side_s=float(rest[0]) if rest else 2.0)
            print("square: done")
        elif name in ACTIONS:
            print(ACTIONS[name]())
        else:
            print(f"unknown command {name!r}; try: gateway.py help")
            return 2
    except GatewayError as e:
        print(f"error: {e}")
        return 1
    except KeyboardInterrupt:
        print("interrupted - going limp")
        limp()
        return 130
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
