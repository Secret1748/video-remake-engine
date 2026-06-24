#!/usr/bin/env python3
"""
serve.py — a tiny local drag-and-drop UI for video-remake-engine.

No extra installs (stdlib http.server) and nothing leaves your machine. Drop a video,
say whether it has on-screen text, and it runs the engine and shows the variants with the
GREEN gate, players, and download buttons.

    python3 serve.py            # -> http://127.0.0.1:8000
    python3 serve.py --port 9000

Processing a clip takes ~1-2 minutes (it renders + measures both fingerprint layers).
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
import time
import urllib.parse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

HERE = Path(__file__).resolve().parent
RUNS = HERE / ".webruns"
RUNS.mkdir(exist_ok=True)
MAX_UPLOAD = 1024 * 1024 * 1024  # 1 GB

PAGE = """<!doctype html><html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>video-remake-engine</title>
<style>
:root{--bg:#0e0f13;--card:#171922;--line:#262a36;--ink:#e7e9ee;--dim:#9aa2b1;--ok:#27c08a;--warn:#f0b232;--accent:#6ea8fe}
*{box-sizing:border-box}body{margin:0;background:var(--bg);color:var(--ink);font:15px/1.5 -apple-system,BlinkMacSystemFont,"Segoe UI",Roboto,sans-serif}
.wrap{max-width:1040px;margin:0 auto;padding:32px 20px 80px}
h1{font-size:24px;margin:0 0 4px}.sub{color:var(--dim);margin:0 0 24px}
.drop{border:2px dashed var(--line);border-radius:16px;padding:48px 24px;text-align:center;background:var(--card);transition:.15s;cursor:pointer}
.drop.hot{border-color:var(--accent);background:#1b1f2b}
.drop h2{margin:0 0 6px;font-size:18px}.drop p{color:var(--dim);margin:6px 0}
.row{display:flex;gap:16px;align-items:center;flex-wrap:wrap;margin:18px 0}
label.ck{display:flex;gap:9px;align-items:center;background:var(--card);border:1px solid var(--line);padding:10px 14px;border-radius:12px;cursor:pointer}
button{background:var(--accent);color:#06101f;border:0;border-radius:12px;padding:12px 20px;font-weight:600;font-size:15px;cursor:pointer}
button:disabled{opacity:.5;cursor:default}
.note{font-size:12.5px;color:var(--dim);border-left:3px solid var(--line);padding:8px 12px;margin:18px 0;background:#12141c;border-radius:0 8px 8px 0}
.banner{padding:14px 18px;border-radius:12px;font-weight:600;margin:20px 0}
.banner.ok{background:rgba(39,192,138,.12);color:var(--ok);border:1px solid rgba(39,192,138,.35)}
.banner.no{background:rgba(240,178,50,.12);color:var(--warn);border:1px solid rgba(240,178,50,.35)}
.grid{display:grid;grid-template-columns:repeat(auto-fill,minmax(230px,1fr));gap:16px}
.card{background:var(--card);border:1px solid var(--line);border-radius:14px;overflow:hidden}
.card video{width:100%;display:block;background:#000;aspect-ratio:16/9;object-fit:contain}
.card .meta{padding:10px 12px}.card .name{font-weight:600;display:flex;justify-content:space-between;align-items:center}
.pill{font-size:11px;padding:2px 8px;border-radius:99px}.pill.ok{background:rgba(39,192,138,.16);color:var(--ok)}.pill.no{background:rgba(240,178,50,.16);color:var(--warn)}
.stat{font-size:12px;color:var(--dim);margin-top:4px}
.dl{display:inline-block;margin-top:8px;font-size:13px;color:var(--accent);text-decoration:none}
.spin{display:none;text-align:center;color:var(--dim);padding:30px}
.spin.on{display:block}.spin .bar{height:3px;background:var(--line);border-radius:9px;overflow:hidden;margin:16px auto;max-width:320px}
.spin .bar i{display:block;height:100%;width:40%;background:var(--accent);animation:slide 1.1s ease-in-out infinite}
@keyframes slide{0%{margin-left:-40%}100%{margin-left:100%}}
small{color:var(--dim)}
</style></head><body><div class="wrap">
<h1>video-remake-engine</h1>
<p class="sub">One video → 10 fresh variants, each distinct from the source and each other on the video <em>and</em> audio fingerprint.</p>

<div id="drop" class="drop">
  <h2>Drop a video here</h2>
  <p>or click to choose · stays on your machine</p>
  <input id="file" type="file" accept="video/*" hidden>
</div>

<div class="row">
  <label class="ck"><input type="checkbox" id="mirror"> This clip has <strong>no on-screen text/logo</strong> (enable mirror — invisible &amp; lossless)</label>
  <button id="go" disabled>Make 10 variants</button>
  <span id="fname" class="stat"></span>
</div>

<div class="note">For <strong>your own content on your own channels</strong> (reposting fresh, A/B-picking). Not for laundering others' content, impersonation, or spam. GREEN clears two conservative fingerprint proxies — also vary your caption/cover frame and space posts out.</div>

<div id="spin" class="spin"><div>Rendering + measuring both fingerprint layers… ~1–2 min</div><div class="bar"><i></i></div></div>
<div id="out"></div>
</div>
<script>
const file=document.getElementById('file'),drop=document.getElementById('drop'),go=document.getElementById('go'),
fname=document.getElementById('fname'),spin=document.getElementById('spin'),out=document.getElementById('out');
let picked=null;
function set(f){picked=f;go.disabled=!f;fname.textContent=f?(f.name+' · '+(f.size/1048576).toFixed(1)+' MB'):''}
drop.onclick=()=>file.click();
file.onchange=e=>set(e.target.files[0]);
['dragover','dragenter'].forEach(ev=>drop.addEventListener(ev,e=>{e.preventDefault();drop.classList.add('hot')}));
['dragleave','drop'].forEach(ev=>drop.addEventListener(ev,e=>{e.preventDefault();drop.classList.remove('hot')}));
drop.addEventListener('drop',e=>{if(e.dataTransfer.files[0])set(e.dataTransfer.files[0])});
go.onclick=async()=>{
  if(!picked)return; go.disabled=true; out.innerHTML=''; spin.classList.add('on');
  const mirror=document.getElementById('mirror').checked?1:0;
  try{
    const buf=await picked.arrayBuffer();
    const r=await fetch(`/api/spin?mirror=${mirror}&name=${encodeURIComponent(picked.name)}`,
      {method:'POST',body:buf});
    const j=await r.json();
    render(j);
  }catch(err){out.innerHTML='<div class="banner no">Error: '+err+'</div>'}
  spin.classList.remove('on'); go.disabled=false;
};
function render(j){
  if(j.error){out.innerHTML='<div class="banner no">'+j.error+'</div>';return}
  const b=j.green?'<div class="banner ok">🟢 GREEN — all '+j.variants.length+' variants are distinct on both layers (postable as separate content)</div>'
                 :'<div class="banner no">🟡 '+j.green_count+'/'+j.variants.length+' GREEN — ⚠️ rows are still too close; '+(j.audio?'':'(audio layer off — install Chromaprint) ')+'see notes</div>';
  let cards=j.variants.map(v=>`<div class="card">
    <video src="${v.url}" controls preload="metadata" muted loop></video>
    <div class="meta"><div class="name">${v.name}
      <span class="pill ${v.green?'ok':'no'}">${v.green?'✅ distinct':'⚠️ close'}</span></div>
      <div class="stat">video ${v.v_src} src / ${v.v_pair} pair${v.a_src!=null?' · audio '+v.a_src+' / '+v.a_pair:''} · detail ${v.detail}%</div>
      <a class="dl" href="${v.url}" download>↓ download</a></div></div>`).join('');
  out.innerHTML=b+'<div class="grid">'+cards+'</div>';
}
</script></body></html>"""


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *a):  # quiet
        pass

    def _send(self, code, body, ctype="application/json"):
        if isinstance(body, (dict, list)):
            body = json.dumps(body).encode()
        elif isinstance(body, str):
            body = body.encode()
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        path = urllib.parse.urlparse(self.path).path
        if path == "/":
            return self._send(200, PAGE, "text/html; charset=utf-8")
        if path == "/favicon.ico":
            return self._send(204, b"")
        if path.startswith("/runs/"):
            return self._serve_file(path[len("/runs/"):])
        self._send(404, {"error": "not found"})

    def _serve_file(self, rel):
        target = (RUNS / urllib.parse.unquote(rel)).resolve()
        if RUNS.resolve() not in target.parents or not target.is_file():
            return self._send(404, {"error": "not found"})
        ctype = "video/mp4" if target.suffix == ".mp4" else \
            "image/jpeg" if target.suffix in (".jpg", ".jpeg") else "application/octet-stream"
        data = target.read_bytes()
        self.send_response(200)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Accept-Ranges", "none")
        self.end_headers()
        self.wfile.write(data)

    def do_POST(self):
        parsed = urllib.parse.urlparse(self.path)
        if parsed.path != "/api/spin":
            return self._send(404, {"error": "not found"})
        q = urllib.parse.parse_qs(parsed.query)
        mirror = q.get("mirror", ["0"])[0] == "1"
        name = q.get("name", ["clip.mp4"])[0]
        length = int(self.headers.get("Content-Length", 0))
        if length <= 0 or length > MAX_UPLOAD:
            return self._send(400, {"error": "bad upload size"})
        data = self.rfile.read(length)

        run_id = time.strftime("%Y%m%d-%H%M%S")
        run_dir = RUNS / run_id
        run_dir.mkdir(parents=True, exist_ok=True)
        ext = Path(name).suffix or ".mp4"
        in_path = run_dir / f"input{ext}"
        in_path.write_bytes(data)
        out_dir = run_dir / "out"

        cmd = [sys.executable, "spin.py", str(in_path), "--out", str(out_dir)]
        if mirror:
            cmd.append("--allow-mirror")
        proc = subprocess.run(cmd, cwd=HERE, capture_output=True, text=True)
        man_path = out_dir / "manifest.json"
        if not man_path.exists():
            err = (proc.stderr or proc.stdout or "render failed").strip().splitlines()[-3:]
            return self._send(500, {"error": "render failed: " + " ".join(err)})
        man = json.loads(man_path.read_text())

        def url(fn):
            return f"/runs/{run_id}/out/{urllib.parse.quote(fn)}"

        variants = [{
            "id": v["id"], "name": v["name"], "green": v["GREEN"],
            "url": url(v["file"]) if v["file"] else None,
            "v_src": v["video_dist_vs_source"], "v_pair": v["video_dist_vs_nearest_variant"],
            "a_src": v["audio_dist_vs_source"], "a_pair": v["audio_dist_vs_nearest_variant"],
            "detail": v["detail_retained_pct"],
        } for v in man["variants"] if v["ok"]]
        return self._send(200, {
            "green": man["GREEN"], "audio": man["audio_layer"],
            "green_count": sum(1 for v in variants if v["green"]),
            "variants": variants,
        })


def main() -> int:
    ap = argparse.ArgumentParser(description="Local drag-and-drop UI for video-remake-engine.")
    ap.add_argument("--port", type=int, default=8000)
    ap.add_argument("--host", default="127.0.0.1")
    args = ap.parse_args()
    srv = ThreadingHTTPServer((args.host, args.port), Handler)
    print(f"video-remake-engine UI → http://{args.host}:{args.port}  (Ctrl-C to stop)")
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        print("\nbye")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
