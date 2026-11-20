"""
Web control panel for the Aegis — runs on the robot, used from a browser.

Join the robot's hotspot and open http://192.168.234.1:8000 on a phone or
laptop: Stand / Lie down / Go limp, FF's built-in tricks, Square, hold-to-drive
arrows, and a STOP button that goes limp and blocks further moves until Reset.

Commands go through FF's control gateway on the robot (127.0.0.1:8768, see
gateway.py), which owns the native control link. Stdlib only, plain python3.

Run on the robot (run_on_robot.sh copies it to ~/ff_app):
    python3 ~/ff_app/panel.py

Env: PANEL_HOST (default 192.168.234.1, the hotspot only — the robot is also
on office Wi-Fi and the panel shouldn't be reachable from there), PANEL_PORT
(default 8000), AEGIS_GATEWAY (default http://127.0.0.1:8768).

Nothing moves until someone presses a button. STOP goes over Wi-Fi and the
gateway may finish a running trick first: the remote's e-stop is the real one.
"""

import asyncio
import json
import os
import signal
import threading

import gateway as gw

HOST = os.environ.get("PANEL_HOST", "192.168.234.1")
PORT = int(os.environ.get("PANEL_PORT", "8000"))
DRIVE_STEP_S = 0.6    # each move lasts this long; resent while the button is held
# Chain moves without the gateway's default 0.15 s warm-up and 0.7 s pause, or
# short moves never ramp the (emulated) stick far enough to walk. Keep a short
# ease back to neutral so the dog never keeps walking on a stale frame.
DRIVE_TIMING = {"warmup": 0.0, "ramp": 0.15, "neutral_end": 0.1, "post_wait": 0.0}
DRIVE_HOLD_S = 0.35   # browser resends every 100 ms; stop if it goes quiet
# The gateway turns these into an emulated gamepad stick (x0.6 forward/turn,
# x0.5 sideways). The dog only walked once the stick passed ~0.4: forward
# (vx 0.75 -> stick 0.45) walks, move vx 0.3 (stick 0.35) doesn't. The page's
# slider therefore runs from these minimums up to full stick.
MAX = {"vx": 1.0, "vy": 1.0, "yaw": 1.0}

ACTIONS = {
    "stand": gw.stand,
    "lie": gw.lie,
    "greet": gw.ACTIONS["greet"],
    "sitdown": gw.ACTIONS["sitdown"],
    "shakehand": gw.ACTIONS["shakehand"],
    "stretchleg": gw.ACTIONS["stretchleg"],
    "frontjump": gw.ACTIONS["frontjump"],
    "dance": gw.dance,
    "recover": gw.ACTIONS["recover"],
    "low_speed": gw.ACTIONS["low_speed"],
    "mid_speed": gw.ACTIONS["mid_speed"],
    "high_speed": gw.ACTIONS["high_speed"],
    "square": gw.square,
    "wiggle": gw.wiggle,
}


def clamp(v, lim):
    return max(-lim, min(lim, float(v)))


def find_battery(obj):
    """The gateway's /feedback shape isn't documented: look for a battery-ish number."""
    if isinstance(obj, dict):
        for k, v in obj.items():
            if any(w in k.lower() for w in ("battery", "soc", "electric", "power_percent")):
                if isinstance(v, (int, float)):
                    return round(v * 100) if 0 <= v <= 1 else round(v)
            found = find_battery(v)
            if found is not None:
                return found
    elif isinstance(obj, list):
        for v in obj:
            found = find_battery(v)
            if found is not None:
                return found
    return None


