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
