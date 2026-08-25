# Gitea on salamander (multi-user workshop testing)
Install: `oc apply -k https://github.com/rhpds/gitea-operator/OLMDeploy` (operator, cluster-wide),
then the Gitea CR below. The workload's gitea.yml stage configures users/orgs/tokens on top.

## Login chain via Keycloak (verified 2026-08-24)

Attendee sign-in is Dev Hub → Keycloak (realm `factory`) → **Gitea** as a brokered OIDC IdP.
Three things make it work, all of which the workshop stages must encode:

1. **Gitea's OAuth2 provider is disabled by the operator's default app.ini** (`[oauth2] ENABLED = false`
   → `/login/oauth/authorize` returns 403 Forbidden). Fix is declarative: `gitea.yaml` sets
   `giteaConfigMapName: gitea-config-factory` (custom `app.ini` in this directory with
   `[oauth2] ENABLED = true`). `giteaHostname` must be set alongside it (operator requirement).
2. **The broker OAuth app** (`keycloak-broker`, redirect `…/realms/factory/broker/gitea/endpoint`)
   carries `skip_secondary_authorization: true` so attendees never see a consent screen.
   ⚠️ Gitea's `PATCH /api/v1/user/applications/oauth2/{id}` **regenerates the client secret** on
   every call — any update must immediately write the returned secret into the Keycloak IdP
   (`identity-provider/instances/gitea` → `config.clientSecret`), or token exchange fails with
   "invalid client secret".
3. **Keycloak first-login auto-link**: realm `factory` has flow `auto-link-broker`
   (`idp-create-user-if-unique` + `idp-auto-link`, both ALTERNATIVE) bound as the gitea IdP's
   `firstBrokerLoginFlowAlias`, so a Gitea `userN` binds silently to the pre-created Keycloak
   `userN` (attendees group) instead of prompting "account already exists". Bound to the **gitea
   IdP only** — GitHub keeps the default confirm-link flow, since GitHub usernames are not ours
   to trust for silent linking.
