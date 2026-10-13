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
| operators | works | skips what the base installed; agent-office operator waits for its published catalog |
| gitea | works | RHPDS gitea-operator, admin + user1..N, per-user orgs, tokens, Secrets, Argo AppProject + ApplicationSet |
| keycloak_users | works | base realm `sso`: attendees group, `rhdh` and `gitea` OIDC clients, provider roles |
| rhoai_maas | deferred | LiteMaaS key is captured; the ModelConnection needs the operator's CRD |
| rhdh | works | chart 1.9.2, OIDC sign-in, Gitea + Argo CD integrations, RBAC, template mirror; custom plugins off until quay.io repos are public |
| showroom, userinfo | work | one guide per seat; URLs and the workshop password on the order page |
| agent_platform, seats, verify | not yet | operator catalog must be published first (`quay.io/dpeterso`, operator branch `quay-io-release`) |

Images: everything the workshop needs is mirrored to `quay.io/dpeterso` (map in `docs/mirror-to-quay-io.txt`); the repositories are private until flipped public in the Quay UI.

## What the first order does

`values.yaml` sets `software_factory_stages: operators,showroom,userinfo`:

1. **operators**: Subscriptions for OpenShift Pipelines, OpenShift AI (`redhat-ods-operator` namespace and OperatorGroup are created first), External Secrets and the MCP gateway operator. Packages that already have a CSV are skipped, so the base's OpenShift GitOps is left alone. The agent-office operator is skipped while `agent_office_catalog_image` is empty.
2. **showroom**: one lab guide per seat, `showroom-user1` to `showroom-userN`, built from https://github.com/enterprisewebservice/showroom-openshift-software-factory with the console, API and GitOps URLs filled in and plain-word placeholders for components not deployed yet. `N` comes from RHDP's `multi_user.num_users` when the order is multi-user, else 5.
3. **userinfo**: ConfigMap `software-factory-userinfo` (label `demo.redhat.com/userinfo`) with the guide URLs and cluster URLs, which RHDP shows on the order page.

Stages still to land, in order: `gitea`, `gitops`, `rhoai_maas`, `rhdh`, `keycloak_users`, `agent_platform`, `verify`. Add each to `software_factory_stages` as it is implemented. Prerequisite for `agent_platform`: the operator catalog, operator, openclaw gateway and skills images published to a registry the ordered cluster can pull; today they exist only in the reference cluster's Quay.

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
