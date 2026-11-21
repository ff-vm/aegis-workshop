# Start here: Aegis robot workshop

This file is written for **Claude Code** (or any coding assistant) to follow on
an attendee's computer. People reading it themselves can follow the same steps.

## How to work through this (for the assistant)

- **Run every command yourself.** Don't just show commands to the user and
  stop: install, check, and fix as you go. The user should only have to answer
  the questions marked **Ask**.
- The user is probably **new to robotics and maybe to programming**. Before
  each step, say in a sentence what it does and why, in plain language.
- Only install things system-wide (Python, Docker) after the user says yes.
- At the end, the user must know **where the files are**, see the first
  sequence **running in its own terminal window and their browser**, and know
  **how to change it by talking to you** (section 8).

What they'll have at the end:

- the **Aegis simulator**: a robot dog on a top-down map in the browser, with
  the workshop's safety rules checked automatically;
- the **spin-and-wave sequence**: the dog looks around and shakes hands when
  something moves in front of it;
- the **FF SDK** installed, if they were given it (on a Mac it runs inside
  Docker, which you'll install for them).

A few words they'll meet:

- **preset**: a built-in move the robot knows, like `shake_hand` or `lie_down`.
- **e-stop**: emergency stop. On a real robot a mentor holds the remote with
  the e-stop button; the simulator plays the mentor and stops the dog if a rule
  is broken.
- **damping**: the motors relax and the dog sinks down softly; every run ends
  this way.

---

## 0. Make sure you're on the user's own computer

Everything has to end up **on the user's laptop**: the files, the simulator
in their browser, their webcam, and later their Wi-Fi connection to the robot.
A cloud session (Claude Code on the web, the desktop app's **Cloud**
environment, or any remote sandbox) installs it all on a computer they can't
see, and nothing reaches their laptop.

Before doing anything else, check:

```bash
echo "remote=$CLAUDE_CODE_REMOTE"              # macOS / Linux
```
```powershell
"remote=$env:CLAUDE_CODE_REMOTE"               # Windows
```

If it prints `remote=true`, or you know you're running in a cloud or remote
sandbox (for example your working folder is a fresh clone in a container, or
there's no way to open a browser or window on the user's screen), **stop
here** and tell the user:

> I'm running in a cloud session, so anything I install stays on a remote
> computer, not your laptop. Please start Claude Code **on your own
> computer** and paste the setup prompt again:
>
> - **Claude desktop app:** open the **Code** tab, start a new session, and
>   choose the **Local** environment (not Cloud), with a folder on your
>   computer.
> - **Or the terminal:** install Claude Code from https://claude.com/claude-code,
>   open Terminal (Windows: PowerShell), and run `claude`.

Don't clone, install, or run anything in the cloud session.

## 1. Ask two things first

Ask both in one message, and wait for the answers.

1. **Ask: where should the workshop folder go?** Suggest a default:
   `~/Documents/aegis-workshop` (Windows: `%USERPROFILE%\Documents\aegis-workshop`).
   If the current folder is already a copy of this repository (it has
   `START_HERE.md` and `aegis_sim.py`), say so and ask whether to use it.
2. **Ask about the FF SDK, and flag it exactly like this** (copy this block
   into your message as it is, emoji included):

   > 🚨🚨 **IMPORTANT: the FF SDK must be copied into your workshop folder** 🚨🚨
   >
   > 📦 Did you download the FF SDK from the workshop link? It's a file called
   > `ff_sdk_aegis_devkit.zip` (or, if your computer already unzipped it, a
   > folder called `aegis_devkit`). **Where is it?** It's usually in your
   > **Downloads** folder.
   >
   > 📁 It has to go **directly inside the workshop folder**, next to
   > `START_HERE.md`, not in a subfolder and not anywhere else. I'll copy it
   > there and unpack it for you.
   >
   > ⚠️ Without it, the simulator still works, but the FF SDK part of setup
   > is skipped.

   If they don't have it yet, carry on without it, and flag it again in your
   final message (section 8) so they don't forget.

Remember the chosen folder; below it's called **the workshop folder**. Use its
full path when you talk to the user.

## 2. Check Python

Needs **Python 3.9 or newer**.

```bash
python3 --version      # macOS / Linux
```
```powershell
py --version           # Windows
```

If it's missing or older than 3.9, **ask the user before installing it**,
then install it:

- macOS: `brew install python` if Homebrew is there; otherwise the installer
  from https://www.python.org/downloads/ (the user clicks through it). A fresh
  Mac may pop up "install command line developer tools" the first time
  `python3` runs: tell them to click **Install** and wait, then retry.
- Windows: `winget install Python.Python.3.12` (or the python.org installer,
  ticking "Add python.exe to PATH"). Open a new terminal afterwards.
- Linux (Debian/Ubuntu): `sudo apt install python3 python3-venv` (they type
  their password).

## 3. Get the code

Clone into the workshop folder (skip if you're already in a copy):

```bash
git clone https://github.com/ff-vm/aegis-workshop.git "<workshop folder>"
cd "<workshop folder>"
```

No `git`? Download
https://github.com/ff-vm/aegis-workshop/archive/refs/heads/main.zip, unzip it,
and rename the `aegis-workshop-main` folder to the workshop folder. Then on
macOS / Linux: `chmod +x run_demo.command robot/*.sh`.

**Every command below runs from the workshop folder.**

## 4. Install the libraries

Make a virtual environment (a private Python just for this project, so nothing
else on the computer changes) and install everything in `requirements.txt`
(OpenCV, the camera library, ~30 MB). Run these yourself:

```bash
# macOS / Linux
python3 -m venv .venv
.venv/bin/python -m pip install --upgrade pip
.venv/bin/python -m pip install -r requirements.txt
.venv/bin/python -c "import cv2; print('OpenCV', cv2.__version__)"
```
```powershell
# Windows
py -m venv .venv
.venv\Scripts\python -m pip install --upgrade pip
.venv\Scripts\python -m pip install -r requirements.txt
.venv\Scripts\python -c "import cv2; print('OpenCV', cv2.__version__)"
```

The last line must print an OpenCV version. If `pip install` fails on venue
Wi-Fi, retry a couple of times before moving on.

## 5. Check the sequence works

Run the sequence once without the browser, to make sure everything is in
place (about 35 seconds; use a timeout of at least 90 s):

```bash
FF_SIM_PORT=0 FF_SIM_OUT=off .venv/bin/python spin_and_wave.py                        # macOS / Linux
```
```powershell
$env:FF_SIM_PORT=0; $env:FF_SIM_OUT='off'; .venv\Scripts\python spin_and_wave.py      # Windows
```

The report must end with `PASS: ready for a test station.` If it doesn't, fix
it before going on. (In PowerShell, clear the two settings afterwards with
`Remove-Item Env:FF_SIM_PORT, Env:FF_SIM_OUT`.)

## 6. The FF SDK (if they have the zip)

The FF SDK is FF's proprietary SDK: **never upload or commit it** (it's in
`.gitignore`). With it, the same skill files run on the real SDK in
**dry-run** (commands are checked, nothing moves). It's built for Python
**3.10** on Linux and Windows only; **on a Mac it needs Docker**.

### 6a. 🚨 Copy the SDK into the workshop folder

Tell the user: "🚨 Copying the FF SDK into your workshop folder now." Copy it
from where they said into the **top level** of the workshop folder (copy, so
their download stays where it is):

If it's the **zip**:
```bash
cp ~/Downloads/ff_sdk_aegis_devkit.zip .        # use the path the user gave
unzip -q ff_sdk_aegis_devkit.zip
```
```powershell
Copy-Item "$HOME\Downloads\ff_sdk_aegis_devkit.zip" .
Expand-Archive ff_sdk_aegis_devkit.zip -DestinationPath .
```

If it's already an **`aegis_devkit` folder**:
```bash
cp -R ~/Downloads/aegis_devkit .                # use the path the user gave
```
```powershell
Copy-Item -Recurse "$HOME\Downloads\aegis_devkit" .
```

Then check it landed in the right place:
```bash
ls aegis_devkit/wheels          # Windows: Get-ChildItem aegis_devkit\wheels
```
It must list `ff_sdk-…whl` files. If it unpacked somewhere deeper (for
example `ff_sdk_aegis_devkit/aegis_devkit/`), move `aegis_devkit` up so it
sits directly in the workshop folder, then check again. Tell the user: "✅ The
FF SDK is in your workshop folder: `<workshop folder>/aegis_devkit`."

### 6b. Linux / Windows: install into a Python 3.10 environment

```bash
python3.10 -m venv .venv-sdk
.venv-sdk/bin/python -m pip install --find-links aegis_devkit/wheels ff_sdk
FF_TARGET=dry-run .venv-sdk/bin/python task1_routine.py
```
```powershell
py -3.10 -m venv .venv-sdk
.venv-sdk\Scripts\python -m pip install --find-links aegis_devkit\wheels ff_sdk
$env:FF_TARGET='dry-run'; .venv-sdk\Scripts\python task1_routine.py; Remove-Item Env:FF_TARGET
```

If Python 3.10 is missing, ask, then install it (`winget install
Python.Python.3.10`, or `sudo apt install python3.10 python3.10-venv` / the
deadsnakes PPA on Ubuntu). pip fetches one small dependency
(`typing-extensions`) from the internet.

### 6c. macOS: install Docker, then the SDK inside it

1. Check Docker: `docker info`.
   - Works → go to step 3.
   - `/Applications/Docker.app` exists but `docker info` fails → it's not
     running: `open -a Docker`, then go to step 2's last part (waiting).
   - Not installed → step 2.
2. **Ask: "To run the FF SDK on a Mac I need to install Docker Desktop (a free
   app, ~570 MB download). OK?"** On yes, install it:
   ```bash
   ARCH=$( [ "$(uname -m)" = arm64 ] && echo arm64 || echo amd64 )
   curl -fL -o "$TMPDIR/Docker.dmg" "https://desktop.docker.com/mac/main/$ARCH/Docker.dmg"
   hdiutil attach -nobrowse -quiet "$TMPDIR/Docker.dmg"
   cp -R /Volumes/Docker/Docker.app /Applications/ || cp -R /Volumes/Docker/Docker.app ~/Applications/
   hdiutil detach -quiet /Volumes/Docker
   rm "$TMPDIR/Docker.dmg"
   open -a Docker
   ```
   Tell the user: **a Docker window opens; click Accept on the agreement, pick
   "Use recommended settings" (it asks for their Mac password), and skip the
   sign-in.** Then wait for Docker to start, checking every few seconds for up
   to 3 minutes:
   ```bash
   export PATH="$PATH:/Applications/Docker.app/Contents/Resources/bin"
   for i in $(seq 36); do docker info >/dev/null 2>&1 && echo "Docker ready" && break; sleep 5; done
   ```
   If it isn't ready, ask whether the Docker window is still waiting for a
   click.
