#!/usr/bin/env python3
"""Remove a seat entirely (admin action). The inverse of provision-seat.py.

Runs reset-seat.sh first (agents, repos, catalog entries), then deletes
the seat's namespaces, cluster bindings, ApplicationSet generators,
ArgoCD RBAC rows, Gitea org + user, Mattermost account, group
memberships, the seat Secret and the seats-record. The OpenShift User
and its Keycloak account are kept — the person can come back and get a
fresh seat. With PURGE_LOGIN=1 (the admin page's wholesale removal) the
sign-in goes too: the Keycloak account and the OpenShift User +
Identity, so nothing of the person remains on the platform.
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
PURGE = os.environ.get("PURGE_LOGIN") == "1"
print(f"deprovision {H} (user={USER}, purge_login={PURGE})", flush=True)


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

print("== gitea token + org + user ==", flush=True)
g = secret_data("gitea-admin-credentials", "gitea")
G = f"https://gitea-gitea.{S.APPS}/api/v1"
auth = (g["username"], g["password"])
try:
    tid = secret_data(f"seat-{H}", S.HUB_NS).get("gitea_token_id")
    if tid:
        print("  token revoke:", http("DELETE", f"{G}/users/{H}/tokens/{tid}", auth=auth, headers={"Sudo": H}))
except Exception as e:
    print("  token: none recorded", str(e)[:60])
# Gitea refuses to delete an org that still owns repositories (500).
# reset-seat.sh removed the agent/service repos; anything else the
# person created in their org goes too — the org is theirs alone.
try:
    req = urllib.request.Request(f"{G}/orgs/{ORG}/repos?limit=100")
    req.add_header("Authorization", "Basic " + base64.b64encode(f"{auth[0]}:{auth[1]}".encode()).decode())
    with urllib.request.urlopen(req, timeout=30) as r:
        for repo in json.load(r):
            print("  repo:", repo["name"], http("DELETE", f"{G}/repos/{ORG}/{repo['name']}", auth=auth))
except Exception as e:
    print("  repo listing:", str(e)[:80])
print("  org:", http("DELETE", f"{G}/orgs/{ORG}", auth=auth), " user:", http("DELETE", f"{G}/admin/users/{H}?purge=true", auth=auth))

print("== mattermost account (deactivate) ==", flush=True)
mm = secret_data("mattermost-admin-token", "mattermost")
MM = f"https://mattermost-mattermost.{S.APPS}/api/v4"
hdr = {"Authorization": "Bearer " + mm["token"]}
req = urllib.request.Request(f"{MM}/users/username/{H}", headers=hdr)
try:
    with urllib.request.urlopen(req, timeout=20) as r:
        mid = json.load(r)["id"]
    # permanent when the server allows it (EnableAPIUserDeletion), else deactivate
    code = http("DELETE", f"{MM}/users/{mid}?permanent=true", headers=hdr)
    print("  permanent delete:", code, "" if code == 200 else "-> deactivate: %s" % http("DELETE", f"{MM}/users/{mid}", headers=hdr))
except Exception as e:
    print("  none:", str(e)[:80])

print("== keycloak attendees membership ==", flush=True)
try:
    kc = secret_data("keycloak-admin", "claude-code-agent")
    import urllib.parse
    form = urllib.parse.urlencode({"grant_type": "password", "client_id": "admin-cli",
                                   "username": kc["KEYCLOAK_ADMIN"], "password": kc["KEYCLOAK_ADMIN_PASSWORD"]}).encode()
    with urllib.request.urlopen(urllib.request.Request("https://auth.runtab.io/realms/master/protocol/openid-connect/token", data=form), timeout=20) as r:
        ktok = json.load(r)["access_token"]
    KCR = "https://auth.runtab.io/admin/realms/factory"
    khdr = {"Authorization": "Bearer " + ktok}
    req = urllib.request.Request(f"{KCR}/users?exact=true&username=" + urllib.parse.quote(USER), headers=khdr)
    users = json.load(urllib.request.urlopen(req, timeout=20))
    req = urllib.request.Request(f"{KCR}/groups?search=attendees&exact=true", headers=khdr)
    groups = json.load(urllib.request.urlopen(req, timeout=20))
    if users and groups:
        print("  removed from attendees:", http("DELETE", f"{KCR}/users/{users[0]['id']}/groups/{groups[0]['id']}", headers=khdr))
    else:
        print("  no keycloak account / group")
    if PURGE and users:
        print("  keycloak account deleted:", http("DELETE", f"{KCR}/users/{users[0]['id']}", headers=khdr))
except Exception as e:
    print("  keycloak step skipped:", str(e)[:80])

print("== cluster objects ==", flush=True)
S.oc("delete", "clusterrolebinding", f"seat-{H}-workshop-viewer", "--ignore-not-found")
S.oc("delete", "ns", SNS, NS, "--ignore-not-found", "--wait=false")
if USER:
    print("  workshop group:", S.oc("adm", "groups", "remove-users", "redhat-workshop-users", USER, check=False).strip() or "removed")
    print("  per-user group:", S.oc("delete", "group", USER, "--ignore-not-found", check=False).strip() or "(no output)")
S.oc("delete", "secret", f"seat-{H}", "-n", S.HUB_NS, "--ignore-not-found")
if PURGE and USER:
    print("== openshift user + identity (purge) ==", flush=True)
    idents = S.oc("get", "identity", "-o", "jsonpath={range .items[?(@.user.name=='" + USER + "')]}{.metadata.name}{'\n'}{end}", check=False).split()
    for ident in idents:
        print("  identity:", ident, S.oc("delete", "identity", ident, "--ignore-not-found", check=False).strip())
    print("  user:", S.oc("delete", "user", USER, "--ignore-not-found", check=False).strip())
S.oc("patch", "cm", S.SEATS_CM, "-n", S.HUB_NS, "--type=json", "-p", json.dumps([{"op": "remove", "path": f"/data/{H}"}]), check=False)
print(f"SEAT REMOVED: {H}", flush=True)
