#!/usr/bin/env bash
# reset-seat.sh <userN> [agent] [--confirm]
#
# With [agent] given, only that agent's footprint is removed (its
# catalog entries, Argo app, and Gitea repo); other agents in the seat
# are untouched. Without it, every agent in the seat is reset.
#
# Returns a workshop seat to its PRE-HIRE state so the Dev Hub genesis
# template runs clean again — closing the classic re-run failures:
#   * RHDH catalog Location left behind -> 409 at "Register in Catalog"
#   * Gitea repo left behind            -> publish:gitea "repo exists"
#   * Argo app / workspace resources    -> stale agent, name collisions
#
# What it deletes, per agent found in the seat's workspace:
#   1. RHDH catalog Location + orphaned entities for the agent's repo
#   2. The Argo Application (cascade: workspace AW/AG/PVCs go with it,
#      and the operator's AW finalizer deactivates the Mattermost user
#      and archives the channel)
#   3. The Gitea repo <user>-agents/<agent>-agent-gitops
#
# What it NEVER touches: the seat substrate (namespace, RoleBindings,
# Groups, ExternalSecrets, pull/admin secrets, Gitea org+user+token,
# Keycloak/Mattermost seat accounts, the Showroom instance).
#
# Default is DRY-RUN: prints the exact deletion set. Add --confirm to act.
set -euo pipefail
SEAT="${1:?usage: reset-seat.sh <userN> [agent] [--confirm]}"
ONLY=""
CONFIRM=""
for a in "${2:-}" "${3:-}"; do
  case "$a" in
    --confirm) CONFIRM="--confirm";;
    "") ;;
    *) ONLY="$a";;
  esac
done
NS="${SEAT}-agent-workspace"
ORG="${SEAT}-agents"
G="https://gitea-gitea.apps.salamander.aimlworkbench.com"
RHDH="https://v1-developer-hub-rhdh-test.apps.salamander.aimlworkbench.com"

GAU=$(oc get secret gitea-admin-credentials -n gitea -o jsonpath='{.data.username}' | base64 -d)
GAP=$(oc get secret gitea-admin-credentials -n gitea -o jsonpath='{.data.password}' | base64 -d)
RTOK=$(oc get secret agent-office-rhdh-token -n rhdh-test -o jsonpath='{.data.token}' | base64 -d)

AGENTS=$(oc get agentworkstations -n "$NS" --no-headers -o custom-columns=N:.metadata.name 2>/dev/null || true)
if [ -n "$ONLY" ]; then
  AGENTS=$(printf '%s\n' $AGENTS | grep -x "$ONLY" || true)
  [ -z "$AGENTS" ] && { echo "seat $SEAT: agent '$ONLY' not found in $NS"; exit 1; }
fi
if [ -z "$AGENTS" ]; then
  echo "seat $SEAT: no agents in $NS — nothing to reset"
  exit 0
fi

for AGENT in $AGENTS; do
  REPO="${AGENT}-agent-gitops"
  APP="${ORG}-${AGENT}-agent"
  echo "== seat $SEAT / agent $AGENT =="

  # 1. catalog: the Location registered for this repo, plus its entities
  LOCS=$(curl -s -H "Authorization: Bearer $RTOK" "$RHDH/api/catalog/locations" \
    | python3 -c "import sys,json;[print(l['data']['id']) for l in json.load(sys.stdin) if '$ORG/$REPO' in l['data'].get('target','')]")
  ENTS=$(curl -s -H "Authorization: Bearer $RTOK" "$RHDH/api/catalog/entities/by-query?filter=metadata.name=$AGENT" \
    | python3 -c "import sys,json;[print(e['metadata']['uid']) for e in json.load(sys.stdin).get('items',[])]")
  echo "  catalog locations: ${LOCS:-none}"
  echo "  catalog entities (uid): ${ENTS:-none}"
  echo "  argo application: $(oc get application.argoproj.io "$APP" -n openshift-gitops --no-headers 2>/dev/null || echo absent)"
  echo "  gitea repo: $ORG/$REPO -> HTTP $(curl -s -o /dev/null -w '%{http_code}' -u "$GAU:$GAP" "$G/api/v1/repos/$ORG/$REPO")"
  echo "  workspace resources: $(oc get agentworkstation,agentgateway,pvc -n "$NS" --no-headers 2>/dev/null | wc -l | tr -d ' ') objects"
  echo "  installed skills: $(oc get skills -n "$NS" --no-headers 2>/dev/null | wc -l | tr -d ' ') (module 4 hand-applies these; not argo-cascaded)"

  if [ "$CONFIRM" != "--confirm" ]; then
    echo "  DRY-RUN (add --confirm to delete the above)"
    continue
  fi

  for L in $LOCS; do
    curl -s -o /dev/null -w "  location $L deleted: %{http_code}\n" -X DELETE \
      -H "Authorization: Bearer $RTOK" "$RHDH/api/catalog/locations/$L"
  done
  for U in $ENTS; do
    curl -s -o /dev/null -w "  entity $U deleted: %{http_code}\n" -X DELETE \
      -H "Authorization: Bearer $RTOK" "$RHDH/api/catalog/entities/by-uid/$U"
  done

  # 2. argo app with cascade (finalizer already on it); repo deleted after
  #    so the ApplicationSet cannot regenerate mid-teardown
  if oc get application.argoproj.io "$APP" -n openshift-gitops >/dev/null 2>&1; then
    oc delete application.argoproj.io "$APP" -n openshift-gitops --wait=true --timeout=180s
    echo "  argo app deleted (cascaded workspace resources)"
  fi

  # 3. the repo
  curl -s -o /dev/null -w "  gitea repo deleted: %{http_code}\n" -X DELETE \
    -u "$GAU:$GAP" "$G/api/v1/repos/$ORG/$REPO"

  # 4. hand-applied Skill CRs (module 4) — namespaced but not argo-managed,
  #    so the app cascade never removes them; module 4 exercise 1 expects
  #    "No resources found" on a fresh run. Seat-wide state — skipped when
  #    resetting a single agent.
  [ -n "$ONLY" ] || oc delete skills --all -n "$NS" --ignore-not-found >/dev/null 2>&1 || true
  echo "  installed skills deleted: $(oc get skills -n "$NS" --no-headers 2>/dev/null | wc -l | tr -d ' ') remain"

  # settle + verify
  for i in $(seq 1 12); do
    LEFT=$(oc get agentworkstation,agentgateway -n "$NS" --no-headers 2>/dev/null | wc -l | tr -d ' ')
    [ "$LEFT" = "0" ] && break
    sleep 10
  done
  echo "  verify: workspace agent objects: $(oc get agentworkstation,agentgateway -n "$NS" --no-headers 2>/dev/null | wc -l | tr -d ' ')"
  echo "  verify: catalog '$AGENT': $(curl -s -H "Authorization: Bearer $RTOK" "$RHDH/api/catalog/entities/by-query?filter=metadata.name=$AGENT" | python3 -c 'import sys,json;print(len(json.load(sys.stdin).get("items",[])))') entities"
  echo "  verify: gitea repo: HTTP $(curl -s -o /dev/null -w '%{http_code}' -u "$GAU:$GAP" "$G/api/v1/repos/$ORG/$REPO") (want 404)"
done
echo "seat $SEAT reset complete — the genesis template will run clean"
