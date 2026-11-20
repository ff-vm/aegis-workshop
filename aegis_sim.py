"""aegis_sim: top-down workshop simulator for the FF Robotics Aegis.

Stands in for `ff_sdk.connect()` so attendees can see what their skill does
before they queue for a real robot. Same calls as the FF SDK quick reference
(stand, cmd_vel, stop, damping, do_preset, known_presets, preset_timeout,
pose, battery, status, e_stop, estop.reset, diagnose, close).

It simulates *where the dog goes*, not how it walks: no legs, no physics.
It enforces tonight's safety rules the way a mentor would, and shows a live
top-down view in the browser (http://localhost:8765). When the run ends the
same view is saved as run.html for replay.

Use it from a skill:

    from aegis_sim import run_skill

    async def routine(dog):
        await dog.motion.stand()
        ...

    run_skill(routine)

    FF_TARGET=sim               (default) simulator, no SDK needed
    FF_TARGET=dry-run           real ff_sdk in dry-run (aegis://DEMO)
    FF_TARGET=aegis://<serial>  real robot (test stations only)

Simulator settings (environment variables):
    FF_SIM_TASK     0 | 1 | 2 | 3    which task's checks to run (default 1; 0 = safety only)
    FF_SIM_MOVERS   "ax,ay>bx,by@s"  things pacing between two points (seen by motion_eyes.py)
    FF_SIM_VARIANT  footed | wheeled (default footed)
    FF_SIM_SLIP     0.0 – 0.2        floor slip / drift (default 0, Task 3: 0.04)
    FF_SIM_SEED     int              repeatable slip
    FF_SIM_POSE     on | off         off = pose() x/y/yaw are NaN, like a robot without feedback
    FF_SIM_PORT     8765             viewer port
    FF_SIM_OUT      run.html         replay file
    FF_SIM_HOLD     seconds          keep the viewer up after the run (default 3)

Standard library only. Python 3.9+.
"""
from __future__ import annotations

import asyncio
import atexit
import enum
import json
import math
import os
import random
import signal
import sys
import threading
import time
from dataclasses import dataclass, field
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

__all__ = ["connect_dog", "run_skill", "AegisSim"]

# Windows consoles and pipes may not be UTF-8: print ✓ → ° as "?" instead of crashing.
for _stream in (sys.stdout, sys.stderr):
    try:
        _stream.reconfigure(errors="replace")
    except Exception:
        pass

# ── Types: reuse the real SDK's when it is installed, so `except` clauses match ──
try:  # pragma: no cover - depends on the attendee's machine
    from ff_sdk.core.exceptions import (  # type: ignore
        CapabilityNotSupported,
        EStopActiveError,
        FfSdkError,
        StateError,
    )
except Exception:  # SDK not installed (e.g. a Mac running natively)
    class FfSdkError(Exception):
        pass

    class EStopActiveError(FfSdkError):
        pass

    class StateError(FfSdkError):
        pass

    class CapabilityNotSupported(FfSdkError):
        pass

try:  # pragma: no cover
    from ff_sdk.capabilities.types import BatteryState, MotionResult, Pose, RobotStatus  # type: ignore
except Exception:
    class RobotStatus(enum.Enum):
        UNKNOWN = "unknown"
        IDLE = "idle"
        STANDING = "standing"
        MOVING = "moving"
        LYING = "lying"
        DAMPING = "damping"
        ESTOPPED = "estopped"

    @dataclass(frozen=True)
    class Pose:
        x: float = 0.0
        y: float = 0.0
        z: float = 0.0
        roll: float = 0.0
        pitch: float = 0.0
        yaw: float = 0.0

    @dataclass(frozen=True)
    class BatteryState:
        percent: float
        voltage: float | None = None
        is_charging: bool = False

    @dataclass(frozen=True)
    class MotionResult:
        success: bool
        message: str = ""
        details: dict = field(default_factory=dict)


# ── Workshop rules (from the developer guide) ──
MAX_LINEAR = 0.3       # m/s, forward/back and sideways
MAX_ANGULAR = 0.6      # rad/s
WATCHDOG_S = 0.5       # Task 2: no new command for 0.5 s while moving → must stop()
ARENA_HALF = 2.5       # m, cleared area drawn in the viewer
ACCEL_LIN = 0.8        # m/s², how fast the dog reaches a commanded speed
ACCEL_ANG = 2.0        # rad/s²

# Durations match ff_sdk 0.1.0a10 preset_timeout() for the Aegis.
PRESET_TIMEOUTS = {
    "stand": 4.0, "stand_up": 4.0, "recover": 3.0, "damping": 1.0, "lie_down": 3.0,
    "shake_hand": 10.0, "jump": 4.0, "front_jump": 4.0, "two_leg_stand": 4.0, "backflip": 5.0,
}
ALLOWED_TRICKS = {"shake_hand", "lie_down"}
BANNED_TRICKS = {"jump", "front_jump", "two_leg_stand", "backflip"}
POSTURE_PRESETS = {"stand", "stand_up", "recover", "damping"}


def _env(name: str, default: str) -> str:
    return os.environ.get(name, default).strip()


def _wrap(a: float) -> float:
    return math.atan2(math.sin(a), math.cos(a))


# ══════════════════════════════════════════════════════════════════════
#  Simulation core
# ══════════════════════════════════════════════════════════════════════

