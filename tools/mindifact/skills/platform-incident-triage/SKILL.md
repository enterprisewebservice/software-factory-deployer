---
name: platform-incident-triage
description: Triage a reported platform incident with a fixed severity rubric and a reproducible investigation sequence. Use whenever a user reports an outage, degradation, error, or "something is broken" on the cluster.
---
# Platform incident triage

When a user reports a platform incident, ALWAYS structure the reply with
exactly these four sections, in this order, using these exact headings:

## Severity
Classify using this rubric and state the classification with one
sentence of justification:
- **P1** — production service down or data loss in progress
- **P2** — production degraded, or a non-production blocker with a deadline
- **P3** — degraded with a workaround available
- **P4** — cosmetic, informational, or a question

Classify from the facts as stated, at the worst credible reading. If
measured data contradicts the report, keep the classification
provisional and say what would settle it.

## What we know
Bullet only the facts stated in the report or verified with tools.
Output from a governed tool call counts as verified — cite the tool
name and the number it returned. Never speculate here. If a fact is
unverified, it does not belong in this section.

## Investigation sequence
A numbered list, always in this order:
1. Business impact now, through the governed metrics tools
   (`metrics_stuck_orders`, `metrics_weekly_summary`) — run them and
   cite the numbers before anything else.
2. Pod status in the affected namespace.
3. Recent events.
4. Logs of the failing workload.
5. Recent changes (deployments, config, secrets).
Run every step through governed tools when available. This
workstation has no `oc` or `kubectl` and no raw cluster access:
never attempt a cluster command yourself — write the exact command
for the human without trying it first. Where a step cannot be
verified with a governed tool, do not stop and do not ask
permission to continue: mark that step "blocked — needs access",
name the exact command for the human, and move on to the next step.

## Next actions
At most three bullets: the single most valuable next step first,
who acts (agent or human), and what evidence would raise or lower
the severity.

Keep the whole reply under 250 words. No preamble before the first
heading. Never fabricate command output: if a tool cannot answer,
write "unverified" in What we know.
