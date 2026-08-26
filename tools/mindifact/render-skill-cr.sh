#!/usr/bin/env bash
# render-skill-cr.sh <name> <version> [registry-base]
#
# Registry row -> Skill CR YAML on stdout, with the exact provenance
# vocabulary the operator's installSkill writes (labels skill-tier/pack,
# annotations registry/pack-ref/manifest/content-url, spec.version and
# dependencies, source.inline fetched from the registry). Apply the output
# into any namespace whose agents should learn the skill:
#   render-skill-cr.sh platform-incident-triage 1.0.0 | oc apply -n user1-agent-workspace -f -
# This is the client-side twin of POST /catalog/install, per-seat capable
# today; the server-side namespace parameter is the named roadmap item.
set -euo pipefail
NAME="${1:?name}"; VER="${2:?version}"; BASE="${3:-https://mindifact.ai/v1}"
python3 - "$NAME" "$VER" "$BASE" <<'PY'
import json, sys, urllib.request
name, ver, base = sys.argv[1:4]
man = json.load(urllib.request.urlopen(f"{base}/{name}/{ver}/mindifact.json"))
content_url = man.get("content") or f"{base}/{name}/{ver}/skills/{name}.md"
# the registry may emit content as a site-relative path
if content_url.startswith("/"):
    from urllib.parse import urlparse
    o = urlparse(base)
    content_url = f"{o.scheme}://{o.netloc}{content_url}"
body = urllib.request.urlopen(content_url).read().decode()
deps = [d for d in (man.get("requires") or []) if isinstance(d, dict) and d.get("kind")]
def yq(s): return json.dumps(s)
print(f"""apiVersion: agentoffice.ai/v1alpha1
kind: Skill
metadata:
  name: {name}
  labels:
    app.kubernetes.io/managed-by: agent-office
    agentoffice.ai/skill-tier: mindifact""")
if man.get("member"):
    print(f"    agentoffice.ai/pack: {man['member']}")
print(f"""  annotations:
    agentoffice.ai/registry: mindifact.ai
    agentoffice.ai/pack-ref: {man.get('namespace','agent-office')}/{name}:{ver}
    agentoffice.ai/manifest: {base}/{name}/{ver}/mindifact.json
    agentoffice.ai/content-url: {content_url}
spec:
  displayName: {yq(man.get('displayName') or name.replace('-',' ').title())}
  description: {yq(man.get('description',''))}
  version: {yq(ver)}""")
if deps:
    print("  dependencies:")
    for d in deps:
        print(f"    - kind: {d['kind']}\n      name: {d['name']}")
print("  source:\n    inline: |")
for line in body.splitlines():
    print(f"      {line}")
PY
