#!/usr/bin/env bash
# publish-mindifact.sh <SKILL.md> <namespace/name:version> [--description "..."] \
#   [--requires '<json array>'] [--provides '<json array>'] [--member <pack>]
#
# The thin publish wrapper mindifact's CLI does not ship yet: assembles the
# registry artifact JSON from a SKILL.md body plus coordinates and POSTs it
# to https://mindifact.ai/v1/publish (upsert on namespace/name/version).
# Token: env MINDIFACT_TOKEN, or read from Secret mindifact-registry-token
# in namespace mindifact when oc is logged in.
set -euo pipefail
MD="${1:?SKILL.md path}"; COORD="${2:?namespace/name:version}"; shift 2
NSNAME="${COORD%:*}"; VER="${COORD##*:}"
NS="${NSNAME%/*}"; NAME="${NSNAME#*/}"
DESC=""; REQ="[]"; PROV="[]"; MEMBER=""
while [ $# -gt 0 ]; do case "$1" in
  --description) DESC="$2"; shift 2;;
  --requires) REQ="$2"; shift 2;;
  --provides) PROV="$2"; shift 2;;
  --member) MEMBER="$2"; shift 2;;
  *) echo "unknown flag $1" >&2; exit 2;;
esac; done
TOK="${MINDIFACT_TOKEN:-$(oc get secret mindifact-registry-token -n mindifact -o jsonpath='{.data.token}' | base64 -d)}"
BODY=$(python3 - "$MD" "$NS" "$NAME" "$VER" "$DESC" "$REQ" "$PROV" "$MEMBER" <<'PY'
import json, sys
md, ns, name, ver, desc, req, prov, member = sys.argv[1:9]
art = {"namespace": ns, "name": name, "version": ver, "kind": "skill",
       "description": desc, "requires": json.loads(req), "provides": json.loads(prov),
       "body": open(md).read()}
if member: art["member"] = member
print(json.dumps(art))
PY
)
curl -sf -X POST https://mindifact.ai/v1/publish \
  -H "Authorization: Bearer $TOK" -H 'Content-Type: application/json' -d "$BODY"
echo
