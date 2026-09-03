"""Shared seat vocabulary for the factory hub (broker + provisioner).

A person signs in with their Keycloak/OpenShift USERNAME (exact, any
chars OpenShift allows). Their SEAT HANDLE is the DNS-safe name that
drives every namespaced thing — <handle>-agent-workspace,
showroom-<handle>, <handle>-agents in Gitea. The seats ConfigMap
(factory-seats, ns factory-hub) is keyed by handle and records the
username, so the mapping is stored, never recomputed on the fly.
"""
import json, re, subprocess

SEATS_CM = "factory-seats"
HUB_NS = "factory-hub"
APPS = "apps.salamander.aimlworkbench.com"


def sanitize(username: str) -> str:
    h = re.sub(r"[^a-z0-9]+", "-", username.lower()).strip("-")
    h = re.sub(r"-{2,}", "-", h)[:20].strip("-")
    if not h or not h[0].isalpha():
        h = ("u-" + h)[:20].strip("-")
    return h or "seat"


def oc(*args, input=None, check=True):
    r = subprocess.run(["oc", *args], input=input, capture_output=True, text=True)
    if check and r.returncode != 0:
        raise RuntimeError(f"oc {' '.join(args[:3])}: {r.stderr.strip()[:300]}")
    return r.stdout


def read_seats() -> dict:
    """{handle: {username, phase, ...}}"""
    try:
        cm = json.loads(oc("get", "cm", SEATS_CM, "-n", HUB_NS, "-o", "json"))
    except RuntimeError:
        return {}
    out = {}
    for h, raw in (cm.get("data") or {}).items():
        try:
            out[h] = json.loads(raw)
        except Exception:
            out[h] = {"phase": "error", "step": "corrupt record"}
    return out


def seat_for(username: str, seats: dict):
    for h, rec in seats.items():
        if rec.get("username") == username:
            return h, rec
    return None, None


def allocate_handle(username: str, seats: dict) -> str:
    h, _ = seat_for(username, seats)
    if h:
        return h
    base = sanitize(username)
    cand, n = base, 2
    while cand in seats:
        cand, n = f"{base[:17]}-{n}", n + 1
    return cand


def write_seat(handle: str, rec: dict):
    patch = json.dumps({"data": {handle: json.dumps(rec)}})
    oc("patch", "cm", SEATS_CM, "-n", HUB_NS, "--type=merge", "-p", patch)


def links(handle: str) -> dict:
    return {
        "workshop": f"https://showroom-{handle}-showroom-{handle}.{APPS}/",
        "console": f"https://console-openshift-console.{APPS}",
        "devhub": f"https://v1-developer-hub-rhdh-test.{APPS}",
        "gitops": f"https://openshift-gitops-server-openshift-gitops.{APPS}",
        "mattermost": f"https://mattermost-mattermost.{APPS}/oauth/gitlab/login?redirect_to=%2F",
        "gitea": f"https://gitea-gitea.{APPS}/{handle}-agents",
    }

# Front doors. The hub serves two hostnames; each is an edition of the same
# workshop repo. brand_for() maps the request host to a brand, BRANDS holds
# what differs per brand when a seat is rendered.
BRANDS = {
    "redhat": {
        "site_file": "site.yml",
        "zt_bundle": "https://github.com/rhpds/nookbag/releases/download/nookbag-v0.4.0/nookbag-v0.4.0.zip",
        "hub_url": "https://factory.apps.salamander.aimlworkbench.com",
        "title": "Red Hat Software Factory",
    },
    "handsonmode": {
        "site_file": "site-handsonmode.yml",
        # brand-patched nookbag shipped in the workshop repo (ui-handsonmode/)
        "zt_bundle": "file:///showroom/repo/ui-handsonmode/nookbag-handsonmode-v0.4.0.zip",
        "hub_url": "https://handsonmode.ai",
        "title": "Hands-On Mode",
    },
}


def brand_for(host: str) -> str:
    h = (host or "").split(":", 1)[0].lower()
    return "handsonmode" if h.endswith("handsonmode.ai") or h.endswith("handsonmode.com") else "redhat"

