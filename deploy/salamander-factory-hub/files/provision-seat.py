#!/usr/bin/env python3
"""Mint (or repair) one person's seat. Runs as a Job (SA factory-provisioner).

env SEAT_USER: the OpenShift username (already exists — created by their
first Keycloak login through the hub). Everything is idempotent.

Identity model (service-account terminal, decided 2026-09-01):
  * browser surfaces (console, Dev Hub, GitOps, Gitea SSO) = their real
    Keycloak login;
  * the workshop terminal = the seat's own ServiceAccount, bound to admin
    in the person's workspace — no password IdP, a projected token the
    kubelet rotates, so the session cannot lapse;
  * Mattermost and Gitea local accounts use one generated seat password
    (stored in Secret seat-<handle>, shown on the person's lab page).
Nothing here touches cluster auth config.
"""
import io, json, os, secrets, string, subprocess, sys, tarfile, urllib.request, urllib.parse, urllib.error, base64
sys.path.insert(0, "/scripts")
import seatlib as S

USER = os.environ["SEAT_USER"]
HUB_NS = S.HUB_NS
APPS = S.APPS
CUR = "start"


def step(name):
    global CUR
    CUR = name
    print(f"== {name} ==", flush=True)


def fail(msg):
    print(f"PROVISION FAILED at {CUR}: {msg}", flush=True)
    try:
        h = HANDLE if "HANDLE" in globals() else S.sanitize(USER)
        prev = S.read_seats().get(h, {})
        prev.update({"username": USER, "handle": h, "phase": "error", "step": CUR, "detail": str(msg)[:200]})
        S.write_seat(h, prev)
    except Exception as e:
        print("could not record error:", e)
    sys.exit(1)


def http(method, url, body=None, headers=None, auth=None, ok=(200, 201, 204)):
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(url, data=data, method=method)
    req.add_header("Content-Type", "application/json")
    for k, v in (headers or {}).items():
        req.add_header(k, v)
    if auth:
        req.add_header("Authorization", "Basic " + base64.b64encode(f"{auth[0]}:{auth[1]}".encode()).decode())
    try:
        with urllib.request.urlopen(req, timeout=30) as r:
            raw = r.read()
            return r.status, (json.loads(raw) if raw else None)
    except urllib.error.HTTPError as e:
        return e.code, None


def secret_data(name, ns):
    s = json.loads(S.oc("get", "secret", name, "-n", ns, "-o", "json"))
    return {k: base64.b64decode(v).decode() for k, v in (s.get("data") or {}).items()}