3. Build the SDK image (Apple silicon; on an Intel Mac use `linux/amd64` and
   the `linux_x86_64` wheel):
   ```bash
   docker build --platform linux/arm64 -t ff-sdk -f - aegis_devkit/wheels <<'EOF'
   FROM python:3.10-slim
   COPY *.whl /tmp/
   RUN pip install -q --find-links /tmp /tmp/ff_sdk-*-linux_aarch64.whl
   WORKDIR /w
   EOF
   ```
4. Check it:
   ```bash
   docker run --rm --platform linux/arm64 -v "$PWD":/w -e FF_TARGET=dry-run ff-sdk python -u task1_routine.py
   ```

On every system the check is quiet for about 30 s (the routine's waits), then
prints `[skill] finished on DEMO`: the real SDK accepted every command.

## 7. Open it for them

Now start the sequence **in its own terminal window**, with the live view in
their browser. The launcher scripts in the workshop folder do both:

| System | Command you run |
|---|---|
| macOS | `open run_demo.command` |
| Windows | `Start-Process run_demo.bat` (from cmd: `start run_demo.bat`) |
| Linux | `x-terminal-emulator -e ./run_demo.command` (or `gnome-terminal -- ./run_demo.command`) |

A terminal window opens, the dog's map opens in the browser
(http://localhost:8765), and they'll see:

- the dog stand up, then turn in 45° steps;
- at each stop, a blue wedge for what its camera sees;
- someone pacing behind it. When the dog turns to face them it spots the
  motion (the dot turns green), stops, and **shakes hands**.

The terminal ends with a report and `PASS`. The run is saved as `run.html`:
open it any time to replay it with the time slider.

If the browser didn't open by itself, tell them to open http://localhost:8765
(or the address the terminal printed after `live view →`).

## 8. Final message and cheat sheet

The user will run things **without you** later: on the robot's Wi-Fi there's
no internet, so Claude Code won't work there. So finish by giving them a
step-by-step cheat sheet with every command written out to copy and paste.

1. Write the cheat sheet below to **`my_cheatsheet.md`** in the workshop folder:
   - keep only **their** system's version (macOS / Linux *or* Windows);
   - replace `<workshop folder>` with the real full path everywhere;
   - drop the SDK lines if the SDK isn't installed.
2. Open it for them (macOS `open my_cheatsheet.md`, Windows
   `Start-Process my_cheatsheet.md`, Linux `xdg-open my_cheatsheet.md`).
3. Your final message to the user, in this order:
   - **Where everything is**: the full folder path, and the file table below.
   - **The SDK**: either "✅ The FF SDK is installed, in
     `<workshop folder>/aegis_devkit`", or, if it isn't there yet, this flag:
     "🚨 **The FF SDK isn't in your workshop folder yet.** Download
     `ff_sdk_aegis_devkit.zip` from the workshop link, then tell me where it
     is and I'll copy it in and finish the setup (section 6)."
   - **What's running now**: the terminal window and browser tab from section 7.
   - **How to vibe code it** (below).
   - **The rules**, in two or three lines.
   - Last, under the heading **"Next: run it on the real robot"**: part B of
     their cheat sheet, **copied word for word**, with their real folder path
     in step 3. Don't summarise or shorten it: the user follows it later on
     their own, without you, so every step and every copy-paste line must be
     there. End with: "These steps are also saved in `my_cheatsheet.md` in
     your workshop folder, along with how to run the simulator."

### Files

| File | What it is |
|---|---|
| `spin_and_wave.py` | The sequence you just watched |
| `task1_routine.py`, `task3_triangle.py` | Two more examples (a routine; a walked triangle) |
| `aegis_sim.py` | The simulator (no need to edit it) |
| `motion_eyes.py` | The camera motion detector |
| `run_demo.command` / `run_demo.bat` | Double-click to run the sequence again |
| `run.html` | Replay of the last run |
| `my_cheatsheet.md` | Your step-by-step commands |
| `aegis_devkit/` | The FF SDK (if installed) |
| `docs/developer-guide-en.html` | The workshop guide: schedule, rules, tasks, scoring |

### Cheat sheet: macOS / Linux

````markdown
# Aegis workshop cheat sheet

Workshop folder: `<workshop folder>`

## A. Run a sequence in the simulator (any time, anywhere)

1. Open **Terminal** (macOS: Cmd+Space, type "Terminal", Enter).
2. Go to the workshop folder:
   ```bash
   cd "<workshop folder>"
   ```
3. Run the sequence. The dog's map opens in your browser by itself:
   ```bash
   FF_SIM_OPEN=1 .venv/bin/python spin_and_wave.py
   ```
   (Shortcut: double-click `run_demo.command` in the folder.)
4. Watch the browser. The terminal ends with a report: **PASS** means it's
   ready for a real robot. `run.html` in the folder replays the last run.
5. Other sequences, same way:
   ```bash
   FF_SIM_OPEN=1 .venv/bin/python task1_routine.py
   ```
   ```bash
   FF_SIM_OPEN=1 .venv/bin/python task3_triangle.py
   ```
6. Your laptop webcam as the dog's eyes (wave at the laptop while it looks):
   ```bash
   FF_CAMERA=0 FF_SIM_OPEN=1 .venv/bin/python spin_and_wave.py
   ```
7. Stop a run early: **Ctrl+C**.

## B. Put your sequence on the real robot

**Before you start**

- Your sequence says **PASS** in the simulator (part A).
- You're at a **test station** with a mentor. The mentor holds the remote with
  the **stop button**.
- Ask the mentor for the robot's **user name** and **password**.
- The dog has about **1 metre of empty floor** all around it.

**Step 1. Connect to the robot's Wi-Fi.**
Click the Wi-Fi icon at the top right of your screen and pick the network
that starts with **AEGIS-2A-**. Your internet stops working while you're on
it. That's normal.

**Step 2. Open Terminal.**
Press **Cmd + Space**, type `Terminal`, press **Enter**.

**Step 3. Go to your workshop folder.**
Copy this line, paste it into Terminal, press **Enter**:
```bash
cd "<workshop folder>"
```

**Step 4. Start your sequence on the robot.**
Copy this line and paste it into Terminal. **Before pressing Enter, change
`USER` to the user name the mentor gave you.**
```bash
./robot/run_real.sh USER spin_and_wave.py
```
(For a different sequence, change `spin_and_wave.py` to its file name.)

**Step 5. Type the robot's password.**
Type the password and press **Enter**. Nothing appears while you type; that's
normal. The very first time, it may ask `Are you sure you want to continue
connecting?` first: type `yes` and press **Enter**.

**Step 6. Say go.**
It asks you to confirm the floor is clear. Type `yes` and press **Enter**.
The dog starts, and your browser shows where it's heading.

**Step 7. Stopping.**
Press **Control + C** in Terminal at any time: the dog goes limp. The
mentor's stop button always works too.

**Step 8. When you're done.**
Switch your Wi-Fi back to your normal network.

**If something goes wrong**

- *"Robot not reachable"*: you're not on the robot's Wi-Fi. Do step 1 again.
- *"Permission denied"*: wrong user name or password. Check with the mentor
  and do step 4 again.
- *The dog turns the wrong way*: use this line in step 4 instead:
  ```bash
  FF_GW_YAW_SIGN=-1 ./robot/run_real.sh USER spin_and_wave.py
  ```
- *Want to check the robot's camera first?* (The dog doesn't move; press `q`
  to close the window.)
  ```bash
  .venv/bin/python motion_eyes.py --source rtsp://192.168.234.1:8554/test
  ```

## C. Check with the FF SDK (if it's installed)

macOS (Docker Desktop must be running):
```bash
docker run --rm --platform linux/arm64 -v "$PWD":/w -e FF_TARGET=dry-run ff-sdk python -u task1_routine.py
```
Linux:
```bash
FF_TARGET=dry-run .venv-sdk/bin/python task1_routine.py
```
About 30 s later it prints `[skill] finished on DEMO`: the real SDK accepted
every command. (Camera sequences like spin_and_wave need a real camera, so
check those in the simulator instead.)

## D. Change the sequence

Ask Claude Code in plain words, for example "make it turn in 30° steps", then
run step A3 again to see it.
````

### Cheat sheet: Windows

````markdown
# Aegis workshop cheat sheet

Workshop folder: `<workshop folder>`

## A. Run a sequence in the simulator (any time, anywhere)

1. Open **PowerShell** (Start menu, type "PowerShell", Enter).
2. Go to the workshop folder:
   ```powershell
   cd "<workshop folder>"
   ```
3. Run the sequence. The dog's map opens in your browser by itself:
   ```powershell
   $env:FF_SIM_OPEN=1; .venv\Scripts\python spin_and_wave.py
   ```
   (Shortcut: double-click `run_demo.bat` in the folder.)
4. Watch the browser. The window ends with a report: **PASS** means it's
   ready for a real robot. `run.html` in the folder replays the last run.
5. Other sequences, same way:
   ```powershell
   .venv\Scripts\python task1_routine.py
   ```
   ```powershell
   .venv\Scripts\python task3_triangle.py
   ```
6. Your laptop webcam as the dog's eyes (wave at the laptop while it looks):
   ```powershell
   $env:FF_CAMERA=0; .venv\Scripts\python spin_and_wave.py
   ```
   Settings like `$env:FF_CAMERA` stay on until you close the window; to
   switch back to the simulated camera: `Remove-Item Env:FF_CAMERA`
7. Stop a run early: **Ctrl+C**.

## B. Put your sequence on the real robot

**Before you start**

- Your sequence says **PASS** in the simulator (part A).
- You're at a **test station** with a mentor. The mentor holds the remote with
  the **stop button**.
- Ask the mentor for the robot's **user name** and **password**.
- The dog has about **1 metre of empty floor** all around it.

**Step 1. Connect to the robot's Wi-Fi.**
Click the Wi-Fi icon at the bottom right of your screen and pick the network
that starts with **AEGIS-2A-**. Your internet stops working while you're on
it. That's normal.

**Step 2. Open PowerShell.**
Press the **Windows key**, type `PowerShell`, press **Enter**.

**Step 3. Go to your workshop folder.**
Copy this line, paste it into PowerShell (right-click pastes), press **Enter**:
```powershell
cd "<workshop folder>"
```

**Step 4. Start your sequence on the robot.**
Copy this line and paste it into PowerShell. **Before pressing Enter, change
`USER` to the user name the mentor gave you.**
```powershell
powershell -ExecutionPolicy Bypass -File robot\run_real.ps1 USER spin_and_wave.py
```
(For a different sequence, change `spin_and_wave.py` to its file name.)

**Step 5. Type the robot's password in the new window.**
A second black window opens. Type the password there and press **Enter**.
Nothing appears while you type; that's normal. The very first time, it may
ask `Are you sure you want to continue connecting?` first: type `yes` and
press **Enter**. Leave that window open.

**Step 6. Say go.**
Go back to the first window. It asks you to confirm the floor is clear. Type
`yes` and press **Enter**. The dog starts, and your browser shows where it's
heading.

**Step 7. Stopping.**
Press **Ctrl + C** in the first window at any time: the dog goes limp. The
mentor's stop button always works too.

**Step 8. When you're done.**
Switch your Wi-Fi back to your normal network.

**If something goes wrong**

- *"Robot not reachable"*: you're not on the robot's Wi-Fi. Do step 1 again.
- *"ssh closed (wrong password?)"*: wrong user name or password. Check with
  the mentor and do step 4 again.
- *"ssh not found"*: Settings → System → Optional features → add
  **OpenSSH Client**, then do step 4 again.
- *The dog turns the wrong way*: use this line in step 4 instead:
  ```powershell
  $env:FF_GW_YAW_SIGN=-1; powershell -ExecutionPolicy Bypass -File robot\run_real.ps1 USER spin_and_wave.py
  ```
- *Want to check the robot's camera first?* (The dog doesn't move; press `q`
  to close the window.)
  ```powershell
  .venv\Scripts\python motion_eyes.py --source rtsp://192.168.234.1:8554/test
  ```