class Panel:
    def __init__(self):
        self.task = None
        self.task_name = None
        self.cancel = threading.Event()
        self.latched = False
        self.last_error = None
        self.drive_vel = {}
        self.drive_until = 0.0
        self.driving = False
        self.gw_state = {"gateway": False, "last_cmd": None, "battery": None}
        self.gw_failures = 0

    def can_move(self):
        if self.latched:
            return "STOP is latched - press Reset first"
        return None

    # ------------------------------------------------------------ actions

    def start(self, name):
        fn = ACTIONS.get(name)
        if fn is None:
            return 404, f"unknown action {name}"
        why = self.can_move()
        if why:
            return 409, why
        if self.task:
            return 409, f"busy running {self.task_name}"
        self.drive_until = 0.0
        self.last_error = None
        self.cancel.clear()
        self.task_name = name
        self.task = asyncio.create_task(self._run(name, fn))
        return 200, f"started {name}"

    async def _run(self, name, fn):
        try:
            await asyncio.to_thread(fn, stop=self.cancel.is_set)
            print(f"{name}: done")
        except gw.Stopped:
            print(f"{name}: stopped")
        except Exception as e:
            self.last_error = f"{name}: {e}"
            print(f"{name} failed: {e} - going limp")
            await self._limp()
        finally:
            self.task = None
            self.task_name = None

    async def _limp(self):
        try:
            await asyncio.to_thread(gw.limp)
        except Exception as e:
            self.last_error = f"limp failed: {e}"
            print(self.last_error)

    async def limp(self):
        """Stop whatever runs (between steps) and go limp, without latching."""
        self.drive_until = 0.0
        self.cancel.set()
        await self._limp()
        return 200, "limp"

    async def stop(self):
        """STOP: go limp and refuse every move until Reset."""
        self.latched = True
        await self.limp()
        return 200, "stopped - press Reset when the area is safe"

    def reset(self):
        self.latched = False
        self.last_error = None
        return 200, "reset"

    # -------------------------------------------------------------- drive

    def drive(self, body):
        why = self.can_move()
        if why:
            return 409, why
        if self.task:
            return 409, f"busy running {self.task_name}"
        self.drive_vel = {k: clamp(body.get(k, 0), lim) for k, lim in MAX.items()}
        moving = any(self.drive_vel.values())
        loop = asyncio.get_running_loop()
        self.drive_until = loop.time() + DRIVE_HOLD_S if moving else 0.0
        return 200, "ok"

    async def drive_loop(self):
        """Resend short moves while the button is held; each ends by itself."""
        loop = asyncio.get_running_loop()
        while True:
            if self.drive_until > loop.time() and not self.task and not self.latched:
                self.driving = True
                try:
                    await asyncio.to_thread(gw.move, duration=DRIVE_STEP_S, **DRIVE_TIMING, **self.drive_vel)
                except Exception as e:
                    self.drive_until = 0.0
                    self.last_error = f"drive: {e}"
                    print(self.last_error)
            else:
                self.driving = False
                await asyncio.sleep(0.05)

    # ------------------------------------------------------------- status

    async def poll_gateway(self):
        """Keep a cached gateway state. The gateway answers one request at a
        time, so polling it while a move or trick runs only times out and
        delays the commands: pause then, and only call it down after a few
        failures in a row."""
        n = 0
        while True:
            await asyncio.sleep(2.0)
            if self.task or self.driving:
                continue
            try:
                st = await asyncio.to_thread(gw.status, 3)
                self.gw_failures = 0
                self.gw_state.update(
                    gateway=bool(st.get("connected") and st.get("connection_ok")),
                    last_cmd=st.get("last_cmd"), gateway_error=None)
            except Exception as e:
                self.gw_failures += 1
                if self.gw_failures >= 3:
                    self.gw_state.update(gateway=False, gateway_error=str(e))
                continue
            n += 1
            if n % 5 == 1:   # battery: first poll, then every ~10 s
                try:
                    self.gw_state["battery"] = find_battery(await asyncio.to_thread(gw.feedback, 3))
                except Exception:
                    pass

    async def status(self):
        return {"latched": self.latched, "busy": self.task_name, "driving": self.driving,
                "error": self.last_error, **self.gw_state}


# ------------------------------------------------------------------ http

