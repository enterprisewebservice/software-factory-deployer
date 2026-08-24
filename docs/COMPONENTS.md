# Component inventory → install plan

Ground truth: `agent-office/cluster/` on the reference cluster. Workshop = subset, no pet-cluster
extras (vault/gpu-power-cap/newsdesks stay out; secrets are generated per-order, not vaulted).

| Component | Source | Workshop install | Status |
|---|---|---|---|
| OpenShift GitOps | redhat-operators | operators.yml (Subscription) | reuse RHDP pattern |
| OpenShift Pipelines | redhat-operators | operators.yml | reuse RHDP pattern |
| OpenShift AI (RHOAI) + **MaaS** | redhat-operators | operators.yml + rhoai_maas.yml (DataScienceCluster, MaaS enablement, external provider from attrs) | MaaS = early access; label honestly |
| External Secrets Operator | community-operators, `stable` | operators.yml | as reference cluster |
| MCP Gateway operator | redhat-operators, `preview` | operators.yml | as reference cluster (`mcp-gateway`) |
| agent-office-operator | own CatalogSource `agent-office-operator-catalog`, channel `alpha` | operators.yml (catalogsource.yml.j2 + subscription) | pin catalog image per release |
| Red Hat Developer Hub + dynamic plugins + golden-path templates | cluster/rhdh + template repo | rhdh.yml | template repo fork for workshop (TODO: pin) |
| Keycloak + realm + user1..N + per-user namespaces | new for workshop | keycloak_users.yml | replaces htpasswd pattern |
| **Gitea (in-cluster git)** | Gitea operator (agnosticd pattern) | gitea.yml | attendee agent repos live in per-user Gitea namespaces via Keycloak OIDC; reference cluster keeps GitHub untouched |
| AgentGateway class / skills catalog / Skill CRs | cluster/skills*, cluster/runtime | agent_platform.yml | subset: lab skills only |
| Mattermost (agent chat) | cluster/mattermost | agent_platform.yml | needed for M1 first-contact |
| MLflow | cluster/mlflow | optional flag, default off | only if M5+ uses it |
| Optional sovereign vLLM (behind MaaS) | new | rhoai_maas.yml (`sovereign_vllm: true`) | GPU node required when on |
| Vault / vault-secret-store | reference cluster only | **not installed** — per-order generated secrets | workshop simplification |
| Showroom UI | RHDP workload | AgnosticV `common.yaml` (Milestone B) | reuse RHDP workload |
