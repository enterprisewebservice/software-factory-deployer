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
import io, json, os, secrets, string, subprocess, sys, tarfile, time, urllib.request, urllib.parse, urllib.error, base64
sys.path.insert(0, "/scripts")
import seatlib as S

USER = os.environ["SEAT_USER"]
HUB_NS = S.HUB_NS
APPS = S.APPS
CUR = "start"


def step(name):
    """Print the step AND record it on the seat, so the workbench page's
    checklist advances in real time instead of jumping from the first
    step to done."""
    global CUR
    CUR = name
    print(f"== {name} ==", flush=True)
    try:
        if "HANDLE" in globals():
            rec = dict(S.read_seats().get(HANDLE) or {})
            rec.update({"username": USER, "handle": HANDLE, "phase": "provisioning", "step": name})
            S.write_seat(HANDLE, rec)
    except Exception as e:
        print("  (step not recorded:", str(e)[:80], ")")


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
    KC_SUB = ""  # the Keycloak user id (OIDC subject): Gitea's direct OAuth2 match key
    KC_TOK, KC_UID = "", ""
    try:
        kc = secret_data("keycloak-admin", "claude-code-agent")
        form = urllib.parse.urlencode({"grant_type": "password", "client_id": "admin-cli",
                                       "username": kc["KEYCLOAK_ADMIN"], "password": kc["KEYCLOAK_ADMIN_PASSWORD"]}).encode()
        with urllib.request.urlopen(urllib.request.Request("https://auth.runtab.io/realms/master/protocol/openid-connect/token", data=form), timeout=20) as r:
            KC_TOK = json.load(r)["access_token"]
        code, users = http("GET", "https://auth.runtab.io/admin/realms/factory/users?exact=true&username=" + urllib.parse.quote(USER), headers={"Authorization": "Bearer " + KC_TOK})
        if code == 200 and users:
            KC_UID = users[0]["id"]
            if users[0].get("email"):
                EMAIL = users[0]["email"]
                KC_SUB = users[0].get("id", "")
    except Exception as e:
        print("keycloak lookup skipped:", str(e)[:120])
    print("email:", EMAIL)

    step("keycloak attendees group (routes hires to Gitea)")
    # The genesis template's gitProvider=auto publishes to Gitea ONLY for
    # users whose catalog entity is memberOf `attendees` (RHDH ingests
    # Keycloak groups); everyone else goes to publish:github into the
    # platform's GitHub org. Every seat is an attendee.
    if KC_TOK and KC_UID:
        KCR = "https://auth.runtab.io/admin/realms/factory"
        hdr = {"Authorization": "Bearer " + KC_TOK}
        code, groups = http("GET", f"{KCR}/groups?search=attendees&exact=true", headers=hdr)
        gid = groups[0]["id"] if code == 200 and groups else ""
        if not gid:
            http("POST", f"{KCR}/groups", {"name": "attendees"}, headers=hdr)
            code, groups = http("GET", f"{KCR}/groups?search=attendees&exact=true", headers=hdr)
            gid = groups[0]["id"] if code == 200 and groups else ""
        if gid:
            code, _ = http("PUT", f"{KCR}/users/{KC_UID}/groups/{gid}", headers=hdr)
            print("attendees membership:", code)
        else:
            print("attendees group unavailable — hires would route to GitHub!")
    else:
        print("no keycloak account for this user (test seat) — skipped")

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
    # The agent gateway pulls its runtime image from the internal Quay
    # with `quay-pull-secret`; the deployer copies that secret into the
    # fixed seats, so a dynamic seat gets the same copy here. Without it
    # the gateway pod sits in ImagePullBackOff and the hire never leaves
    # Pending.
    src = json.loads(S.oc("get", "secret", "quay-pull-secret", "-n", "agent-office", "-o", "json"))
    S.oc("apply", "-f", "-", input=json.dumps({
        "apiVersion": "v1", "kind": "Secret", "type": src["type"],
        "metadata": {"name": "quay-pull-secret", "namespace": NS,
                     "labels": {"app.kubernetes.io/managed-by": "factory-hub"}},
        "data": src["data"],
    }))

    step("operator discovery label")
    # The operator's MANAGED_NAMESPACES env is GitOps-owned (Argo selfHeal
    # reverts live patches within seconds — proven 2026-09-01). Dynamic
    # seats are discovered by label instead (operator >= v1.7.63 unions
    # the env list with namespaces carrying agentoffice.ai/managed=true
    # and restarts itself when the set changes). Reversible by unlabeling.
    S.oc("label", "ns", NS, "agentoffice.ai/managed=true", "--overwrite")
    # Chat provisioning: the operator creates an agent's Mattermost user +
    # private channel (and invites the seat owner) only for namespaces
    # carrying this label — it is what lets a tenant namespace use the
    # central Mattermost admin token without holding it. The deployer
    # labels the fixed seats; without it a hire ran fine but had no chat
    # presence at all (research seat, 2026-09-02).
    S.oc("label", "ns", NS, "agentoffice.ai/chat-provisioning=enabled", "--overwrite")

    step("gitea user + org")
    g = secret_data("gitea-admin-credentials", "gitea")
    G = f"https://gitea-gitea.{APPS}/api/v1"
    auth = (g["username"], g["password"])
    # SSO-linked account (Gitea auth source `keycloak`, id from env): no
    # password — the person signs in to Gitea with their Keycloak login,
    # and Gitea's ACCOUNT_LINKING=auto binds that login to this account.
    code, gu = http("GET", f"{G}/users/{HANDLE}", auth=auth)
    if code != 200:
        # login_name is the Keycloak SUBJECT, not the username: on an OAuth2
        # sign-in Gitea first looks for a user whose login_name equals the
        # provider's user id (the OIDC sub) on this source — a direct match
        # that needs no account-linking or auto-registration at all. With
        # the username there instead, the sign-in fell through to Gitea's
        # "link your account" page and a password nobody has (2026-09-02).
        body = {"username": HANDLE, "email": EMAIL, "must_change_password": False, "visibility": "public",
                "source_id": int(os.environ.get("GITEA_KEYCLOAK_SOURCE_ID", "1")), "login_name": KC_SUB or USER,
                "password": PW}
        code, gu = http("POST", f"{G}/admin/users", body, auth=auth)
        if code not in (201,):
            fail(f"gitea user create -> {code}")
    else:
        # Re-provision / re-signup: the Keycloak account may be new (fresh
        # subject) — keep the direct match current.
        if KC_SUB and (gu or {}).get("login_name") != KC_SUB:
            print("  login_name -> keycloak subject:", http("PATCH", f"{G}/admin/users/{HANDLE}",
                  {"login_name": KC_SUB, "source_id": int(os.environ.get("GITEA_KEYCLOAK_SOURCE_ID", "1"))}, auth=auth)[0])
    GITEA_UID = str((gu or {}).get("id", ""))
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
    # The same Secret is the seat's identity at the MCP gateway front door
    # (agent-office cluster/github-mcp/gateway/callers-auth-policy.yaml):
    # Authorino matches the agent's bearer token against `api_key` (second
    # key, same value) and the annotations say what this seat may call —
    # the shared platform servers plus anything registered in its own
    # namespace. Applied on create AND on re-provision (older seats).
    mcp_identity = {
        "labels": {"agentoffice.ai/mcp-gateway-caller": "true", "authorino.kuadrant.io/managed-by": "authorino"},
        "annotations": {"agentoffice.ai/mcp-caller": f"seat-{HANDLE}",
                        "agentoffice.ai/mcp-namespace": NS,
                        "agentoffice.ai/mcp-servers": "agent-office/gitea,agent-office/ops-metrics,agent-office/golden-path-probe"},
    }
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
        tok_b64 = base64.b64encode(tok["sha1"].encode()).decode()
        gsec = {"apiVersion": "v1", "kind": "Secret",
                "metadata": {"name": f"{HANDLE}-gitea-token", "namespace": NS,
                             "labels": {"app.kubernetes.io/managed-by": "factory-hub", "factory-hub.redhat.com/seat": HANDLE,
                                        **mcp_identity["labels"]},
                             "annotations": dict(mcp_identity["annotations"])},
                "type": "Opaque", "data": {"GITEA_TOKEN": tok_b64, "api_key": tok_b64}}
        S.oc("apply", "-f", "-", input=json.dumps(gsec))
        S.oc("patch", "secret", f"seat-{HANDLE}", "-n", HUB_NS, "--type=merge",
             "-p", json.dumps({"data": {"gitea_token_id": base64.b64encode(str(tok["id"]).encode()).decode()}}))
    else:
        cur = json.loads(S.oc("get", "secret", f"{HANDLE}-gitea-token", "-n", NS, "-o", "json"))
        S.oc("patch", "secret", f"{HANDLE}-gitea-token", "-n", NS, "--type=merge", "-p",
             json.dumps({"metadata": mcp_identity, "data": {"api_key": cur["data"]["GITEA_TOKEN"]}}))

    step("supply chain (pipelines-as-code webhook, quay push, chains key)")
    # Modules 5/6: the platform side the guide says "is already standing".
    # Fixed seats got this by hand on 2026-08-27 (salamander-workspaces/
    # manifests.yaml header); self-service seats get it here, idempotently:
    #   1. Secret pac-gitea-webhook (key `secret`) — the PaC webhook secret the
    #      attendee's Repository CR references (Module 6, Exercise 3)
    #   2. org-level Gitea webhook <handle>-agents -> Pipelines-as-Code
    #      controller (push + pull_request events, json, that secret), so the
    #      merge event lands on a listening pipeline
    #   3. Secret quay-push-secret (org-local robot, canonical copy in
    #      factory-hub/seat-quay-push-secret) LINKED to the `pipeline` SA for
    #      pull+mount — the link is load-bearing: buildah's push reads registry
    #      auth from the SA's linked dockercfg secrets
    #   4. ConfigMap chains-public-key (cosign.pub from
    #      openshift-pipelines/signing-secrets) for the cosign verify step
    #   5. Quay repo <org>/<handle>-order-metrics pre-created with the robot as
    #      admin — only when factory-hub/quay-admin-token (key `token`) exists;
    #      otherwise a WARN (a push cannot auto-create the repo unless the
    #      robot holds Creator rights in the org)
    PAC_URL = f"https://pipelines-as-code-controller-openshift-pipelines.{APPS}"
    if subprocess.run(["oc", "get", "secret", "pac-gitea-webhook", "-n", NS], capture_output=True).returncode != 0:
        S.oc("create", "secret", "generic", "pac-gitea-webhook", "-n", NS, f"--from-literal=secret={secrets.token_hex(24)}")
    hook_secret = secret_data("pac-gitea-webhook", NS)["secret"]
    code, hooks = http("GET", f"{G}/orgs/{ORG}/hooks", auth=auth)
    if code != 200:
        fail(f"gitea org hooks list -> {code}")
    if not any((h.get("config") or {}).get("url") == PAC_URL for h in (hooks or [])):
        code, _ = http("POST", f"{G}/orgs/{ORG}/hooks", {
            "type": "gitea", "active": True, "branch_filter": "*",
            "config": {"url": PAC_URL, "content_type": "json", "secret": hook_secret},
            "events": ["push", "pull_request", "pull_request_sync", "pull_request_label", "pull_request_comment",
                       "pull_request_review", "pull_request_review_request", "pull_request_assign", "pull_request_milestone"]},
            auth=auth)
        if code != 201:
            fail(f"gitea org webhook -> {code}")
    if subprocess.run(["oc", "get", "secret", "quay-push-secret", "-n", NS], capture_output=True).returncode != 0:
        src = json.loads(S.oc("get", "secret", "seat-quay-push-secret", "-n", HUB_NS, "-o", "json"))
        S.oc("apply", "-f", "-", input=json.dumps({
            "apiVersion": "v1", "kind": "Secret", "type": src["type"],
            "metadata": {"name": "quay-push-secret", "namespace": NS,
                         "labels": {"app.kubernetes.io/managed-by": "factory-hub", "factory-hub.redhat.com/seat": HANDLE}},
            "data": src["data"]}))
    for _ in range(45):  # OpenShift Pipelines creates the `pipeline` SA shortly after the namespace appears
        if subprocess.run(["oc", "get", "sa", "pipeline", "-n", NS], capture_output=True).returncode == 0:
            break
        time.sleep(2)
    else:
        fail("pipeline ServiceAccount never appeared in the workspace (OpenShift Pipelines namespace controller)")
    S.oc("secrets", "link", "pipeline", "quay-push-secret", "--for=pull,mount", "-n", NS)
    pub = secret_data("signing-secrets", "openshift-pipelines")["cosign.pub"]
    S.oc("apply", "-f", "-", input=json.dumps({
        "apiVersion": "v1", "kind": "ConfigMap",
        "metadata": {"name": "chains-public-key", "namespace": NS,
                     "labels": {"app.kubernetes.io/managed-by": "factory-hub", "factory-hub.redhat.com/seat": HANDLE}},
        "data": {"cosign.pub": pub}}))
    qcfg = json.loads(secret_data("quay-push-secret", NS)[".dockerconfigjson"])
    qhost = next(iter(qcfg["auths"]))
    robot = base64.b64decode(qcfg["auths"][qhost]["auth"]).decode().split(":")[0]
    qorg, repo = robot.split("+")[0], f"{HANDLE}-order-metrics"
    if subprocess.run(["oc", "get", "secret", "quay-admin-token", "-n", HUB_NS], capture_output=True).returncode == 0:
        qh = {"Authorization": "Bearer " + secret_data("quay-admin-token", HUB_NS)["token"]}
        QAPI = f"https://{qhost}/api/v1"
        code, _ = http("GET", f"{QAPI}/repository/{qorg}/{repo}", headers=qh)
        if code == 404:
            code, _ = http("POST", f"{QAPI}/repository",
                           {"namespace": qorg, "repository": repo, "visibility": "private",
                            "description": f"seat {HANDLE}: order-metrics (Module 6)"}, headers=qh)
            if code not in (200, 201):
                fail(f"quay repo create -> {code}")
        code, _ = http("PUT", f"{QAPI}/repository/{qorg}/{repo}/permissions/user/{robot}", {"role": "admin"}, headers=qh)
        if code != 200:
            fail(f"quay robot grant -> {code}")
    else:
        print(f"  WARN: factory-hub/quay-admin-token absent -> Quay repo {qorg}/{repo} not pre-created; "
              f"the Module 6 push needs {robot} to hold Creator rights in org {qorg}, or add the token", flush=True)

    step("argo applicationset generators")
    for name in ("agent-office-agents-gitea", "seat-services-gitea"):
        o = json.loads(S.oc("get", "applicationset", name, "-n", "openshift-gitops", "-o", "json"))
        gens = o["spec"]["generators"]
        mine = [x for x in gens if x.get("scmProvider", {}).get("gitea", {}).get("owner") == ORG]
        changed = False
        if not mine:
            t = json.loads(json.dumps(next(x for x in gens if "scmProvider" in x)))
            t["scmProvider"]["gitea"]["owner"] = ORG
            gens.append(t); mine = [t]; changed = True
        # A cloned generator carries the SOURCE seat's values (once sent a
        # service into user1's namespace). The templates now derive the
        # namespace from the org, but keep the value truthful anyway.
        for t in mine:
            if (t["scmProvider"].get("values") or {}).get("namespace") != NS and "values" in t["scmProvider"]:
                t["scmProvider"]["values"]["namespace"] = NS; changed = True
        if changed:
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
    # SSO-linked through Gitea (Mattermost Team Edition has no OpenID; its
    # GitLab integration points at Gitea's OAuth2 provider). auth_data is
    # the Gitea user id, so the first "Sign in with your workshop account"
    # lands on this pre-created account instead of minting a duplicate.
    mm_code, mm_user = http("GET", f"{MM}/users/username/{HANDLE}", headers=hdr)
    if mm_code == 200 and mm_user:
        # A previous seat with this username left a (deactivated) account
        # keyed to a Gitea id that no longer exists: reactivate and re-key
        # it to the fresh Gitea account so the SSO link resolves.
        mid = mm_user["id"]
        if mm_user.get("delete_at"):
            print("  reactivating:", http("PUT", f"{MM}/users/{mid}/active", {"active": True}, headers=hdr)[0])
        if str(mm_user.get("auth_data") or "") != GITEA_UID or mm_user.get("auth_service") != "gitlab":
            print("  re-keying auth:", http("PUT", f"{MM}/users/{mid}/auth", {"auth_data": GITEA_UID, "auth_service": "gitlab"}, headers=hdr)[0])
    else:
        body = {"email": EMAIL, "username": HANDLE, "auth_service": "gitlab", "auth_data": GITEA_UID}
        code, _ = http("POST", f"{MM}/users", body, headers=hdr)
        if code not in (201,):
            print(f"mattermost sso user create -> {code}; falling back to a local account")
            code, _ = http("POST", f"{MM}/users", {"email": EMAIL, "username": HANDLE, "password": PW}, headers=hdr)
            if code not in (201,):
                print(f"mattermost user create -> {code} (continuing; chat login may need admin help)")
    # Team membership: the workshop team is invite-only, and a sign-in
    # before the first hire otherwise lands on Mattermost's "no teams"
    # page. The operator adds the person to their agents' PRIVATE channels
    # at hire time; the team itself is joined here. Idempotent.
    mm_code, mm_user = http("GET", f"{MM}/users/username/{HANDLE}", headers=hdr)
    t_code, team = http("GET", f"{MM}/teams/name/{os.environ.get('MM_TEAM', 'agents')}", headers=hdr)
    if mm_code == 200 and t_code == 200:
        print("  team membership:", http("POST", f"{MM}/teams/{team['id']}/members", {"team_id": team["id"], "user_id": mm_user["id"]}, headers=hdr)[0])
    else:
        print(f"  team membership skipped (user {mm_code}, team {t_code})")

    step("seat showroom")
    # Cookie secret for the seat's own oauth-proxy (kept across repairs).
    if subprocess.run(["oc", "get", "secret", f"showroom-{HANDLE}-proxy", "-n", SNS], capture_output=True).returncode != 0:
        psec = {"apiVersion": "v1", "kind": "Secret", "metadata": {"name": f"showroom-{HANDLE}-proxy", "namespace": SNS,
                "labels": {"app.kubernetes.io/managed-by": "factory-hub"}}, "type": "Opaque",
                "data": {"cookie-secret": base64.b64encode(secrets.token_urlsafe(32).encode()).decode()}}
        S.oc("apply", "-f", "-", input=json.dumps(psec))
    # The guide's {password} attribute: self-service seats never see a
    # generated password — every surface is their own sign-up login — so
    # the attribute renders as a reminder sentence wherever it appears.
    # (Instructor seats keep a real value in their own userdata.)
    tmpl = open("/scripts/seat-showroom.yaml").read().replace("__USER__", HANDLE).replace(
        "__PASSWORD__", "the password you chose when you signed up")
    # seat_mode drives the guide's ifeval blocks: a self-service reader
    # only ever sees self-service instructions (no mention of other seat
    # types); instructor seats default to seat_mode=seat in the content.
    tmpl = tmpl.replace("__KEYCLOAK_USER__", USER).replace("__SEAT_MODE__", "self-service")
    if "__" in tmpl.split("user_data.yml")[1][:600]:
        fail("userdata placeholders left unreplaced")
    S.oc("apply", "-f", "-", input=tmpl)
    # Showroom renders the guide from userdata at pod start only, so a
    # ConfigMap-only change would leave the old guide serving. A hash of
    # the rendered userdata on the pod template rolls the pod when it
    # changes (no-op when unchanged). Plain text hashing — no YAML lib.
    import hashlib
    ud_doc = next((d for d in tmpl.split("\n---\n") if f"name: showroom-{HANDLE}-userdata" in d), "")
    if not ud_doc:
        fail("userdata ConfigMap not found in the rendered seat template")
    digest = hashlib.sha256(ud_doc.encode()).hexdigest()[:16]
    S.oc("patch", "deployment", f"showroom-{HANDLE}", "-n", SNS, "--type=merge",
         "-p", json.dumps({"spec": {"template": {"metadata": {"annotations": {"factory-hub.redhat.com/userdata-sha": digest}}}}}))
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
