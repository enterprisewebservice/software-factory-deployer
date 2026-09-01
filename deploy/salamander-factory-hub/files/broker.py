#!/usr/bin/env python3
"""factory-hub broker — seat lookup, provisioning, reset, admin.

Listens on 127.0.0.1:9000 behind nginx, which sits behind OpenShift
oauth-proxy. Identity is the X-Forwarded-User header oauth-proxy sets
from the OpenShift session; nginx forwards it and nothing else can
reach this port. Seat state lives in the factory-seats ConfigMap
(keyed by handle); provisioning and reset run as Jobs.

  GET  /api/resolve            nginx auth_request: 200 + X-Seat-Upstream, else 401
  GET  /api/seat               this person's seat status (+ links, seat password when ready)
  POST /api/seat/provision     kick provisioning (idempotent, capped by MAX_SEATS)
  POST /api/seat/reset         "restart workshop": reset-seat.sh <handle> --confirm
  GET  /api/admin/seats        all seats (admins only)
  POST /api/admin/seats/<h>/reset | /provision | /deprovision (admin)
"""
import json, os, ssl, sys, time, urllib.request, urllib.error
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
sys.path.insert(0, "/app")
import seatlib as S

ADMINS = {u.strip() for u in os.environ.get("ADMIN_USERS", "").split(",") if u.strip()}
MAX_SEATS = int(os.environ.get("MAX_SEATS", "25"))
PROVISION_IMAGE = os.environ.get("JOB_IMAGE", "registry.access.redhat.com/ubi9/python-312:latest")
K8S = "https://kubernetes.default.svc"
TOKEN = open("/var/run/secrets/kubernetes.io/serviceaccount/token").read().strip()
CTX = ssl.create_default_context(cafile="/var/run/secrets/kubernetes.io/serviceaccount/ca.crt")


def k8s(method, path, body=None, ok=(200, 201, 202)):
    req = urllib.request.Request(K8S + path, data=json.dumps(body).encode() if body is not None else None, method=method)
    req.add_header("Authorization", "Bearer " + TOKEN)
    req.add_header("Content-Type", "application/merge-patch+json" if method == "PATCH" else "application/json")
    try:
        with urllib.request.urlopen(req, context=CTX, timeout=20) as r:
            raw = r.read()
            return r.status, (json.loads(raw) if raw else {})
    except urllib.error.HTTPError as e:
        return e.code, {}


def seats():
    code, cm = k8s("GET", f"/api/v1/namespaces/{S.HUB_NS}/configmaps/{S.SEATS_CM}")
    out = {}
    for h, raw in ((cm.get("data") or {}) if code == 200 else {}).items():
        try:
            out[h] = json.loads(raw)
        except Exception:
            out[h] = {"phase": "error", "step": "corrupt record"}
    return out


def write_seat(handle, rec):
    k8s("PATCH", f"/api/v1/namespaces/{S.HUB_NS}/configmaps/{S.SEATS_CM}", {"data": {handle: json.dumps(rec)}})


def seat_password(handle):
    code, sec = k8s("GET", f"/api/v1/namespaces/{S.HUB_NS}/secrets/seat-{handle}")
    if code != 200:
        return None
    import base64
    return base64.b64decode(sec["data"]["password"]).decode()


def job_active(name):
    code, j = k8s("GET", f"/apis/batch/v1/namespaces/{S.HUB_NS}/jobs/{name}")
    if code != 200:
        return False
    st = j.get("status", {})
    return bool(st.get("active")) and not st.get("succeeded") and not st.get("failed")


def run_job(name, command, env, backoff=0):
    # replace any finished job of the same name
    k8s("DELETE", f"/apis/batch/v1/namespaces/{S.HUB_NS}/jobs/{name}?propagationPolicy=Background")
    for _ in range(20):
        if k8s("GET", f"/apis/batch/v1/namespaces/{S.HUB_NS}/jobs/{name}")[0] == 404:
            break
        time.sleep(0.5)
    job = {"apiVersion": "batch/v1", "kind": "Job",
           "metadata": {"name": name, "namespace": S.HUB_NS, "labels": {"app": "factory-hub-job"}},
           "spec": {"backoffLimit": backoff, "ttlSecondsAfterFinished": 86400,
                    "template": {"metadata": {"labels": {"app": "factory-hub-job"}},
                                 "spec": {"serviceAccountName": "factory-provisioner", "restartPolicy": "Never",
                                          "containers": [{"name": "run", "image": PROVISION_IMAGE, "command": command,
                                                          "env": [{"name": k, "value": v} for k, v in env.items()],
                                                          "volumeMounts": [{"name": "scripts", "mountPath": "/scripts", "readOnly": True}]}],
                                          "volumes": [{"name": "scripts", "configMap": {"name": "factory-hub-scripts", "defaultMode": 0o755}}]}}}}
    code, _ = k8s("POST", f"/apis/batch/v1/namespaces/{S.HUB_NS}/jobs", job)
    return code in (200, 201)


