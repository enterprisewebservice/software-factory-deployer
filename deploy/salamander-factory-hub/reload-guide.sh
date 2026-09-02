#!/usr/bin/env bash
# reload-guide.sh [namespace ...] — re-render the workshop guide IN PLACE in
# each Showroom pod (default: every showroom-* namespace). The content
# container serves /showroom/www with a plain http.server, so a rebuild
# there goes live without replacing the pod — the terminal (same pod)
# stays connected. It pulls the content repo, then re-runs the image's
# OWN entrypoint with the final `exec http.server` line removed: antora
# build, nookbag UI bundle, ui-config, www symlink — exactly the boot
# sequence. (Running antora alone overwrites the UI shell's index.html
# with Antora's redirect page: the tabs and terminal vanish.)
set -uo pipefail
# plain word lists: this runs from laptops with bash 3.2 (macOS) as well as Linux
NSS="$*"; [ -n "$NSS" ] || NSS=$(oc get ns -o name | sed 's#namespace/##' | grep -E '^showroom-' | tr '\n' ' ')
for ns in $NSS; do
  # the Showroom pod is the one with a `content` container (a namespace may also host a hub/nginx pod)
  pod=$(oc get pods -n "$ns" -o json --field-selector=status.phase=Running 2>/dev/null | python3 -c '
import json,sys
for p in json.load(sys.stdin)["items"]:
    if any(c["name"]=="content" for c in p["spec"]["containers"]): print("pod/"+p["metadata"]["name"]); break')
  [ -n "$pod" ] || { echo "$ns: no running Showroom pod"; continue; }
  if oc exec -n "$ns" "$pod" -c content -- bash -c '
      set -e; cd /showroom/repo
      # the clone is owned by the build-time user; the container runs as an arbitrary uid
      export GIT_CONFIG_COUNT=1 GIT_CONFIG_KEY_0=safe.directory GIT_CONFIG_VALUE_0="*"
      git checkout -q -- content/antora.yml
      git pull -q
      rev=$(git log -1 --format=%h)
      # 1. the guide itself. The image entrypoint SKIPS the antora build when
      #    content is already present (it only builds on a fresh clone), so
      #    build explicitly — same command, same merged antora.yml.
      yq -i ".asciidoc.attributes *= load(\"/user_data/user_data.yml\")" content/antora.yml
      antora --to-dir=/showroom/www "${ANTORA_PLAYBOOK:-site.yml}" >/tmp/antora.log 2>&1 || { tail -20 /tmp/antora.log; exit 1; }
      # 2. the UI shell: antora just overwrote index.html with its redirect
      #    page, so re-run the entrypoint (minus its final exec) — it re-downloads
      #    the nookbag bundle, rewrites ui-config.yml and the www symlink.
      sed "/^exec python3 -m http.server/d" /usr/local/bin/entrypoint.sh > /tmp/rerender.sh
      cd / && bash /tmp/rerender.sh >/tmp/rerender.log 2>&1 || { tail -25 /tmp/rerender.log; exit 1; }
      test -f /showroom/www/index.html && grep -q "assets/index-" /showroom/www/index.html || { echo "UI shell missing after render"; exit 1; }
      test -L /showroom/www/www || { echo "www symlink missing"; exit 1; }
      echo "  rendered $rev"' 2>&1 | tail -3; then
    echo "$ns: guide reloaded in place (UI shell intact)"
  else
    echo "$ns: RELOAD FAILED"
  fi
done
