---
name: golden-path-service
description: Scaffold and structure services for the ACME platform golden path. Use whenever building, scaffolding, restructuring, or fixing a service repository (microservice, MCP service, API) that the platform supply chain will build and deploy.
---
# Golden-path service contract

Every service repository you create or modify MUST conform to this
contract. The supply-chain pipeline, the deployment lane, and the
gateway registration all depend on it. Implementation language and
framework are yours to choose; these interfaces are not.

## Repository layout (non-negotiable)

- `Dockerfile` at the REPOSITORY ROOT. Never in a subdirectory.
- `deploy/deployment.yaml` — Deployment plus Service (see below).
- `.tekton/on-push.yaml` — copy the pipeline template at the end of
  this skill VERBATIM, substituting only the image path.
- Source, tests, and a README laid out however the language expects.

## Runtime contract

- The service listens on port 8080.
- `GET /healthz` returns `{"ok": true}`.
- MCP services serve Streamable HTTP on `POST /mcp`, and MUST run
  STATELESS: the platform's MCP gateway broker retries and re-routes
  across your replicas, so per-session server state strands and
  crashes (`anyio.ClosedResourceError` storms, paused circuits). In
  Python FastMCP, construct with `stateless_http=True` AND
  `host="0.0.0.0"`. The host argument is not about the socket (your
  ASGI server binds that) — without it the 1.29+ SDK assumes a
  localhost server and arms its DNS-rebinding protection, so every
  call arriving through the platform gateway is rejected with
  "Invalid Host header" while localhost probes pass. In other
  stacks, disable server-side session affinity for `/mcp` and any
  host-header allowlist.
- MCP SDK version: in Python pin `mcp>=1.29,<2` — BOTH bounds
  matter. The platform's agents negotiate MCP protocol 2025-11-25;
  the 1.12.x SDK tops out at 2025-06-18 and REJECTS the handshake,
  so every governed call returns "rejected the MCP protocol
  version" while plain curl probes still look fine. And mcp 2.x
  renames FastMCP (`mcp.server.fastmcp` is gone —
  `ModuleNotFoundError` at startup), so an unbounded `>=1.29`
  crash-loops the pod the moment 2.x resolves. Do not copy an
  older pin from a reference repository. Equivalent rule in other
  stacks: the MCP library must support protocol revision
  2025-11-25 or newer.
- Read upstream endpoints from environment variables with sane
  defaults; never hardcode credentials.
- Python + FastMCP trap: never put `from __future__ import
  annotations` in a module that defines MCP tools — postponed
  evaluation turns parameter annotations into strings and tool
  registration crashes at startup with
  `TypeError: issubclass() arg 1 must be a class`. Use concrete
  annotations in that module instead.

## deploy/deployment.yaml contract

- Image: `quay-quay-quay-test.apps.salamander.aimlworkbench.com/deanpeterson/<seat>-<service>:main`
  where `<seat>` is your Gitea org name minus its `-agents` suffix
  (find the org with your gitea tools) and `<service>` is the
  repository name. `imagePullPolicy: Always`.
- `imagePullSecrets` naming `quay-pull-secret`.
- `containerPort: 8080`; Service `port: 8080` with a named
  `targetPort`.
- Labels: `app.kubernetes.io/name: <service>` on the Deployment's
  selector and pod template AND as the Service's selector. Use this
  exact key — platform tooling and dashboards select on it.
- Readiness and liveness probes on `/healthz`.
- Hardened securityContext (runAsNonRoot, drop ALL capabilities,
  no privilege escalation, RuntimeDefault seccomp) and explicit
  resource requests and limits.

## Delivery flow

- Work on a feature branch; open a pull request for review.
- When a reviewer asks for conformance fixes, push them promptly —
  to the same branch while the PR is open, or directly to `main`
  when asked to fix an already-merged repository.
- Never change the interface contract (ports, paths, image
  location) on your own initiative.

## .tekton/on-push.yaml template (copy verbatim; substitute IMAGE only)

```yaml
apiVersion: tekton.dev/v1
kind: PipelineRun
metadata:
  name: SERVICE-on-push
  annotations:
    pipelinesascode.tekton.dev/on-cel-expression: 'event == "push" && target_branch == "main"'
    pipelinesascode.tekton.dev/cancel-in-progress: "true"
    pipelinesascode.tekton.dev/max-keep-runs: "5"
spec:
  params:
    - name: git-url
      value: '{{source_url}}'
    - name: revision
      value: '{{revision}}'
    - name: output-image
      value: 'IMAGE'
    - name: dockerfile
      value: Dockerfile
    - name: path-context
      value: .
    - name: rebuild
      value: "true"
    - name: skip-checks
      value: "true"
  pipelineRef:
    resolver: bundles
    params:
      - name: bundle
        value: 'quay.io/konflux-ci/tekton-catalog/pipeline-docker-build@sha256:1b2a544f5308a50b8ebf76e76f10f8be3e12342d6ce7233d2520aca724294a82'
      - name: name
        value: docker-build
      - name: kind
        value: pipeline
  workspaces:
    - name: workspace
      volumeClaimTemplate:
        spec:
          accessModes: [ReadWriteOnce]
          resources:
            requests: {storage: 1Gi}
    - name: git-auth
      secret:
        secretName: '{{ git_auth_secret }}'
```

Replace `SERVICE` with the repository name and `IMAGE` with the full
image path from the deploy contract. Do not alter anything else —
`{{source_url}}`, `{{revision}}`, and `{{ git_auth_secret }}` are
pipeline placeholders the platform substitutes at run time.
