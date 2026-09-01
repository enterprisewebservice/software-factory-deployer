#!/usr/bin/env bash
# Job wrapper around reset-seat.sh: fetch oc, run the seat reset, record phase.
set -uo pipefail
H="${SEAT_HANDLE:?}"
mkdir -p /tmp/bin; export PATH="/tmp/bin:$PATH"
if ! command -v oc >/dev/null; then
  curl -s http://downloads.openshift-console.svc.cluster.local/amd64/linux/oc.tar | tar -x -C /tmp/bin && chmod 755 /tmp/bin/oc
fi
REC=$(oc get cm factory-seats -n factory-hub -o jsonpath="{.data.$H}")
if bash /scripts/reset-seat.sh "$H" --confirm; then
  NEW=$(printf '%s' "$REC" | python3 -c 'import json,sys,datetime; r=json.load(sys.stdin); r["phase"]="ready"; r["last_reset"]=datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"); print(json.dumps(r))')
else
  NEW=$(printf '%s' "$REC" | python3 -c 'import json,sys; r=json.load(sys.stdin); r["phase"]="error"; r["step"]="reset"; print(json.dumps(r))')
fi
oc patch cm factory-seats -n factory-hub --type=merge -p "$(python3 -c 'import json,sys; print(json.dumps({"data":{sys.argv[1]: sys.argv[2]}}))' "$H" "$NEW")"
