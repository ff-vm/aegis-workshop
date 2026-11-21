"""Spin and wave: the Aegis looks around in steps, and when it sees something
move in front of it, it offers a paw (shake_hand).

    python3 spin_and_wave.py                           # simulator
    FF_CAMERA=0 .venv/bin/python spin_and_wave.py      # sim dog, your laptop webcam as its eyes
    ./robot/run_real.sh firefly spin_and_wave.py        # real dog + its camera (mentors, on the hotspot)

A turning dog sees the whole picture move, so it can only notice motion while
it stands still. So it turns ~45°, stops, lets its body settle, watches for a
few seconds, and repeats. After a full circle with nothing moving, it lies down.
"""
import asyncio
import math
import os

# Sim only: someone pacing back and forth behind the dog's left shoulder.
os.environ.setdefault("FF_SIM_MOVERS", "-1.6,0.5>-0.9,1.6@3")
os.environ.setdefault("FF_SIM_TASK", "0")          # sim: safety checks only

from aegis_sim import run_skill        # noqa: E402
from motion_eyes import open_eyes      # noqa: E402

STEP = math.radians(45)     # turn this much between looks
TURN = 0.3                  # rad/s while turning
SETTLE_S = 0.8              # wait after stopping, so the body stops swaying
WATCH_S = 3.0               # look for motion this long at each stop
CAMERA_QUIET_S = 1.0        # no frames for this long → stop


async def watch(eyes, seconds):
    """True if motion shows up within `seconds`."""
    loop = asyncio.get_running_loop()
    end = loop.time() + seconds
    while loop.time() < end:
        if not eyes.alive() or eyes.frame_age() > CAMERA_QUIET_S:
            raise RuntimeError("camera went quiet")
        if eyes.moving():
            return True
        await asyncio.sleep(0.05)
    return False


async def routine(dog):
    eyes = open_eyes(dog)
    try:
        await dog.motion.stand()
        await asyncio.sleep(dog.motion.preset_timeout("stand"))

        steps = round(2 * math.pi / STEP)
        for i in range(steps + 1):            # look at the start, then after each step
            if i:
                await dog.motion.cmd_vel(angular=TURN)
                await asyncio.sleep(STEP / TURN)
                await dog.motion.stop()
            await asyncio.sleep(SETTLE_S)
            eyes.reset()
            print(f"Looking ({i}/{steps})…")
            if await watch(eyes, WATCH_S):
                print(f"Motion at {math.degrees(eyes.where()):+.0f}° → waving")
                if "shake_hand" in dog.motion.known_presets():
                    await dog.motion.do_preset("shake_hand")
                    await asyncio.sleep(dog.motion.preset_timeout("shake_hand"))
                return

        print("Nothing moved all the way round. Lying down.")
        await dog.motion.do_preset("lie_down")
        await asyncio.sleep(dog.motion.preset_timeout("lie_down"))
    finally:
        eyes.close()


if __name__ == "__main__":
    run_skill(routine)
