# field-content: ordering this workshop through RHDP

This directory is the Helm chart that RHDP's **Field Sourced Content - OpenShift Base** catalog item syncs. It follows the Ansible pattern of https://github.com/rhpds/field-sourced-content-template: OpenShift GitOps creates a Job in `software-factory-deployer`, the Job clones this repository and runs `run_local.yml`, which is the `ocp4_workload_software_factory` workload.

## Order parameters

| Field | Value |
|---|---|
| Cloud provider | CNV (the item's own recommendation; AWS only for AWS integration) |
| Cluster size | multi-node (OpenShift AI and the agent platform do not fit SNO) |
| OpenShift version | the newest offered (4.21 or later: OpenShift AI 3.x and the MCP gateway operator need it) |
| Models-as-a-Service model | a Claude model. The order's LiteMaaS key becomes the default brain; the small open models in the list do not call tools reliably, and Modules 3 to 7 need tool calls |
| Git repository URL | `https://github.com/enterprisewebservice/software-factory-deployer` |
| Revision | `main` (or the branch under test) |
| Path | `field-content` |

RHDP creates Argo Application `field-content` in `openshift-gitops` (project `default`, no prune, no self-heal) and stops watching. From there the deployment is yours to read in Argo and OpenShift.

## Self-service rule

Nobody is watching when an attendee's cluster is ordered. Every fix goes into this repository, never onto a cluster by hand; the Job is idempotent and retries itself (`backoffLimit: 2`, three-hour deadline), every network step inside it retries on its own, and the Job keeps off the control-plane nodes (RHDP leaves them schedulable, and one of them had a flaky DNS path on 13 October 2026); every stage must tolerate what the base already installed. The first order (13 October 2026, CNV multi-node, OpenShift 4.22.16, 2 users) proved the path end to end after three fixes that are now in the code: no cluster-wide CSV listing (788 copied CSVs took the Job out of memory), no second OperatorGroup next to the base's OpenShift AI install, and a CSV wait limited to subscriptions this run created.

One Argo CD behaviour to know: when the hook Job fails, the retried Job is built from the manifests of the operation that started, not from a newer commit, while the Job itself clones the branch tip. Chart and role therefore tolerate each other across one revision (for example both names of the runner namespace variable). A clean new order never hits this.

## Where the stages stand (13 October 2026)

| Stage | State on an RHDP order | Notes |
|---|---|---|
| operators | works | skips what the base installed; subscribes the agent-office operator from `quay.io/dpeterso/agent-office-operator-catalog:v1.7.81`, with the order's model endpoint wired in as the hiring form's recommender |
| gitea | works | RHPDS gitea-operator, admin + user1..N, per-user orgs, tokens, Secrets, Argo AppProject + two ApplicationSets (agents, seat services; resources preserved if a generated Application is ever deleted); the operator's app.ini copied with OAuth2 on and repository archives on the repositories volume (the pod's data directory is not persistent); archive records purged every run |
| keycloak_users | works | base realm `sso`: attendees group, `rhdh` and `gitea` OIDC clients, provider roles |
| rhdh | works | chart 1.9.2 at `devhub.<domain>`, OIDC sign-in, Gitea + Argo CD, RBAC, the workshop's own plugins; the hire template is mirrored with this cluster's Gitea and the published OpenClaw image |
| agent_platform | works | platform namespace, SCC + ClusterRole, attendee group, the operator's CSV, the catalog Service name Developer Hub proxies to, the console plugin |
| rhoai_maas | works | ModelConnection `rhdp-litemaas` (marked default brain) from the LiteMaaS key RHDP issued, offered to the attendees group |
| mattermost | works | Mattermost + PostgreSQL at `mattermost-mattermost.<domain>`, Gitea sign-in, admin token, agents team, bridge |
| seats | works | `userN-agent-workspace`: RBAC, quota, limits, isolation, Gitea token, SCC and bridge bindings, the operator's tenant annotations (`agentoffice.ai/user`, `agentoffice.ai/groups`) |
| mcp_gateway | works | Kuadrant instance, MCP gateway on the OpenShift AI gateway class, policies, Gitea MCP server (54 tools) |
| showroom, userinfo | work | one guide per seat; URLs and the workshop password on the order page |
| verify | works | Gitea, Developer Hub, Mattermost and the first guide answer; the MCP registration is Ready |
| quay | works (verified on 9cwxl, 13 October 2026, including a re-run: every step idempotent) | the cluster's own registry: Quay operator (stable-3.18) and a managed QuayRegistry (postgres, redis, NooBaa bucket, route, wildcard TLS; Clair off) at `quay-quay-registry.<domain>`; superuser `quayadmin` (Secret `registry/quay-admin`), organization `workshop`, robots `mirror` (admin team), `pull` (read on every repository by default permission) and one per seat (creator team: a seat's pipeline pushes a new repository into being and owns it); the workshop's skill artifacts mirrored from `quay.io/dpeterso` with digests preserved; Tekton Chains keyed with a cosign key pair generated in the Job (`openshift-pipelines/signing-secrets`). Seats then get `quay-push-secret`/`quay-pull-secret` on the `pipeline`/`default` service accounts, `pac-gitea-webhook`, a `chains-public-key` ConfigMap and an org webhook into Pipelines-as-Code; the gitea stage's second ApplicationSet adopts any seat-org repository carrying `deploy/deployment.yaml` |
| model_desk | works | the facilitator's Claude lane: Secret `model-desk/model-desk-gcp-adc` (Vertex application-default credential, the guide's first step) → LiteLLM proxy in front of Claude on Vertex AI, admin key, agent key, ModelConnection `claude-work` (Opus 5, Sonnet 5, Opus 4.8, Opus 4.6) as the default brain; without the Secret the stage reports it and the order stays on LiteMaaS |
| OpenShift AI MaaS under the connections (Module 7 swap), guide content for the RHDP flavour | not yet | |

