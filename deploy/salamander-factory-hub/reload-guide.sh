#!/usr/bin/env bash
# reload-guide.sh [namespace ...] — re-render the workshop guide IN PLACE in
# each Showroom pod (default: every showroom-* namespace). The content
# container serves /showroom/www with a plain http.server, so a fresh
# antora build there goes live without replacing the pod — the terminal
# (same pod) stays up. Mirrors the image's entrypoint: pull, re-merge
# user_data into antora.yml, antora --to-dir. Never `oc rollout restart`
# a seat someone is working in for a content change.
set -uo pipefail
NSS=("$@"); [ ${#NSS[@]} -gt 0 ] || mapfile -t NSS < <(oc get ns -o name | sed 's#namespace/##' | grep -E '^showroom-')
for ns in "${NSS[@]}"; do
  # the Showroom pod is the one with a `content` container (a namespace may also host a hub/nginx pod)
  pod=$(oc get pods -n "$ns" -o json --field-selector=status.phase=Running 2>/dev/null | python3 -c '
import json,sys
for p in json.load(sys.stdin)["items"]:
    if any(c["name"]=="content" for c in p["spec"]["containers"]): print("pod/"+p["metadata"]["name"]); break')
  [ -n "$pod" ] || { echo "$ns: no running pod"; continue; }
  if oc exec -n "$ns" "$pod" -c content -- bash -c '
      set -e; cd /showroom/repo
      # the clone is owned by the build-time user; the container runs as an
      # arbitrary uid, so git needs the safe.directory override per call
      export GIT_CONFIG_COUNT=1 GIT_CONFIG_KEY_0=safe.directory GIT_CONFIG_VALUE_0="*"
      git checkout -q -- content/antora.yml
      git pull -q
      yq -i ".asciidoc.attributes *= load(\"/user_data/user_data.yml\")" content/antora.yml
      # the edition picks its playbook through ANTORA_PLAYBOOK (handsonmode uses its own)
      antora --to-dir=/showroom/www "${ANTORA_PLAYBOOK:-site.yml}" >/tmp/antora.log 2>&1 || { tail -20 /tmp/antora.log; exit 1; }
      git -C /showroom/repo log -1 --format="  rendered %h %s"' 2>&1 | tail -3; then
    echo "$ns: guide reloaded in place"
  else
    echo "$ns: RELOAD FAILED"
  fi
done