async def handle(panel, reader, writer):
    try:
        head = await asyncio.wait_for(reader.readuntil(b"\r\n\r\n"), 5)
        lines = head.decode("latin-1").split("\r\n")
        method, path, _ = lines[0].split(" ", 2)
        headers = {k.lower(): v.strip() for k, v in
                   (l.split(":", 1) for l in lines[1:] if ":" in l)}
        n = int(headers.get("content-length", "0") or 0)
        body = json.loads(await reader.readexactly(n) or b"{}") if n else {}
        code, payload = await route(panel, method, path, body)
    except Exception as e:
        code, payload = 400, f"bad request: {e}"

    if isinstance(payload, str) and payload.startswith("<!doctype"):
        ctype, data = "text/html; charset=utf-8", payload.encode()
    else:
        if isinstance(payload, str):
            payload = {"ok": code == 200, "message": payload}
        ctype, data = "application/json", json.dumps(payload).encode()
    reason = {200: "OK", 400: "Bad Request", 404: "Not Found", 409: "Conflict"}.get(code, "")
    writer.write(f"HTTP/1.1 {code} {reason}\r\nContent-Type: {ctype}\r\n"
                 f"Content-Length: {len(data)}\r\nCache-Control: no-store\r\n"
                 f"Connection: close\r\n\r\n".encode() + data)
    try:
        await writer.drain()
    finally:
        writer.close()


async def route(panel, method, path, body):
    if method == "GET" and path in ("/", "/index.html"):
        return 200, PAGE
    if method == "GET" and path == "/api/status":
        return 200, await panel.status()
    if method != "POST":
        return 404, "not found"
    if path == "/api/stop":
        return await panel.stop()
    if path == "/api/limp":
        return await panel.limp()
    if path == "/api/reset":
        return panel.reset()
    if path == "/api/drive":
        return panel.drive(body)
    if path.startswith("/api/action/"):
        return panel.start(path.rsplit("/", 1)[-1])
    return 404, "not found"


async def main():
    panel = Panel()
    try:
        st = await asyncio.to_thread(gw.status)
        print(f"gateway {gw.URL}: connected={st.get('connected')} build={st.get('gateway_build')}")
        panel.gw_state["gateway"] = bool(st.get("connected") and st.get("connection_ok"))
    except Exception as e:
        print(f"WARNING: {e} - buttons will fail until the gateway is up")

    server = await asyncio.start_server(lambda r, w: handle(panel, r, w), HOST, PORT)
    driver = asyncio.create_task(panel.drive_loop())
    poller = asyncio.create_task(panel.poll_gateway())
    print(f"panel on http://{HOST}:{PORT}  (Ctrl+C to quit; the dog goes limp)")

    stop_event = asyncio.Event()
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        loop.add_signal_handler(sig, stop_event.set)
    try:
        await stop_event.wait()
    finally:
        print("shutting down - going limp")
        server.close()
        driver.cancel()
        poller.cancel()
        await panel.limp()


# ------------------------------------------------------------------ page