## C. Check with the FF SDK (if it's installed)

```powershell
$env:FF_TARGET='dry-run'; .venv-sdk\Scripts\python task1_routine.py; Remove-Item Env:FF_TARGET
```
Ends with `[skill] finished on DEMO`: the real SDK accepted every command.

## D. Change the sequence

Ask Claude Code in plain words, for example "make it turn in 30° steps", then
run step A3 again to see it.
````

### How to vibe code it (tell the user)

They don't need to write code by hand. They tell you what they want the dog
to do, you edit the sequence and rerun it in the simulator (with
`FF_SIM_OPEN=1` so the browser opens), they watch and say what to change.
Repeat until the report says PASS, then take it to a test station
(cheat sheet B). Give them a few prompts to try:

- "Make the dog look around in 30° steps instead of 45°."
- "When it sees motion, shake hands and then lie down."
- "Before shaking hands, turn to face where the motion was."
- "Make a new sequence: walk forward half a metre, turn around, and come back."
- "Make it wiggle left and right three times like a little dance."
- "Use my laptop webcam as the dog's eyes, so it shakes hands when I wave."
- "Explain what each line of `routine(dog)` in spin_and_wave.py does."

And show the four lines everything is built from:

```python
await dog.motion.stand()                    # stand up
await dog.motion.cmd_vel(angular=0.3)       # turn left at 0.3 rad/s (about 17° a second)
await dog.motion.stop()                     # stop
await dog.motion.do_preset("shake_hand")    # offer a paw
```

New sequence files go in the workshop folder next to `spin_and_wave.py`, so
`run_real.sh` / `run_real.ps1` can find them.

## Rules to keep (tell the user these)

- **Speed limits:** walking at most 0.3 m/s, turning at most 0.6 rad/s. The
  simulator plays the mentor: it stops the dog and fails the run if a sequence
  goes faster. Don't raise the limits, even if asked.
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
| `run_demo.command` "can't be opened" (macOS) | `chmod +x run_demo.command`, or right-click it → Open |
| Webcam window doesn't open (macOS) | System Settings → Privacy & Security → Camera → allow the terminal app |
| `?` instead of ✓ or → (Windows) | Harmless. Run `chcp 65001` first for proper symbols |
| `docker: command not found` after installing | `export PATH="$PATH:/Applications/Docker.app/Contents/Resources/bin"`, or finish Docker's first-run window |
| 🚨 SDK install can't find `ff_sdk` | The SDK isn't directly in the workshop folder: `aegis_devkit/wheels/` must be next to `START_HERE.md` |
