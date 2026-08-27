#!/usr/bin/env bash
# render-skill-cr-oci.sh <name> <version> [quay-host]
# Registry-artifact -> Skill CR in the oci:// packageRef form
# (operator >= v1.7.53). The digest is read from the artifact's own
# config blob (contentSha256) — the package self-describes its content.
set -euo pipefail
NAME="${1:?name}"; VER="${2:?version}"
QUAY="${3:-quay-quay-quay-test.apps.salamander.aimlworkbench.com}"
ORG=deanpeterson
REPO="agent-office-skill-${NAME}"
AUTH=$(oc get secret quay-push-secret -n agent-office -o jsonpath='{.data.\.dockerconfigjson}' | base64 -d | python3 -c "import sys,json;a=json.load(sys.stdin)['auths'];print(next(iter(a.values()))['auth'])")
TOK=$(curl -sk "https://$QUAY/v2/auth?service=$QUAY&scope=repository:$ORG/$REPO:pull" -H "Authorization: Basic $AUTH" | python3 -c "import sys,json;print(json.load(sys.stdin)['token'])")
CFGD=$(curl -sk "https://$QUAY/v2/$ORG/$REPO/manifests/$VER" -H "Authorization: Bearer $TOK" -H "Accept: application/vnd.oci.image.manifest.v1+json" | python3 -c "import sys,json;print(json.load(sys.stdin)['config']['digest'])")
CFGFILE=$(mktemp)
curl -skL "https://$QUAY/v2/$ORG/$REPO/blobs/$CFGD" -H "Authorization: Bearer $TOK" -o "$CFGFILE"
python3 - "$NAME" "$VER" "$QUAY" "$ORG" "$REPO" "$CFGFILE" <<'PY'
import json, sys
cfg = json.load(open(sys.argv[6]))
name, ver, quay, org, repo = sys.argv[1:6]
display = " ".join(w.capitalize() for w in name.split("-"))
desc = cfg.get("description","").replace('"','\\"')
out = f"""apiVersion: agentoffice.ai/v1alpha1
kind: Skill
metadata:
  name: {name}
  labels:
    app.kubernetes.io/managed-by: agent-office
    agentoffice.ai/skill-tier: registry
  annotations:
    agentoffice.ai/registry: {quay}
    agentoffice.ai/pack-ref: agent-office/{name}:{ver}
spec:
  displayName: "{display}"
  description: "{desc}"
  version: "{ver}"
"""
deps = cfg.get("requires", [])
if deps:
    out += "  dependencies:\n"
    for d in deps:
        out += f"    - kind: {d.get('kind','')}\n      name: {d.get('name','')}\n"
out += f"""  source:
    packageRef:
      ref: oci://{quay}/{org}/{repo}:{ver}
      digest: {cfg['contentSha256']}
      pullSecretName: quay-pull-secret
      insecureSkipTLSVerify: true"""
print(out)
PY
