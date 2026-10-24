#!/usr/bin/env python3
"""InstaBox keeper: keeps your scrape machines running without you. Runs OUTSIDE the machines (GitHub Actions or your Mac) every ~10 min.

For each machine named in --machines / $INSTABOX_MACHINES:
  - paused (suspended)  -> POST /v1/machines/{id}/resume   (the run continues where it left off; watchdog + boot unit handle the rest)
  - stopped             -> POST /v1/machines/{id}/start
  - running longer than --cycle-hours since its last resume/cycle -> pause it and resume it at once, so the platform's
    "8 running hours" pause never comes (OFF by default; enable after you saw that a manual resume restarts the 8 h clock)
  - error / deleting / unknown -> reported, exit code 1 (never fixed automatically)
It never deletes, stops or creates anything. The clock start is kept in the machine's labels keeper_cycled_at / keeper_stamped_at. On first sight the clock starts at the machine's
updatedAt (its last state change = the last resume), not at 'now'; a later manual resume (updatedAt newer than our stamp) restarts it.

  INSTABOX_API_KEY=ibk_... python3 keeper/instabox_keeper.py --machines vm1,vm2 [--cycle-hours 7.25] [--dry-run]
Env: INSTABOX_API_KEY (required), INSTABOX_API_URL (default https://api.instabox.sh), INSTABOX_MACHINES (comma list).
Standard library only. Never print or log the API key.
"""
import argparse, datetime, json, os, re, sys, time, urllib.error, urllib.request, uuid

BASE = os.environ.get("INSTABOX_API_URL", "https://api.instabox.sh").rstrip("/")
KEY = os.environ.get("INSTABOX_API_KEY", "")
LABEL = "keeper_cycled_at"      # epoch: when the machine's 8 h clock last started (last resume/cycle)
LABEL_AT = "keeper_stamped_at"  # epoch: when this script wrote the label (to tell later outside changes, e.g. a manual resume)


def log(**kw): print(json.dumps({"t": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()), **kw}), flush=True)


def api(method, path, body=None):
    data = json.dumps(body).encode() if body is not None else None
    headers = {"X-API-Key": KEY, "Accept": "application/json"}
    if data is not None: headers["Content-Type"] = "application/json"
    if method in ("POST", "PATCH", "DELETE"): headers["Idempotency-Key"] = str(uuid.uuid4())
    req = urllib.request.Request(BASE + path, data=data, headers=headers, method=method)
    try:
        with urllib.request.urlopen(req, timeout=60) as r:
            raw = r.read(); return r.status, (json.loads(raw) if raw else {})
    except urllib.error.HTTPError as e:
        raw = e.read()
        try: return e.code, json.loads(raw)
        except Exception: return e.code, {"error": {"code": "http_%d" % e.code, "message": raw[:200].decode("utf-8", "replace")}}


def get(name):
    st, m = api("GET", f"/v1/machines/{name}")
    if st != 200: raise RuntimeError(f"GET {name}: {st} {m.get('error', {}).get('code')}")
    return m


def wait_state(name, wanted, timeout=180):
    end = time.time() + timeout
    while time.time() < end:
        m = get(name)
        if m["state"] in wanted: return m
        time.sleep(3 if not os.environ.get("KEEPER_FAST") else 0.05)
    raise RuntimeError(f"{name}: did not reach {sorted(wanted)} in {timeout}s (state {m['state']})")


def post(name, action, dry, ident=None):
    if dry: log(machine=name, action=action, dry_run=True); return
    st, r = api("POST", f"/v1/machines/{ident or name}/{action}")  # GET takes a name, changes need the m_... id
    if st not in (200, 202): raise RuntimeError(f"{action} {name}: {st} {r.get('error', {}).get('code')} {r.get('error', {}).get('message', '')}")
    log(machine=name, action=action, op=r.get("id"), state=r.get("state"))


def parse_ts(v):
    try:
        v = re.sub(r"(\.\d{6})\d+", r"\1", str(v)).replace("Z", "+00:00")
        d = datetime.datetime.fromisoformat(v)
        return (d if d.tzinfo else d.replace(tzinfo=datetime.timezone.utc)).timestamp()
    except Exception:
        return None


def stamp(name, m, dry, start=None):
    if dry: return
    now = int(time.time()); labels = dict(m.get("metadata") or {})
    labels[LABEL] = str(int(start or now)); labels[LABEL_AT] = str(now)
    st, r = api("PATCH", f"/v1/machines/{m.get('id') or name}", {"metadata": labels})
    if st != 200: log(machine=name, warn=f"could not set label: {st}")


def clock_start(m, now):
    """When this running machine's 8 h clock started (epoch), and whether we still need to write the label."""
    labels = m.get("metadata") or {}; upd = parse_ts(m.get("updatedAt", ""))
    if LABEL not in labels:
        return (upd if upd and upd <= now else now), True
    start = float(labels[LABEL]); at = float(labels.get(LABEL_AT, start))
    if upd and upd > at + 120: return upd, True      # changed after our stamp (e.g. a manual resume): the clock restarted then
    return start, False


def handle(name, cycle_hours, dry):
    m = get(name); s = m["state"]
    if s in ("starting", "resuming", "suspending", "stopping", "creating"):
        log(machine=name, state=s, note="operation in progress, skipping"); return True
    if s == "suspended":
        post(name, "resume", dry, m.get("id"))
        if not dry: m = wait_state(name, {"running"}); stamp(name, m, dry)
        return True
    if s == "stopped":
        post(name, "start", dry, m.get("id"))
        if not dry: m = wait_state(name, {"running"}); stamp(name, m, dry)
        return True
    if s == "running":
        now = time.time(); start, need_stamp = clock_start(m, now); age_h = (now - start) / 3600
        if need_stamp: stamp(name, m, dry, start); log(machine=name, state=s, note=f"clock start recorded: {age_h:.2f} h ago")
        if cycle_hours and age_h >= cycle_hours:
            log(machine=name, state=s, note=f"running {age_h:.2f} h since the last resume: cycling (pause + resume)")
            post(name, "suspend", dry, m.get("id"))
            if not dry: wait_state(name, {"suspended"}, 120)
            post(name, "resume", dry, m.get("id"))
            if not dry: m = wait_state(name, {"running"}); stamp(name, m, dry)
        else:
            log(machine=name, state=s, running_h=round(age_h, 2))
        return True
    log(machine=name, state=s, problem="needs a human: not touched"); return False


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--machines", default=os.environ.get("INSTABOX_MACHINES", ""), help="comma-separated machine names")
    ap.add_argument("--cycle-hours", type=float, default=0.0, help="pause+resume a machine that has run this long since its last resume (0 = off)")
    ap.add_argument("--dry-run", action="store_true")
    a = ap.parse_args()
    names = [n.strip() for n in a.machines.split(",") if n.strip()]
    if not KEY: sys.exit("INSTABOX_API_KEY is not set")
    if not names: sys.exit("no machines: use --machines or INSTABOX_MACHINES")
    ok = True
    for n in names:
        try: ok = handle(n, a.cycle_hours, a.dry_run) and ok
        except Exception as e: log(machine=n, error=str(e)[:300]); ok = False
    sys.exit(0 if ok else 1)


if __name__ == "__main__":
    main()