class _World:
    """Kinematic dog on a floor. Runs in its own thread so a blocking script
    (time.sleep, slow vision code) can't freeze the dog, just like a real robot."""

    def __init__(self) -> None:
        self.task = int(_env("FF_SIM_TASK", "1") or 1)
        self.variant = _env("FF_SIM_VARIANT", "footed").lower()
        slip_default = "0.04" if self.task == 3 else "0"
        self.slip = max(0.0, min(0.3, float(_env("FF_SIM_SLIP", slip_default) or 0)))
        seed = _env("FF_SIM_SEED", "")
        self.rng = random.Random(int(seed) if seed else None)
        self.noise = random.Random()
        self.run_id = '%08x' % random.getrandbits(32)
        self.pose_feedback = _env("FF_SIM_POSE", "on").lower() not in ("off", "0", "false", "no")
        # Things moving back and forth in the room: "ax,ay>bx,by@period;..." in
        # metres and seconds (the dog starts at 0,0 facing +x).
        self.movers: list[dict] = []
        for part in _env("FF_SIM_MOVERS", "").split(";"):
            if part.strip():
                path, _, period = part.partition("@")
                a, _, b = path.partition(">")
                self.movers.append({"a": [float(v) for v in a.split(",")],
                                    "b": [float(v) for v in b.split(",")],
                                    "period": float(period or 4)})
        self._still_since: float | None = None
        self.cam_hfov = math.radians(float(_env("FF_CAM_HFOV", "70") or 70))
        self.cam_range = 4.0

        self.lock = threading.RLock()
        self.t0 = time.monotonic()
        # true state
        self.x = self.y = self.yaw = 0.0
        self.v = self.w = self.l = 0.0            # actual body velocities
        self.cmd = (0.0, 0.0, 0.0)                # commanded linear, angular, lateral
        self.k = (1.0, 1.0, 0.0)                  # slip: lin scale, ang scale, yaw bias
        self.posture = "down"                      # down | standing | lying | damping
        self.preset: str | None = None
        self.preset_start = 0.0
        self.preset_end = 0.0
        self.estopped = False
        self.estop_reason = ""
        self.estop_reset = False
        self.closed = False
        self.battery = 0.87
        self.last_cmd_t = 0.0
        self.wd_flagged = False
        self.arena_flagged = False
        self.warned_not_standing = False
        self.warned_lateral = False

        # bookkeeping for checks
        self.frames: list[list] = []
        self.events: list[dict] = []
        self.distance = 0.0
        self.turn_in_place = 0.0                   # |rad| turned while ~not translating
        self.max_turn_run = 0.0                    # biggest single in-place turn (rad)
        self._turn_run = 0.0
        self.stood = False
        self.tricks_done: list[str] = []
        self.violations: list[str] = []
        self.first_cmd_t: float | None = None
        self.last_motion_t = 0.0
        self.exit_kind = ""                       # clean | estop | none
        self.last_damping_t = -1.0
        self.last_move_cmd_t = -1.0
        self.max_lin = self.max_ang = 0.0
        self.report: dict | None = None
        self.bridge: _GatewayBridge | None = None
        self._last_frame_t = -1.0
        self._listeners: list[threading.Event] = []

        self._running = True
        self._thread = threading.Thread(target=self._loop, name="aegis-sim", daemon=True)
        self._thread.start()

    # ── time / logging ──
    def now(self) -> float:
        return time.monotonic() - self.t0

    def log(self, kind: str, msg: str, echo: bool | None = None) -> None:
        with self.lock:
            self.events.append({"t": round(self.now(), 3), "kind": kind, "msg": msg})
        if echo is None:
            echo = kind in ("warn", "bad", "estop")
        if echo:
            tag = {"warn": "WARN", "bad": "RULE", "estop": "E-STOP"}.get(kind, "sim")
            print(f"[sim {self.now():6.2f}s] {tag}: {msg}", file=sys.stderr, flush=True)
        self._notify()

    def _notify(self) -> None:
        for ev in list(self._listeners):
            ev.set()

    # ── physics loop ──
    def _loop(self) -> None:
        dt = 0.02
        nxt = time.monotonic()
        while self._running:
            nxt += dt
            self.step(dt)
            delay = nxt - time.monotonic()
            if delay > 0:
                time.sleep(delay)
            else:
                nxt = time.monotonic()

    def step(self, dt: float) -> None:
        with self.lock:
            t = self.now()
            if self.preset and t >= self.preset_end:
                self._finish_preset(t)

            can_move = (self.posture == "standing" and self.preset is None
                        and not self.estopped and not self.closed)
            cl, ca, cs = self.cmd if can_move else (0.0, 0.0, 0.0)
            kl, ka, bias = self.k
            tv, tl = cl * kl, cs * kl
            tw = ca * ka + bias * abs(self.v)
            self.v += max(-ACCEL_LIN * dt, min(ACCEL_LIN * dt, tv - self.v))
            self.l += max(-ACCEL_LIN * dt, min(ACCEL_LIN * dt, tl - self.l))
            self.w += max(-ACCEL_ANG * dt, min(ACCEL_ANG * dt, tw - self.w))

            c, s = math.cos(self.yaw), math.sin(self.yaw)
            self.x += (self.v * c - self.l * s) * dt
            self.y += (self.v * s + self.l * c) * dt
            self.yaw = _wrap(self.yaw + self.w * dt)

            trans = math.hypot(self.v, self.l)
            self.distance += trans * dt
            if trans < 0.05 and abs(self.w) > 0.05:
                self.turn_in_place += abs(self.w) * dt
                self._turn_run += self.w * dt
                self.max_turn_run = max(self.max_turn_run, abs(self._turn_run))
            elif abs(self.w) <= 0.05:
                self._turn_run = 0.0
            if trans > 0.01 or abs(self.w) > 0.01:
                self.last_motion_t = t

            moving_cmd = any(abs(c_) > 1e-6 for c_ in self.cmd)
            if (self.task == 2 and moving_cmd and can_move and not self.wd_flagged
                    and t - self.last_cmd_t > WATCHDOG_S):
                self.wd_flagged = True
                self._violate(f"watchdog: still moving {t - self.last_cmd_t:.2f}s after the last "
                              f"command (no fresh command for {WATCHDOG_S}s must mean stop())")
            if not self.arena_flagged and (abs(self.x) > ARENA_HALF or abs(self.y) > ARENA_HALF):
                self.arena_flagged = True
                self.log("warn", f"left the {2 * ARENA_HALF:.0f} m cleared area "
                                 f"at ({self.x:.2f}, {self.y:.2f})")

            if t - self._last_frame_t >= 0.05:
                self._last_frame_t = t
                self.frames.append([round(t, 3), round(self.x, 4), round(self.y, 4),
                                    round(self.yaw, 4), round(self.v, 3), round(self.w, 3),
                                    round(self.l, 3), self.status_str(), self.preset or ""])
                self._notify()

    # ── state helpers ──
    def status_str(self) -> str:
        if self.estopped:
            return "estopped"
        if self.preset:
            return "preset"
        if self.posture == "standing":
            moving = math.hypot(self.v, self.l) > 0.01 or abs(self.w) > 0.01
            return "moving" if moving else "standing"
        return {"down": "idle"}.get(self.posture, self.posture)

    def _violate(self, msg: str) -> None:
        self.violations.append(msg)
        self.log("bad", msg)

    def mentor_estop(self, why: str) -> None:
        with self.lock:
            self._violate(why)
            self._estop(f"mentor: {why}")

    def _estop(self, reason: str) -> None:
        self.estopped = True
        self.estop_reason = reason
        self.cmd = (0.0, 0.0, 0.0)
        self.preset = None
        self.posture = "damping"
        self.exit_kind = self.exit_kind or "estop"
        self.log("estop", reason, echo=True)
        if self.bridge:
            self.bridge.halt(reason)

    def _cut_preset(self, by: str) -> None:
        if self.preset and self.now() < self.preset_end - 0.05:
            left = self.preset_end - self.now()
            self.log("warn", f"{self.preset} cut off by {by}: it needed "
                             f"{PRESET_TIMEOUTS.get(self.preset, 0):.1f}s, {left:.1f}s were left. "
                             f"Wait preset_timeout() before the next command.")
            self.preset = None

    def _finish_preset(self, t: float) -> None:
        name = self.preset
        self.preset = None
        self.last_motion_t = t
        if name in ("stand", "stand_up", "recover"):
            self.posture = "standing"
            self.stood = True
        elif name == "lie_down":
            self.posture = "lying"
        elif name == "damping":
            self.posture = "damping"
        else:
            self.posture = "standing"
        if name in ALLOWED_TRICKS:
            self.tricks_done.append(name)
        self.log("preset", f"{name} done")

    def start_preset(self, name: str) -> None:
        t = self.now()
        self.preset = name
        self.preset_start = t
        self.preset_end = t + PRESET_TIMEOUTS.get(name, 2.0)
        self.cmd = (0.0, 0.0, 0.0)
        self.log("preset", f"{name} started ({PRESET_TIMEOUTS.get(name, 2.0):.1f}s)")

    def note_cmd(self) -> None:
        t = self.now()
        # A script that slept exactly preset_timeout() may land a few ms before
        # the physics thread's next tick; count the preset as finished.
        if self.preset and t >= self.preset_end - 0.05:
            self._finish_preset(t)
        if self.first_cmd_t is None:
            self.first_cmd_t = t
        self.last_cmd_t = t
        self.wd_flagged = False

    def check_operable(self) -> None:
        if self.closed:
            raise StateError("session is closed")
        if self.estop_reset:
            raise StateError("e-stop was reset: connect again before sending commands")
        if self.estopped:
            raise EStopActiveError(f"e_stop active: {self.estop_reason}")

    # ── simulated camera ──
    def mover_at(self, m: dict, t: float) -> tuple[float, float]:
        p = m["period"]
        u = (t % (2 * p)) / p
        k = u if u <= 1 else 2 - u
        return (m["a"][0] + (m["b"][0] - m["a"][0]) * k, m["a"][1] + (m["b"][1] - m["a"][1]) * k)

    def camera_motion(self) -> list[float]:
        """Bearings (rad, + = left) of moving things the camera would notice
        right now. A turning or walking dog sees the whole image move, so it
        notices nothing until it has been still for a moment."""
        with self.lock:
            t = self.now()
            still = abs(self.w) < 0.05 and math.hypot(self.v, self.l) < 0.02
            if not still:
                self._still_since = None
                return []
            if self._still_since is None:
                self._still_since = t
            if t - self._still_since < 0.4:     # body still swaying
                return []
            out = []
            for m in self.movers:
                mx, my = self.mover_at(m, t)
                dx, dy = mx - self.x, my - self.y
                b = _wrap(math.atan2(dy, dx) - self.yaw)
                if math.hypot(dx, dy) <= self.cam_range and abs(b) <= self.cam_hfov / 2:
                    out.append(b)
            return out

    # ── end of run ──
    def finish(self, exit_kind: str | None = None) -> dict:
        with self.lock:
            if self.report is not None:
                return self.report
            if exit_kind and not self.exit_kind:
                self.exit_kind = exit_kind
            self.closed = True
            self.cmd = (0.0, 0.0, 0.0)
            self.report = self._build_report()
            self.log("end", "run finished", echo=False)
        self._running = False
        self._notify()
        return self.report

    def _build_report(self) -> dict:
        t_end = self.now()
        checks: list[dict] = []

        def add(name: str, state: str, detail: str = "") -> None:
            checks.append({"name": name, "state": state, "detail": detail})

        speed_ok = not any(v.startswith("speed") for v in self.violations)
        add("Speed ≤ 0.3 m/s, turning ≤ 0.6 rad/s", "pass" if speed_ok else "fail",
            f"max {self.max_lin:.2f} m/s, {self.max_ang:.2f} rad/s")
        trick_viol = [v for v in self.violations if v.startswith("trick")]
        add("Only allowed tricks", "fail" if trick_viol else "pass",
            "shake_hand, lie_down" if not trick_viol else trick_viol[0])

        if self.task == 1:
            add("Stand", "pass" if self.stood else "fail")
            add("Move (forward, back or sideways)", "pass" if self.distance >= 0.2 else "fail",
                f"{self.distance:.2f} m travelled")
            deg = math.degrees(self.max_turn_run)
            add("Turn in place", "pass" if deg >= 90 else "fail",
                f"{deg:.0f}° in one turn" + (" (a full spin is 360°)" if 90 <= deg < 340 else ""))
            add("A preset", "pass" if self.tricks_done else "fail",
                ", ".join(self.tricks_done) or "none completed")
            start = self.first_cmd_t or 0.0
            show = max(0.0, self.last_motion_t - start)
            add("Show length 20–30 s", "pass" if 20 <= show <= 30 else "warn", f"{show:.1f} s")
        elif self.task == 2:
            wd = [v for v in self.violations if v.startswith("watchdog")]
            add("Watchdog stops the dog within 0.5 s", "fail" if wd else "pass",
                f"{len(wd)} lapse(s)" if wd else "no lapses")
        elif self.task == 3:
            err = math.hypot(self.x, self.y)
            herr = abs(math.degrees(_wrap(self.yaw)))
            add("Stop within 50 cm of the cross", "pass" if err <= 0.5 else "fail",
                f"{err * 100:.0f} cm off, heading error {herr:.0f}°")
            add("Walked the triangle", "pass" if self.distance >= 2.4 else "warn",
                f"{self.distance:.2f} m of 3 m")

        if self.exit_kind == "clean":
            add("Clean exit (stop → damping → close)", "pass")
        elif self.exit_kind == "estop":
            add("Exit via e-stop", "warn" if not self.violations else "fail",
                self.estop_reason)
        else:
            add("Clean exit (stop → damping → close)", "fail",
                "no damping() before close(); use the template")

        if self.violations:
            verdict = "FAIL: a mentor would have hit the e-stop. This run scores zero."
        elif any(c["state"] == "fail" for c in checks):
            verdict = "NOT DONE: see the failed checks."
        else:
            verdict = "PASS: ready for a test station."
        return {
            "task": self.task, "variant": self.variant, "slip": self.slip,
            "duration": round(t_end, 2), "distance": round(self.distance, 3),
            "final": {"x": round(self.x, 3), "y": round(self.y, 3),
                      "yaw_deg": round(math.degrees(self.yaw), 1)},
            "checks": checks, "violations": self.violations, "verdict": verdict,
        }

    def meta(self) -> dict:
        return {"task": self.task, "variant": self.variant, "slip": self.slip,
                "target": "robot" if self.bridge else "sim",
                "pose_feedback": self.pose_feedback, "arena": ARENA_HALF,
                "limits": {"linear": MAX_LINEAR, "angular": MAX_ANGULAR},
                "movers": self.movers, "cam_hfov": self.cam_hfov, "cam_range": self.cam_range,
                "presets": PRESET_TIMEOUTS, "started": time.strftime("%Y-%m-%d %H:%M:%S")}


