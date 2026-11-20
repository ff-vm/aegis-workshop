"""motion_eyes: notice something moving in front of the Aegis's camera.

The simplest useful thing a camera can do, with OpenCV only (no models):
compare each frame with the one before, and if enough of the middle of the
picture changed, something moved.

It only works while the dog is still: when the dog turns, the whole picture
moves. A standing legged robot also sways a little, so each frame is first
shifted to line up with the previous one (phase correlation) before comparing.

Camera sources (FF_CAMERA):
    sim                              moving things from FF_SIM_MOVERS (default
                                     when the dog is the simulator)
    rtsp://192.168.234.1:8554/test   the robot's camera (default on the real
                                     robot; your laptop must be on its hotspot)
    0, 1, ...                        a laptop webcam, for testing

Try it on its own (wave your hand at the camera):
    python motion_eyes.py --source 0

Settings: FF_MOTION_PIXEL (pixel change 0-255 that counts, default 25),
          FF_MOTION_AREA  (share of the middle that must change, default 0.015)
Needs: pip install opencv-python   (not needed for "sim")
"""
from __future__ import annotations

import math
import os
import sys
import threading
import time
from pathlib import Path
from urllib.parse import urlparse

ROBOT_RTSP = "rtsp://192.168.234.1:8554/test"   # ff_sdk D1Vision.DEFAULT_RTSP
SMALL = (160, 120)       # frames are shrunk to this before comparing
CONFIRM_FRAMES = 3       # motion must show in this many frames in a row


class Eyes:
    """moving() is True once motion has been seen in CONFIRM_FRAMES frames in
    a row since the last reset(). Call reset() each time the dog stops."""

    def reset(self) -> None:
        raise NotImplementedError

    def moving(self) -> bool:
        raise NotImplementedError

    def where(self) -> float:
        """Bearing of the motion, radians, + = left (0 if unknown)."""
        return 0.0

    def alive(self) -> bool:
        return True

    def frame_age(self) -> float:
        return 0.0

    def close(self) -> None:
        pass


# ── simulator ──────────────────────────────────────────────────────────

class SimEyes(Eyes):
    """Reads the simulator's moving things, with camera-like latency."""

    def __init__(self, dog) -> None:
        self._w = dog._w
        self._lock = threading.Lock()
        self._streak = 0
        self._bearing = 0.0
        self._ignore_until = 0.0
        self._logged = False
        self._stop = threading.Event()
        threading.Thread(target=self._loop, daemon=True, name="sim-eyes").start()

    def _loop(self) -> None:
        while not self._stop.is_set():
            time.sleep(0.1)    # 10 fps
            seen = self._w.camera_motion()
            with self._lock:
                if time.monotonic() < self._ignore_until:
                    continue
                self._streak = self._streak + 1 if seen else 0
                if seen:
                    self._bearing = seen[0]

    def reset(self) -> None:
        with self._lock:
            self._streak = 0
            self._logged = False
            self._ignore_until = time.monotonic() + 0.2

    def moving(self) -> bool:
        with self._lock:
            hit = self._streak >= CONFIRM_FRAMES
            if hit and not self._logged:
                self._logged = True
                self._w.log("cam", f"camera: motion at {math.degrees(self._bearing):+.0f}°")
            return hit

    def where(self) -> float:
        return self._bearing

    def close(self) -> None:
        self._stop.set()


# ── real camera ────────────────────────────────────────────────────────

class MotionDetector:
    """Frame in, (changed share of the middle, bearing, mask) out."""

    def __init__(self, hfov_deg: float | None = None) -> None:
        self.pixel = int(os.environ.get("FF_MOTION_PIXEL", "25"))
        self.area = float(os.environ.get("FF_MOTION_AREA", "0.015"))
        self.hfov = math.radians(hfov_deg if hfov_deg is not None
                                 else float(os.environ.get("FF_CAM_HFOV", "70")))
        self._prev = None

    def reset(self) -> None:
        self._prev = None

    def step(self, frame_bgr):
        import cv2
        import numpy as np
        g = cv2.cvtColor(cv2.resize(frame_bgr, SMALL, interpolation=cv2.INTER_AREA),
                         cv2.COLOR_BGR2GRAY)
        g = cv2.GaussianBlur(g, (5, 5), 0).astype(np.float32)
        prev, self._prev = self._prev, g
        if prev is None:
            return 0.0, 0.0, None
        # Line the frame up with the last one, so body sway isn't "motion".
        (dx, dy), _ = cv2.phaseCorrelate(prev, g)
        m = np.float32([[1, 0, -dx], [0, 1, -dy]])
        aligned = cv2.warpAffine(g, m, SMALL, borderMode=cv2.BORDER_REPLICATE)
        diff = cv2.absdiff(aligned, prev)
        mask = (diff > self.pixel).astype(np.uint8)
        # Ignore a margin (warp edges) and judge the middle 70% of the width.
        w, h = SMALL
        x0, x1, y0, y1 = int(w * 0.15), int(w * 0.85), 4, h - 4
        mid = mask[y0:y1, x0:x1]
        share = float(mid.mean())
        bearing = 0.0
        if share > 0:
            cols = mid.sum(axis=0)
            cx = x0 + float((cols * np.arange(len(cols))).sum() / cols.sum())
            f_px = (w / 2) / math.tan(self.hfov / 2)
            bearing = math.atan2((w / 2) - cx, f_px)
        return share, bearing, mask


