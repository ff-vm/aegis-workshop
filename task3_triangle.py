"""Task 3: triangle back home, run through the simulator.

    python3 task3_triangle.py              # closed-loop (uses pose())
    MODE=open python3 task3_triangle.py    # open-loop timing only

The sim adds a little floor slip for Task 3, so open-loop drifts off the cross
the way a real floor does. Closed-loop corrects for it with pose().
"""
import asyncio
import math
import os

os.environ.setdefault("FF_SIM_TASK", "3")   # simulator: check this run against Task 3

from aegis_sim import run_skill

SPEED = 0.25     # m/s (limit 0.3)
TURN = 0.6       # rad/s (limit 0.6)


def wrap(a):
    return math.atan2(math.sin(a), math.cos(a))


async def open_loop(dog):
    for _ in range(3):
        await dog.motion.cmd_vel(linear=SPEED)
        await asyncio.sleep(1.0 / SPEED)              # 4 s ≈ 1 m
        await dog.motion.cmd_vel(angular=TURN)
        await asyncio.sleep((2 * math.pi / 3) / TURN)  # ≈ 3.5 s ≈ 120°
    await dog.motion.stop()


async def closed_loop(dog):
    p = await dog.state.pose()
    if not math.isfinite(p.x):
        print("pose() is NaN: no feedback, falling back to open-loop")
        return await open_loop(dog)
    for _ in range(3):
        # straight: watch the distance covered, slow down for the last 20 cm
        start = await dog.state.pose()
        while True:
            p = await dog.state.pose()
            left = 1.0 - math.hypot(p.x - start.x, p.y - start.y)
            if left <= 0.01:
                break
            await dog.motion.cmd_vel(linear=min(SPEED, max(0.05, left)))
            await asyncio.sleep(0.05)
        await dog.motion.stop()
        await asyncio.sleep(0.5)
        # turn: add up the angle actually turned, slow down for the last 20°
        turned, last = 0.0, (await dog.state.pose()).yaw
        while True:
            left = 2 * math.pi / 3 - turned
            if left <= 0.01:
                break
            await dog.motion.cmd_vel(angular=min(TURN, max(0.1, 1.5 * left)))
            await asyncio.sleep(0.05)
            yaw = (await dog.state.pose()).yaw
            turned += wrap(yaw - last)
            last = yaw
        await dog.motion.stop()
        await asyncio.sleep(0.5)


async def routine(dog):
    await dog.motion.stand()
    await asyncio.sleep(dog.motion.preset_timeout("stand"))
    if os.environ.get("MODE", "closed") == "open":
        await open_loop(dog)
    else:
        await closed_loop(dog)


if __name__ == "__main__":
    run_skill(routine)
