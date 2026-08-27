#!/usr/bin/env python3
"""Publish mindifact artifacts to Quay as spec-shaped OCI artifacts.

  publish-oci.py skill <skill-dir> <name>:<version> [--requires JSON] [--provides JSON]
  publish-oci.py tool  <recipe.json> <name>:<version> [--description TEXT] [--provides JSON]
  publish-oci.py pack  <name>:<version> --member <repo>:<tag> ... [--description TEXT] [--provides JSON]

Shape follows the Agent Skills OCI artifacts draft: config blob carries the
artifact metadata (the mindifact manifest, INSIDE the package), a single
tar+gzip layer carries the content. A pack is an OCI image index whose
descriptors reference member artifacts by digest; members' manifests and
blobs are mounted/copied into the pack repo so the bundle physically
contains what it claims. Deterministic bytes (mtime=0) so re-publishing
identical content yields identical digests.
Credentials: oc secret agent-office/quay-push-secret (dockerconfigjson).
"""
import base64, gzip, re, hashlib, io, json, os, ssl, subprocess, sys, tarfile, urllib.request, urllib.error

HOST = "quay-quay-quay-test.apps.salamander.aimlworkbench.com"
ORG = "deanpeterson"
CTX = ssl._create_unverified_context()
MT_CFG_SKILL = "application/vnd.agentskills.skill.config.v1+json"
MT_LAYER = "application/vnd.agentskills.skill.content.v1.tar+gzip"
MT_CFG_TOOL = "application/vnd.agentskills.tool.config.v1+json"
MT_LAYER_TOOL = "application/vnd.agentskills.tool.content.v1.tar+gzip"
MT_MAN = "application/vnd.oci.image.manifest.v1+json"
MT_IDX = "application/vnd.oci.image.index.v1+json"

def creds():
    dcj = subprocess.run(["oc","get","secret","quay-push-secret","-n","agent-office",
        "-o","jsonpath={.data.\\.dockerconfigjson}"],capture_output=True,text=True,check=True).stdout
    auths = json.loads(base64.b64decode(dcj))["auths"]
    ent = auths.get(HOST) or next(iter(auths.values()))
    return ent["auth"]

BASIC = None
BEARER = {}
def req(method, path, body=None, ctype=None, accept=None, repo=None, anon=False):
    # Location headers can be absolute (sometimes with quay's internal host):
    # keep only path+query and always go through the route
    if path.startswith("http"):
        import urllib.parse as _up
        u = _up.urlsplit(path)
        path = u.path + (f"?{u.query}" if u.query else "")
    hdr = {}
    if accept: hdr["Accept"] = accept
    if ctype: hdr["Content-Type"] = ctype
    if repo and repo in BEARER: hdr["Authorization"] = f"Bearer {BEARER[repo]}"
    r = urllib.request.Request(f"https://{HOST}{path}", data=body, method=method, headers=hdr)
    try:
        with urllib.request.urlopen(r, context=CTX) as res:
            return res.status, dict(res.headers), res.read()
    except urllib.error.HTTPError as e:
        if e.code == 401 and not anon:
            ch = e.headers.get("WWW-Authenticate","")
            # k="v" pairs — scope's value itself contains commas (pull,push),
            # so never comma-split the header
            parts = dict(re.findall(r'(\w+)="([^"]*)"', ch))
            realm = parts.get("realm",""); svc = parts.get("service","")
            scope = parts.get("scope","")
            tr = urllib.request.Request(f"{realm}?service={svc}&scope={scope}",
                headers={"Authorization": f"Basic {BASIC}"})
            with urllib.request.urlopen(tr, context=CTX) as res:
                tok = json.loads(res.read())["token"]
            if repo: BEARER[repo] = tok
            hdr["Authorization"] = f"Bearer {tok}"
            r = urllib.request.Request(f"https://{HOST}{path}", data=body, method=method, headers=hdr)
            try:
                with urllib.request.urlopen(r, context=CTX) as res:
                    return res.status, dict(res.headers), res.read()
            except urllib.error.HTTPError as e2:
                return e2.code, dict(e2.headers), e2.read()
        return e.code, dict(e.headers), e.read()