def _check_reachable(url: str, timeout: float = 3.0) -> None:
    """Fail in 3 s, not OpenCV's 30 s, when the robot's camera isn't there."""
    import socket
    u = urlparse(url)
    try:
        socket.create_connection((u.hostname, u.port or 554), timeout).close()
    except OSError:
        raise ConnectionError(f"robot camera {url} not reachable. Are you on the "
                              f"robot's hotspot?") from None


class CameraEyes(Eyes):
    """Reads a camera (RTSP or webcam) in one thread and compares frames in
    another. Prints what it sees once a second and saves a snapshot when it
    spots motion (FF_CAM_SNAPS, default ./camera_snaps, "off" to disable)."""

    def __init__(self, source, on_motion=None) -> None:
        import cv2
        self.source = source
        if isinstance(source, str) and source.startswith("rtsp"):
            _check_reachable(source)
            os.environ.setdefault("OPENCV_FFMPEG_CAPTURE_OPTIONS", "rtsp_transport;tcp")
        self._cap = cv2.VideoCapture(source)
        if not self._cap.isOpened():
            raise ConnectionError(f"camera {source!r} didn't open")
        self._det = MotionDetector()
        self._lock = threading.Lock()
        self._frame = None
        self._frame_t = 0.0
        self._frames = 0
        self._streak = 0
        self._share = 0.0
        self._bearing = 0.0
        self._reset = True
        self._snapped = False
        self._on = on_motion
        self._dead = ""
        self._stop = threading.Event()
        snaps = os.environ.get("FF_CAM_SNAPS", "camera_snaps")
        self._snap_dir = None if snaps.lower() == "off" else Path(snaps)
        if self._snap_dir:
            self._snap_dir.mkdir(parents=True, exist_ok=True)
            for old in self._snap_dir.glob("motion_*.jpg"):
                old.unlink()
        self._started = time.monotonic()
        self._t_read = threading.Thread(target=self._read, daemon=True, name="eyes-read")
        self._t_det = threading.Thread(target=self._detect, daemon=True, name="eyes-detect")
        self._t_read.start()
        self._t_det.start()

    def _read(self) -> None:
        fails = 0
        while not self._stop.is_set():
            ok, frame = self._cap.read()
            if not ok:
                fails += 1
                if fails > 50:
                    self._dead = "camera stopped sending frames"
                    return
                time.sleep(0.02)
                continue
            fails = 0
            self._frames += 1
            self._frame, self._frame_t = frame, time.monotonic()

    def _detect(self) -> None:
        last = 0.0
        t_status = time.monotonic()
        frames_at_status = n_det = 0
        peak = 0.0
        while not self._stop.is_set():
            frame = self._frame
            if frame is None or self._frame_t == last:
                time.sleep(0.005)
                continue
            last = self._frame_t
            with self._lock:
                if self._reset:
                    self._det.reset()
                    self._streak = 0
                    self._reset = False
                    self._snapped = False
            share, bearing, mask = self._det.step(frame)
            n_det += 1
            peak = max(peak, share)
            with self._lock:
                self._share = share
                self._streak = self._streak + 1 if share >= self._det.area else 0
                if share >= self._det.area:
                    self._bearing = bearing
                hit = self._streak >= CONFIRM_FRAMES
            if hit and not self._snapped:
                self._snapped = True
                if self._on:
                    self._on(share, bearing)
                self._save(frame, mask, share, bearing)
            now = time.monotonic()
            if now - t_status >= 1.0:
                h, w = frame.shape[:2]
                fps = (self._frames - frames_at_status) / (now - t_status)
                print(f"[eyes] {now - self._started:5.1f}s {w}x{h} {fps:4.1f} fps · "
                      f"change {peak * 100:4.1f}% (needs {self._det.area * 100:.1f}%)"
                      + ("  MOTION" if hit else ""), file=sys.stderr, flush=True)
                t_status, frames_at_status, n_det, peak = now, self._frames, 0, 0.0

    def _save(self, frame, mask, share, bearing) -> None:
        if not self._snap_dir or mask is None:
            return
        import cv2
        h, w = frame.shape[:2]
        big = cv2.resize(mask * 255, (w, h), interpolation=cv2.INTER_NEAREST)
        out = frame.copy()
        out[big > 0] = (0.5 * out[big > 0] + [0, 0, 127]).astype(out.dtype)
        cv2.putText(out, f"motion {share * 100:.1f}% at {math.degrees(bearing):+.0f} deg",
                    (10, 30), cv2.FONT_HERSHEY_SIMPLEX, 0.8, (110, 211, 111), 2)
        cv2.imwrite(str(self._snap_dir / f"motion_{time.monotonic() - self._started:06.1f}s.jpg"), out)

    def reset(self) -> None:
        with self._lock:
            self._reset = True
            self._streak = 0

    def moving(self) -> bool:
        with self._lock:
            return self._streak >= CONFIRM_FRAMES and not self._reset

    def where(self) -> float:
        return self._bearing

    def alive(self) -> bool:
        return not self._dead and not self._stop.is_set()

    def frame_age(self) -> float:
        return time.monotonic() - self._frame_t if self._frame_t else float("inf")

    def close(self) -> None:
        # Stop both threads before releasing the camera: releasing it under a
        # running read crashes (segfault) in native code.
        self._stop.set()
        self._t_det.join(timeout=3.0)
        self._t_read.join(timeout=5.0)
        if not self._t_read.is_alive():
            try:
                self._cap.release()
            except Exception:
                pass
        if self._snap_dir and any(self._snap_dir.glob("motion_*.jpg")):
            print(f"[eyes] snapshots → {self._snap_dir.resolve()}", file=sys.stderr)


