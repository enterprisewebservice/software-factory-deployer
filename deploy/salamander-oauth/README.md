# Cluster login for workshop seats (verified live 2026-08-25)

Three additive identity providers on the cluster `OAuth/cluster` singleton —
the pre-existing admin IdP is never touched:

| IdP name      | Type     | mappingMethod | Who / why |
|---------------|----------|---------------|-----------|
| `htpasswd`    | HTPasswd | claim         | Pre-existing platform admin (dpeterson). DO NOT rename — renaming an IdP orphans its existing identities. |
| `workshop`    | HTPasswd | **add**       | Seats user1..user5, non-admin (no rolebindings granted). Exists because `oc login -u -p` in the Showroom terminal needs a password-capable IdP — OIDC IdPs only support web login. Secret `workshop-users-htpasswd` in `openshift-config` (bcrypt, one shared workshop password). |
| `factory-sso` | OpenID   | **add**       | Keycloak realm `factory` (issuer `https://auth.runtab.io/realms/factory`), which federates Gitea + GitHub. Console button for the same identity used by Dev Hub. Client `openshift-console` in the realm; secret `factory-sso-client-secret` in `openshift-config`. Callback: `https://oauth-openshift.<apps-domain>/oauth2callback/factory-sso`. |

`mappingMethod: add` on both new IdPs is load-bearing: `user1` arriving via
password login and via Keycloak SSO merge into the SAME OpenShift User
instead of erroring with "user already exists".

The IdP list applied (see `oauth-identity-providers.yaml` for the exact
value; secrets are created imperatively and never committed):

```bash
htpasswd -Bb <file> user1 "<workshop password>"   # ... user2..user5
oc create secret generic workshop-users-htpasswd -n openshift-config --from-file=htpasswd=<file>
oc create secret generic factory-sso-client-secret -n openshift-config --from-literal=clientSecret=<keycloak client secret>
oc patch oauth cluster --type=json --patch-file=<patch adding the two entries>
```

Workshop-stage encoding (keycloak_users.yml scope): the SAME seat list must
exist in Keycloak (`factory` realm, group `attendees`), Gitea (user +
`<user>-agents` org), and the `workshop` htpasswd secret — one username, one
shared password, three surfaces. Showroom seat identity is per-release
`user_data` in `deploy/salamander-showroom/values.yaml`.
