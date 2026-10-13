# field-content: ordering this workshop through RHDP

This directory is the Helm chart that RHDP's **Field Sourced Content - OpenShift Base** catalog item syncs. It follows the Ansible pattern of https://github.com/rhpds/field-sourced-content-template: OpenShift GitOps creates a Job in `software-factory-deployer`, the Job clones this repository and runs `run_local.yml`, which is the `ocp4_workload_software_factory` workload.

## Order parameters

| Field | Value |
|---|---|
| Cloud provider | CNV (the item's own recommendation; AWS only for AWS integration) |
| Cluster size | multi-node (OpenShift AI and the agent platform do not fit SNO) |
| OpenShift version | the newest offered (4.21 or later: OpenShift AI 3.x and the MCP gateway operator need it) |
| Git repository URL | `https://github.com/enterprisewebservice/software-factory-deployer` |
| Revision | `main` (or the branch under test) |
| Path | `field-content` |

RHDP creates Argo Application `field-content` in `openshift-gitops` (project `default`, no prune, no self-heal) and stops watching. From there the deployment is yours to read in Argo and OpenShift.

## Self-service rule

Nobody is watching when an attendee's cluster is ordered. Every fix goes into this repository, never onto a cluster by hand; the Job is idempotent and retries itself (`backoffLimit: 2`, three-hour deadline); every stage must tolerate what the base already installed. The first order (13 October 2026, CNV multi-node, OpenShift 4.22.16, 2 users) proved the path end to end after three fixes that are now in the code: no cluster-wide CSV listing (788 copied CSVs took the Job out of memory), no second OperatorGroup next to the base's OpenShift AI install, and a CSV wait limited to subscriptions this run created.

One Argo CD behaviour to know: when the hook Job fails, the retried Job is built from the manifests of the operation that started, not from a newer commit, while the Job itself clones the branch tip. Chart and role therefore tolerate each other across one revision (for example both names of the runner namespace variable). A clean new order never hits this.

## Where the stages stand (13 October 2026)

| Stage | State on an RHDP order | Notes |
|---|---|---|
| operators | works | skips what the base installed; subscribes the agent-office operator from `quay.io/dpeterso/agent-office-operator-catalog:v1.7.81`, with the order's model endpoint wired in as the hiring form's recommender |
| gitea | works | RHPDS gitea-operator, admin + user1..N, per-user orgs, tokens, Secrets, Argo AppProject + ApplicationSet |
| keycloak_users | works | base realm `sso`: attendees group, `rhdh` and `gitea` OIDC clients, provider roles |
| rhdh | works | chart 1.9.2 at `devhub.<domain>`, OIDC sign-in, Gitea + Argo CD, RBAC, the workshop's own plugins; the hire template is mirrored with this cluster's Gitea and the published OpenClaw image |
| agent_platform | works | platform namespace, SCC + ClusterRole, attendee group, the operator's CSV, the catalog Service name Developer Hub proxies to, the console plugin |
| rhoai_maas | works | ModelConnection `rhdp-litemaas` (marked default brain) from the LiteMaaS key RHDP issued, offered to the attendees group |
| mattermost | works | Mattermost + PostgreSQL at `mattermost-mattermost.<domain>`, Gitea sign-in, admin token, agents team, bridge |
| seats | works | `userN-agent-workspace`: RBAC, quota, limits, isolation, Gitea token, SCC and bridge bindings, the operator's tenant annotations (`agentoffice.ai/user`, `agentoffice.ai/groups`) |
| mcp_gateway | works | Kuadrant instance, MCP gateway on the OpenShift AI gateway class, policies, Gitea MCP server (54 tools) |
| showroom, userinfo | work | one guide per seat; URLs and the workshop password on the order page |
| verify | works | Gitea, Developer Hub, Mattermost and the first guide answer; the MCP registration is Ready |
| Quay + Pipelines-as-Code (Module 6), MaaS lane (Module 7), guide content for the RHDP flavour | not yet | |

Images: everything the workshop needs is public under `quay.io/dpeterso` (mirror map in `docs/mirror-to-quay-io.txt`; the operator, bundle and catalog are pushed there by the operator repository's own pipelines on the reference cluster, robot account `dpeterso+salamander`).

## What an order does

`values.yaml` runs every stage, in this order: `operators`, `gitea`, `keycloak_users`, `rhdh`, `agent_platform`, `rhoai_maas`, `mattermost`, `seats`, `mcp_gateway`, `showroom`, `verify`, `userinfo`. Each is idempotent and skips what already exists, so a second sync of the same order converges instead of failing. The stages that exist only for the agent platform read `agent_office_catalog_image`; with it empty the order still produces Gitea, Developer Hub, Mattermost, the MCP gateway, the seats and the guides, and the operator-dependent pieces are skipped with a message in the Job log.

`N` seats come from RHDP's `multi_user.num_users` when the order is multi-user, else 5. The lab guides are built from https://github.com/enterprisewebservice/showroom-openshift-software-factory with the console, API, Gitea, Developer Hub and Mattermost URLs filled in. The ConfigMap `software-factory-userinfo` (label `demo.redhat.com/userinfo`) carries those URLs and the workshop password, which RHDP shows on the order page.

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