# ── pick a source for a dog ────────────────────────────────────────────

def open_eyes(dog) -> Eyes:
    """Sim dog → simulated camera, real robot → the robot's RTSP camera.
    FF_CAMERA overrides (e.g. FF_CAMERA=0 for the laptop webcam)."""
    src = os.environ.get("FF_CAMERA", "").strip()
    w = getattr(dog, "_w", None)
    if not src:
        src = "sim" if w is not None and getattr(w, "bridge", None) is None else ROBOT_RTSP
    if src == "sim":
        if w is None:
            raise ValueError("FF_CAMERA=sim needs the simulator dog")
        return SimEyes(dog)
    source = int(src) if src.isdigit() else src
    on = None
    if w is not None:
        def on(share: float, bearing: float) -> None:
            w.log("cam", f"camera: motion {share * 100:.1f}% at {math.degrees(bearing):+.0f}°")
    print(f"[eyes] camera: {source}", file=sys.stderr)
    return CameraEyes(source, on_motion=on)


# ── live preview ───────────────────────────────────────────────────────

def _main(argv: list[str]) -> int:
    import argparse
    import cv2
    ap = argparse.ArgumentParser(description="Preview motion detection on a camera.")
    ap.add_argument("--source", default=os.environ.get("FF_CAMERA", "0"),
                    help="webcam index or RTSP URL (robot: %s)" % ROBOT_RTSP)
    a = ap.parse_args(argv)
    source = int(a.source) if str(a.source).isdigit() else a.source
    if isinstance(source, str) and source.startswith("rtsp"):
        _check_reachable(source)
        os.environ.setdefault("OPENCV_FFMPEG_CAPTURE_OPTIONS", "rtsp_transport;tcp")
    cap = cv2.VideoCapture(source)
    if not cap.isOpened():
        print(f"camera {source!r} didn't open", file=sys.stderr)
        return 1
    det = MotionDetector()
    streak = 0
    print("Wave at the camera. q or Esc to quit.")
    while True:
        ok, frame = cap.read()
        if not ok:
            print("no frame", file=sys.stderr)
            break
        share, bearing, mask = det.step(frame)
        streak = streak + 1 if share >= det.area else 0
        hit = streak >= CONFIRM_FRAMES
        if mask is not None:
            h, w = frame.shape[:2]
            big = cv2.resize(mask * 255, (w, h), interpolation=cv2.INTER_NEAREST)
            frame[big > 0] = (0.5 * frame[big > 0] + [0, 0, 127]).astype(frame.dtype)
        col = (110, 211, 111) if hit else (200, 200, 200)
        cv2.putText(frame, f"change {share * 100:4.1f}% (needs {det.area * 100:.1f}%)"
                    + (f"  MOTION {math.degrees(bearing):+.0f} deg" if hit else ""),
                    (10, 30), cv2.FONT_HERSHEY_SIMPLEX, 0.7, col, 2)
        cv2.imshow("motion_eyes", frame)
        if cv2.waitKey(1) & 0xFF in (27, ord("q")):
            break
    cap.release()
    cv2.destroyAllWindows()
    return 0


if __name__ == "__main__":
    sys.exit(_main(sys.argv[1:]))
