"""Read-only local frame viewer. Run with Python; open http://127.0.0.1:8765.

Uses existing inspector outputs; creates no captures, logs or build files.
The browser reconnects across runner/server restarts. Rebuilding/restarting the
game remains the development workflow's responsibility, not a filesystem watcher.
"""
import argparse
import json
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlsplit

RUNTIME = Path(__file__).resolve().parents[1] / "PS2Recomp/out/build/ps2xRuntime"
PAGE = r'''<!doctype html>
<html lang="en"><meta charset="utf-8"><meta name="viewport" content="width=device-width">
<title>PS2Recomp Â· Development viewer</title>
<style>
:root{color-scheme:dark;font:15px system-ui;background:#11151b;color:#eef2f7}
body{margin:0;min-height:100vh;display:flex;flex-direction:column}
header{padding:18px 24px;display:flex;align-items:center;justify-content:space-between;gap:20px;flex-wrap:wrap}
h1{font-size:18px;margin:0}p{color:#a6b3c3;margin:6px 0 0;font-size:13px}
#status{font-size:14px;color:#f4c97d}#status[data-live=true]{color:#9ce3ba}
main{flex:1;display:grid;place-items:center;padding:8px 24px 24px}
img{display:block;max-width:100%;max-height:calc(100vh - 130px);width:min(100%,1024px);height:auto;object-fit:contain;background:#000;box-shadow:0 0 0 1px #303843}
#empty{color:#a6b3c3}footer{padding:0 24px 18px;color:#a6b3c3;font-size:12px}
</style>
<header><div><h1>PS2Recomp</h1><p>Development viewer Â· refreshes automatically</p></div>
<div id="status" role="status">Connectingâ€¦</div></header>
<main><span id="empty">Waiting for the first captured frameâ€¦</span><img id="frame" alt="Latest game frame" hidden style="display:none"></main>
<footer id="detail">Keeps the last frame while the game rebuilds or restarts. No audio or controls.</footer>
<script>
const frame=document.querySelector('#frame'), status=document.querySelector('#status');
const detail=document.querySelector('#detail'), empty=document.querySelector('#empty');
let version='', objectURL=null;
async function refresh(){
 try{
  const reply=await fetch('/status',{cache:'no-store',signal:AbortSignal.timeout(4000)});
  if(!reply.ok)throw Error('status unavailable');
  const state=await reply.json();
  status.dataset.live=String(state.live);
  status.textContent=state.live?'Receiving frames':'Waiting for the game Â· reconnecting automatically';
  detail.textContent=state.pid?`Runner ${state.pid} Â· sample ${state.sequence} Â· capture ${state.age}s ago Â· no audio or controls`:'Waiting for the inspector. This tab can stay open during restarts.';
  if(state.frame && state.frame!==version){
   const image=await fetch('/frame?v='+encodeURIComponent(state.frame),{cache:'no-store',signal:AbortSignal.timeout(4000)});
   if(!image.ok)throw Error('frame unavailable');
   const next=URL.createObjectURL(await image.blob());
   const decoded=new Image();decoded.src=next;
   try{await decoded.decode();}catch(error){URL.revokeObjectURL(next);throw error;}
   const previous=objectURL;objectURL=next;frame.src=next;
   frame.hidden=false;frame.style.display='block';empty.hidden=true;
   if(previous)URL.revokeObjectURL(previous);
   version=state.frame;
  }
 }catch(error){status.dataset.live='false';status.textContent='Reconnectingâ€¦ Â· last frame retained';}
 setTimeout(refresh,1000);
}
refresh();
</script></html>'''


def snapshot():
    try:
        data = json.loads((RUNTIME / "inspector.json").read_bytes())
        if not isinstance(data, dict):
            raise ValueError("Invalid inspector report")
        current = data.get("snapshot") or {}
        age = max(0, round(time.time() - current.get("captured_unix_ms", 0) / 1000, 1))
        state = dict(pid=data.get("process_id"), sequence=current.get("sequence", 0), age=age,
                     live=data.get("runtime_state") == "running" and age < 5)
    except (OSError, ValueError, TypeError):
        state = dict(pid=None, sequence=0, age=None, live=False)
    try:
        stat = (RUNTIME / "inspector.png").stat()
        state["frame"] = f"{stat.st_mtime_ns}-{stat.st_size}"
    except OSError:
        state["frame"] = None
    return state


class Viewer(BaseHTTPRequestHandler):
    def log_message(self, *_):
        pass

    def do_GET(self):
        # Explicit routes only: never serve directory contents or arbitrary files.
        path = urlsplit(self.path).path
        try:
            if path == "/":
                body, mime = PAGE.encode(), "text/html; charset=utf-8"
            elif path == "/status":
                body, mime = json.dumps(snapshot()).encode(), "application/json"
            elif path == "/frame":
                body, mime = (RUNTIME / "inspector.png").read_bytes(), "image/png"
            else:
                self.send_error(404)
                return
        except OSError:
            self.send_error(503, "Waiting for capture")
            return
        self.send_response(200)
        self.send_header("Content-Type", mime)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store, max-age=0")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.end_headers()
        try:
            self.wfile.write(body)
        except (BrokenPipeError, ConnectionResetError):
            pass


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    global RUNTIME
    parser.add_argument("--runtime-dir", type=Path, default=RUNTIME)
    parser.add_argument("--port", type=int, default=8765)
    args = parser.parse_args()
    RUNTIME = args.runtime_dir.resolve()
    with ThreadingHTTPServer(("127.0.0.1", args.port), Viewer) as server:
        print(f"Viewer: http://127.0.0.1:{args.port}", flush=True)
        try:
            server.serve_forever()
        except KeyboardInterrupt:
            pass


if __name__ == "__main__":
    main()
