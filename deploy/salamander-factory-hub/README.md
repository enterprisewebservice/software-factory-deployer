# factory-hub — the Red Hat share-out front door

`https://factory.apps.salamander.aimlworkbench.com` — browse the workshop library with no
account; sign in with your email (Keycloak, self-registration) to claim a workbench that is
yours: your own OpenShift project, agent workspace, git organization, chat account, and
workshop instance.

| Tier | Path | What |
|---|---|---|
| Public | `/`, `/hub/assets/*` | landing page (hub assets deliberately NOT under `/assets/` — Showroom's shell loads `./assets/index-*.js`) |
| Auth | `/oauth/*` | **OpenShift oauth-proxy** (release-payload image) → cluster OAuth server (`idp=factory-sso` hint) → Keycloak realm `factory` |
| Signed-in | `/hub/mylab.html`, `/hub/admin.html`, `/api/*` | workbench page (provision, credentials, links, *Restart workshop*), admin seats page, broker API |
| Workshop | `https://showroom-<handle>-showroom-<handle>.apps…` (the seat's **own** hostname) | guarded by the seat's own Red Hat oauth-proxy sidecar with a SubjectAccessReview (`get services` in `showroom-<handle>`) — only the owner and cluster admins pass; the hub only links to it (`/workshop/…` → workbench page) |

The hub never proxies Showroom: a proxy would have to know Showroom's URL layout (that smell produced a terminal 404 and a blank shell before this design). Each seat is on its own hostname behind its own oauth-proxy, exactly how OpenShift guards any workload. Everything is Red Hat platform components plus a small broker: no community proxies, no
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
* **Gitea and Mattermost — the same Keycloak login.** Gitea has an OpenID Connect auth
  source `keycloak` (auto-registration + auto account-linking, see `deploy/salamander-gitea`);
  the seat pre-creates the person's Gitea account linked to that source (`source_id`,
  `login_name`) so their org exists before their first click. Mattermost Team Edition has no
  OpenID Connect, so its GitLab integration points at Gitea's OAuth2 provider (app
  `mattermost` in Gitea, Secret `mattermost-gitea-oauth`; env `MM_GITLABSETTINGS_*` on the
  Deployment). **Team Edition v11 renders no SSO button on its login page** even with the provider active, so chat is entered through the SSO URL `…/oauth/gitlab/login?redirect_to=%2F` — the seat userdata sets `mattermost_url` to it, so every module link and the workbench tile sign the person in automatically; the seat pre-creates the
  Mattermost account as a `gitlab`-auth user keyed to the Gitea user id, so the first SSO
  click lands on it. A generated seat password still exists (Secret `seat-<handle>`) only as
  the fallback if SSO account creation fails; it is no longer shown.
* **The seat agent's Gitea credential** — the operator renders `${GITEA_TOKEN}` into the
  gateway's Gitea MCP header from Secret `<handle>-gitea-token` in the workspace (the same
  contract user1..5 fill from Vault via the refresher, which only knows those five). Dynamic
  seats mint it with the Gitea admin acting as the user (`Sudo`): scopes
  `write:repository, write:organization, write:issue, read:user`, id recorded on the seat
  Secret, **revoked on Remove**. The person never types a Gitea password for it.
* Keycloak realm `factory` enforces DNS-safe usernames at registration
  (`^[a-z0-9][a-z0-9-]{2,19}$`) because every template and module derives names from
  `{user}` (`<user>-agent-workspace`, `<user>-agents`); the Keycloak→Gitea broker button
  is hidden on the login page (no seat depends on it; `storeToken` was never on).

## A seat, minted per person (`files/provision-seat.py`, Job `provision-<handle>`)

