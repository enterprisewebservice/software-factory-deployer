= software-factory-deployer

AgnosticD-v2-style workload that turns a fresh OpenShift cluster into the environment for the
"OpenShift Software Factory — Agents as Staff" Showroom workshop
(https://github.com/enterprisewebservice/showroom-openshift-software-factory).

Reference implementation: the `agent-office` platform (operator + Developer Hub golden paths +
MCP gateway governance + OpenShift AI). This repo re-packages that stack as a repeatable,
per-order install for RHDP.

== Model access

`model_access: maas` — attendees' agents consume models through OpenShift AI
**Models-as-a-Service** (early access in current OpenShift AI; the lab labels it with its real
support status). External frontier models are brokered by MaaS (keys/quotas issued per user);
an optional in-cluster vLLM registers into the same MaaS as the sovereign endpoint for the
"swap the brain" module. Agents never hold provider credentials either way.

== Layout

* `roles/ocp4_workload_software_factory` — the workload (AgnosticD v2 conventions:
  `ACTION=provision|destroy`, pre/workload/post/remove task files).
* `run_local.yml` — iteration loop: run the role against whatever cluster your current
  `KUBECONFIG` points at, no AgnosticD harness needed.
* `docs/COMPONENTS.md` — what gets installed, from where, and what is reused vs. new.

== Iteration loop

[source,bash]
----
ansible-playbook -i inventory/local run_local.yml -e ACTION=provision -e num_users=2
----

Milestones: (A) installs clean on an RHDP CNV base cluster twice in a row →
(B) AgnosticV catalog item referencing this workload.
