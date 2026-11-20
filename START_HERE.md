# Start here: Aegis robot workshop

This file is written for **Claude Code** (or any coding assistant) to follow on
an attendee's computer. People reading it themselves can follow the same steps.

The person you're helping is probably **new to robotics and maybe to
programming**. Explain each step in a sentence or two of plain language, say
what you're about to install before you install it, and keep going unless
something needs their decision.

At the end, they'll have:

- the **Aegis simulator**: a robot dog on a top-down map in their browser, with
  the workshop's safety rules checked automatically;
- the **spin-and-wave sequence**: the dog looks around and shakes hands when
  something moves in front of it;
- everything installed inside one project folder (nothing system-wide except
  Python, if it's missing).

**The FF SDK is not needed** for any of this: the simulator is plain Python,
and real-robot runs go through the robot's own control gateway. If the
attendee was given the SDK file (`ff_sdk_aegis_devkit.zip`), there's an
optional step at the end. Don't search for it or download it.

A few words they'll meet:

- **preset**: a built-in move the robot knows, like `shake_hand` or `lie_down`.
- **e-stop**: emergency stop. On a real robot a mentor holds the remote with
  the e-stop button; the simulator plays the mentor and stops the dog if a rule
  is broken.
- **damping**: the motors relax and the dog sinks down softly; every run ends
  this way.

---

## 1. Check Python

Needs **Python 3.9 or newer**.

```bash
python3 --version      # macOS / Linux
```
```powershell
py --version           # Windows
```

If it's missing or older than 3.9, **stop and ask the user before installing
it**, and suggest the usual way for their system:

- macOS: the installer from https://www.python.org/downloads/ (or `brew install python` if they use Homebrew)
- Windows: the installer from https://www.python.org/downloads/ (tick "Add python.exe to PATH"), or `winget install Python.Python.3.12`
- Linux (Debian/Ubuntu): `sudo apt install python3 python3-venv`

## 2. Get the code

If you're not already inside a copy of this repository, clone it into the
current folder (or one the user names):

```bash
git clone https://github.com/ff-vm/aegis-workshop.git
cd aegis-workshop
```

No `git`? Download https://github.com/ff-vm/aegis-workshop/archive/refs/heads/main.zip,
unzip it, and go into the `aegis-workshop-main` folder.

**Every command below runs from this folder** (the one with `START_HERE.md`).

## 3. Install the libraries

Make a virtual environment (a private Python just for this project, so nothing
else on the computer changes) and install OpenCV, the camera library (~30 MB):

```bash
# macOS / Linux
python3 -m venv .venv
.venv/bin/python -m pip install -r requirements.txt
.venv/bin/python -c "import cv2; print('OpenCV', cv2.__version__)"
```
```powershell
# Windows
py -m venv .venv
.venv\Scripts\python -m pip install -r requirements.txt
.venv\Scripts\python -c "import cv2; print('OpenCV', cv2.__version__)"
```

The last line should print an OpenCV version.

## 4. Run the sequence in the simulator

This is the sequence the workshop is built around. Tell the user what will
happen, and ask them to **open http://localhost:8765 in their browser now**:
the page waits for the run and fills in by itself.

Then start it. It takes about 35 seconds. If you're an assistant, run it in
the background (or with a timeout of at least 60 s) so you can talk to the
user meanwhile:

```bash
.venv/bin/python spin_and_wave.py           # macOS / Linux
```
```powershell
.venv\Scripts\python spin_and_wave.py       # Windows
```

In the browser they'll see:

- the dog stand up, then turn in 45° steps;
- at each stop, a blue wedge for what its camera sees;
- someone pacing behind it. When the dog turns to face them it spots the
  motion (the dot turns green), stops, and **shakes hands**.

When it finishes, the terminal prints a report. Show the user the whole
report; it should end with `PASS: ready for a test station.` (Progress lines
like `Looking (3/8)…` may appear after it; that's normal.)

The live page stops updating when the run ends, but the run is saved as
**`run.html`** in this folder: open it in a browser any time to replay it with
the time slider.

## 5. Show them what else they can do

Give the user this menu, with the commands for their system:

| Try | macOS / Linux | Windows (PowerShell) |
|---|---|---|
| The spin-and-wave sequence again | `.venv/bin/python spin_and_wave.py` | `.venv\Scripts\python spin_and_wave.py` |
| Task 1: stand, walk, spin, shake hands, lie down | `.venv/bin/python task1_routine.py` | `.venv\Scripts\python task1_routine.py` |
| Task 3: walk a triangle back to the start | `.venv/bin/python task3_triangle.py` | `.venv\Scripts\python task3_triangle.py` |
| Your **laptop webcam** as the dog's eyes (wave at the laptop while the sim dog looks) | `FF_CAMERA=0 .venv/bin/python spin_and_wave.py` | `$env:FF_CAMERA=0; .venv\Scripts\python spin_and_wave.py` |
| See what the motion detector sees (a window opens; `q` closes it) | `.venv/bin/python motion_eyes.py --source 0` | `.venv\Scripts\python motion_eyes.py --source 0` |

The webcam items ask for camera permission the first time. On macOS, allow
it for the terminal app, then run the command again.

Then explain, briefly, how a sequence is written: open `spin_and_wave.py`
and point at `routine(dog)`. Every robot command is one line:

```python
await dog.motion.stand()                    # stand up
await dog.motion.cmd_vel(angular=0.3)       # turn left at 0.3 rad/s (about 17° a second)
await dog.motion.stop()                     # stop
await dog.motion.do_preset("shake_hand")    # offer a paw
```

Offer to help them change it, for example a different trick, more steps, or a
different turn size. Re-running the file shows the change in the simulator.

## Rules to keep (tell the user these)

- **Speed limits:** walking at most 0.3 m/s, turning at most 0.6 rad/s. The
  simulator plays the mentor: it stops the dog and fails the run if a sequence
  goes faster. Don't raise the limits.
- **Allowed tricks:** only `shake_hand` and `lie_down`. Others (jumps,
  backflip) are blocked.
- **Real robots** run only at a test station, with a mentor holding the
  e-stop. **Never connect to a robot or run anything in `robot/` unless the
  user explicitly asks and says a mentor is with them.** The mentor's steps are
  in `README.md`.

## If something goes wrong

| Problem | Fix |
|---|---|
| `pip install` fails on venue Wi-Fi | Retry. If it keeps failing, the simulator itself still works without OpenCV: `python3 task1_routine.py` (no camera features) |
| The page shows an old run, or nothing | A previous run was still going, so this one used the next port: the terminal prints the address (`live view → http://localhost:8766`). Or stop the old one (macOS / Linux: `pkill -f spin_and_wave`) and run again |
| Webcam window doesn't open (macOS) | System Settings → Privacy & Security → Camera → allow the terminal app |
| `?` instead of ✓ or → (Windows) | Harmless. Run `chcp 65001` first for proper symbols |

---

## Optional: the FF SDK (only if the attendee was given it)

Skip this unless the user has `ff_sdk_aegis_devkit.zip`. It's FF's
proprietary SDK; **never upload or commit it** (it's already in `.gitignore`).
It lets the same skill files run on the real SDK in **dry-run** (commands are
checked but nothing moves). The SDK is built for Python **3.10** on Linux and
Windows only; on a Mac it runs inside Docker, which provides Python 3.10.
Installing it needs internet: pip fetches one small dependency
(`typing-extensions`) that isn't in the zip.

1. Unzip it **in this folder**, so you get `aegis_devkit/wheels/` next to
   `START_HERE.md`.
2. Install it:
   - **Linux / Windows:** make a second environment with Python 3.10
     (`python3.10 -m venv .venv-sdk`, Windows `py -3.10 -m venv .venv-sdk`;
     ask before installing 3.10 if it's missing), then install the SDK from
     the devkit:
     `.venv-sdk/bin/python -m pip install --find-links aegis_devkit/wheels ff_sdk`
     (Windows: `.venv-sdk\Scripts\python ...`).
   - **macOS:** there's no Mac build, so it runs in Docker. Check Docker is
     installed and running with `docker info`; if not, ask before installing
     Docker Desktop, and the user has to start it once. From this folder:
     ```bash
     docker build --platform linux/arm64 -t ff-sdk -f - aegis_devkit/wheels <<'EOF'
     FROM python:3.10-slim
     COPY *.whl /tmp/
     RUN pip install -q --find-links /tmp /tmp/ff_sdk-*-linux_aarch64.whl
     WORKDIR /w
     EOF
     ```
     (Intel Mac: `linux/amd64` and the `linux_x86_64` wheel.)
3. Check it: run `task1_routine.py` with `FF_TARGET=dry-run`:
   - Linux: `FF_TARGET=dry-run .venv-sdk/bin/python task1_routine.py`
   - macOS: `docker run --rm --platform linux/arm64 -v "$PWD":/w -e FF_TARGET=dry-run ff-sdk python -u task1_routine.py`

   It's quiet for about 30 s (the routine's waits), then prints
   `[skill] finished on DEMO`: the real SDK accepted every command.
   Run it from this folder (the Docker command shares the current folder).
