#!/usr/bin/env python3
"""Remove a seat entirely (admin action). The inverse of provision-seat.py.

Runs reset-seat.sh first (agents, repos, catalog entries), then deletes
the seat's namespaces, cluster bindings, ApplicationSet generators,
ArgoCD RBAC rows, Gitea org + user, Mattermost account, group
memberships, the seat Secret and the seats-record. The OpenShift User
and its Keycloak account are kept — the person can come back and get a
fresh seat.
"""
import base64, json, os, subprocess, sys, urllib.request, urllib.error, io, tarfile
sys.path.insert(0, "/scripts")
import seatlib as S

H = os.environ["SEAT_HANDLE"]
os.makedirs("/tmp/bin", exist_ok=True)
os.environ["PATH"] = "/tmp/bin:" + os.environ.get("PATH", "")
if subprocess.run(["which", "oc"], capture_output=True).returncode != 0:
    with urllib.request.urlopen("http://downloads.openshift-console.svc.cluster.local/amd64/linux/oc.tar", timeout=60) as r:
        tarfile.open(fileobj=io.BytesIO(r.read())).extractall("/tmp/bin")
    os.chmod("/tmp/bin/oc", 0o755)

rec = S.read_seats().get(H) or {}
USER = rec.get("username") or os.environ.get("SEAT_USER", "")
NS, SNS, ORG = f"{H}-agent-workspace", f"showroom-{H}", f"{H}-agents"
print(f"deprovision {H} (user={USER})", flush=True)


def http(method, url, headers=None, auth=None):
    req = urllib.request.Request(url, method=method)
    for k, v in (headers or {}).items():
        req.add_header(k, v)
    if auth:
        req.add_header("Authorization", "Basic " + base64.b64encode(f"{auth[0]}:{auth[1]}".encode()).decode())
    try:
        with urllib.request.urlopen(req, timeout=30) as r:
            return r.status
    except urllib.error.HTTPError as e:
        return e.code


def secret_data(name, ns):
    s = json.loads(S.oc("get", "secret", name, "-n", ns, "-o", "json"))
    return {k: base64.b64decode(v).decode() for k, v in (s.get("data") or {}).items()}


print("== agents / repos / catalog (reset-seat.sh) ==", flush=True)
subprocess.run(["bash", "/scripts/reset-seat.sh", H, "--confirm"], check=False)

print("== argo generators + rbac rows ==", flush=True)
for name in ("agent-office-agents-gitea", "seat-services-gitea"):
    o = json.loads(S.oc("get", "applicationset", name, "-n", "openshift-gitops", "-o", "json"))
    gens = [g for g in o["spec"]["generators"] if g.get("scmProvider", {}).get("gitea", {}).get("owner") != ORG]
    if len(gens) != len(o["spec"]["generators"]):
        S.oc("patch", "applicationset", name, "-n", "openshift-gitops", "--type=json",
             "-p", json.dumps([{"op": "replace", "path": "/spec/generators", "value": gens}]))
a = json.loads(S.oc("get", "argocd", "openshift-gitops", "-n", "openshift-gitops", "-o", "json"))
pol = a["spec"]["rbac"].get("policy", "")
uid = S.oc("get", "user", USER, "-o", "jsonpath={.metadata.uid}", check=False).strip() if USER else ""
keep = [l for l in pol.splitlines() if f"agent-office/{ORG}-*" not in l and not (uid and l.startswith(f"p, {uid},")) and not (USER and l.startswith(f"p, {USER}, projects"))]
if len(keep) != len(pol.splitlines()):
    S.oc("patch", "argocd", "openshift-gitops", "-n", "openshift-gitops", "--type=merge",
         "-p", json.dumps({"spec": {"rbac": {"policy": "\n".join(keep) + "\n"}}}))

print("== gitea org + user ==", flush=True)
g = secret_data("gitea-admin-credentials", "gitea")
G = f"https://gitea-gitea.{S.APPS}/api/v1"
auth = (g["username"], g["password"])
print("  org:", http("DELETE", f"{G}/orgs/{ORG}", auth=auth), " user:", http("DELETE", f"{G}/admin/users/{H}?purge=true", auth=auth))

print("== mattermost account (deactivate) ==", flush=True)
mm = secret_data("mattermost-admin-token", "mattermost")
MM = f"https://mattermost-mattermost.{S.APPS}/api/v4"
hdr = {"Authorization": "Bearer " + mm["token"]}
req = urllib.request.Request(f"{MM}/users/username/{H}", headers=hdr)
try:
    with urllib.request.urlopen(req, timeout=20) as r:
        mid = json.load(r)["id"]
    print("  deactivate:", http("DELETE", f"{MM}/users/{mid}", headers=hdr))
except Exception as e:
    print("  none:", str(e)[:80])

print("== cluster objects ==", flush=True)
S.oc("delete", "clusterrolebinding", f"seat-{H}-workshop-viewer", "--ignore-not-found")
S.oc("delete", "ns", SNS, NS, "--ignore-not-found", "--wait=false")
if USER:
    S.oc("adm", "groups", "remove-users", "redhat-workshop-users", USER, check=False)
    S.oc("delete", "group", USER, "--ignore-not-found", check=False)
S.oc("delete", "secret", f"seat-{H}", "-n", S.HUB_NS, "--ignore-not-found")
S.oc("patch", "cm", S.SEATS_CM, "-n", S.HUB_NS, "--type=json", "-p", json.dumps([{"op": "remove", "path": f"/data/{H}"}]), check=False)
print(f"SEAT REMOVED: {H}", flush=True)
