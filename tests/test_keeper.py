"""Offline test of instabox_keeper.py against a fake InstaBox API (no network, no real account)."""
import json, os, subprocess, sys, threading, time
from http.server import BaseHTTPRequestHandler, HTTPServer
M = {}
POSTS = []
class H(BaseHTTPRequestHandler):
    def log_message(self, *a): pass
    def _send(self, code, obj): b = json.dumps(obj).encode(); self.send_response(code); self.send_header("Content-Type", "application/json"); self.end_headers(); self.wfile.write(b)
    def do_GET(self):
        n = self.path.rsplit("/", 1)[1]
        if self.headers.get("X-API-Key") != "k": return self._send(401, {"error": {"code": "unauthenticated"}})
        self._send(200, M[n]) if n in M else self._send(404, {"error": {"code": "not_found"}})
    def do_POST(self):
        assert self.headers.get("Idempotency-Key"), "missing Idempotency-Key"
        _, _, _, n, act = self.path.split("/")  # /v1/machines/<n>/<act>
        POSTS.append((n, act)); m = M[n]
        m["state"] = {"resume": "running", "start": "running", "suspend": "suspended"}[act]
        self._send(202, {"id": "op_1", "state": "queued"})
    def do_PATCH(self):
        n = self.path.rsplit("/", 1)[1]; body = json.loads(self.rfile.read(int(self.headers["Content-Length"]))); M[n]["metadata"] = body["metadata"]; self._send(200, M[n])
srv = HTTPServer(("127.0.0.1", 0), H); threading.Thread(target=srv.serve_forever, daemon=True).start()
env = {**os.environ, "INSTABOX_API_URL": f"http://127.0.0.1:{srv.server_port}", "INSTABOX_API_KEY": "k", "KEEPER_FAST": "1"}
K = os.path.join(os.path.dirname(__file__), "..", "instabox_keeper.py")
def run(*args): r = subprocess.run([sys.executable, "-I", K, *args], env=env, capture_output=True, text=True); return r.returncode, r.stdout
def reset(**ms): M.clear(); M.update({k: {"id": "m_" + k, "state": v[0], "metadata": v[1]} for k, v in ms.items()}); M.update({"m_" + k: M[k] for k in ms}); POSTS.clear()
now = int(time.time())
reset(a=("suspended", {}), b=("stopped", {}), c=("running", {}))
rc, out = run("--machines", "a,b,c"); assert rc == 0, out
assert sorted(POSTS) == [("m_a", "resume"), ("m_b", "start")], POSTS  # changes go by id
assert "keeper_cycled_at" in M["a"]["metadata"] and "keeper_cycled_at" in M["b"]["metadata"] and "keeper_cycled_at" in M["c"]["metadata"]
reset(a=("running", {"keeper_cycled_at": str(now - 3600), "x": "keep"}))
rc, out = run("--machines", "a", "--cycle-hours", "7.25"); assert rc == 0 and POSTS == [], (POSTS, out)       # 1 h old: no cycle
reset(a=("running", {"keeper_cycled_at": str(now - 8 * 3600), "x": "keep"}))
rc, out = run("--machines", "a", "--cycle-hours", "7.25"); assert rc == 0 and POSTS == [("m_a", "suspend"), ("m_a", "resume")], (POSTS, out)
assert M["a"]["metadata"]["x"] == "keep" and int(M["a"]["metadata"]["keeper_cycled_at"]) >= now, M["a"]  # other labels kept, clock restarted
reset(a=("running", {"keeper_cycled_at": str(now - 8 * 3600)}))
rc, out = run("--machines", "a"); assert rc == 0 and POSTS == [], "cycle must be off by default"
reset(a=("suspended", {}), b=("stopped", {}))
rc, out = run("--machines", "a,b", "--dry-run"); assert rc == 0 and POSTS == [] and M["a"]["state"] == "suspended", "dry run changed something"
reset(a=("error", {}), b=("suspended", {}))
rc, out = run("--machines", "a,b"); assert rc == 1 and POSTS == [("m_b", "resume")], (rc, POSTS)          # error: untouched, others still handled, exit 1
reset(a=("resuming", {}))
rc, out = run("--machines", "a"); assert rc == 0 and POSTS == [], "must skip while an operation runs"
rc, out = run("--machines", "nope"); assert rc == 1 and "error" in out
env2 = {k: v for k, v in env.items() if k != "INSTABOX_API_KEY"}
r = subprocess.run([sys.executable, "-I", K, "--machines", "a"], env=env2, capture_output=True, text=True); assert r.returncode != 0 and "INSTABOX_API_KEY" in r.stderr
assert "k" != "" and "X-API-Key" not in out
print("all keeper tests passed")


def _clock_tests():
    sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
    import instabox_keeper as k
    now = 1_000_000_000.0
    iso = lambda t: __import__("datetime").datetime.fromtimestamp(t, __import__("datetime").timezone.utc).isoformat().replace("+00:00", "Z")
    # first sight: clock starts at updatedAt (last resume), not now
    s, need = k.clock_start({"updatedAt": iso(now - 3 * 3600)}, now)
    assert need and abs(s - (now - 3 * 3600)) < 1, (s, need)
    # labelled and unchanged since our stamp: keep label
    m = {"updatedAt": iso(now - 3600), "metadata": {k.LABEL: str(now - 7 * 3600), k.LABEL_AT: str(now - 3600)}}
    s, need = k.clock_start(m, now)
    assert not need and s == now - 7 * 3600
    # manual resume after our stamp: clock restarts at updatedAt
    m = {"updatedAt": iso(now - 600), "metadata": {k.LABEL: str(now - 9 * 3600), k.LABEL_AT: str(now - 9 * 3600)}}
    s, need = k.clock_start(m, now)
    assert need and abs(s - (now - 600)) < 1
    print("clock tests passed")


_clock_tests()


# loop mode: a machine that gets suspended during the loop is resumed on a later pass
reset(a=("running", {}))
import threading as _t
_t.Timer(0.3, lambda: M["a"].update(state="suspended")).start()
rc, out = subprocess.run([sys.executable, "-I", K, "--machines", "a", "--loop-minutes", "0.05", "--interval-seconds", "0.5"], env=env, capture_output=True, text=True).returncode, ""
assert rc == 0 and ("m_a", "resume") in POSTS, (rc, POSTS)
print("loop test passed")