# ══════════════════════════════════════════════════════════════════════
#  Robot bridge: mirror every command to FF's control gateway
# ══════════════════════════════════════════════════════════════════════

class _GatewayBridge:
    """Sends the skill's commands to the real Aegis through FF's gateway
    (HTTP POST /cmd), the path the workshop robots answer to.

    The gateway emulates a gamepad: a move is a stick deflection held for a
    duration, and it answers one request at a time. So cmd_vel() is turned
    into chained 0.6 s moves (same timing as aegis-skill/panel.py) for as long
    as the speed is set, and every move ends by itself. If this process dies,
    the dog stops within ~0.6 s.

    Speeds map onto the stick: anything moving starts at the smallest stick
    that makes the dog walk (0.75; 0.8 sideways) and the workshop limit
    (0.3 m/s, 0.6 rad/s) is full stick. Real m/s per stick is not calibrated:
    time a turn and a 1 m walk on the floor before trusting the numbers.
    """

    STEP = 0.6
    TIMING = {"warmup": 0.0, "ramp": 0.15, "neutral_end": 0.1, "post_wait": 0.0}
    MIN_STICK = {"vx": 0.75, "yaw": 0.75, "vy": 0.8}
    PRESETS = {"stand": "stand", "stand_up": "stand", "recover": "recover",
               "lie_down": "lie", "shake_hand": "shakehand", "damping": "passive"}

    def __init__(self, url: str, world: "_World") -> None:
        self.url = url.rstrip("/")
        self.w = world
        self.cv = threading.Condition()
        self.queue: list[tuple[str, dict]] = []
        self.drive: dict | None = None
        self.busy = False
        self.alive = True
        self.dead_reason = ""
        self.yaw_sign = float(_env("FF_GW_YAW_SIGN", "1") or 1)
        self.vy_sign = float(_env("FF_GW_VY_SIGN", "1") or 1)
        self.thread = threading.Thread(target=self._loop, name="aegis-gateway", daemon=True)

    # ── HTTP ──
    def _request(self, path: str, body: dict | None = None, timeout: float = 45.0) -> dict:
        import urllib.error
        import urllib.request
        data = None if body is None else json.dumps(body).encode()
        req = urllib.request.Request(self.url + path, data=data,
                                     method="GET" if body is None else "POST",
                                     headers={"Content-Type": "application/json"})
        try:
            with urllib.request.urlopen(req, timeout=timeout) as r:
                return json.loads(r.read() or b"{}")
        except urllib.error.HTTPError as e:
            try:
                return json.loads(e.read() or b"{}")
            except ValueError:
                return {"ok": False, "error": f"HTTP {e.code}"}

    def status(self) -> dict:
        return self._request("/status", timeout=5.0)

    def start(self) -> None:
        self.thread.start()

    # ── commands from the skill (never block) ──
    def preset(self, name: str) -> None:
        cmd = self.PRESETS.get(name)
        if cmd is None:
            self.w.log("warn", f"robot: no gateway command for preset {name!r}; skipped")
            return
        with self.cv:
            self.drive = None
            self.queue.append((cmd, {}))
            self.cv.notify_all()

    def set_drive(self, linear: float, angular: float, lateral: float) -> None:
        def stick(v: float, limit: float, axis: str) -> float:
            if abs(v) < 1e-6:
                return 0.0
            lo = self.MIN_STICK[axis]
            return math.copysign(lo + (1 - lo) * min(1.0, abs(v) / limit), v)
        d = {"vx": stick(linear, MAX_LINEAR, "vx"),
             "vy": self.vy_sign * stick(lateral, MAX_LINEAR, "vy"),
             "yaw": self.yaw_sign * stick(angular, MAX_ANGULAR, "yaw")}
        with self.cv:
            self.drive = d if any(d.values()) else None
            self.cv.notify_all()

    def halt(self, reason: str) -> None:
        """E-stop: drop everything queued and go limp (passive)."""
        if not self.alive:
            return
        with self.cv:
            self.queue.clear()
            self.drive = None
            self.queue.append(("passive", {}))
            self.cv.notify_all()
        self.w.log("gw", "robot: e-stop → passive (motors off). The remote's e-stop is the real one.")

    def finish(self, timeout: float = 20.0) -> None:
        end = time.monotonic() + timeout
        with self.cv:
            self.drive = None
            while (self.queue or self.busy) and self.alive and time.monotonic() < end:
                self.cv.wait(0.1)
            self.alive = False
            self.cv.notify_all()

    # ── worker: the only client of the gateway ──
    def _loop(self) -> None:
        while True:
            with self.cv:
                while self.alive and not self.queue and not self.drive:
                    self.cv.wait(0.1)
                if not self.alive and not self.queue:
                    return
                if self.queue:
                    name, args = self.queue.pop(0)
                else:
                    name, args = "move", dict(self.drive or {}, duration=self.STEP, **self.TIMING)
                self.busy = True
            try:
                if name != "move":
                    self.w.log("gw", f"robot ← {name}")
                res = self._request("/cmd", {"cmd": name, "source": "aegis-sim", **args})
                if not res.get("ok"):
                    self.w.log("warn", f"robot refused {name}: {res.get('error') or res}")
                    if name == "move":
                        with self.cv:
                            self.drive = None
                elif name != "move":
                    self.w.log("gw", f"robot ✓ {name}")
            except Exception as e:  # tunnel dropped, gateway down
                self.dead_reason = str(e)
                with self.cv:
                    self.queue.clear()
                    self.drive = None
                    self.alive = False
                    self.busy = False
                    self.cv.notify_all()
                with self.w.lock:
                    self.w._violate(f"robot link lost: {e}. The dog stops by itself within "
                                    f"~0.6 s; use the remote's e-stop if it doesn't.")
                    if not self.w.estopped:
                        self.w._estop("robot link lost")
                return
            finally:
                with self.cv:
                    self.busy = False
                    self.cv.notify_all()


# ══════════════════════════════════════════════════════════════════════
#  SDK-shaped facade
# ══════════════════════════════════════════════════════════════════════

