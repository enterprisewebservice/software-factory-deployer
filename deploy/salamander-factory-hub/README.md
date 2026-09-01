# factory-hub — the Red Hat–branded share-out front door

`https://factory.apps.salamander.aimlworkbench.com`

| Tier | Path | Who |
|---|---|---|
| Public | `/`, `/assets/*` | anyone — browse the library with no account |
| Auth | `/oauth2/*` | oauth2-proxy ↔ Keycloak realm `factory` (self-registration ON) |
| Gated | `/workshop/agents-as-staff/*` | signed-in session; identity forwarded as `X-Auth-Request-User` |

Deploy: `oc apply -k deploy/salamander-factory-hub` (namespace `factory-hub`).

## One-time, out of band (done 2026-09-01, not in git)

* Keycloak realm `factory`: `registrationAllowed=true`, `verifyEmail=false`
  (no SMTP), `loginWithEmailAllowed=true`, `resetPasswordAllowed=false`.
* Keycloak client `factory-hub` (confidential, standard flow, redirect
  `https://factory.apps.salamander.aimlworkbench.com/oauth2/callback`).
* Secret `factory-hub-oidc` in ns `factory-hub`: `client-id`,
  `client-secret` (from the Keycloak client), `cookie-secret` (32 random bytes, b64).

## Seat provisioning (next milestone)

`files/seat-showroom.yaml` is the per-person Showroom render (from the
user2 seat, `__USER__` / `__PASSWORD__` slots) and `files/reset-seat.sh`
the seat reset engine. The gated location is written to resolve
`showroom-<user>.svc` per signed-in user; until the provisioner lands it
points at the shared Red Hat showroom so the gate is provable.
