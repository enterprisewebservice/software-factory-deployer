#!/usr/bin/env bash
# render-skill-cr.sh <name> <version> [registry-base]
#
# Registry row -> Skill CR YAML on stdout, in the packageRef form
# (operator >= v1.7.51): content by REFERENCE — the CR pins the
# coordinate and the content's SHA-256 digest; the operator fetches
# the body from the registry and refuses content that does not match.
# tier 'registry' = neutral pattern vocabulary for workshop-facing CRs
# (operator's own installSkill writes tier 'mindifact'; nothing branches on it).
# Apply the output into any namespace whose agents should learn the skill:
#   render-skill-cr.sh platform-incident-triage 1.1.0 | oc apply -n user1-agent-workspace -f -
# This is the client-side twin of POST /catalog/install, per-seat capable
# today; the server-side namespace parameter is the named roadmap item.
set -euo pipefail
NAME="${1:?name}"; VER="${2:?version}"; BASE="${3:-https://mindifact.ai/v1}"
python3 - "$NAME" "$VER" "$BASE" <<'PY'
import hashlib, json, sys, urllib.request, urllib.parse
name, ver, base = sys.argv[1:4]
man = json.load(urllib.request.urlopen(f"{base}/{name}/{ver}/mindifact.json"))
content_url = urllib.parse.urljoin(base + "/", man.get("content") or f"{base}/{name}/{ver}/skills/{name}.md")
body = urllib.request.urlopen(content_url).read()
digest = hashlib.sha256(body).hexdigest()
reg_host = urllib.parse.urlparse(base).hostname
ns = man.get("namespace", "agent-office")
deps = man.get("requires", [])
desc = (man.get("description") or "").replace('"', '\\"')
display = " ".join(w.capitalize() for w in name.split("-"))
out = f"""apiVersion: agentoffice.ai/v1alpha1
kind: Skill
metadata:
  name: {name}
  labels:
    app.kubernetes.io/managed-by: agent-office
    agentoffice.ai/skill-tier: registry
  annotations:
    agentoffice.ai/registry: {reg_host}
    agentoffice.ai/pack-ref: {ns}/{name}:{ver}
    agentoffice.ai/manifest: {base}/{name}/{ver}/mindifact.json
    agentoffice.ai/content-url: {content_url}
spec:
  displayName: "{display}"
  description: "{desc}"
  version: "{ver}"
"""
if deps:
    out += "  dependencies:\n"
    for d in deps:
        out += f"    - kind: {d.get('kind','')}\n      name: {d.get('name','')}\n"
out += f"""  source:
    packageRef:
      ref: {name}:{ver}
      digest: {digest}
      registry: {base}"""
print(out)
PY