class _Motion:
    def __init__(self, w: _World) -> None:
        self._w = w

    def known_presets(self) -> set[str]:
        names = set(PRESET_TIMEOUTS)
        if self._w.variant == "wheeled":
            names.discard("shake_hand")
        return names

    def preset_timeout(self, name: str) -> float:
        return PRESET_TIMEOUTS.get(name, 2.0)

    async def stand(self) -> MotionResult:
        w = self._w
        with w.lock:
            w.check_operable()
            w.note_cmd()
            w.log("cmd", "stand()")
            w._cut_preset("stand()")
            if w.posture == "standing":
                return MotionResult(True, "already standing")
            w.start_preset("stand")
            if w.bridge:
                w.bridge.preset("stand")
        return MotionResult(True, "standing up")

    async def cmd_vel(self, linear: float = 0.0, angular: float = 0.0,
                      lateral: float = 0.0) -> MotionResult:
        w = self._w
        with w.lock:
            w.check_operable()
            w.note_cmd()
            w.log("cmd", f"cmd_vel(linear={linear:g}, angular={angular:g}, lateral={lateral:g})")
            w.max_lin = max(w.max_lin, abs(linear), abs(lateral))
            w.max_ang = max(w.max_ang, abs(angular))
            if abs(linear) > MAX_LINEAR + 1e-9 or abs(lateral) > MAX_LINEAR + 1e-9:
                w.mentor_estop(f"speed {max(abs(linear), abs(lateral)):.2f} m/s is over the "
                               f"{MAX_LINEAR} m/s limit")
                w.check_operable()
            if abs(angular) > MAX_ANGULAR + 1e-9:
                w.mentor_estop(f"speed {abs(angular):.2f} rad/s turning is over the "
                               f"{MAX_ANGULAR} rad/s limit")
                w.check_operable()
            if lateral and w.variant == "wheeled":
                if not w.warned_lateral:
                    w.log("warn", "wheeled variant can't move sideways; lateral is ignored")
                    w.warned_lateral = True
                lateral = 0.0
            moving = any(abs(c) > 1e-6 for c in (linear, angular, lateral))
            if moving:
                w._cut_preset("cmd_vel()")
                w.last_move_cmd_t = w.now()
                if w.posture != "standing" or w.preset:
                    if not w.warned_not_standing:
                        w.log("warn", "cmd_vel() while the dog isn't standing: it won't move. "
                                      "Call stand() and wait preset_timeout('stand') first.")
                        w.warned_not_standing = True
            if w.slip and (linear, angular, lateral) != w.cmd:
                g = w.rng.gauss
                w.k = (1 + g(0, w.slip), 1 + g(0, w.slip), g(0, w.slip * 1.5))
            w.cmd = (float(linear), float(angular), float(lateral))
            if w.bridge:
                w.bridge.set_drive(*w.cmd)
        return MotionResult(True)

    async def stop(self) -> MotionResult:
        w = self._w
        with w.lock:
            w.check_operable()
            w.note_cmd()
            w.log("cmd", "stop()")
            w.cmd = (0.0, 0.0, 0.0)
            if w.bridge:
                w.bridge.set_drive(0.0, 0.0, 0.0)
        return MotionResult(True)

    async def damping(self) -> MotionResult:
        w = self._w
        with w.lock:
            w.check_operable()
            w.note_cmd()
            w.log("cmd", "damping()")
            w._cut_preset("damping()")
            w.cmd = (0.0, 0.0, 0.0)
            w.posture = "damping"
            w.last_damping_t = w.now()
            if w.bridge:
                w.bridge.preset("damping")
        return MotionResult(True, "motors relaxed")

    async def do_preset(self, name: str) -> MotionResult:
        w = self._w
        with w.lock:
            w.check_operable()
            w.note_cmd()
            w.log("cmd", f"do_preset({name!r})")
            if name not in self.known_presets():
                raise CapabilityNotSupported(
                    f"preset {name!r} is not supported on the {w.variant} variant; "
                    f"check known_presets() first")
            if name in BANNED_TRICKS:
                w.mentor_estop(f"trick {name!r} is not allowed tonight "
                               f"(only {', '.join(sorted(ALLOWED_TRICKS))})")
                w.check_operable()
            w._cut_preset(f"do_preset({name!r})")
            if name == "damping":
                w.posture = "damping"
                w.last_damping_t = w.now()
            if name in ALLOWED_TRICKS and w.posture != "standing":
                w.log("warn", f"{name} needs the dog standing first")
                return MotionResult(False, "dog must be standing")
            w.start_preset(name)
            if w.bridge:
                w.bridge.preset(name)
        return MotionResult(True, "ok")


class _State:
    def __init__(self, w: _World) -> None:
        self._w = w

    async def pose(self) -> Pose:
        w = self._w
        with w.lock:
            if not w.pose_feedback:
                nan = float("nan")
                return Pose(x=nan, y=nan, z=nan, roll=nan, pitch=nan, yaw=nan)
            g = w.noise.gauss
            return Pose(x=w.x + g(0, 0.005), y=w.y + g(0, 0.005), z=0.3,
                        roll=0.0, pitch=0.0, yaw=_wrap(w.yaw + g(0, 0.004)))

    async def battery(self) -> BatteryState:
        return BatteryState(percent=self._w.battery, voltage=50.1, is_charging=False)

    async def status(self) -> RobotStatus:
        s = self._w.status_str()
        s = {"preset": "standing"}.get(s, s)
        try:
            return RobotStatus(s)
        except ValueError:
            return RobotStatus.UNKNOWN

    async def velocity(self, frame: str = "body"):
        raise CapabilityNotSupported(
            "Aegis velocity needs the native backend; the workshop robots report "
            "attitude only. Use pose() instead.")


class _EStop:
    def __init__(self, w: _World) -> None:
        self._w = w

    @property
    def is_active(self) -> bool:
        return self._w.estopped

    def reset(self) -> None:
        w = self._w
        with w.lock:
            if w.estopped:
                w.estopped = False
                w.estop_reset = True
                w.log("info", "estop.reset(): connect again before sending commands", echo=True)


class _Diag:
    def __init__(self, lines: list[str], ok: bool) -> None:
        self._lines, self.ok = lines, ok

    def summary(self) -> str:
        return "\n".join(self._lines)

    def __str__(self) -> str:
        return self.summary()


class AegisSim:
    """What `connect_dog()` returns in sim mode. Mirrors the ff_sdk session."""

    target = "sim://aegis"

    def __init__(self) -> None:
        self._w = _World()
        self.motion = _Motion(self._w)
        self.state = _State(self._w)
        self.estop = _EStop(self._w)
        self._viewer = _Viewer(self._w)
        self._closed = False
        atexit.register(self._atexit)

    @classmethod
    async def start(cls) -> "AegisSim":
        dog = cls()
        w = dog._w
        print(f"[sim] Aegis simulator · task {w.task} · {w.variant}"
              f"{f' · slip {w.slip:.0%}' if w.slip else ''}", file=sys.stderr)
        if dog._viewer.url:
            print(f"[sim] live view → {dog._viewer.url}", file=sys.stderr, flush=True)
        w.log("info", f"connected to sim://aegis ({w.variant})", echo=False)
        return dog

    @classmethod
    async def start_with_robot(cls, url: str) -> "AegisSim":
        """Sim + viewer + rule checks, with every command mirrored to the real
        dog through FF's gateway."""
        dog = cls.__new__(cls)
        dog._w = _World()
        bridge = _GatewayBridge(url, dog._w)
        try:
            st = await asyncio.to_thread(bridge.status)
        except Exception as e:
            dog._w._running = False
            raise ConnectionError(f"FF gateway not reachable at {url}: {e}. "
                                  f"Is the ssh tunnel up (run_real.sh) and are you on the "
                                  f"robot's hotspot?") from None
        ok = bool(st.get("connected", True) and st.get("connection_ok", True))
        print(f"[robot] gateway {url}: {'connected' if ok else 'NOT connected to the dog'} "
              f"(last_cmd={st.get('last_cmd')})", file=sys.stderr)
        if not ok:
            dog._w._running = False
            raise ConnectionError(f"gateway is up but not connected to the dog: {st}")
        if _env("FF_GW_YES", "") != "1":
            print("[robot] The dog WILL MOVE. 1 m of clear floor all round, someone on the "
                  "remote's e-stop.", file=sys.stderr)
            if input("[robot] Type yes to go: ").strip().lower() != "yes":
                dog._w._running = False
                raise SystemExit("aborted")
        dog._w.bridge = bridge
        bridge.start()
        dog.motion = _Motion(dog._w)
        dog.state = _State(dog._w)
        dog.estop = _EStop(dog._w)
        dog._viewer = _Viewer(dog._w)
        dog._closed = False
        atexit.register(dog._atexit)
        w = dog._w
        print(f"[sim] Aegis simulator + REAL ROBOT · task {w.task} · {w.variant}", file=sys.stderr)
        if dog._viewer.url:
            print(f"[sim] live view (planned path) → {dog._viewer.url}", file=sys.stderr, flush=True)
        w.log("info", f"connected to the robot through {url}", echo=False)
        return dog

    def capabilities(self) -> set[str]:
        return {"motion", "state"}

    def diagnose(self) -> _Diag:
        w = self._w
        lines = [f"[✓] Aegis · SIM ({w.variant})",
                 "  ✓ simulator — top-down, real-time",
                 f"  ✓ checks — task {w.task}",
                 f"  {'✓' if w.pose_feedback else '⚠'} pose feedback — "
                 f"{'on' if w.pose_feedback else 'off (pose() returns NaN)'}",
                 f"  ✓ viewer — {self._viewer.url or 'off'}"]
        return _Diag(lines, True)

    async def e_stop(self, reason: str = "manual") -> None:
        w = self._w
        with w.lock:
            if not w.estopped:
                w._estop(reason)

    async def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        w = self._w
        clean = (w.last_damping_t >= 0 and w.last_damping_t >= w.last_move_cmd_t
                 and not any(abs(c) > 1e-6 for c in w.cmd))
        if w.bridge:
            print("[sim] waiting for the robot to finish its last command…", file=sys.stderr)
            await asyncio.to_thread(w.bridge.finish)
        report = w.finish("clean" if clean else None)
        out = self._viewer.save_replay()
        _print_report(report, out)
        await asyncio.sleep(0)
        self._viewer.hold_and_stop()

    def _atexit(self) -> None:
        if not self._closed:
            self._closed = True
            if self._w.bridge:
                self._w.bridge.halt("script ended without close()")
                self._w.bridge.finish(5.0)
            report = self._w.finish(None)
            out = self._viewer.save_replay()
            _print_report(report, out)
            self._viewer.stop()

    async def __aenter__(self) -> "AegisSim":
        return self

    async def __aexit__(self, *exc) -> None:
        await self.close()


def _print_report(r: dict, out: Path | None) -> None:
    sym = {"pass": "✓", "fail": "✗", "warn": "!"}
    p = lambda s="": print(s, file=sys.stderr)  # noqa: E731
    p()
    p(f"── Sim report · {'Task ' + str(r['task']) if r['task'] else 'Free skill'} · {r['duration']:.1f} s "
      f"──────────────────────────────")
    for c in r["checks"]:
        detail = f"  ({c['detail']})" if c["detail"] else ""
        p(f"  {sym[c['state']]} {c['name']}{detail}")
    p(f"  end pose: x={r['final']['x']:.2f} m  y={r['final']['y']:.2f} m  "
      f"yaw={r['final']['yaw_deg']:.0f}°")
    p(f"  {r['verdict']}")
    if out:
        p(f"  replay → {out}")
    p()


