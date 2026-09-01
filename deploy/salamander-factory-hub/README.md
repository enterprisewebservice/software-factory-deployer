# factory-hub — the Red Hat share-out front door

`https://factory.apps.salamander.aimlworkbench.com` — browse the workshop library with no
account; sign in with your email (Keycloak, self-registration) to claim a workbench that is
yours: your own OpenShift project, agent workspace, git organization, chat account, and
workshop instance.

| Tier | Path | What |
|---|---|---|
| Public | `/`, `/assets/*` | landing page |
| Auth | `/oauth/*` | **OpenShift oauth-proxy** (release-payload image) → cluster OAuth server (`idp=factory-sso` hint) → Keycloak realm `factory` |
| Signed-in | `/hub/mylab.html`, `/hub/admin.html`, `/api/*` | workbench page (provision, credentials, links, *Restart workshop*), admin seats page, broker API |
| Gated | `/workshop/agents-as-staff/*` | the person's **own** Showroom (`showroom-<handle>.svc`), resolved per request via nginx `auth_request` → broker |

Everything is Red Hat platform components plus a small broker: no community proxies, no
custom identity providers, nothing in cluster auth config is touched.

## Identity model

* **Browser surfaces** (console, Developer Hub, OpenShift GitOps, Gitea SSO) — the person's
  Keycloak account. OpenShift creates their `User` on first login (`factory-sso`, OIDC,
  `mappingMethod: add`).
* **Workshop terminal** — the seat's own ServiceAccount (`showroom-<handle>`), which the
  Showroom chart already creates. It is `admin` in `<handle>-agent-workspace`, has the
  `workshop-viewer` ClusterRole (cluster-scoped reads the modules need), and nothing else.
  Its projected token is rotated by the kubelet, so the session never lapses. It cannot see
  other seats and is not in `system:authenticated:oauth` (no self-provisioning).
* **Mattermost and Gitea local accounts** — one generated seat password (Secret
  `seat-<handle>` in `factory-hub`, shown on the workbench page).

## A seat, minted per person (`files/provision-seat.py`, Job `provision-<handle>`)

handle = DNS-safe form of the username (`seatlib.sanitize`, collision-suffixed; recorded in
ConfigMap `factory-seats`) → `<handle>-agent-workspace` (RoleBindings for the user and the
terminal SA, ResourceQuota, LimitRange, isolation NetworkPolicy, codex ExternalSecret,
label `agentoffice.ai/managed=true` for operator discovery) → `showroom-<handle>` (from
`files/seat-showroom.yaml`) → groups (`<user>`, `redhat-workshop-users`) → Gitea user +
`<handle>-agents` org → generator blocks on both ApplicationSets → ArgoCD RBAC rows (name +
UID) → Mattermost account. Idempotent; re-running repairs a seat.

* **Reset** (`reset-entry.sh` → `reset-seat.sh <handle> --confirm`): agents, repos, catalog
  entries, hand-applied skills — back to Module 1. Self-service on the workbench page; admins
  from the admin page.
* **Remove** (`deprovision-seat.py`, admins): everything above, gone; the login stays.

## Deploy

```
oc apply -k deploy/salamander-factory-hub
oc rollout restart deployment/factory-hub -n factory-hub     # after page/broker edits
```

Out of band (done 2026-09-01, never in git): Keycloak realm `factory` registration on
(`verifyEmail=false`, no SMTP); Secret `factory-hub-proxy` (`cookie-secret`). Requires
agent-office-operator ≥ v1.7.63 (label-driven cache scope).
