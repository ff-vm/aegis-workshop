# Aegis robot workshop

Write a skill for the FF Robotics **Aegis** robot dog, try it in a simulator on
your laptop, then run it on the real robot at a test station.

## Get started

1. Download the FF SDK file (`ff_sdk_aegis_devkit.zip`) from the link you were
   given. It has to end up **in the workshop folder itself** (next to
   `START_HERE.md`); Claude Code moves it there for you.
2. Paste this into **Claude Code**. It asks where to put the workshop, installs
   everything (Python libraries, the SDK, and Docker on a Mac), and opens the
   first sequence in a terminal and your browser:

```
Set up the Aegis robot workshop from https://github.com/ff-vm/aegis-workshop: ask me where to put it, clone it there, then open START_HERE.md in the clone and follow every step, running the commands yourself.
```

Or follow [START_HERE.md](START_HERE.md) yourself. You need Python 3.9+ and
~30 MB of downloads (OpenCV); the SDK on a Mac also needs Docker Desktop
(~570 MB). The simulator works without the SDK.

## What's here

| File | What it is |
|---|---|
| `START_HERE.md` | Setup steps, written for Claude Code to follow |
| `aegis_sim.py` | The simulator: same calls as the FF SDK, a live top-down view at http://localhost:8765, and the workshop's safety rules checked like a mentor would |
| `spin_and_wave.py` | The demo sequence: look around in 45° steps, shake hands when something moves in front of the camera |
| `motion_eyes.py` | Spots motion in the camera picture (OpenCV, no AI models); `--source 0` previews your webcam |
| `task1_routine.py` | Task 1: stand, walk, spin, shake hands, lie down |
| `task3_triangle.py` | Task 3: walk a 1 m triangle back to the start |
| `run_demo.command` / `run_demo.bat` | Double-click to run the demo sequence with the live view (macOS / Windows) |
| `robot/` | Mentor tools for the real robot (below) |
| `docs/developer-guide-en.html` | The workshop guide: schedule, rules, SDK reference, tasks, scoring |

## Writing a skill

A skill is an `async def routine(dog)` handed to `run_skill()`:

```python
import asyncio
from aegis_sim import run_skill

async def routine(dog):
    await dog.motion.stand()
    await asyncio.sleep(dog.motion.preset_timeout("stand"))
    await dog.motion.cmd_vel(angular=0.3)       # turn left
    await asyncio.sleep(3)
    await dog.motion.stop()
    await dog.motion.do_preset("shake_hand")
    await asyncio.sleep(dog.motion.preset_timeout("shake_hand"))

run_skill(routine)
```

`run_skill()` handles the safe exits: Ctrl+C or an error triggers the e-stop,
and a normal finish runs `stop → damping → close`. The same file runs in the
simulator, on the FF SDK in dry-run, and on the real robot.

### Simulator settings

| Variable | Values | Default |
|---|---|---|
| `FF_SIM_TASK` | `0` safety checks only, `1` / `2` / `3` the task's checks | set by each example |
| `FF_SIM_VARIANT` | `footed`, `wheeled` | `footed` |
| `FF_SIM_SLIP` | floor slip, `0`–`0.2` | `0` (`0.04` for Task 3) |
| `FF_SIM_MOVERS` | moving things for the camera, `"ax,ay>bx,by@seconds;…"` | set by `spin_and_wave.py` |
| `FF_SIM_POSE` | `off` makes `pose()` return NaN, like a robot without position feedback | `on` |
| `FF_SIM_PORT` | live view port, `0` turns it off | `8765` |
| `FF_SIM_OUT` | replay file, `off` for none | `run.html` |
| `FF_SIM_OPEN` | `1` opens the live view in the browser | off (on in `run_demo`) |
| `FF_CAMERA` | `sim`, a webcam number (`0`), or an RTSP URL | `sim` in the simulator, the robot's camera on the robot |
| `FF_MOTION_AREA` | share of the picture that must change to count as motion | `0.015` |

### Rules

- Walk at most **0.3 m/s**, turn at most **0.6 rad/s**. Going faster triggers a mentor e-stop and the run scores zero.
- Only two tricks are allowed: `shake_hand` and `lie_down`.
- Real robots run only at a test station, with a mentor on the e-stop.

## For mentors: running on the real robot

The laptop runs the skill and keeps the live view and rule checks, and each
command is sent to FF's control gateway on the robot through an ssh tunnel.
The robot's camera is read directly at `rtsp://192.168.234.1:8554/test`.

1. Join the robot's Wi-Fi hotspot (`AEGIS-2A-...`). The robot is at `192.168.234.1`.
2. Clear 1 m of floor all round; someone holds the remote's e-stop.
3. From the repo root (macOS / Linux):
   ```bash
   ./robot/run_real.sh <user> spin_and_wave.py      # <user>: the robot's ssh account
   ```
   Windows (PowerShell; ssh asks for the password in a second window):
   ```powershell
   powershell -ExecutionPolicy Bypass -File robot\run_real.ps1 <user> spin_and_wave.py
   ```
4. ssh asks for the robot's password once; type `yes` to confirm the floor is clear.
5. Ctrl+C sends **passive** (motors off) right after the current 0.6 s step. The remote's e-stop is still the real one.

How commands map to the gateway:

| Skill | Gateway |
|---|---|
| `stand()` | `stand` |
| `cmd_vel(...)` | chained 0.6 s `move`s, from stick 0.75 (slowest walk) up to full stick at the speed limits |
| `do_preset("shake_hand")` / `("lie_down")` | `shakehand` / `lie` |
| `damping()`, `e_stop()` | `passive` |

The live view shows the **planned** path. The real dog's speed for each stick
value isn't calibrated yet: time a 1 m walk and a full spin before trusting
distances and angles. If it turns the wrong way, rerun with `FF_GW_YAW_SIGN=-1`.

Check the robot's camera without moving the dog (`q` to quit):

```bash
.venv/bin/python motion_eyes.py --source rtsp://192.168.234.1:8554/test
```

If motion triggers on nothing, raise `FF_MOTION_AREA` (for example `0.03`); if
it misses real motion, lower it (`0.008`). Each detection is saved to
`camera_snaps/`.

### Other robot tools (`robot/`)

| File | What it does |
|---|---|
| `run_real.sh`, `run_real.ps1` | Run a skill on the real dog through FF's gateway (above; `.ps1` for Windows) |
| `gateway.py` | Talks to FF's gateway: `stand`, `lie`, `limp`, `greet`, `shakehand`, `forward 1.5`, `square 2`, … |
| `run_on_robot.sh` | Copy the tools to the robot, install the SDK there, run a gateway command or the panel: `./robot/run_on_robot.sh <user> stand` |
| `panel.py`, `aegis-panel.service` | A phone-friendly control panel served by the robot at http://192.168.234.1:8000: `./robot/run_on_robot.sh <user> panel` |
| `aegis_skills.py`, `aegis-run` | Skills written directly against the FF SDK (`check`, `greet`, `square`, `nod_dance`), for when FF's gateway isn't running |
| `robot_info.py` | Robot info helper |

`run_on_robot.sh` needs the FF SDK: unzip `ff_sdk_aegis_devkit.zip` at the repo
root (`aegis_devkit/`) first.

## The FF SDK

The FF SDK is proprietary to Faraday Future and is **not** in this repository.
It's handed out separately at the workshop; unzip it at the repo root and see
the optional step at the end of [START_HERE.md](START_HERE.md). `.gitignore`
keeps it out of commits.
