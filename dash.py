"""The face. A local page showing what Zetsu is doing, at a glance.

The hero is a WebGL ring ported from the React `voice-dictator` component —
same Bayer-dithered shader, same breathing, but vanilla JS so this project
stays dependency-free. It is driven by state/live.json, which the conversation
loop writes as it moves between listening, thinking and speaking.

Everything below the hero reads the same files everything else writes.

Run:  ./.venv/bin/python zetsu.py --dash    then open http://localhost:8765
"""

import html
import json
import os
import re
import signal
import subprocess
import sys
from datetime import date, datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import heartbeat
import live
import memory
import rails
import tools
from config import CONFIG, NAME, ROOT as ROOT_DIR, STATE as STATE_DIR

COST = re.compile(r"claude-cli \$([0-9.]+)")

PAGE = r"""<!doctype html>
<html lang="en"><head>
<meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>__NAME__</title>
<style>
  :root {
    --ink: oklch(22% 0.02 260); --ink-soft: oklch(52% 0.02 260);
    --paper: oklch(97.5% 0.008 90); --card: oklch(100% 0 0);
    --rule: oklch(89% 0.01 260); --live: oklch(58% 0.15 155);
    --halt: oklch(60% 0.19 25); --accent: oklch(52% 0.16 265);
    --mono: ui-monospace, SFMono-Regular, Menlo, monospace;
  }
  * { box-sizing: border-box; }
  body { margin:0; background:var(--paper); color:var(--ink);
    font:400 15px/1.55 ui-sans-serif,-apple-system,system-ui,sans-serif; }

  /* --- hero ------------------------------------------------------------ */
  .stage { position:relative; height:min(74vh,620px); background:#000; color:#fff;
    overflow:hidden; display:grid; place-items:center; }
  .stage canvas { position:absolute; inset:0; width:100%; height:100%; }
  .core { position:absolute; top:50%; left:50%; width:30px; height:30px;
    margin:-15px 0 0 -15px; border-radius:50%; background:rgba(255,255,255,.85);
    transition:background .5s ease; }
  .say { position:absolute; bottom:clamp(2.5rem,9vh,5rem); z-index:2; width:min(46rem,88vw);
    text-align:center; font-size:clamp(1rem,2.4vw,1.35rem); line-height:1.5;
    color:rgba(255,255,255,.78); text-wrap:pretty; min-height:3em; }
  .phase { position:absolute; top:clamp(1.5rem,5vh,2.5rem); z-index:2;
    font-family:var(--mono); font-size:.72rem; letter-spacing:.22em;
    text-transform:uppercase; color:rgba(255,255,255,.55); transition:color .4s; }
  .loader { position:absolute; bottom:clamp(1rem,4vh,2rem); z-index:2;
    width:min(22rem,70vw); opacity:0; transition:opacity .35s; }
  .loader.on { opacity:1; }
  .bar { height:2px; background:rgba(255,255,255,.16); overflow:hidden; }
  .bar i { display:block; height:100%; width:0; background:#fff; transition:width .25s linear; }
  .clock { display:flex; justify-content:space-between; margin-top:.5rem;
    font-family:var(--mono); font-size:.68rem; color:rgba(255,255,255,.5); }
  .veil { position:absolute; inset:auto 0 0 0; height:45%; z-index:1; pointer-events:none;
    background:linear-gradient(to top,#000,rgba(0,0,0,.45),transparent); }

  .controls { position:absolute; top:clamp(1.2rem,4vh,2rem); right:clamp(1.2rem,4vw,2.5rem);
    z-index:3; text-align:right; }
  .micstate { margin:0 0 .6rem; font-family:var(--mono); font-size:.7rem;
    letter-spacing:.18em; text-transform:uppercase; color:rgba(255,255,255,.45); }
  .micstate::before { content:"●"; margin-right:.45rem; font-size:.9em; }
  .micstate.live { color:oklch(78% 0.17 155); }
  .micstate.muted { color:oklch(80% 0.16 75); }
  .buttons { display:flex; gap:.4rem; }
  .buttons button { font:inherit; font-size:.8rem; letter-spacing:.02em; cursor:pointer;
    padding:.45rem .85rem; border-radius:999px; color:rgba(255,255,255,.75);
    background:rgba(255,255,255,.06); border:1px solid rgba(255,255,255,.16);
    transition:background .18s ease, color .18s ease, border-color .18s ease, opacity .18s; }
  .buttons button:hover:not(:disabled) { background:rgba(255,255,255,.14); color:#fff; }
  .buttons button:active:not(:disabled) { transform:translateY(1px); }
  .buttons button:disabled { opacity:.3; cursor:default; }
  .buttons button.armed { background:oklch(62% 0.17 155); border-color:transparent; color:#04140b; font-weight:600; }
  .buttons button.warn  { background:oklch(78% 0.16 75);  border-color:transparent; color:#1a1204; font-weight:600; }
  .buttons button:focus-visible { outline:2px solid #fff; outline-offset:2px; }

  /* --- everything below ------------------------------------------------ */
  main { max-width:940px; margin:0 auto; padding:clamp(2rem,5vw,3.5rem) 1.5rem 5rem; }
  header { display:flex; align-items:baseline; gap:1rem; flex-wrap:wrap;
    border-bottom:2px solid var(--ink); padding-bottom:.75rem; margin-bottom:2rem; }
  h1 { font-size:clamp(1.6rem,4vw,2.4rem); letter-spacing:-.03em; margin:0; font-weight:600; }
  .state { font-family:var(--mono); font-size:.72rem; letter-spacing:.1em;
    text-transform:uppercase; padding:.3rem .6rem; border-radius:2px; color:#fff; }
  .on { background:var(--live); } .off { background:var(--halt); }
  .meta { margin-left:auto; font-family:var(--mono); font-size:.8rem; color:var(--ink-soft); }
  h2 { font-size:.78rem; letter-spacing:.13em; text-transform:uppercase;
    color:var(--ink-soft); font-weight:600; margin:2.5rem 0 .75rem; }
  .lede { font-size:clamp(1.05rem,2.4vw,1.3rem); line-height:1.45; margin:0 0 1rem;
    color:var(--ink-soft); max-width:34em; }
  .lede b { color:var(--ink); font-weight:600; }
  ul { list-style:none; padding:0; margin:0; }
  li { padding:.6rem 0; border-bottom:1px solid var(--rule); }
  li:last-child { border-bottom:0; }
  .done { color:var(--ink-soft); text-decoration:line-through; }
  .empty { color:var(--ink-soft); font-style:italic; padding:.6rem 0; }
  .notice { background:var(--card); border-left:3px solid var(--accent);
    padding:.7rem 1rem; margin-bottom:.5rem; }
  pre { background:var(--card); border:1px solid var(--rule); border-radius:3px;
    padding:1rem; overflow-x:auto; font-family:var(--mono); font-size:.78rem;
    line-height:1.7; margin:0; }
  .cols { display:grid; gap:clamp(1.5rem,4vw,3rem);
    grid-template-columns:repeat(auto-fit,minmax(260px,1fr)); }
  b.tag { font-family:var(--mono); font-size:.72rem; color:var(--accent); }
  @media (prefers-reduced-motion: reduce) { .core { transition:none; } }
</style></head>
<body>

<section class="stage" aria-label="Live status">
  <canvas id="ring"></canvas>
  <div class="core" id="core"></div>
  <p class="phase" id="phase">idle</p>
  <p class="say" id="say">__IDLE_TEXT__</p>
  <div class="veil"></div>
  <div class="controls" id="controls">
    <p class="micstate" id="micstate">mic stopped</p>
    <div class="buttons">
      <button type="button" data-do="start" id="b-start">▶ Start</button>
      <button type="button" data-do="mute"  id="b-mute">◼ Mute</button>
      <button type="button" data-do="stop"  id="b-stop">✕ Stop</button>
    </div>
  </div>
  <div class="loader" id="loader">
    <div class="bar"><i id="fill"></i></div>
    <div class="clock"><span id="elapsed">0.0s</span><span id="typical"></span></div>
  </div>
</section>

<main>
<header>
  <h1>__NAME__</h1>
  <span class="state __CLS__">__STATE__</span>
  <span class="meta">__BACKEND__ · __COST__ today · __WHEN__</span>
</header>
<p class="lede">__LEDE__</p>
<section><h2>Waiting to be said</h2>__NOTICES__</section>
<div class="cols">
  <section><h2>Todos</h2><ul>__TODOS__</ul></section>
  <section><h2>What it remembers about you</h2><ul>__FACTS__</ul></section>
</div>
<section><h2>Audit trail — everything it did, and why</h2><pre>__AUDIT__</pre></section>
</main>

<script>
// Ported from the voice-dictator React component: same Bayer-dithered ring and
// amplitude smoothing, driven by real phases instead of a faux transcript.
const VERT = `attribute vec2 aPosition;
void main(){ gl_Position = vec4(aPosition,0.0,1.0); }`;

const FRAG = `precision highp float;
uniform float uTime; uniform float uAmplitude; uniform vec2 uResolution; uniform vec3 uColor;
float bayerDither(vec2 coord){
  vec2 p = floor(mod(coord,8.0)); float x=p.x, y=p.y;
  float index = 1.0*mod(x,2.0) + 2.0*mod(y,2.0) + 4.0*mod(floor(x/2.0),2.0)
    + 8.0*mod(floor(y/2.0),2.0) + 16.0*mod(floor(x/4.0),2.0) + 32.0*mod(floor(y/4.0),2.0);
  return (index+0.5)/64.0;
}
void main(){
  vec2 n = gl_FragCoord.xy/uResolution; vec2 uv = n*2.0-1.0;
  uv.x *= uResolution.x/uResolution.y;
  float t = uTime*0.5; float a = clamp(uAmplitude,0.0,1.2);
  float radius = 0.21 + a*0.11 + sin(t*0.9)*0.012;
  float thickness = 0.07 + a*0.05 + sin(t*0.63)*0.009;
  float d = length(uv);
  float ring = smoothstep(radius+thickness,radius,d) - smoothstep(radius,radius-thickness,d);
  float glow = exp(-14.0*abs(d-radius));
  float halo = exp(-6.5*d*(1.0+a*0.35));
  float intensity = clamp(ring*0.75 + glow*0.5 + halo*0.08, 0.0, 1.0);
  float shade = step(bayerDither(gl_FragCoord.xy), intensity);
  gl_FragColor = vec4(uColor*shade, 1.0);
}`;

// phase -> how it looks and how it moves. Idle is deliberately still.
const MOODS = {
  idle:         { colour:[0.88,0.88,0.90], base:0.05, pulse:0,    label:"idle",
                  say:"__IDLE_TEXT__" },
  listening:    { colour:[0.42,0.86,1.00], base:0.30, pulse:0.65, label:"listening",
                  say:"Go ahead — I'm listening." },
  transcribing: { colour:[0.70,0.62,1.00], base:0.28, pulse:0.12, label:"hearing you",
                  say:"Working out what you said…" },
  thinking:     { colour:[1.00,0.72,0.30], base:0.16, pulse:0.22, label:"thinking",
                  say:"Thinking about it…" },
  working:      { colour:[1.00,0.60,0.35], base:0.22, pulse:0.28, label:"using a tool",
                  say:"Checking something…" },
  speaking:     { colour:[0.45,0.95,0.62], base:0.34, pulse:0.42, label:"replying",
                  say:"Answering…" },
};

const canvas = document.getElementById("ring");
const gl = canvas.getContext("webgl", { antialias:false, premultipliedAlpha:false });
let amp = 0.05, target = 0.05, colour = MOODS.idle.colour.slice(), wantColour = colour.slice();
let mood = MOODS.idle, phase = "idle", since = 0, typical = 0;

if (gl) {
  const compile = (type, src) => {
    const sh = gl.createShader(type); gl.shaderSource(sh, src); gl.compileShader(sh);
    if (!gl.getShaderParameter(sh, gl.COMPILE_STATUS)) throw new Error(gl.getShaderInfoLog(sh));
    return sh;
  };
  const prog = gl.createProgram();
  gl.attachShader(prog, compile(gl.VERTEX_SHADER, VERT));
  gl.attachShader(prog, compile(gl.FRAGMENT_SHADER, FRAG));
  gl.linkProgram(prog); gl.useProgram(prog);

  const buf = gl.createBuffer();
  gl.bindBuffer(gl.ARRAY_BUFFER, buf);
  gl.bufferData(gl.ARRAY_BUFFER, new Float32Array([-1,-1,1,-1,-1,1,-1,1,1,-1,1,1]), gl.STATIC_DRAW);
  const loc = gl.getAttribLocation(prog, "aPosition");
  gl.enableVertexAttribArray(loc); gl.vertexAttribPointer(loc, 2, gl.FLOAT, false, 0, 0);

  const U = { t:gl.getUniformLocation(prog,"uTime"), a:gl.getUniformLocation(prog,"uAmplitude"),
              r:gl.getUniformLocation(prog,"uResolution"), c:gl.getUniformLocation(prog,"uColor") };

  const resize = () => {
    const rect = canvas.getBoundingClientRect(), dpr = window.devicePixelRatio || 1;
    const w = Math.round(rect.width*dpr), h = Math.round(rect.height*dpr);
    if (canvas.width !== w || canvas.height !== h) { canvas.width = w; canvas.height = h; }
    gl.viewport(0, 0, w, h);
  };

  const core = document.getElementById("core");
  const draw = (ms) => {
    requestAnimationFrame(draw);
    resize();
    gl.clearColor(0,0,0,1); gl.clear(gl.COLOR_BUFFER_BIT);
    amp += (target - amp) * 0.05;                       // the same 0.95 smoothing
    for (let i = 0; i < 3; i++) colour[i] += (wantColour[i] - colour[i]) * 0.06;
    gl.uniform1f(U.t, ms*0.001); gl.uniform1f(U.a, amp);
    gl.uniform2f(U.r, gl.drawingBufferWidth, gl.drawingBufferHeight);
    gl.uniform3f(U.c, colour[0], colour[1], colour[2]);
    core.style.transform = `scale(${1 + amp*0.5})`;
    core.style.background = `rgba(${colour.map(v=>Math.round(v*255)).join(",")},.9)`;
    core.style.boxShadow = `0 0 ${(12+amp*90).toFixed(1)}px rgba(255,255,255,${(0.08+amp*0.25).toFixed(3)})`;
    gl.drawArrays(gl.TRIANGLES, 0, 6);
  };
  requestAnimationFrame(draw);

  // the breath: idle sits still, everything else pulses at its own rate
  setInterval(() => {
    target = mood.pulse === 0 ? mood.base : mood.base + Math.random() * mood.pulse;
  }, 150);
}

const $ = (id) => document.getElementById(id);

const send = async (action) => {
  for (const b of document.querySelectorAll(".buttons button")) b.disabled = true;
  try { await fetch("/control", { method:"POST", body: action }); } catch (e) {}
  await poll();
};
document.getElementById("controls").addEventListener("click", (event) => {
  const button = event.target.closest("button[data-do]");
  if (!button) return;
  const action = button.dataset.do === "mute" && button.classList.contains("warn")
    ? "unmute" : button.dataset.do;
  send(action);
});

const paintControls = (mic) => {
  const start = $("b-start"), mute = $("b-mute"), stop = $("b-stop");
  const label = $("micstate");
  label.className = "micstate" + (mic === "listening" ? " live" : mic === "muted" ? " muted" : "");
  label.textContent = mic === "listening" ? "mic live — say the wake word"
                    : mic === "muted" ? "muted — not hearing you"
                    : "mic stopped";
  start.disabled = mic !== "stopped";
  mute.disabled = mic === "stopped";
  stop.disabled = mic === "stopped";
  start.classList.toggle("armed", mic === "listening");
  mute.classList.toggle("warn", mic === "muted");
  mute.textContent = mic === "muted" ? "◼ Unmute" : "◼ Mute";
};
const poll = async () => {
  try {
    const s = await (await fetch("/state", { cache:"no-store" })).json();
    paintControls(s.mic);
    if (s.phase !== phase) { phase = s.phase; since = Date.now() - (s.elapsed*1000); }
    mood = MOODS[s.phase] || MOODS.idle;
    wantColour = mood.colour;
    typical = s.typical_seconds || 0;
    $("phase").textContent = mood.label + (s.backend && s.phase !== "idle" ? " · " + s.backend : "");
    $("phase").style.color = `rgba(${mood.colour.map(v=>Math.round(v*255)).join(",")},.8)`;
    $("say").textContent = s.detail || mood.say;
    const busy = ["thinking","working","transcribing"].includes(s.phase);
    $("loader").classList.toggle("on", busy);
    if (busy) {
      const secs = s.elapsed;
      $("elapsed").textContent = secs.toFixed(1) + "s";
      $("typical").textContent = typical ? "usually ~" + typical.toFixed(1) + "s" : "first run…";
      const done = typical ? Math.min(secs / typical, 1) : Math.min(secs / 8, 0.95);
      $("fill").style.width = (done * 100).toFixed(0) + "%";
      if (typical && secs > typical * 1.5) $("typical").textContent = "slower than usual…";
    }
  } catch (e) { $("phase").textContent = "dashboard offline"; }
};
poll(); setInterval(poll, 300);
// the page below changes far more slowly than the ring does
setInterval(() => { if (phase === "idle") location.reload(); }, 15000);
</script>
</body></html>"""