class H(BaseHTTPRequestHandler):
    server_version = "factory-hub-broker/1.0"

    def log_message(self, fmt, *args):
        sys.stdout.write("%s %s\n" % (self.headers.get("X-Forwarded-User", "-"), fmt % args)); sys.stdout.flush()

    def user(self):
        return (self.headers.get("X-Forwarded-User") or "").strip()

    def send(self, code, obj, extra=None):
        body = json.dumps(obj).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Cache-Control", "no-store")
        self.send_header("Content-Length", str(len(body)))
        for k, v in (extra or {}).items():
            self.send_header(k, v)
        self.end_headers()
        self.wfile.write(body)

    def status_for(self, u, all_seats):
        h, rec = S.seat_for(u, all_seats)
        if not rec:
            return {"user": u, "phase": "none", "admin": u in ADMINS}
        out = {"user": u, "handle": h, "phase": rec.get("phase"), "step": rec.get("step"), "detail": rec.get("detail"),
               "started": rec.get("started"), "ready": rec.get("ready"), "last_reset": rec.get("last_reset"),
               "admin": u in ADMINS, "links": S.links(h)}
        if rec.get("phase") == "ready":
            out["seat_password"] = seat_password(h)
        return out

    def do_GET(self):
        u = self.user()
        if self.path.startswith("/api/resolve"):
            h, rec = S.seat_for(u, seats()) if u else (None, None)
            if rec and rec.get("phase") == "ready":
                return self.send(200, {"handle": h}, {"X-Seat-Upstream": f"showroom-{h}.showroom-{h}.svc.cluster.local:8080"})
            return self.send(401, {"phase": rec.get("phase") if rec else "none"})
        if not u:
            return self.send(401, {"error": "no identity"})
        if self.path.startswith("/api/seat"):
            return self.send(200, self.status_for(u, seats()))
        if self.path.startswith("/api/admin/seats"):
            if u not in ADMINS:
                return self.send(403, {"error": "admins only"})
            all_seats = seats()
            rows = [{"handle": h, **{k: v for k, v in rec.items() if k != "handle"},
                     "job_running": job_active(f"provision-{h}") or job_active(f"reset-{h}")} for h, rec in sorted(all_seats.items())]
            return self.send(200, {"seats": rows, "max": MAX_SEATS})
        return self.send(404, {"error": "not found"})

    def do_POST(self):
        u = self.user()
        if not u:
            return self.send(401, {"error": "no identity"})
        all_seats = seats()
        parts = [p for p in self.path.split("?")[0].split("/") if p]
        # /api/seat/provision | /api/seat/reset
        if parts[:2] == ["api", "seat"] and len(parts) == 3:
            h, rec = S.seat_for(u, all_seats)
            return self.act(parts[2], u, h, rec, all_seats)
        # /api/admin/seats/<h>/<action>
        if parts[:3] == ["api", "admin", "seats"] and len(parts) == 5:
            if u not in ADMINS:
                return self.send(403, {"error": "admins only"})
            h = parts[3]
            rec = all_seats.get(h)
            if not rec:
                return self.send(404, {"error": "no such seat"})
            return self.act(parts[4], rec.get("username", ""), h, rec, all_seats)
        return self.send(404, {"error": "not found"})

    def act(self, action, u, h, rec, all_seats):
        if action == "provision":
            if rec and rec.get("phase") in ("provisioning", "resetting") and job_active(f"provision-{h}"):
                return self.send(202, {"phase": rec["phase"], "note": "already running"})
            if not rec and sum(1 for r in all_seats.values() if r.get("phase") in ("ready", "provisioning")) >= MAX_SEATS:
                return self.send(429, {"error": f"all {MAX_SEATS} workbenches are taken right now — ask your host to free one"})
            h = h or S.allocate_handle(u, all_seats)
            write_seat(h, {"username": u, "handle": h, "phase": "provisioning"})
            ok = run_job(f"provision-{h}", ["python3", "/scripts/provision-seat.py"], {"SEAT_USER": u})
            return self.send(202 if ok else 500, {"phase": "provisioning" if ok else "error", "handle": h})
        if action == "reset":
            if not rec or rec.get("phase") not in ("ready", "error"):
                return self.send(409, {"error": "seat is not in a resettable state", "phase": rec.get("phase") if rec else "none"})
            rec = dict(rec, phase="resetting")
            write_seat(h, rec)
            ok = run_job(f"reset-{h}", ["bash", "/scripts/reset-entry.sh", h], {"SEAT_HANDLE": h, "SEAT_USER": u})
            return self.send(202 if ok else 500, {"phase": "resetting" if ok else "error"})
        if action == "deprovision":
            if self.user() not in ADMINS:
                return self.send(403, {"error": "admins only"})
            if job_active(f"provision-{h}") or job_active(f"reset-{h}"):
                return self.send(409, {"error": "a provision/reset job is still running for this seat — wait for it to finish"})
            write_seat(h, dict(rec or {}, phase="removing"))
            ok = run_job(f"deprovision-{h}", ["python3", "/scripts/deprovision-seat.py"], {"SEAT_HANDLE": h, "SEAT_USER": u})
            return self.send(202 if ok else 500, {"phase": "removing" if ok else "error"})
        return self.send(404, {"error": "unknown action"})


if __name__ == "__main__":
    print(f"broker on 127.0.0.1:9000 admins={sorted(ADMINS)} max_seats={MAX_SEATS}", flush=True)
    ThreadingHTTPServer(("127.0.0.1", 9000), H).serve_forever()