**Keycloak group `attendees`** (the genesis template's `gitProvider: auto` publishes to Gitea only for `memberOf: attendees` entities — everyone else goes to `publish:github` into the platform GitHub org) → seat host guard: SA `showroom-<handle>` is the OAuth client (`oauth-redirectreference` → its Route), oauth-proxy sidecar (`--openshift-sar`), cookie Secret `showroom-<handle>-proxy`, RoleBinding `view` for the owner in `showroom-<handle>` → handle = DNS-safe form of the username (`seatlib.sanitize`, collision-suffixed; recorded in
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
* **Remove selected / Remove all attendees** (admin page, `POST /api/admin/remove`): wholesale removal — one
  deprovision Job per seat, in parallel. Admin seats are never included; seats with a running job are skipped.
  With *also delete their sign-in accounts* (default on) the Job runs with `PURGE_LOGIN=1` and also deletes the
  Keycloak account and the OpenShift User + Identity, so nothing of the person remains for the next workshop.
  The page previews the plan (`dry_run`) and requires typing `REMOVE`.

## Deploy

```
oc apply -k deploy/salamander-factory-hub
oc rollout restart deployment/factory-hub -n factory-hub     # after page/broker edits
```

Out of band (done 2026-09-01, never in git): Keycloak realm `factory` registration on
(`verifyEmail=false`, no SMTP); Secret `factory-hub-proxy` (`cookie-secret`). Requires
agent-office-operator ≥ v1.7.63 (label-driven cache scope).

## Open signup: username collisions (hardening, 2026-09-01)

`factory-sso` uses `mappingMethod: add`, which is what lets an htpasswd login and a Keycloak
login share one OpenShift `User` (the seat model). With self-registration open, that also means
**registering a Keycloak username equal to an existing privileged OpenShift user would attach
the new identity to it** — `dpeterson` is cluster-admin via htpasswd and was not a Keycloak
username. Every privileged OpenShift username is therefore *reserved* in Keycloak as a disabled
placeholder (`dpeterson`, `admin-user1..5`; attribute `reserved`), so registration of those
names fails with a conflict. **Standing rule:** any new OpenShift user with elevated rights that
is not itself a Keycloak account must be reserved the same way while signup is open.

Signing in through the GitHub broker is fine: Keycloak imports the GitHub handle as the
username; if it fails the DNS-safe pattern (longer than 20 characters, etc.) the first-login
Review Profile page asks for a valid one, and everything downstream sees an ordinary Keycloak
user. Nothing in the workshop depends on GitHub itself.

## Where hires publish: Gitea, never the person's GitHub

The genesis template's `gitProvider: auto` publishes to Gitea **only when the signed-in user's
catalog entity is `memberOf: attendees`** (a Keycloak group RHDH ingests); anyone else goes to
`publish:github` into the platform's GitHub org with the platform token. So every seat is added
to the Keycloak `attendees` group by the provisioner (and removed on Remove), the Keycloak org
sync runs every minute (RHDH app-config `catalog.providers.keycloakOrg.default.schedule.frequency`;
the live ConfigMap `v1-developer-hub-app-config` is Helm-owned and was patched directly — that
schedule is not declared in git anywhere, so carry it into the next `helm upgrade` values), and
the workbench page holds "Enter the workshop" until Developer Hub's
entity for the person shows `attendees` (`devhub_ready` from the broker). Signing in through the
GitHub broker changes nothing here — the routing never looks at GitHub identity; nobody needs a
GitHub account or any GitHub setup.

## oauth-proxy gotcha: never link to `/oauth/start`

OpenShift's oauth-proxy ignores the `rd` query parameter and records the **request URI it was
called with** as the post-login return target. A login started at `/oauth/start?rd=/x` therefore
returns the browser to `/oauth/start?rd=/x`, which starts another (instant) login — an infinite
redirect loop, seen live on the first real signup (2026-09-01). Start every login by navigating
to the protected page itself (`/workshop/agents-as-staff/`, `/hub/mylab.html`); the proxy signs
the person in and returns them there.

## Reloading the guide without restarting seats

`./reload-guide.sh [namespace ...]` re-renders the workshop guide in place in each
Showroom pod (all `showroom-*` namespaces by default): it pulls the content repo,
re-merges the seat's `user_data` into `antora.yml` and runs `antora` into the
served `/showroom/www`. The pod is not replaced, so the attendee's terminal —
which lives in the same pod — stays connected. `oc rollout restart` on a seat is
only for changes to the pod itself (proxy flags, images); every restart kills the
terminal websocket and shows "Press ↵ to Reconnect".

## Imperative companions (secrets never live in git)

The provisioner copies the Module 6 supply-chain substrate into every self-service seat
(`pac-gitea-webhook`, org webhook → Pipelines-as-Code, `quay-push-secret` linked to the
`pipeline` SA, `chains-public-key`). Two inputs are cluster-only:

* `factory-hub/seat-quay-push-secret` — dockerconfigjson for the org-local Quay robot
  (`deanpeterson+pipeline`), the same credential every fixed seat holds. Create once:
  `oc get secret quay-push-secret -n user3-agent-workspace -o json | <rename to seat-quay-push-secret, ns factory-hub> | oc apply -f -`
* `factory-hub/quay-admin-token` (key `token`, optional) — a Quay OAuth token with
  repo create + permission rights. When present the provisioner pre-creates
  `deanpeterson/<handle>-order-metrics` and grants the robot admin; when absent it logs
  a WARN and the first push must be allowed to create the repo (give the robot Creator
  rights in the org).