# ══════════════════════════════════════════════════════════════════════
#  Live viewer (Server-Sent Events) + replay file
# ══════════════════════════════════════════════════════════════════════

class _Viewer:
    def __init__(self, w: _World) -> None:
        self.w = w
        self.url = ""
        self.httpd: ThreadingHTTPServer | None = None
        self._stop = threading.Event()
        port = int(_env("FF_SIM_PORT", "8765") or 8765)
        if port <= 0:
            return
        in_docker = Path("/.dockerenv").exists()
        host = _env("FF_SIM_HOST", "0.0.0.0" if in_docker else "127.0.0.1")
        handler = self._make_handler()
        for p in range(port, port + 10):
            try:
                ThreadingHTTPServer.allow_reuse_address = True
                self.httpd = ThreadingHTTPServer((host, p), handler)
                self.httpd.daemon_threads = True
                self.url = f"http://localhost:{p}"
                break
            except OSError:
                continue
        if self.httpd:
            threading.Thread(target=self.httpd.serve_forever, daemon=True,
                             name="aegis-sim-viewer").start()

    def _make_handler(self):
        viewer = self

        class H(BaseHTTPRequestHandler):
            def log_message(self, *a):  # silence
                pass

            def do_GET(self):  # noqa: N802
                if self.path.startswith("/events"):
                    return viewer._stream(self)
                body = _page(None).encode("utf-8")
                self.send_response(200)
                self.send_header("Content-Type", "text/html; charset=utf-8")
                self.send_header("Cache-Control", "no-store")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

        return H

    def _stream(self, h: BaseHTTPRequestHandler) -> None:
        w = self.w
        h.send_response(200)
        h.send_header("Content-Type", "text/event-stream")
        h.send_header("Cache-Control", "no-store")
        h.send_header("Connection", "keep-alive")
        h.end_headers()
        wake = threading.Event()
        w._listeners.append(wake)
        fi = ei = 0
        sent_end = False

        def send(obj: dict) -> None:
            h.wfile.write(b"retry: 1000\ndata: " + json.dumps(obj).encode() + b"\n\n")
            h.wfile.flush()

        try:
            send({"type": "hello", "meta": w.meta(), "run": w.run_id})
            while not self._stop.is_set():
                wake.wait(0.25)
                wake.clear()
                with w.lock:
                    frames = w.frames[fi:]
                    events = w.events[ei:]
                    fi += len(frames)
                    ei += len(events)
                    report = w.report
                if frames or events:
                    send({"type": "data", "frames": frames, "events": events})
                if report is not None and not sent_end:
                    send({"type": "end", "report": report})
                    sent_end = True
                elif not frames and not events:
                    h.wfile.write(b": ping\n\n")
                    h.wfile.flush()
        except (BrokenPipeError, ConnectionResetError, OSError):
            pass
        finally:
            if wake in w._listeners:
                w._listeners.remove(wake)

    def trace(self) -> dict:
        w = self.w
        with w.lock:
            return {"meta": w.meta(), "frames": list(w.frames), "events": list(w.events),
                    "report": w.report}

    def save_replay(self) -> Path | None:
        out = _env("FF_SIM_OUT", "run.html")
        if not out or out.lower() == "off":
            return None
        path = Path(out).resolve()
        try:
            path.write_text(_page(self.trace()), encoding="utf-8")
        except OSError as e:
            print(f"[sim] couldn't write {path}: {e}", file=sys.stderr)
            return None
        return path

    def hold_and_stop(self) -> None:
        if not self.httpd:
            return
        hold = float(_env("FF_SIM_HOLD", "3") or 0)
        if hold > 0 and self.w._listeners:
            time.sleep(hold)   # let an open tab receive the final report
        self.stop()

    def stop(self) -> None:
        self._stop.set()
        self.w._notify()
        if self.httpd:
            threading.Thread(target=self.httpd.shutdown, daemon=True).start()


def _page(trace: dict | None) -> str:
    data = "null" if trace is None else json.dumps(trace, separators=(",", ":"))
    data = data.replace("</", "<\\/")
    return _VIEWER_HTML.replace("/*__TRACE__*/null", data)


# ══════════════════════════════════════════════════════════════════════
#  Entry points
# ══════════════════════════════════════════════════════════════════════

async def connect_dog(target: str | None = None, config=None):
    """Connect to the simulator, ff_sdk dry-run, or a real robot.

    target (or FF_TARGET): "sim" (default) | "gateway" (real dog via FF's gateway,
    URL from AEGIS_GATEWAY) | "dry-run" | "aegis://<serial>"
    """
    target = (target or _env("FF_TARGET", "sim")).strip()
    if target.lower() in ("sim", "sim://aegis", "simulator"):
        return await AegisSim.start()
    if target.lower().startswith("gateway"):
        url = target.split("=", 1)[1] if "=" in target else _env("AEGIS_GATEWAY", "http://127.0.0.1:8768")
        return await AegisSim.start_with_robot(url)
    import ff_sdk  # type: ignore
    from ff_sdk import Config  # type: ignore
    cfg = config or Config.from_env()
    if target.lower() in ("dry-run", "dryrun", "dry_run"):
        cfg.dry_run = True
        target = "aegis://DEMO"
    return await ff_sdk.connect(target, config=cfg)


def run_skill(routine, target: str | None = None) -> None:
    """Run `async def routine(dog)` with workshop-safe exits.

    Ctrl+C or an exception → dog.e_stop(); normal finish → stop → damping → close.
    The same wrapper works for the sim, dry-run and the real robot.
    """

    async def _main() -> None:
        try:
            dog = await connect_dog(target)
        except (ConnectionError, OSError) as e:
            print(f"[skill] couldn't connect: {e}", file=sys.stderr)
            raise SystemExit(1) from None
        task = asyncio.current_task()
        loop = asyncio.get_running_loop()
        try:
            loop.add_signal_handler(signal.SIGINT, task.cancel)  # type: ignore[union-attr]
        except (NotImplementedError, RuntimeError, AttributeError):
            pass  # Windows: KeyboardInterrupt path below
        try:
            await routine(dog)
        except (asyncio.CancelledError, KeyboardInterrupt):
            print("\n[skill] Ctrl+C → e_stop", file=sys.stderr)
            await dog.e_stop(reason="Ctrl+C")
        except Exception as e:
            print(f"\n[skill] {type(e).__name__}: {e} → e_stop", file=sys.stderr)
            if not dog.estop.is_active:
                await dog.e_stop(reason=f"{type(e).__name__}: {e}")
        else:
            await dog.motion.stop()
            await dog.motion.damping()
            await asyncio.sleep(dog.motion.preset_timeout("damping"))
        finally:
            await dog.close()

    try:
        asyncio.run(_main())
    except KeyboardInterrupt:
        pass


# ══════════════════════════════════════════════════════════════════════
#  Viewer page (live when served, replay when saved as run.html)
# ══════════════════════════════════════════════════════════════════════