def hget(hd, key, default=None):
    for k, v in hd.items():
        if k.lower() == key.lower(): return v
    return default

def put_blob(repo, data, mount_from=None):
    digest = "sha256:" + hashlib.sha256(data).hexdigest()
    st,_,_ = req("HEAD", f"/v2/{ORG}/{repo}/blobs/{digest}", repo=repo)
    if st == 200: return digest, len(data)
    if mount_from:
        st,hd,_ = req("POST", f"/v2/{ORG}/{repo}/blobs/uploads/?mount={digest}&from={ORG}/{mount_from}", repo=repo)
        if st == 201: return digest, len(data)
        loc = hget(hd, "Location")
    else:
        st,hd,body = req("POST", f"/v2/{ORG}/{repo}/blobs/uploads/", repo=repo)
        assert st == 202, f"upload init {repo} -> {st} {body[:200]}"
        loc = hget(hd, "Location")
        assert loc, f"no Location header from upload init {repo}"
    sep = "&" if "?" in loc else "?"
    st,_,body = req("PUT", f"{loc}{sep}digest={digest}", body=data,
                    ctype="application/octet-stream", repo=repo)
    assert st == 201, f"blob put {repo} -> {st} {body[:200]}"
    return digest, len(data)

def put_manifest(repo, tag, mbytes, mt):
    st,_,body = req("PUT", f"/v2/{ORG}/{repo}/manifests/{tag}", body=mbytes, ctype=mt, repo=repo)
    assert st in (201,202), f"manifest put {repo}:{tag} -> {st} {body[:300]}"
    return "sha256:" + hashlib.sha256(mbytes).hexdigest()

def get_manifest_raw(repo, ref):
    st,hd,body = req("GET", f"/v2/{ORG}/{repo}/manifests/{ref}",
        accept=f"{MT_MAN}, application/vnd.docker.distribution.manifest.v2+json", repo=repo)
    assert st == 200, f"manifest {repo}:{ref} -> {st} {body[:200]}"
    return body, hget(hd, "Content-Type", MT_MAN)

def layer_tar(files):  # {arcname: bytes} -> (gz_bytes, diff_id)
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w") as t:
        for arc in sorted(files):
            ti = tarfile.TarInfo(arc); data = files[arc]
            ti.size = len(data); ti.mode = 0o644; ti.uid = ti.gid = 0; ti.mtime = 0
            t.addfile(ti, io.BytesIO(data))
    raw = buf.getvalue()
    gz = io.BytesIO()
    with gzip.GzipFile(fileobj=gz, mode="wb", mtime=0) as g: g.write(raw)
    return gz.getvalue(), "sha256:" + hashlib.sha256(raw).hexdigest()

def push_artifact(repo, tag, cfg, cfg_mt, layer_files, layer_mt, annotations):
    cfg_b = json.dumps(cfg, separators=(",",":"), sort_keys=True).encode()
    gz, _ = layer_tar(layer_files)
    cd, cl = put_blob(repo, cfg_b)
    ld, ll = put_blob(repo, gz)
    man = {"schemaVersion":2,"mediaType":MT_MAN,
        "config":{"mediaType":cfg_mt,"digest":cd,"size":cl},
        "layers":[{"mediaType":layer_mt,"digest":ld,"size":ll}],
        "annotations":annotations}
    mb = json.dumps(man, separators=(",",":"), sort_keys=True).encode()
    d = put_manifest(repo, tag, mb, MT_MAN)
    print(f"pushed {ORG}/{repo}:{tag} @ {d}")
    return d, len(mb)

