"""Attendee progress ledger: where each person is in the guide, and for how long.

Every guide page sends a beacon to /api/progress on the SEAT's own hostname (so it
passes the seat's sign-in): ev=view on load, ev=beat every 60 s while the tab is
visible, ev=leave when it is hidden. The seat's traefik forwards the beacon to the
hub broker with X-Seat-Handle + X-Seat-Token (rendered per seat by the provisioner).
One JSON record per handle in ConfigMap factory-hub/factory-progress:
  last_seen, first_seen, current {module, path, title, since},
  modules {<module>: {seconds, views, first, last}}, recent [last 40 page views]
Time only accrues between beacons at most GAP_MAX seconds apart, so a closed tab
stops the clock within a couple of minutes."""
import json, re, time
from datetime import datetime, timezone

CM = "factory-progress"
GAP_MAX = 150
MODULES = {"start": "Start", "overview": "Overview", "setup": "Prerequisites", "other": "Other page",
           "1": "Module 1: Hire an agent", "2": "Module 2: The agent is in git", "3": "Module 3: Governed tools",
           "4": "Module 4: Teach with a skill artifact", "5": "Module 5: Headless agent in the pipeline",
           "6": "Module 6: Ship to production", "7": "Module 7: Swap the brain", "8": "Module 8: The gap map"}


def iso(t): return datetime.fromtimestamp(t, timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def epoch(s):
    try: return datetime.strptime(s, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc).timestamp()
    except Exception: return None


def module_of(path, title):
    m = re.search(r"module-0?(\d)", path or "", re.I) or re.search(r"\bModule (\d)\b", title or "")
    if m: return m.group(1)
    p = (path or "").lower()
    if "overview" in p: return "overview"
    if "details" in p: return "setup"
    if "start" in p or p.rstrip("/") in ("", "/index.html") or p.endswith("/index.html"): return "start"
    return "other"


def load(k8s, hub_ns, name=None):
    code, cm = k8s("GET", f"/api/v1/namespaces/{hub_ns}/configmaps/{name or CM}")
    out = {}
    for h, raw in ((cm.get("data") or {}) if code == 200 else {}).items():
        try: out[h] = json.loads(raw)
        except Exception: pass
    return out


def record(k8s, hub_ns, handle, ev, path, title):
    rec = load(k8s, hub_ns).get(handle) or {}
    now = time.time(); mod = module_of(path, title)
    mods = rec.setdefault("modules", {}); cur = rec.get("current") or {}
    last = epoch(rec.get("last_seen") or "")
    if last and cur.get("module") and 0 < now - last <= GAP_MAX:          # the clock runs while beacons keep coming
        m = mods.setdefault(cur["module"], {"seconds": 0, "views": 0})
        m["seconds"] = int(m.get("seconds", 0) + (now - last)); m["last"] = iso(now)
    if ev == "view" or cur.get("module") != mod:
        m = mods.setdefault(mod, {"seconds": 0, "views": 0})
        m["views"] = m.get("views", 0) + (1 if ev == "view" else 0); m.setdefault("first", iso(now)); m["last"] = iso(now)
        rec["current"] = {"module": mod, "path": (path or "")[:200], "title": (title or "")[:120], "since": iso(now)}
        if ev == "view":
            rec.setdefault("recent", []).append({"t": iso(now), "module": mod, "path": (path or "")[:120]}); del rec["recent"][:-40]
    rec.setdefault("first_seen", iso(now)); rec["last_seen"] = iso(now); rec["last_event"] = ev
    k8s("PATCH", f"/api/v1/namespaces/{hub_ns}/configmaps/{CM}", {"data": {handle: json.dumps(rec)}})
    return rec


SWEEP_CM = "factory-sweep"


def rows(k8s, hub_ns, seats):
    prog = load(k8s, hub_ns); sweep = load(k8s, hub_ns, SWEEP_CM); out = []
    for h, rec in seats.items():
        if h.startswith("_"):
            continue
        p = prog.get(h) or {}
        mods = {k: {"label": MODULES.get(k, k), "seconds": v.get("seconds", 0), "views": v.get("views", 0), "first": v.get("first"), "last": v.get("last")}
                for k, v in (p.get("modules") or {}).items()}
        cur = p.get("current") or {}
        out.append({"handle": h, "username": rec.get("username"), "brand": rec.get("brand") or "redhat", "phase": rec.get("phase"),
                    "ready": rec.get("ready"), "last_seen": p.get("last_seen"), "first_seen": p.get("first_seen"),
                    "current": {**cur, "label": MODULES.get(cur.get("module"), cur.get("module"))} if cur else None,
                    "modules": mods, "total_seconds": sum(m["seconds"] for m in mods.values()),
                    "views": sum(m["views"] for m in mods.values()), "recent": (p.get("recent") or [])[-8:],
                    # the sweep's verdicts: module status judged from the seat itself, errors visible now
                    "swept_at": (sweep.get(h) or {}).get("at"), "checks": (sweep.get(h) or {}).get("modules") or {},
                    "errors": (sweep.get(h) or {}).get("errors") or [],
                    "done": sorted(n for n, c in ((sweep.get(h) or {}).get("modules") or {}).items() if c.get("status") == "done"),
                    "wrong": sorted(n for n, c in ((sweep.get(h) or {}).get("modules") or {}).items() if c.get("status") == "looks wrong")})
    out.sort(key=lambda r: r.get("last_seen") or "", reverse=True)
    return out