_VIEWER_HTML = r"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Aegis Sim</title>
<style>
:root{--bg:#0C0C0C;--panel:#141414;--panel-2:#1B1B1B;--line:rgba(255,255,255,.09);--line-2:rgba(255,255,255,.16);
--text:#F4F4F3;--muted:#A3A3A0;--dim:#77736E;--accent:#FF6B2C;--accent-soft:rgba(255,107,44,.13);
--good:#6FD39B;--bad:#FF7A6B;--warn:#F3C451;--info:#7FB2FF;
--sans:Inter,system-ui,-apple-system,"Segoe UI",sans-serif;--mono:"JetBrains Mono",ui-monospace,SFMono-Regular,Menlo,Consolas,monospace}
*{box-sizing:border-box;margin:0;padding:0}
html,body{background:var(--bg);color:var(--text);font-family:var(--sans);font-size:14px;line-height:1.45;-webkit-font-smoothing:antialiased}
header{display:flex;align-items:center;gap:12px;padding:12px 16px;border-bottom:1px solid var(--line);flex-wrap:wrap}
.brand{font-weight:800;letter-spacing:-.01em;font-size:16px}
.brand b{color:var(--accent)}
.pill{font-family:var(--mono);font-size:12px;padding:2px 8px;border-radius:999px;border:1px solid var(--line-2);color:var(--muted)}
.pill.live{color:var(--bad);border-color:rgba(255,122,107,.5)}
.pill.live::before{content:"";display:inline-block;width:7px;height:7px;border-radius:50%;background:var(--bad);margin-right:6px;animation:blink 1s infinite}
.pill.wait{color:var(--dim)}
.pill.done{color:var(--good);border-color:rgba(111,211,155,.45)}
@keyframes blink{50%{opacity:.25}}
.spacer{flex:1}
.clock{font-family:var(--mono);font-size:20px;font-weight:700;font-variant-numeric:tabular-nums}
main{display:grid;grid-template-columns:minmax(0,1fr) 340px;gap:12px;padding:12px 16px;height:calc(100vh - 57px - 64px);min-height:460px}
.stage{position:relative;background:var(--panel);border:1px solid var(--line);border-radius:12px;overflow:hidden}
canvas{display:block;width:100%;height:100%}
.verdict{position:absolute;left:12px;right:12px;bottom:12px;padding:10px 14px;border-radius:10px;font-weight:700;display:none;backdrop-filter:blur(6px)}
.verdict.pass{display:block;background:rgba(111,211,155,.14);border:1px solid rgba(111,211,155,.5);color:var(--good)}
.verdict.fail{display:block;background:rgba(255,122,107,.14);border:1px solid rgba(255,122,107,.5);color:var(--bad)}
.verdict.warn{display:block;background:rgba(243,196,81,.12);border:1px solid rgba(243,196,81,.45);color:var(--warn)}
.legend{position:absolute;left:12px;top:10px;font-size:12px;color:var(--dim);display:flex;gap:12px;flex-wrap:wrap}
.legend i{display:inline-block;width:14px;height:3px;border-radius:2px;margin-right:5px;vertical-align:middle}
aside{display:flex;flex-direction:column;gap:12px;min-height:0;overflow:auto}
.card{background:var(--panel);border:1px solid var(--line);border-radius:12px;padding:12px}
.card h3{font-size:11px;text-transform:uppercase;letter-spacing:.08em;color:var(--dim);margin-bottom:8px;font-weight:700}
.status{font-size:20px;font-weight:800;text-transform:capitalize}
.status small{font-size:12px;color:var(--muted);font-weight:500;margin-left:6px;text-transform:none}
.meter{margin-top:8px}
.meter .row{display:flex;justify-content:space-between;font-size:12px;color:var(--muted);font-family:var(--mono)}
.bar{height:6px;background:var(--panel-2);border-radius:4px;overflow:hidden;margin-top:3px;position:relative}
.bar span{display:block;height:100%;background:var(--accent);border-radius:4px;transition:width .1s}
.bar span.over{background:var(--bad)}
.pose{display:grid;grid-template-columns:repeat(3,1fr);gap:6px;margin-top:10px}
.pose div{background:var(--panel-2);border-radius:8px;padding:6px 8px}
.pose label{display:block;font-size:11px;color:var(--dim)}
.pose b{font-family:var(--mono);font-size:14px;font-variant-numeric:tabular-nums}
.checks li{list-style:none;display:flex;gap:8px;padding:4px 0;border-top:1px solid var(--line);font-size:13px}
.checks li:first-child{border-top:0}
.checks .ic{width:16px;flex:none;text-align:center;font-weight:800}
.checks .pass .ic{color:var(--good)} .checks .fail .ic{color:var(--bad)} .checks .warn .ic{color:var(--warn)} .checks .pending .ic{color:var(--dim)}
.checks .d{color:var(--dim);font-size:12px;display:block}
.log{flex:1;min-height:160px;display:flex;flex-direction:column}
.log ol{list-style:none;overflow:auto;flex:1;font-family:var(--mono);font-size:12px}
.log li{display:flex;gap:8px;padding:2px 0;color:var(--muted)}
.log li.future{opacity:.25}
.log .t{color:var(--dim);flex:none;width:48px;text-align:right;font-variant-numeric:tabular-nums}
.log .gw{color:#B9A6FF} .log .cam{color:var(--good)} .log .cmd{color:var(--text)} .log .preset{color:var(--info)} .log .warn{color:var(--warn)} .log .bad,.log .estop{color:var(--bad);font-weight:700} .log .end{color:var(--good)}
footer{height:64px;display:flex;align-items:center;gap:12px;padding:0 16px;border-top:1px solid var(--line)}
button{font:inherit;color:var(--text);background:var(--panel-2);border:1px solid var(--line-2);border-radius:8px;padding:6px 12px;cursor:pointer}
button:disabled{opacity:.4;cursor:default}
button.on{border-color:var(--accent);color:var(--accent)}
.scrub{flex:1;position:relative;height:28px}
.scrub input{position:absolute;inset:0;width:100%;accent-color:var(--accent)}
.ticks{position:absolute;left:0;right:0;top:0;height:6px;pointer-events:none}
.ticks span{position:absolute;width:2px;height:6px;border-radius:1px}
.empty{position:absolute;inset:0;display:flex;align-items:center;justify-content:center;flex-direction:column;gap:6px;color:var(--muted);text-align:center;padding:24px}
.empty code{font-family:var(--mono);color:var(--text);background:var(--panel-2);padding:2px 6px;border-radius:6px}
@media (max-width:900px){main{grid-template-columns:1fr;height:auto}.stage{height:60vh}}
</style>
</head>
<body>
<header>
  <div class="brand"><b>Aegis</b> Sim</div>
  <span class="pill" id="mode">connecting…</span>
  <span class="pill" id="task">task –</span>
  <span class="pill" id="variant">–</span>
  <span class="pill" id="slip" hidden>slip</span>
  <div class="spacer"></div>
  <div class="clock" id="clock">0.0 s</div>
</header>
<main>
  <section class="stage">
    <canvas id="cv"></canvas>
    <div class="legend"><span><i style="background:var(--accent)"></i>path</span><span id="lg-people" hidden><i style="background:var(--good)"></i>motion seen</span><span id="lg-target" hidden><i style="background:var(--info)"></i>target</span><span><i style="background:var(--line-2)"></i>1 m grid</span></div>
    <div class="empty" id="empty"><div style="font-size:28px">🐕</div><div>Waiting for a run…</div><div>Start your skill with <code>FF_TARGET=sim</code>. This tab updates by itself.</div></div>
    <div class="verdict" id="verdict"></div>
  </section>
  <aside>
    <div class="card">
      <h3>Dog</h3>
      <div class="status" id="status">–<small id="preset"></small></div>
      <div class="meter"><div class="row"><span>linear</span><span id="vtxt">0.00 / 0.30 m/s</span></div><div class="bar"><span id="vbar" style="width:0"></span></div></div>
      <div class="meter"><div class="row"><span>turn</span><span id="wtxt">0.00 / 0.60 rad/s</span></div><div class="bar"><span id="wbar" style="width:0"></span></div></div>
      <div class="pose"><div><label>x</label><b id="px">0.00</b></div><div><label>y</label><b id="py">0.00</b></div><div><label>yaw</label><b id="pyaw">0°</b></div></div>
    </div>
    <div class="card"><h3>Checks</h3><ul class="checks" id="checks"></ul></div>
    <div class="card log"><h3>Event log</h3><ol id="log"></ol></div>
  </aside>
</main>
<footer>
  <button id="play" disabled>▶</button>
  <div class="scrub"><div class="ticks" id="ticks"></div><input type="range" id="scrub" min="0" max="1000" value="1000" disabled></div>
  <button id="sp1" class="on" disabled>1×</button><button id="sp4" disabled>4×</button>
</footer>
<script>
const TRACE = /*__TRACE__*/null;
const $ = id => document.getElementById(id);
const cv = $("cv"), ctx = cv.getContext("2d");
const css = n => getComputedStyle(document.documentElement).getPropertyValue(n).trim();
let meta = null, frames = [], events = [], report = null, runId = null;
let live = false, ended = false, viewT = null, playing = false, speed = 1, lastTick = 0;

const TASK_CHECKS = {
  0: ["Speed ≤ 0.3 m/s, turning ≤ 0.6 rad/s","Only allowed tricks","Clean exit (stop → damping → close)"],
  1: ["Speed ≤ 0.3 m/s, turning ≤ 0.6 rad/s","Only allowed tricks","Stand","Move (forward, back or sideways)","Turn in place","A preset","Show length 20–30 s","Clean exit (stop → damping → close)"],
  2: ["Speed ≤ 0.3 m/s, turning ≤ 0.6 rad/s","Only allowed tricks","Watchdog stops the dog within 0.5 s","Clean exit (stop → damping → close)"],
  3: ["Speed ≤ 0.3 m/s, turning ≤ 0.6 rad/s","Only allowed tricks","Stop within 50 cm of the cross","Walked the triangle","Clean exit (stop → damping → close)"]
};

function reset(m){
  meta = m; frames = []; events = []; report = null; ended = false; viewT = null; playing = false;
  $("empty").style.display = "none";
  $("task").textContent = m.task ? "task " + m.task : "free skill";
  $("variant").textContent = m.variant;
  $("slip").hidden = !m.slip; $("slip").textContent = "slip " + Math.round(m.slip*100) + "%";
  $("lg-target").hidden = m.task !== 3;
  $("lg-people").hidden = !(m.movers && m.movers.length);
  $("verdict").className = "verdict"; $("log").innerHTML = ""; $("ticks").innerHTML = "";
  setControls(false); renderChecks(); draw();
}
function setMode(text, cls){ const el = $("mode"); el.textContent = text; el.className = "pill " + (cls||""); }
function setControls(on){ ["play","scrub","sp1","sp4"].forEach(id => $(id).disabled = !on); }
const tEnd = () => frames.length ? frames[frames.length-1][0] : 0;
const curT = () => viewT == null ? tEnd() : viewT;

function frameAt(t){
  if (!frames.length) return null;
  let lo = 0, hi = frames.length - 1;
  while (lo < hi){ const mid = (lo + hi + 1) >> 1; if (frames[mid][0] <= t) lo = mid; else hi = mid - 1; }
  return lo;
}

function renderChecks(){
  if (!meta) return;
  const ul = $("checks"); ul.innerHTML = "";
  const atEnd = report && curT() >= tEnd() - 0.01;
  const list = atEnd ? report.checks : TASK_CHECKS[meta.task].map(n => ({name:n, state:"pending", detail:""}));
  if (!atEnd){
    const t = curT();
    const done = n => events.some(e => e.t <= t && e.kind === "preset" && e.msg === n + " done");
    const mark = (name, ok, detail) => { const c = list.find(c => c.name === name); if (c && ok) { c.state = "pass"; c.detail = detail || ""; } };
    if (meta.task === 1){
      const n = frameAt(t); let dist = 0, run = 0, best = 0;
      for (let i = 1; n != null && i <= n; i++){
        const f = frames[i], dt = f[0] - frames[i-1][0], tr = Math.hypot(f[4], f[6]);
        dist += tr * dt;
        if (tr < .05 && Math.abs(f[5]) > .05){ run += f[5] * dt; best = Math.max(best, Math.abs(run)); } else if (Math.abs(f[5]) <= .05) run = 0;
      }
      mark("Stand", done("stand") || done("stand_up") || done("recover"));
      mark("Move (forward, back or sideways)", dist >= .2, dist.toFixed(2) + " m travelled");
      mark("Turn in place", best >= Math.PI/2, Math.round(best*180/Math.PI) + "° in one turn");
      const tricks = ["shake_hand","lie_down"].filter(done);
      mark("A preset", tricks.length > 0, tricks.join(", "));
    }
    const bad = events.filter(e => e.t <= t && (e.kind === "bad"));
    bad.forEach(e => {
      const target = e.msg.startsWith("speed") ? 0 : e.msg.startsWith("trick") ? 1 : e.msg.startsWith("watchdog") ? 2 : -1;
      if (target >= 0 && list[target]) { list[target].state = "fail"; list[target].detail = e.msg; }
    });
  }
  for (const c of list){
    const li = document.createElement("li"); li.className = c.state;
    li.innerHTML = `<span class="ic">${{pass:"✓",fail:"✗",warn:"!",pending:"·"}[c.state]}</span><span>${esc(c.name)}${c.detail?`<span class="d">${esc(c.detail)}</span>`:""}</span>`;
    ul.appendChild(li);
  }
  const v = $("verdict");
  if (atEnd){ v.textContent = report.verdict; v.className = "verdict " + (report.verdict.startsWith("PASS") ? "pass" : report.violations.length ? "fail" : "warn"); }
  else v.className = "verdict";
}
const esc = s => String(s).replace(/[&<>"]/g, c => ({"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;"}[c]));

function appendEvents(list){
  const ol = $("log"), ticks = $("ticks");
  for (const e of list){
    if (e.kind === "info") continue;
    const li = document.createElement("li"); li.dataset.t = e.t;
    li.innerHTML = `<span class="t">${e.t.toFixed(1)}s</span><span class="${e.kind}">${esc(e.msg)}</span>`;
    ol.appendChild(li);
    if (["bad","estop","warn","preset"].includes(e.kind)){
      const s = document.createElement("span"); s.dataset.t = e.t;
      s.style.background = e.kind === "preset" ? css("--info") : e.kind === "warn" ? css("--warn") : css("--bad");
      ticks.appendChild(s);
    }
  }
  if (viewT == null) ol.scrollTop = ol.scrollHeight;
}
function layoutTicks(){
  const T = tEnd() || 1;
  for (const s of $("ticks").children) s.style.left = (100 * s.dataset.t / T) + "%";
}
function syncLog(){
  const t = curT();
  let lastVisible = null;
  for (const li of $("log").children){ const f = +li.dataset.t > t + 1e-6; li.classList.toggle("future", f); if (!f) lastVisible = li; }
  if (viewT != null && lastVisible) lastVisible.scrollIntoView({block:"nearest"});
}

function updatePanel(){
  const i = frameAt(curT()); const f = i == null ? null : frames[i];
  $("clock").textContent = curT().toFixed(1) + " s";
  if (!f) return;
  const [t,x,y,yaw,v,w,l,st,pr] = f;
  const lin = Math.hypot(v, l);
  $("status").firstChild.textContent = st === "preset" ? "performing" : st;
  $("preset").textContent = pr ? pr.replace(/_/g," ") : "";
  const L = meta.limits;
  $("vtxt").textContent = `${lin.toFixed(2)} / ${L.linear.toFixed(2)} m/s`;
  $("wtxt").textContent = `${Math.abs(w).toFixed(2)} / ${L.angular.toFixed(2)} rad/s`;
  const vb = $("vbar"), wb = $("wbar");
  vb.style.width = Math.min(100, 100*lin/L.linear) + "%"; vb.classList.toggle("over", lin > L.linear + 1e-3);
  wb.style.width = Math.min(100, 100*Math.abs(w)/L.angular) + "%"; wb.classList.toggle("over", Math.abs(w) > L.angular + 1e-3);
  $("px").textContent = x.toFixed(2); $("py").textContent = y.toFixed(2);
  $("pyaw").textContent = Math.round(yaw*180/Math.PI) + "°";
}

// ── drawing ──
function draw(){
  const dpr = window.devicePixelRatio || 1, W = cv.clientWidth, H = cv.clientHeight;
  if (cv.width !== W*dpr || cv.height !== H*dpr){ cv.width = W*dpr; cv.height = H*dpr; }
  ctx.setTransform(dpr,0,0,dpr,0,0); ctx.clearRect(0,0,W,H);
  if (!meta) return;
  const A = meta.arena;
  let minX=-1.2, maxX=1.6, minY=-1.2, maxY=1.6;
  for (const m of (meta.movers||[])) for (const [px,py] of [m.a, m.b]){ minX=Math.min(minX,px); maxX=Math.max(maxX,px); minY=Math.min(minY,py); maxY=Math.max(maxY,py); }
  for (const f of frames){ if (f[1]<minX) minX=f[1]; if (f[1]>maxX) maxX=f[1]; if (f[2]<minY) minY=f[2]; if (f[2]>maxY) maxY=f[2]; }
  minX=Math.max(minX-.6,-A-.3); maxX=Math.min(maxX+.6,A+.3); minY=Math.max(minY-.6,-A-.3); maxY=Math.min(maxY+.6,A+.3);
  const s = Math.min(W/(maxX-minX), (H-40)/(maxY-minY));
  const ox = W/2 - s*(minX+maxX)/2, oy = (H+10)/2 + s*(minY+maxY)/2;
  const P = (x,y) => [ox + s*x, oy - s*y];

  // grid
  ctx.lineWidth = 1;
  for (let g = Math.floor(minX*2)/2; g <= maxX; g += .5){ const [a] = P(g,0); ctx.strokeStyle = Math.abs(g%1)<1e-6 ? css("--line-2") : css("--line"); ctx.beginPath(); ctx.moveTo(a,0); ctx.lineTo(a,H); ctx.stroke(); }
  for (let g = Math.floor(minY*2)/2; g <= maxY; g += .5){ const [,b] = P(0,g); ctx.strokeStyle = Math.abs(g%1)<1e-6 ? css("--line-2") : css("--line"); ctx.beginPath(); ctx.moveTo(0,b); ctx.lineTo(W,b); ctx.stroke(); }
  // arena
  ctx.setLineDash([6,6]); ctx.strokeStyle = "rgba(255,122,107,.35)";
  const [ax,ay] = P(-A,A); ctx.strokeRect(ax, ay, 2*A*s, 2*A*s); ctx.setLineDash([]);
  // scale bar
  ctx.fillStyle = css("--dim"); ctx.font = "11px " + css("--mono");
  const [sx,sy] = [W-20-s, H-16]; ctx.fillRect(sx, sy, s, 2); ctx.fillText("1 m", sx + s/2 - 10, sy - 6);

  // task 3 target
  if (meta.task === 3){
    const tri = [[0,0],[1,0],[0.5,Math.sqrt(3)/2],[0,0]];
    ctx.strokeStyle = css("--info"); ctx.globalAlpha = .7; ctx.setLineDash([8,6]); ctx.lineWidth = 2; ctx.beginPath();
    tri.forEach(([x,y],i) => { const [a,b] = P(x,y); i ? ctx.lineTo(a,b) : ctx.moveTo(a,b); }); ctx.stroke(); ctx.setLineDash([]);
    ctx.globalAlpha = .12; ctx.fillStyle = css("--good"); const [cx,cy] = P(0,0); ctx.beginPath(); ctx.arc(cx,cy,.5*s,0,7); ctx.fill();
    ctx.globalAlpha = .5; ctx.strokeStyle = css("--good"); ctx.lineWidth = 1; ctx.beginPath(); ctx.arc(cx,cy,.5*s,0,7); ctx.stroke(); ctx.globalAlpha = 1;
    ctx.fillStyle = css("--good"); ctx.fillText("50 cm", cx + .5*s*.72 + 4, cy + .5*s*.72 + 12);
  }
  // tape cross
  { const [cx,cy] = P(0,0), r = .15*s; ctx.strokeStyle = "#E8E2C8"; ctx.lineWidth = Math.max(3,.04*s); ctx.lineCap = "round";
    ctx.beginPath(); ctx.moveTo(cx-r,cy-r); ctx.lineTo(cx+r,cy+r); ctx.moveTo(cx+r,cy-r); ctx.lineTo(cx-r,cy+r); ctx.stroke(); ctx.lineCap = "butt"; }

  const t = curT(), n = frameAt(t);
  if (n == null) return;
  // path
  ctx.strokeStyle = css("--accent"); ctx.lineWidth = 2.5; ctx.lineJoin = "round"; ctx.beginPath();
  for (let i = 0; i <= n; i++){ const [a,b] = P(frames[i][1], frames[i][2]); i ? ctx.lineTo(a,b) : ctx.moveTo(a,b); }
  ctx.stroke();
  // markers for presets / violations on the path
  for (const e of events){
    if (e.t > t || !["preset","bad","estop"].includes(e.kind) || (e.kind==="preset" && !e.msg.includes("started"))) continue;
    const f = frames[frameAt(e.t) ?? 0]; if (!f) continue; const [a,b] = P(f[1],f[2]);
    ctx.fillStyle = e.kind === "preset" ? css("--info") : css("--bad");
    ctx.beginPath(); ctx.arc(a,b,4,0,7); ctx.fill();
  }
  drawCamera(frames[n], P, s);
  drawDog(frames[n], P, s, t);
}

function moverAt(m, t){
  const p = m.period || 4, u = (t % (2*p)) / p, k = u <= 1 ? u : 2 - u;
  return [m.a[0] + (m.b[0]-m.a[0])*k, m.a[1] + (m.b[1]-m.a[1])*k];
}
function drawCamera(f, P, s){
  const movers = meta.movers || [];
  if (!movers.length) return;
  const [ft,x,y,yaw,v,w,l] = f; const [cx,cy] = P(x,y);
  const half = (meta.cam_hfov || 1.2) / 2, R = (meta.cam_range || 4) * s;
  const still = Math.abs(w) < .05 && Math.hypot(v,l) < .02;
  // camera view wedge: solid while the dog is still (it can see motion), faint while turning
  ctx.fillStyle = still ? "rgba(127,178,255,.10)" : "rgba(127,178,255,.03)"; ctx.strokeStyle = "rgba(127,178,255,.25)"; ctx.lineWidth = 1;
  ctx.beginPath(); ctx.moveTo(cx,cy); ctx.arc(cx, cy, R, -yaw - half, -yaw + half); ctx.closePath(); ctx.fill(); ctx.stroke();
  for (const m of movers){
    const [ax,ay] = P(m.a[0], m.a[1]), [bx,by] = P(m.b[0], m.b[1]);
    ctx.setLineDash([3,5]); ctx.strokeStyle = "rgba(255,255,255,.18)"; ctx.beginPath(); ctx.moveTo(ax,ay); ctx.lineTo(bx,by); ctx.stroke(); ctx.setLineDash([]);
    const [px,py] = moverAt(m, ft), dx = px - x, dy = py - y, d = Math.hypot(dx,dy);
    let b = Math.atan2(dy,dx) - yaw; b = Math.atan2(Math.sin(b), Math.cos(b));
    const inView = d <= (meta.cam_range||4) && Math.abs(b) <= half, seen = inView && still;
    const [a,c] = P(px,py), r = Math.max(6, .13*s);
    ctx.fillStyle = seen ? css("--good") : inView ? css("--info") : "#8a8780";
    ctx.beginPath(); ctx.arc(a, c, r, 0, 7); ctx.fill();
    ctx.fillStyle = css("--muted"); ctx.font = "11px " + css("--mono"); ctx.fillText(seen ? "moving · seen" : "moving", a + r + 4, c + 4);
    if (seen){ ctx.strokeStyle = css("--good"); ctx.lineWidth = 1; ctx.beginPath(); ctx.moveTo(cx,cy); ctx.lineTo(a,c); ctx.stroke(); }
  }
}

function drawDog(f, P, s, t){
  const [ft,x,y,yaw,v,w,l,st,pr] = f; const [cx,cy] = P(x,y);
  ctx.save(); ctx.translate(cx,cy); ctx.rotate(-yaw);
  const L = .70*s, Wd = .32*s;
  const lying = st === "lying" || st === "damping" || st === "idle" || st === "estopped";
  // legs
  if (!lying){
    ctx.fillStyle = "#3a3a38";
    const gait = (st === "moving") ? Math.sin(t*12)*.05*s : 0;
    const leg = (lx,ly,ph) => { ctx.beginPath(); ctx.ellipse(lx + ph, ly, .07*s, .045*s, 0, 0, 7); ctx.fill(); };
    leg(L*.32,  Wd*.62,  gait); leg(L*.32, -Wd*.62, -gait); leg(-L*.32, Wd*.62, -gait); leg(-L*.32, -Wd*.62, gait);
  }
  // shake-hand paw
  if (pr === "shake_hand"){ const wave = Math.sin(t*6)*.06*s; ctx.fillStyle = css("--info"); ctx.beginPath(); ctx.ellipse(L*.55 + Math.abs(wave), Wd*.5 + wave, .06*s, .04*s, 0, 0, 7); ctx.fill(); }
  // body
  const body = st === "estopped" ? "#5a2a26" : lying ? "#4a4a47" : "#E9E6DF";
  ctx.fillStyle = body; ctx.strokeStyle = st === "estopped" ? css("--bad") : "rgba(0,0,0,.35)"; ctx.lineWidth = st === "estopped" ? 3 : 1;
  roundRect(-L/2, -Wd/2, L, Wd, Wd*.35); ctx.fill(); ctx.stroke();
  // head
  ctx.fillStyle = lying ? "#5a5a56" : css("--accent");
  roundRect(L*.36, -Wd*.32, L*.2, Wd*.64, Wd*.2); ctx.fill();
  ctx.restore();
  // heading arrow (velocity)
  const sp = Math.hypot(v,l);
  if (sp > .02){ const ang = yaw + Math.atan2(l, v); const k = .25*s + sp*2*s;
    ctx.strokeStyle = css("--accent"); ctx.lineWidth = 2; ctx.beginPath(); ctx.moveTo(cx,cy);
    const ex = cx + Math.cos(ang)*k, ey = cy - Math.sin(ang)*k; ctx.lineTo(ex,ey); ctx.stroke(); }
  // preset badge with progress
  const label = st === "estopped" ? "E-STOP" : pr ? pr.replace(/_/g," ") : "";
  if (label){
    ctx.font = "600 12px " + css("--sans"); const tw = ctx.measureText(label).width + 18;
    const bx = cx - tw/2, by = cy - .55*s - 26;
    ctx.fillStyle = st === "estopped" ? css("--bad") : "rgba(20,20,20,.9)"; roundRect(bx, by, tw, 22, 11); ctx.fill();
    ctx.strokeStyle = st === "estopped" ? css("--bad") : css("--info"); ctx.lineWidth = 1; ctx.stroke();
    if (pr && st !== "estopped"){
      const start = [...events].reverse().find(e => e.t <= t && e.kind === "preset" && e.msg.startsWith(pr + " started"));
      const dur = meta.presets[pr] || 1; const p = start ? Math.min(1, (t - start.t)/dur) : 0;
      ctx.save(); roundRect(bx, by, tw, 22, 11); ctx.clip(); ctx.fillStyle = "rgba(127,178,255,.22)"; ctx.fillRect(bx, by, tw*p, 22); ctx.restore();
    }
    ctx.fillStyle = st === "estopped" ? "#140605" : css("--text"); ctx.fillText(label, bx + 9, by + 15);
  }
}
function roundRect(x,y,w,h,r){ ctx.beginPath(); ctx.moveTo(x+r,y); ctx.arcTo(x+w,y,x+w,y+h,r); ctx.arcTo(x+w,y+h,x,y+h,r); ctx.arcTo(x,y+h,x,y,r); ctx.arcTo(x,y,x+w,y,r); ctx.closePath(); }

function render(){ updatePanel(); draw(); renderChecks(); syncLog(); }

// ── replay controls ──
function enterReplay(){
  ended = true; setControls(true); layoutTicks();
  $("scrub").value = 1000; viewT = null; render();
}
$("scrub").addEventListener("input", e => { playing = false; $("play").textContent = "▶"; viewT = tEnd() * e.target.value / 1000; render(); });
$("play").addEventListener("click", () => {
  if (!ended) return;
  if (!playing && curT() >= tEnd() - 0.01) viewT = 0;
  playing = !playing; $("play").textContent = playing ? "❚❚" : "▶"; lastTick = performance.now();
  if (viewT == null) viewT = 0;
});
[["sp1",1],["sp4",4]].forEach(([id,k]) => $(id).addEventListener("click", () => { speed = k; $("sp1").classList.toggle("on", k===1); $("sp4").classList.toggle("on", k===4); }));
function tick(now){
  if (playing){
    viewT = Math.min(tEnd(), curT() + (now - lastTick)/1000*speed);
    $("scrub").value = 1000 * viewT / (tEnd() || 1);
    if (viewT >= tEnd()){ playing = false; $("play").textContent = "▶"; viewT = null; }
    render();
  }
  lastTick = now; requestAnimationFrame(tick);
}
requestAnimationFrame(tick);
window.addEventListener("resize", draw);

// ── data sources ──
if (TRACE){
  reset(TRACE.meta); frames = TRACE.frames; events = TRACE.events; report = TRACE.report;
  appendEvents(events); setMode("replay · " + TRACE.meta.started, "done"); enterReplay();
} else {
  let es, pending = false;
  const connect = () => {
    es = new EventSource("/events");
    es.onmessage = m => {
      const d = JSON.parse(m.data);
      if (d.type === "hello"){
        runId = d.run; reset(d.meta);
        setMode(d.meta.target === "robot" ? "live · real robot" : "live", "live"); live = true;
      } else if (d.type === "data"){
        if (!meta) return;
        frames.push(...d.frames); appendEvents(d.events);
        if (!pending){ pending = true; requestAnimationFrame(() => { pending = false; if (!ended) render(); }); }
      } else if (d.type === "end"){
        report = d.report; setMode("finished · scrub to replay", "done"); enterReplay();
      }
    };
    es.onerror = () => { if (!ended) setMode(meta ? "disconnected" : "waiting for a run", "wait"); };
  };
  connect();
}
</script>
</body>
</html>
"""