IDLE_TEXT = "Not listening. Start a session and this ring wakes up."


def spent_today():
    if not rails.LOG.exists():
        return "$0.00"
    today = date.today().isoformat()
    total = sum(
        float(found.group(1))
        for line in rails.LOG.read_text().splitlines()
        if line.startswith(today)
        for found in [COST.search(line)]
        if found
    )
    return f"${total:.2f}"


def state_json():
    import time

    current = live.read()
    phase = current.get("phase", "idle")
    backend = current.get("backend", "")
    listening = live.mic_pid() is not None
    return {
        "mic": "muted" if (listening and rails.is_paused()) else
               ("listening" if listening else "stopped"),
        "phase": phase,
        "detail": current.get("detail", ""),
        "backend": backend,
        "elapsed": max(0.0, time.time() - current.get("since", 0)) if phase != "idle" else 0.0,
        "typical_seconds": current.get("typical", {}).get(backend, 0),
    }


def control(action):
    """Start, mute or stop the open mic. Localhost only, so no auth beyond that.

    Mute is deliberately the same kill switch as everywhere else — one question,
    one answer. Muted means the loop keeps running but takes no audio in.
    """
    running = live.mic_pid()
    if action == "start":
        if running:
            return {"ok": True, "note": "already listening"}
        logfile = (STATE_DIR / "wake.log").open("a")
        subprocess.Popen(
            [sys.executable, "-u", str(ROOT_DIR / "zetsu.py"), "--wake"],
            cwd=str(ROOT_DIR), stdout=logfile, stderr=logfile,
            stdin=subprocess.DEVNULL, start_new_session=True,
        )
        rails.resume()
        rails.log("MIC", "started from the dashboard")
        return {"ok": True}
    if action == "stop":
        if running:
            os.kill(running, signal.SIGTERM)
            rails.log("MIC", "stopped from the dashboard")
        live.set_phase("idle")
        return {"ok": True}
    if action == "mute":
        rails.pause("muted from the dashboard")
        live.set_phase("idle")
        return {"ok": True}
    if action == "unmute":
        rails.resume()
        return {"ok": True}
    return {"ok": False, "note": "unknown action"}


