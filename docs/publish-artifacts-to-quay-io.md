# Publish the workshop's skill artifacts to quay.io (one time, by the owner)

**Done on 13 October 2026** (Dean's go-ahead, run from his signed-in quay.io session and the
reference cluster): the three repositories exist on `quay.io/dpeterso`, public, with the robot
`dpeterso+salamander` granted write; `golden-path-service` 1.0.6 is live on mindifact.ai and
as an OCI artifact on the reference registry; the copy below ran once. Re-run step 2 whenever a
new artifact version is published on the reference registry (the deployer mirrors whatever
quay.io holds at order time). The staged credential (`agent-office/quayio-dpeterso-robot` on
salamander) stays for that.

The deployer's `quay` stage mirrors three artifact repositories from `quay.io/dpeterso` into
every ordered cluster's own registry (anonymous pull, digests preserved). Those repositories
exist only on the reference cluster's Quay today, where they are private. Publishing them is
an owner action: it creates public repositories under your quay.io account.

## 1. Create the repositories (public) and let the robot push

In quay.io, signed in as `dpeterso`, create three **public** repositories and grant the robot
`dpeterso+salamander` the **Write** permission on each:

- `agent-office-skill-platform-incident-triage`
- `agent-office-pack-supply-chain-ops`
- `agent-office-skill-golden-path-service`

(The robot cannot create repositories in a user namespace, so they have to exist first. The
same was done for the operator images.)

## 2. Copy from the reference cluster's Quay

Run on salamander. The source credential is the platform's `agent-office/quay-push-secret`;
the quay.io robot credential is staged as `agent-office/quayio-dpeterso-robot` (the Secret
you downloaded from quay.io, applied as is). Every tag of each repository is copied with its
manifest byte for byte, so content hashes and version lists in the guide hold.

```bash
oc apply -n agent-office -f - <<'YAML'
apiVersion: v1
kind: Pod
metadata:
  name: artifact-publish
  labels: { app.kubernetes.io/name: artifact-publish }
spec:
  restartPolicy: Never
  containers:
    - name: skopeo
      image: quay.io/skopeo/stable@sha256:97772f3c64fd2fba720b2bafaf37239dc44d8b1c625c8383c64044832efe9ed9
      command: ["/bin/sh", "-ec"]
      args:
        - |
          for repo in agent-office-skill-platform-incident-triage agent-office-pack-supply-chain-ops agent-office-skill-golden-path-service; do
            echo "== ${repo}"
            skopeo sync --all --preserve-digests --src docker --dest docker \
              --src-authfile /src/.dockerconfigjson --dest-authfile /dst/.dockerconfigjson \
              "quay-quay-quay-test.apps.salamander.aimlworkbench.com/deanpeterson/${repo}" "quay.io/dpeterso"
          done
          echo "publish complete"
      volumeMounts:
        - { name: src, mountPath: /src, readOnly: true }
        - { name: dst, mountPath: /dst, readOnly: true }
  volumes:
    - { name: src, secret: { secretName: quay-push-secret } }
    - { name: dst, secret: { secretName: quayio-dpeterso-robot } }
YAML
oc logs -f pod/artifact-publish -n agent-office
oc delete pod/artifact-publish -n agent-office
```

## 3. Check

```bash
for r in agent-office-skill-platform-incident-triage agent-office-pack-supply-chain-ops agent-office-skill-golden-path-service; do
  T=$(curl -s "https://quay.io/v2/auth?service=quay.io&scope=repository:dpeterso/$r:pull" | python3 -c "import sys,json;print(json.load(sys.stdin)['token'])")
  printf "%s: " "$r"; curl -s "https://quay.io/v2/dpeterso/$r/tags/list" -H "Authorization: Bearer $T"; echo
done
```

Anonymous tag lists for all three mean the deployer's default `artifact_mirror_source`
(`quay.io/dpeterso`) works with no credential. Until then, a cluster can still be ordered:
the `quay` stage fails at the mirror step and names the missing source.

## Also: golden-path-service 1.0.6 on mindifact.ai

The hire flow resolves skills from mindifact.ai. Version 1.0.6 (this repository,
`tools/mindifact/skills/golden-path-service/SKILL.md`) makes the image path come from the
request instead of a hardcoded registry, sizes the build for shared clusters (a `taskRunSpecs`
block in the pipeline template: without it the buildah step's 4.6 CPU / 8 GiB request never
schedules on an RHDP worker), and carries the 1.0.4/1.0.5 fixes that were never published
(mcp pin bounds, `host="0.0.0.0"`). Publish it with the registry token:

```bash
cd tools/mindifact
./publish-mindifact.sh skills/golden-path-service/SKILL.md agent-office/golden-path-service:1.0.6 \
  --description "Scaffold and structure services for the ACME platform golden path. Use whenever building, scaffolding, restructuring, or fixing a service repository (microservice, MCP service, API) that the platform supply chain will build and deploy." \
  --provides '["dev.golden-path-service"]'
```

The reference registry also carries each version as an OCI artifact (tags 1.0.0 to 1.0.5
today; mindifact.ai stops at 1.0.2). Publish 1.0.6 there too, then re-run step 2 so the
mirror source carries it:

```bash
cd tools/mindifact
./publish-oci.py skill skills/golden-path-service golden-path-service:1.0.6 --provides '["dev.golden-path-service"]'
```