try:
    step("tooling")
    # Arbitrary-uid pod: /usr/local/bin is read-only. The cluster's own oc
    # goes into a writable dir that we prepend to PATH for every subprocess.
    os.makedirs("/tmp/bin", exist_ok=True)
    os.environ["PATH"] = "/tmp/bin:" + os.environ.get("PATH", "")
    if subprocess.run(["which", "oc"], capture_output=True).returncode != 0:
        with urllib.request.urlopen("http://downloads.openshift-console.svc.cluster.local/amd64/linux/oc.tar", timeout=60) as r:
            tarfile.open(fileobj=io.BytesIO(r.read())).extractall("/tmp/bin")
        os.chmod("/tmp/bin/oc", 0o755)
    print(S.oc("whoami").strip())

    step("seat record")
    seats = S.read_seats()
    HANDLE = S.allocate_handle(USER, seats)
    NS, SNS, ORG = f"{HANDLE}-agent-workspace", f"showroom-{HANDLE}", f"{HANDLE}-agents"
    # Repairs keep history (first provision time, last reset).
    rec = dict(seats.get(HANDLE) or {})
    rec.update({"username": USER, "handle": HANDLE, "phase": "provisioning", "step": None, "detail": None})
    rec.setdefault("started", subprocess.check_output(["date", "-u", "+%FT%TZ"], text=True).strip())
    S.write_seat(HANDLE, rec)
    print(f"user={USER} handle={HANDLE}")

    step("email from keycloak")
    EMAIL = f"{HANDLE}@workshop.invalid"
    try:
        kc = secret_data("keycloak-admin", "claude-code-agent")
        form = urllib.parse.urlencode({"grant_type": "password", "client_id": "admin-cli",
                                       "username": kc["KEYCLOAK_ADMIN"], "password": kc["KEYCLOAK_ADMIN_PASSWORD"]}).encode()
        with urllib.request.urlopen(urllib.request.Request("https://auth.runtab.io/realms/master/protocol/openid-connect/token", data=form), timeout=20) as r:
            tok = json.load(r)["access_token"]
        code, users = http("GET", "https://auth.runtab.io/admin/realms/factory/users?exact=true&username=" + urllib.parse.quote(USER), headers={"Authorization": "Bearer " + tok})
        if code == 200 and users and users[0].get("email"):
            EMAIL = users[0]["email"]
    except Exception as e:
        print("keycloak email lookup skipped:", str(e)[:120])
    print("email:", EMAIL)

    step("seat password (mattermost + gitea local accounts)")
    try:
        PW = secret_data(f"seat-{HANDLE}", HUB_NS)["password"]
    except RuntimeError:
        alphabet = string.ascii_letters + string.digits
        PW = "".join(secrets.choice(alphabet) for _ in range(14))
        sec = {"apiVersion": "v1", "kind": "Secret", "metadata": {"name": f"seat-{HANDLE}", "namespace": HUB_NS,
               "labels": {"factory-hub.redhat.com/seat": HANDLE}},
               "type": "Opaque", "data": {"password": base64.b64encode(PW.encode()).decode(),
                                          "email": base64.b64encode(EMAIL.encode()).decode(),
                                          "username": base64.b64encode(USER.encode()).decode()}}
        S.oc("apply", "-f", "-", input=json.dumps(sec))

    step("openshift user + groups")
    try:
        S.oc("get", "user", USER)
    except RuntimeError:
        S.oc("create", "user", USER)
    UID = S.oc("get", "user", USER, "-o", "jsonpath={.metadata.uid}").strip()
    S.oc("adm", "groups", "new", USER, check=False)
    S.oc("adm", "groups", "add-users", USER, USER)
    S.oc("adm", "groups", "add-users", "redhat-workshop-users", USER)

    step("workspace namespace + policy")
    S.oc("create", "ns", NS, check=False)
    S.oc("label", "ns", NS, f"factory-hub.redhat.com/seat={HANDLE}", "--overwrite")
    S.oc("create", "ns", SNS, check=False)
    S.oc("label", "ns", SNS, f"factory-hub.redhat.com/seat={HANDLE}", "--overwrite")
    pol = open("/scripts/seat-policy.yaml").read().replace("__HANDLE__", HANDLE).replace("__USERNAME__", USER)
    S.oc("apply", "-f", "-", input=pol)

    step("operator discovery label")
    # The operator's MANAGED_NAMESPACES env is GitOps-owned (Argo selfHeal
    # reverts live patches within seconds — proven 2026-09-01). Dynamic
    # seats are discovered by label instead (operator >= v1.7.63 unions
    # the env list with namespaces carrying agentoffice.ai/managed=true
    # and restarts itself when the set changes). Reversible by unlabeling.
    S.oc("label", "ns", NS, "agentoffice.ai/managed=true", "--overwrite")

    step("gitea user + org")
    g = secret_data("gitea-admin-credentials", "gitea")
    G = f"https://gitea-gitea.{APPS}/api/v1"
    auth = (g["username"], g["password"])
    if http("GET", f"{G}/users/{HANDLE}", auth=auth)[0] != 200:
        code, _ = http("POST", f"{G}/admin/users", {"username": HANDLE, "email": EMAIL, "password": PW,
                                                     "must_change_password": False, "visibility": "public"}, auth=auth)
        if code not in (201,):
            fail(f"gitea user create -> {code}")
    if http("GET", f"{G}/orgs/{ORG}", auth=auth)[0] != 200:
        code, _ = http("POST", f"{G}/admin/users/{HANDLE}/orgs", {"username": ORG, "visibility": "public"}, auth=auth)
        if code not in (201,):
            fail(f"gitea org create -> {code}")

    step("gitea seat token (agent gateway)")
    # The seat's agent reaches Gitea through its gateway's MCP header,
    # which the operator renders from Secret <handle>-gitea-token (key
    # GITEA_TOKEN) in the workspace — the same contract user1..5 fill
    # from Vault via the refresher. Dynamic seats mint the token here
    # with the Gitea admin acting as the user (Sudo): scoped, recorded
    # by id in the seat Secret, revoked on Remove. No user password.
    have_secret = subprocess.run(["oc", "get", "secret", f"{HANDLE}-gitea-token", "-n", NS], capture_output=True).returncode == 0
    if not have_secret:
        code, tok = http("POST", f"{G}/users/{HANDLE}/tokens",
                         {"name": "seat-agent", "scopes": ["write:repository", "write:organization", "write:issue", "read:user"]},
                         auth=auth, headers={"Sudo": HANDLE})
        if code != 201:
            # a stale token of the same name blocks re-minting: drop it and retry once
            code_l, toks = http("GET", f"{G}/users/{HANDLE}/tokens", auth=auth, headers={"Sudo": HANDLE})
            for t in (toks or []) if code_l == 200 else []:
                if t.get("name") == "seat-agent":
                    http("DELETE", f"{G}/users/{HANDLE}/tokens/{t['id']}", auth=auth, headers={"Sudo": HANDLE})
            code, tok = http("POST", f"{G}/users/{HANDLE}/tokens",
                             {"name": "seat-agent", "scopes": ["write:repository", "write:organization", "write:issue", "read:user"]},
                             auth=auth, headers={"Sudo": HANDLE})
        if code != 201:
            fail(f"gitea token mint -> {code}")
        gsec = {"apiVersion": "v1", "kind": "Secret",
                "metadata": {"name": f"{HANDLE}-gitea-token", "namespace": NS,
                             "labels": {"app.kubernetes.io/managed-by": "factory-hub", "factory-hub.redhat.com/seat": HANDLE}},
                "type": "Opaque", "data": {"GITEA_TOKEN": base64.b64encode(tok["sha1"].encode()).decode()}}
        S.oc("apply", "-f", "-", input=json.dumps(gsec))
        S.oc("patch", "secret", f"seat-{HANDLE}", "-n", HUB_NS, "--type=merge",
             "-p", json.dumps({"data": {"gitea_token_id": base64.b64encode(str(tok["id"]).encode()).decode()}}))

    step("argo applicationset generators")
    for name in ("agent-office-agents-gitea", "seat-services-gitea"):
        o = json.loads(S.oc("get", "applicationset", name, "-n", "openshift-gitops", "-o", "json"))
        gens = o["spec"]["generators"]
        if not any(x.get("scmProvider", {}).get("gitea", {}).get("owner") == ORG for x in gens):
            t = json.loads(json.dumps(next(x for x in gens if "scmProvider" in x)))
            t["scmProvider"]["gitea"]["owner"] = ORG
            gens.append(t)
            S.oc("patch", "applicationset", name, "-n", "openshift-gitops", "--type=json",
                 "-p", json.dumps([{"op": "replace", "path": "/spec/generators", "value": gens}]))

    step("argo rbac rows")
    a = json.loads(S.oc("get", "argocd", "openshift-gitops", "-n", "openshift-gitops", "-o", "json"))
    pol = a["spec"]["rbac"].get("policy", "")
    if f"agent-office/{ORG}-*" not in pol:
        rows = []
        for subj in (USER, UID):
            rows += [f"p, {subj}, applications, get, agent-office/{ORG}-*, allow",
                     f"p, {subj}, applications, sync, agent-office/{ORG}-*, allow",
                     f"p, {subj}, projects, get, agent-office, allow"]
        pol = pol.rstrip("\n") + "\n" + "\n".join(rows) + "\n"
        S.oc("patch", "argocd", "openshift-gitops", "-n", "openshift-gitops", "--type=merge",
             "-p", json.dumps({"spec": {"rbac": {"policy": pol}}}))

    step("mattermost account")
    mm = secret_data("mattermost-admin-token", "mattermost")
    MM = f"https://mattermost-mattermost.{APPS}/api/v4"
    hdr = {"Authorization": "Bearer " + mm["token"]}
    if http("GET", f"{MM}/users/username/{HANDLE}", headers=hdr)[0] != 200:
        code, _ = http("POST", f"{MM}/users", {"email": EMAIL, "username": HANDLE, "password": PW}, headers=hdr)
        if code not in (201,):
            print(f"mattermost user create -> {code} (continuing; chat login may need admin help)")

    step("seat showroom")
    tmpl = open("/scripts/seat-showroom.yaml").read().replace("__USER__", HANDLE).replace("__PASSWORD__", PW)
    tmpl = tmpl.replace(f"    user: {HANDLE}\n", f"    user: {HANDLE}\n    keycloak_user: {USER}\n")
    S.oc("apply", "-f", "-", input=tmpl)
    subprocess.run(["oc", "rollout", "status", f"deployment/showroom-{HANDLE}", "-n", SNS, "--timeout=480s"], check=True)

    step("mark ready")
    rec.update({"phase": "ready", "email": EMAIL})
    rec.setdefault("ready", subprocess.check_output(["date", "-u", "+%FT%TZ"], text=True).strip())
    rec["last_provisioned"] = subprocess.check_output(["date", "-u", "+%FT%TZ"], text=True).strip()
    S.write_seat(HANDLE, rec)
    print(f"SEAT READY: {USER} -> {HANDLE}", flush=True)
except SystemExit:
    raise
except Exception as e:
    fail(e)
