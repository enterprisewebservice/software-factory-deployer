#!/usr/bin/env bash
# refresh-template-mirror.sh — re-push the Dev Hub template mirror
# (Gitea workshop-config/software-templates) from the pinned template
# branch, applying the deployer's registration rewrite, then make Dev Hub
# re-read it. One command after any change on the template branch.
# Mirrors roles/ocp4_workload_software_factory/tasks/rhdh_gitea_templates.yml
# step for step (full clone — Gitea rejects shallow pushes; a temporary
# admin token minted and revoked here). bash 3.2 safe.
set -uo pipefail
HOST="${GITEA_HOST:-gitea-gitea.apps.salamander.aimlworkbench.com}"
REPO="${TEMPLATE_REPO:-https://github.com/deanpeterson/tssc-dev-multi-ci.git}"
REF="${TEMPLATE_REF:-devspaces-workspace-for-agents}"
ORG="${MIRROR_ORG:-workshop-config}"; MIRROR="${MIRROR_REPO:-software-templates}"
U=$(oc get secret gitea-admin-credentials -n gitea -o jsonpath='{.data.username}' | base64 -d)
P=$(oc get secret gitea-admin-credentials -n gitea -o jsonpath='{.data.password}' | base64 -d)
api() { /usr/bin/curl -s -u "$U:$P" -H 'Content-Type: application/json' "$@"; }
TOK_JSON=$(api -X POST "https://$HOST/api/v1/users/$U/tokens" -d "{\"name\":\"mirror-refresh-$(date +%s)\",\"scopes\":[\"all\"]}")
TOK=$(printf '%s' "$TOK_JSON" | python3 -c 'import json,sys; d=json.load(sys.stdin); print(d["sha1"])'); TID=$(printf '%s' "$TOK_JSON" | python3 -c 'import json,sys; print(json.load(sys.stdin)["id"])')
trap 'api -X DELETE "https://$HOST/api/v1/users/$U/tokens/$TID" >/dev/null; rm -rf "$W"' EXIT
W=$(mktemp -d); git clone -q --branch "$REF" "$REPO" "$W/src"
cd "$W/src"
python3 - "$HOST" <<'PY'
import re, sys; host=sys.argv[1]
p='samples/templates/openclaw-agent-genesis/template.yaml'; s=open(p).read()
def sub(rx, rep):
    global s; s, n = re.subn(rx, rep, s, flags=re.M); return n
n=[sub(r"^(\s+)default: ''$", lambda m: f"{m.group(1)}default: '{host}'"),
   sub(r"^(\s+)default: github$", r"\1default: auto"),
   sub(r"^      if: .*'false' if .*else 'true'.*$", "      if: ${{ false }}"),
   sub(r"^      if: .*'true' if .*else 'false'.*$", "      if: ${{ true }}"),
   sub(r"^  name: openclaw-agent-create$", "  name: openclaw-agent-genesis"),
   sub(r"^  title: OpenClaw Agent \(GitHub direct\)$", "  title: OpenClaw Agent")]
assert n==[1]*6, f"registration rewrite did not land exactly once: {n}"
assert s.count(f"default: '{host}'")==1
open(p,'w').write(s); print("rewrite applied:", n)
PY
git -c user.name=software-factory-deployer -c user.email="deployer@$HOST" commit -q -am "workshop registration rewrite: giteaHost=$HOST (gitProvider stays auto)"
git push -q --force "https://$U:$TOK@$HOST/$ORG/$MIRROR.git" HEAD:refs/heads/main && echo "mirror pushed: $(git log -1 --format=%h) from $REF"
# make Dev Hub re-read the Location that targets the mirror
RT=$(oc get secret agent-office-rhdh-token -n rhdh-test -o json | python3 -c "import json,sys,base64; d=json.load(sys.stdin)['data']; print(base64.b64decode(d[next(iter(d))]).decode().strip())")
LOC=$(oc exec -n rhdh-test deploy/v1-developer-hub -c backstage-backend -- sh -c "curl -s -H 'Authorization: Bearer $RT' 'http://localhost:7007/api/catalog/entities?filter=kind=location'" 2>/dev/null | python3 -c "
import json,sys
for e in json.load(sys.stdin):
    if '$MIRROR' in (e['spec'].get('target') or ''): print(e['metadata']['name']); break")
[ -n "$LOC" ] && oc exec -n rhdh-test deploy/v1-developer-hub -c backstage-backend -- sh -c "curl -s -o /dev/null -w 'dev hub location refresh -> %{http_code}\n' -X POST -H 'Authorization: Bearer $RT' -H 'Content-Type: application/json' -d '{\"entityRef\":\"location:default/$LOC\"}' http://localhost:7007/api/catalog/refresh" 2>/dev/null