PAGE = """<!doctype html>
<html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1,user-scalable=no">
<title>Aegis panel</title>
<style>
:root{--bg:#111418;--card:#1c2128;--fg:#e8eaed;--mute:#9aa3ad;--ok:#3fb950;--warn:#d29922;--bad:#f85149;--btn:#2d333b}
*{box-sizing:border-box;-webkit-user-select:none;user-select:none;-webkit-tap-highlight-color:transparent}
body{margin:0;background:var(--bg);color:var(--fg);font:16px/1.4 system-ui,-apple-system,sans-serif;padding:12px 16px 120px}
h1{font-size:18px;margin:0 0 10px}
.bar{display:flex;flex-wrap:wrap;gap:6px;margin-bottom:12px}
.pill{background:var(--card);border-radius:999px;padding:4px 10px;font-size:13px;color:var(--mute)}
.pill b{color:var(--fg);font-weight:600}.ok b{color:var(--ok)}.bad b{color:var(--bad)}.warn b{color:var(--warn)}
section{background:var(--card);border-radius:12px;padding:12px;margin-bottom:12px}
h2{font-size:13px;text-transform:uppercase;letter-spacing:.05em;color:var(--mute);margin:0 0 8px}
.grid{display:grid;grid-template-columns:repeat(3,1fr);gap:8px}
.grid.two{grid-template-columns:repeat(2,1fr)}
button{font:inherit;color:var(--fg);background:var(--btn);border:0;border-radius:10px;padding:14px 6px;cursor:pointer;touch-action:manipulation}
button:active,button.held{filter:brightness(1.5)}
button:disabled{opacity:.35}
.pad{display:grid;grid-template-columns:repeat(3,64px);grid-template-rows:repeat(3,64px);gap:6px;justify-content:center}
.pad button{padding:0;font-size:22px;touch-action:none}
.row{display:flex;align-items:center;gap:10px;margin-top:10px;color:var(--mute);font-size:14px}
.row input{flex:1}
.note{font-size:13px;color:var(--mute);margin:8px 0 0}
#msg{min-height:1.4em;font-size:14px;color:var(--mute);margin:4px 0 0}
#msg.err{color:var(--bad)}
#stop{position:fixed;left:16px;right:16px;bottom:16px;background:var(--bad);color:#fff;font-size:22px;font-weight:700;padding:20px;border-radius:14px;box-shadow:0 4px 20px #0008}
#reset{display:none;width:100%;margin-top:8px;background:var(--warn);color:#111;font-weight:600}
</style></head><body>
<h1>Aegis panel</h1>
<div class="bar">
  <span class="pill" id="p-gw">gateway <b>…</b></span>
  <span class="pill" id="p-batt">battery <b>…</b></span>
  <span class="pill" id="p-busy">doing <b>…</b></span>
  <span class="pill" id="p-last">last <b>…</b></span>
</div>
<p id="msg"></p>
<button id="reset" onclick="post('/api/reset')">STOP latched: Reset (only when the area is safe)</button>

<section><h2>Basics</h2><div class="grid">
  <button data-act="stand">Stand</button>
  <button data-act="lie">Lie down</button>
  <button onclick="post('/api/limp')">Go limp</button>
  <button data-act="recover">Recover</button>
</div>
<p class="note">Go limp turns the motors off, so the dog slumps to the floor. Recover gets it back up after a fall.</p></section>

<section><h2>Tricks</h2><div class="grid two">
  <button data-act="greet" data-moves>Greet</button>
  <button data-act="sitdown" data-moves>Sit down</button>
  <button data-act="shakehand" data-moves>Shake hand</button>
  <button data-act="stretchleg" data-moves>Stretch leg</button>
  <button data-act="dance" data-moves>Dance</button>
  <button data-act="frontjump" data-moves data-big>Front jump</button>
</div></section>

<section><h2>Moves</h2><div class="grid two">
  <button data-act="square" data-moves>Square</button>
  <button data-act="wiggle" data-moves>Wiggle</button>
</div></section>

<section><h2>Walking speed (for driving and moves)</h2><div class="grid">
  <button data-act="low_speed">Low</button>
  <button data-act="mid_speed">Mid</button>
  <button data-act="high_speed">High</button>
</div></section>

<section><h2>Drive: hold a button (keys: arrows move/turn, Q E sidestep, Space stops)</h2>
  <div class="pad">
    <button data-d="0,1,0">&#8634;</button><button data-d="1,0,0">&#9650;</button><button data-d="0,-1,0">&#8635;</button>
    <button data-d="0,0,1">&#9664;</button><span></span><button data-d="0,0,-1">&#9654;</button>
    <span></span><button data-d="-1,0,0">&#9660;</button><span></span>
  </div>
  <div class="row">slow<input id="speed" type="range" min="0" max="1" step="0.05" value="0">fast</div>
</section>

<button id="stop" onclick="post('/api/stop')">STOP</button>

<script>
const $=s=>document.querySelector(s), msg=$('#msg');
let st={}, held=null, timer=null;
function say(t,err){msg.textContent=t||'';msg.className=err?'err':''}
async function post(path,body){
  try{
    const r=await fetch(path,{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(body||{})});
    const j=await r.json(); if(path!=='/api/drive'||!j.ok) say(j.message,!j.ok); refresh();
    return j;
  }catch(e){say('no connection to the panel',true)}
}
function pill(id,label,val,cls){const p=$(id);p.className='pill '+(cls||'');p.innerHTML=label+' <b>'+val+'</b>'}
async function refresh(){
  try{st=await (await fetch('/api/status')).json()}catch(e){pill('#p-gw','panel','offline','bad');return}
  pill('#p-gw','gateway',st.gateway?'up':'DOWN',st.gateway?'ok':'bad');
  pill('#p-batt','battery',st.battery==null?'?':st.battery+'%',st.battery!=null&&st.battery<20?'bad':'');
  pill('#p-busy','doing',st.latched?'STOPPED':(st.busy||(st.driving?'driving':'idle')),st.latched?'bad':(st.busy?'warn':''));
  pill('#p-last','last',st.last_cmd?(st.last_cmd.cmd||JSON.stringify(st.last_cmd)):'-');
  $('#reset').style.display=st.latched?'block':'none';
  const locked=st.latched||!st.gateway;
  document.querySelectorAll('[data-act],[data-d]').forEach(b=>b.disabled=locked||!!st.busy);
  if(st.error) say(st.error,true); else if(st.gateway_error) say(st.gateway_error,true);
}
document.querySelectorAll('[data-act]').forEach(b=>b.onclick=()=>{
  if(b.hasAttribute('data-big')&&!confirm('Front jump: the dog leaps forward. 2 m clear in front, full battery, someone on the e-stop?'))return;
  else if(!b.hasAttribute('data-big')&&b.hasAttribute('data-moves')&&!confirm('The dog will move. Area clear?'))return;
  post('/api/action/'+b.dataset.act);
});
// Minimums that make the dog actually walk (see MAX in panel.py); slider adds up to full stick.
const MIN={vx:0.75,yaw:0.75,vy:0.8};
function sp(axis){const k=+$('#speed').value;return MIN[axis]+(1-MIN[axis])*k}
function send(d){post('/api/drive',{vx:d[0]*sp('vx'),yaw:d[1]*sp('yaw'),vy:d[2]*sp('vy')})}
function press(d,el){if(held)return;held={d,el};el&&el.classList.add('held');send(d);timer=setInterval(()=>send(d),100)}
function release(){if(!held)return;clearInterval(timer);held.el&&held.el.classList.remove('held');held=null;post('/api/drive',{})}
document.querySelectorAll('[data-d]').forEach(b=>{
  const d=b.dataset.d.split(',').map(Number);
  b.addEventListener('pointerdown',e=>{e.preventDefault();b.setPointerCapture(e.pointerId);press(d,b)});
  ['pointerup','pointercancel','lostpointercapture'].forEach(ev=>b.addEventListener(ev,release));
});
const keys={ArrowUp:[1,0,0],ArrowDown:[-1,0,0],ArrowLeft:[0,1,0],ArrowRight:[0,-1,0],q:[0,0,1],e:[0,0,-1]};
addEventListener('keydown',e=>{if(e.key===' '||e.key==='Escape'){e.preventDefault();post('/api/stop');return}
  const d=keys[e.key];if(d&&!e.repeat){e.preventDefault();press(d,document.querySelector('[data-d="'+d.join(',')+'"]'))}});
addEventListener('keyup',e=>{if(keys[e.key])release()});
addEventListener('blur',release);document.addEventListener('visibilitychange',release);
refresh();setInterval(refresh,1000);
</script></body></html>"""


if __name__ == "__main__":
    asyncio.run(main())