def main():
    global BASIC
    BASIC = creds()
    cmd = sys.argv[1]
    args = sys.argv[2:]
    opts = {"--requires":"[]","--provides":"[]","--description":"","--member":[]}
    pos = []
    i = 0
    while i < len(args):
        if args[i] == "--member": opts["--member"].append(args[i+1]); i += 2
        elif args[i] in opts: opts[args[i]] = args[i+1]; i += 2
        else: pos.append(args[i]); i += 1

    if cmd == "skill":
        skdir, coord = pos
        name, ver = coord.rsplit(":",1)
        md = open(os.path.join(skdir,"SKILL.md"),encoding="utf-8").read()
        desc = ""
        if md.startswith("---"):
            for ln in md.split("---")[1].splitlines():
                if ln.strip().startswith("description:"): desc = ln.split(":",1)[1].strip()
        csha = hashlib.sha256(md.encode()).hexdigest()
        cfg = {"name":name,"version":ver,"kind":"skill","description":desc,
               "requires":json.loads(opts["--requires"]),"provides":json.loads(opts["--provides"]),
               "contentSha256":csha}
        files = {"SKILL.md": md.encode()}
        for dp,_,fns in os.walk(skdir):
            for fn in fns:
                if fn == "SKILL.md": continue
                p = os.path.join(dp,fn)
                files[os.path.relpath(p,skdir)] = open(p,"rb").read()
        ann = {"org.opencontainers.image.title":name,"org.opencontainers.image.version":ver,
               "ai.agentoffice.kind":"skill","ai.agentoffice.content-sha256":csha}
        push_artifact(f"agent-office-skill-{name}", ver, cfg, MT_CFG_SKILL, files, MT_LAYER, ann)

    elif cmd == "tool":
        recipe_path, coord = pos
        name, ver = coord.rsplit(":",1)
        recipe = open(recipe_path,"rb").read()
        cfg = {"name":name,"version":ver,"kind":"tool","description":opts["--description"],
               "provides":json.loads(opts["--provides"]),
               "recipeSha256":hashlib.sha256(recipe).hexdigest()}
        ann = {"org.opencontainers.image.title":name,"org.opencontainers.image.version":ver,
               "ai.agentoffice.kind":"tool"}
        push_artifact(f"agent-office-tool-{name}", ver, cfg, MT_CFG_TOOL,
                      {"recipe.json":recipe}, MT_LAYER_TOOL, ann)

    elif cmd == "pack":
        coord = pos[0]
        name, ver = coord.rsplit(":",1)
        pack_repo = f"agent-office-pack-{name}"
        descs = []
        for m in opts["--member"]:
            mrepo, mtag = m.rsplit(":",1)
            mb, mt = get_manifest_raw(mrepo, mtag)
            md = "sha256:" + hashlib.sha256(mb).hexdigest()
            man = json.loads(mb)
            # copy member blobs (mount) + manifest into the pack repo so the
            # index is complete: the bundle physically contains its members
            for blob in [man["config"]] + man.get("layers",[]):
                b = req("GET", f"/v2/{ORG}/{mrepo}/blobs/{blob['digest']}", repo=mrepo)[2]
                put_blob(pack_repo, b, mount_from=mrepo)
            put_manifest(pack_repo, md, mb, mt)
            kind = man.get("annotations",{}).get("ai.agentoffice.kind","skill")
            descs.append({"mediaType":mt,"digest":md,"size":len(mb),
                "annotations":{"ai.agentoffice.member":f"{mrepo}:{mtag}",
                               "ai.agentoffice.kind":kind}})
            print(f"member {mrepo}:{mtag} -> {md} ({kind})")
        idx = {"schemaVersion":2,"mediaType":MT_IDX,"manifests":descs,
            "annotations":{"org.opencontainers.image.title":name,
                "org.opencontainers.image.version":ver,
                "ai.agentoffice.kind":"pack",
                "org.opencontainers.image.description":opts["--description"],
                "ai.agentoffice.provides":opts["--provides"]}}
        ib = json.dumps(idx, separators=(",",":"), sort_keys=True).encode()
        d = put_manifest(pack_repo, ver, ib, MT_IDX)
        print(f"pushed pack {ORG}/{pack_repo}:{ver} @ {d} ({len(descs)} members)")
    else:
        sys.exit(f"unknown command {cmd}")

main()
