# gitea — SSO to Keycloak (realm factory)

Gitea (quay.io/rhpds/gitea, ns `gitea`) is not GitOps-managed; this directory records the
pieces added on 2026-09-01 so every seat surface uses the one Keycloak login.

* `gitea-config.yaml` — the live `gitea-config` ConfigMap (app.ini) with the `[oauth2_client]`
  block: auto-registration on first SSO login, auto account-linking by username, username from
  `preferred_username`. Apply + `oc rollout restart deployment/gitea -n gitea`.
* OAuth2 authentication source `keycloak` (OpenID Connect, discovery
  `https://auth.runtab.io/realms/factory/.well-known/openid-configuration`, client `gitea`) —
  lives in Gitea's database; created with `gitea admin auth add-oauth`. Client secret in
  Secret `gitea-keycloak-oidc` (ns gitea), never in git.
* Keycloak client `gitea` (confidential, redirect
  `https://gitea-gitea.apps.salamander.aimlworkbench.com/user/oauth2/keycloak/callback`).
* Mattermost (Team Edition — no OpenID Connect) signs in through Gitea's OAuth2 provider using
  Mattermost's GitLab integration; see `deploy/salamander-factory-hub/README.md`.
