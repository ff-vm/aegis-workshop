"""Task 1: robot routine (the guide's hint code), run through the simulator.

    python task1_routine.py                       # simulator (default)
    FF_TARGET=dry-run python task1_routine.py     # real ff_sdk, dry-run
    ./robot/run_real.sh firefly task1_routine.py   # real dog (mentors, test station)
"""
import asyncio
import os

os.environ.setdefault("FF_SIM_TASK", "1")   # simulator: check this run against Task 1

from aegis_sim import run_skill


async def routine(dog):
    await dog.motion.stand()
    await asyncio.sleep(dog.motion.preset_timeout("stand"))
    await dog.motion.cmd_vel(linear=0.2)     # forward for 2 s
    await asyncio.sleep(2)
    await dog.motion.cmd_vel(angular=0.6)    # one turn left: 2π / 0.6 ≈ 10.5 s
    await asyncio.sleep(10.5)
    await dog.motion.stop()
    for name in ("shake_hand", "lie_down"):
        if name in dog.motion.known_presets():
            await dog.motion.do_preset(name)
            await asyncio.sleep(dog.motion.preset_timeout(name))


if __name__ == "__main__":
    run_skill(routine)