def render():
    paused = rails.is_paused()
    items = tools.load_todos()
    facts = memory.facts()
    queued = heartbeat.load_notices()
    open_count = sum(1 for i in items if not i["done"])

    lede = (
        f"<b>{open_count}</b> open todo{'' if open_count == 1 else 's'}, "
        f"<b>{len(facts)}</b> thing{'' if len(facts) == 1 else 's'} remembered about you, "
        f"<b>{len(queued)}</b> notice{'' if len(queued) == 1 else 's'} waiting. "
        + (
            "Proactive work is stopped — it still answers when spoken to."
            if paused
            else "Running normally."
        )
    )

    swaps = {
        "__NAME__": html.escape(NAME),
        "__IDLE_TEXT__": IDLE_TEXT,
        "__STATE__": "paused" if paused else "live",
        "__CLS__": "off" if paused else "on",
        "__BACKEND__": html.escape(CONFIG["model"]["backend"]),
        "__COST__": spent_today(),
        "__WHEN__": datetime.now().strftime("%H:%M:%S"),
        "__LEDE__": lede,
        "__NOTICES__": "".join(
            f'<p class="notice">{html.escape(n["text"])}</p>' for n in queued
        )
        or '<p class="empty">Nothing pending.</p>',
        "__TODOS__": "".join(
            f'<li class="{"done" if i["done"] else ""}">{html.escape(i["text"])}'
            + (f' <b class="tag">due {html.escape(str(i["due"]))}</b>' if i.get("due") else "")
            + "</li>"
            for i in items
        )
        or '<li class="empty">Nothing on the list.</li>',
        "__FACTS__": "".join(f"<li>{html.escape(f)}</li>" for f in facts)
        or '<li class="empty">Nothing remembered yet.</li>',
        "__AUDIT__": html.escape(rails.tail(CONFIG["dash"]["audit_lines"])),
    }
    page = PAGE
    for token, value in swaps.items():
        page = page.replace(token, value)
    return page


class Handler(BaseHTTPRequestHandler):
    def do_GET(self):
        if self.path.startswith("/state"):
            body = json.dumps(state_json()).encode()
            kind = "application/json"
        else:
            body = render().encode()
            kind = "text/html; charset=utf-8"
        self.send_response(200)
        self.send_header("Content-Type", kind)
        self.send_header("Cache-Control", "no-store")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_POST(self):
        if not self.path.startswith("/control"):
            self.send_error(404)
            return
        size = int(self.headers.get("Content-Length", 0))
        action = self.rfile.read(size).decode().strip()
        body = json.dumps(control(action)).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *_):
        pass  # the audit log is the log; this would just be noise


def serve():
    port = CONFIG["dash"]["port"]
    # localhost only: this page shows your todos, your memory and your audit
    # trail. It has no business being reachable from the network.
    server = ThreadingHTTPServer(("127.0.0.1", port), Handler)
    print(f"{NAME} dashboard: http://localhost:{port}   (Ctrl-C to stop)")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\ndashboard stopped.")