Images: everything the workshop needs is public under `quay.io/dpeterso` (mirror map in `docs/mirror-to-quay-io.txt`; the operator, bundle and catalog are pushed there by the operator repository's own pipelines on the reference cluster, robot account `dpeterso+salamander`). The three skill-artifact repositories the quay stage mirrors (`agent-office-skill-platform-incident-triage`, `agent-office-pack-supply-chain-ops`, `agent-office-skill-golden-path-service`) are published the same way; `docs/publish-artifacts-to-quay-io.md` is the runbook.

## What an order does

`values.yaml` runs every stage, in this order: `operators`, `gitea`, `keycloak_users`, `rhdh`, `agent_platform`, `rhoai_maas`, `mattermost`, `quay`, `seats`, `mcp_gateway`, `model_desk`, `showroom`, `verify`, `userinfo`. The registry instance itself is created at the end of `operators`, so it initialises while the other stages run and `quay` only waits for it. Each is idempotent and skips what already exists, so a second sync of the same order converges instead of failing. The stages that exist only for the agent platform read `agent_office_catalog_image`; with it empty the order still produces Gitea, Developer Hub, Mattermost, the MCP gateway, the seats and the guides, and the operator-dependent pieces are skipped with a message in the Job log.

`N` seats come from RHDP's `multi_user.num_users` when the order is multi-user, else 5. Each seat has one password everywhere: the base creates `userN` in its Keycloak with a password of its own and never passes it to the chart, so the deployer reads it back from the base's `KeycloakRealmImport` and gives Gitea (and through it Mattermost) and the seat's guide the same value; without a realm import every seat gets one generated password. The lab guides are built from https://github.com/enterprisewebservice/showroom-openshift-software-factory with the console, API, Gitea, Developer Hub and Mattermost URLs and the seat's password filled in. The ConfigMap `software-factory-userinfo` (label `demo.redhat.com/userinfo`) carries the URLs and `password_userN`, which RHDP shows on the order page.

## Attendee run-through of 13 October 2026 (order 9cwxl)

Modules 1, 2 and 5 pass as `user1`; Module 4's install path passes through the registry reference. What does not pass, and why:

| Where | What an attendee sees | Cause and status |
|---|---|---|
| Module 3, exercise 1 (and every tool-driven step after it) on the order's own model | the agent answers with the literal text of a tool call, or invents results | the order's LiteMaaS key is scoped to `llama-scout-17b`, which does not reliably emit tool calls through the proxy. Resolved by the `model_desk` stage: on Claude Sonnet 5 the same exercises pass (Module 3's identity and repositories from real Gitea calls, Module 4's four-section triage, Module 5's headless `USER=user1`). |
| Module 4, exercise 2 | passes (13 October 2026, re-verified from a seat): tags `1.0.0`, `1.1.0`, `1.1.1`, the pinned content hash, the pack's skill and tool members | was: the guide hardcoded the reference cluster's Quay and a `quay-pull-secret` the seat did not have. Now the guide reads `{artifact_registry}`/`{registry_org}` (the deployer fills them from the cluster's own Quay), the seat carries `quay-pull-secret`, and the artifacts are mirrored from their public home with digests identical to the source. |
| Module 4, exercise 5 and Module 5's governed prompt | no `metrics_*` tools | the `ops-metrics` MCP server is a reference-cluster demo and is not deployed here. |
| Module 5, exercise 1 probe | `localhost:18789` refuses | the runtime binds the HTTP face to the pod address; the Service name works. |
| Module 6 | passes end to end with the real dev agent (13 October 2026, seat `user1`, Dean signed in, the UI driven as an attendee): hire from the OpenClaw Agent template (golden-path-service 1.0.6, governed Gitea, Claude Opus 5), intent in `user1-dev`, pull request with 12 files seven minutes later (the agent ran the service against the live orders API and ten unit tests first), merge as `user1`, PipelineRun in 10 seconds and Completed in 5m36s, `main` plus `.sig`, `.att`, `.sbom`, `.dockerfile`, SLSA v0.2 provenance verified with cosign against the seat's key, Application Healthy 78 seconds later, `/healthz` and the MCP handshake, HTTPRoute + MCPServerRegistration Ready with 2 tools in 35 seconds, the operator's "Governed tools changed" post, the assistant listing both `user1_orders_` tools and answering Morgan's Monday question (75 stuck orders, a 156-order week) | two findings fixed: the pipeline's buildah step requested 4.6 CPU and 8 GiB and never scheduled on an RHDP worker (1.0.6 carries a `taskRunSpecs` block); Gitea's repository archives lived on the pod's container filesystem, so a Gitea restart broke every later hire with `TAR_BAD_ARCHIVE` (archives now on the repositories volume, records purged every run). One platform quirk left: two agents in one namespace pinning different versions of the same skill name share one Skill resource (Argo warns, the operator flips the older agent's workspace every few minutes); a workshop day hires both from the same catalog, so both pins match. |
| Module 7 | not runnable | a second brain is not built for this flavour yet (OpenShift AI MaaS lane). |
| Guide text | `workshop` identity provider, `admin-userN`, Vault and ESO refreshers | the ordered cluster signs in through `rhbk`, has no admin persona, and delivers seat tokens as plain Secrets (the guide's self-service text matches this flavour better than its fixed-seat text). |

Fixes that came out of the run are in this repository: per-seat passwords adopted from the base, Gitea tokens minted once, the Authorino TLS override made conditional, Gitea's OAuth2 provider enabled, Mattermost allowed to reach Gitea on a private ingress address, the chat sign-in card made uncacheable (a cached card looked like a sign-in loop), an Argo CD project role so attendees see their applications, and the chat bridge minting one access token per bot instead of one per minute.

## Reading the result

```bash
oc get application field-content -n openshift-gitops
oc get jobs,pods -n software-factory-deployer
oc logs -n software-factory-deployer job/field-content-software-factory -f
oc get configmap software-factory-userinfo -n software-factory-deployer -o yaml
```

A re-sync of the Argo Application reruns the Job (`BeforeHookCreation` deletes the previous one). Pushing to the ordered revision and syncing is the iteration loop; every new order is a fresh-cluster test.

## Values RHDP injects

`deployer.domain`, `deployer.apiUrl`, `gitops.{repoURL,revision,path}`, `multi_user.{num_users,users}` for multi-user orders, and `litemaas.{enabled,apiUrl,apiKey,model}` when the catalog provisions a LiteMaaS virtual key. The key is written to Secret `field-content-litemaas`, never onto the Job's command line; the MaaS stage will read it as the external model provider.

## Local check before pushing

```bash
helm lint field-content
helm template sf field-content --set deployer.domain=apps.example.com --set multi_user.num_users=2
ansible-playbook --syntax-check run_local.yml
```
