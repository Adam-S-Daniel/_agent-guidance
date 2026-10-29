#!/usr/bin/env python3
"""A stateful stand-in for the GitHub CLI, for tests only.

Every call appends its argv, as one JSON array per line, to $GH_LOG. State
(repos, settings, labels, environments, rulesets, variables, secrets, pull
requests) lives in the JSON file $GH_STUB_STATE, so a second run sees what
the first one wrote. A secret's value is stored only as a SHA-256, so no
secret text lands in any file.

Knobs: GH_STUB_DRIFT=1 makes a ruleset read-back drop its last rule.
GH_STUB_ENV_LIST_STATUS=<code> makes every environment secret/variable list
fail with that HTTP status.
GH_STUB_FAKE_PEM and GH_STUB_CLIENT_SECRET are what a manifest conversion
returns. Errors look like gh's: a JSON body on stdout, "gh: ... (HTTP n)" on
stderr, exit 1.
"""
import base64
import hashlib, json, os, re, sys

argv = sys.argv[1:]
with open(os.environ["GH_LOG"], "a", encoding="utf-8") as f:
    f.write(json.dumps(argv) + "\n")
STATE = os.environ["GH_STUB_STATE"]
try:
    st = json.load(open(STATE, encoding="utf-8"))
except FileNotFoundError:
    st = {}
for k in ("repos", "envs", "rulesets", "values", "prs", "apps"):
    st.setdefault(k, {})
st.setdefault("next_id", 1000)


def save():
    tmp = STATE + ".tmp"
    json.dump(st, open(tmp, "w", encoding="utf-8"), indent=1, sort_keys=True)
    os.replace(tmp, STATE)


def out(obj, code=0):
    save()
    print(json.dumps(obj))
    sys.exit(code)


def err(status, msg):
    save()
    print(json.dumps({"message": msg, "status": str(status), "secret_looking_body": "BODY-MUST-NOT-BE-PRINTED"}))
    print(f"gh: {msg} (HTTP {status})", file=sys.stderr)
    sys.exit(1)


def new_id():
    st["next_id"] += 1
    return st["next_id"]


def opt(args, name, default=None):
    if name in args:
        i = args.index(name)
        return args[i + 1]
    return default


def new_repo(full, visibility, readme):
    st["repos"][full] = {
        "full_name": full, "name": full.split("/")[1], "visibility": visibility,
        "private": visibility == "private", "default_branch": "main", "has_commits": readme,
        "allow_merge_commit": True, "allow_squash_merge": True, "allow_rebase_merge": True,
        "allow_auto_merge": False, "delete_branch_on_merge": False,
        "actions": {"enabled": True, "allowed_actions": "all", "sha_pinning_required": False},
        "workflow": {"default_workflow_permissions": "write", "can_approve_pull_request_reviews": True},
        "fork": {"approval_policy": "first_time_contributors"},
        "labels": [{"name": "bug"}, {"name": "enhancement"}],
    }


def repo_view(r):
    keys = ["full_name", "name", "visibility", "private", "default_branch", "allow_merge_commit",
            "allow_squash_merge", "allow_rebase_merge", "allow_auto_merge", "delete_branch_on_merge"]
    return {k: r[k] for k in keys}


def api(args):
    method = (opt(args, "-X") or opt(args, "--method") or "GET").upper()
    body = None
    src = opt(args, "--input")
    if src is not None:
        body = json.load(sys.stdin if src == "-" else open(src, encoding="utf-8"))
    skip = {"-X", "--method", "--input", "-H"}
    pos, i = [], 0
    while i < len(args):
        if args[i] in skip:
            i += 2
            continue
        pos.append(args[i])
        i += 1
    path = pos[0].split("?")[0].strip("/")

    m = re.fullmatch(r"users/([^/]+)", path)
    if m:
        u = m.group(1)
        return out({"login": u, "id": 5000 + sum(map(ord, u)), "type": "User"})

    m = re.fullmatch(r"app-manifests/([^/]+)/conversions", path)
    if m and method == "POST":
        if m.group(1) == "expired":
            return err(404, "Not Found")
        n = len(st["apps"]) + 1
        app = {"id": 900000 + n, "slug": f"acme-example-app-{n}", "client_id": f"Iv23liExample{n}",
               "client_secret": os.environ.get("GH_STUB_CLIENT_SECRET", "fake-client-secret"),
               "pem": os.environ.get("GH_STUB_FAKE_PEM", "-----BEGIN FAKE KEY-----\nx\n-----END FAKE KEY-----\n"),
               "webhook_secret": None}
        st["apps"][m.group(1)] = {"id": app["id"], "slug": app["slug"]}
        return out(app)

    m = re.fullmatch(r"repos/([^/]+/[^/]+)(/.*)?", path)
    if not m:
        return err(404, "Not Found")
    full, rest = m.group(1), (m.group(2) or "")
    r = st["repos"].get(full)
    if r is None:
        return err(404, "Not Found")

    if rest == "":
        if method == "GET":
            return out(repo_view(r))
        if method == "PATCH":
            r.update(body)
            return out(repo_view(r))
    if rest == "/commits":
        return out([{"sha": "abc"}]) if r["has_commits"] else err(409, "Git Repository is empty.")
    if rest == "/contents/README.md" and method == "PUT":
        r["has_commits"] = True
        r["readme"] = base64.b64decode(body["content"]).decode("utf-8")
        return out({"content": {"path": "README.md"}})
    if rest.startswith("/contents/") and method == "GET":
        # The default branch's files: README.md once there is a first commit,
        # plus whatever a test puts in the repo's "files" (a merged pull request).
        p = rest[len("/contents/"):]
        if (p == "README.md" and r["has_commits"]) or p in r.get("files", {}):
            return out({"type": "file", "path": p, "name": p.split("/")[-1]})
        return err(404, "Not Found")
    if rest == "/actions/permissions":
        if method == "PUT":
            r["actions"] = body
        return out(r["actions"])
    if rest == "/actions/permissions/workflow":
        if method == "PUT":
            r["workflow"] = body
        return out(r["workflow"])
    if rest == "/actions/permissions/fork-pr-contributor-approval":
        if r["private"]:
            return err(422, "Unprocessable Entity")
        if method == "PUT":
            r["fork"] = body
        return out(r["fork"])
    if rest == "/labels":
        if method == "POST":
            r["labels"].append(body)
            return out(body)
        return out(r["labels"])

    envs = st["envs"].setdefault(full, {})
    m2 = re.fullmatch(r"/environments/([^/]+)(/deployment-branch-policies)?", rest)
    if m2:
        name, pol = m2.group(1), m2.group(2)
        if r["private"] and os.environ.get("GH_STUB_PLAN", "free") == "free":
            return err(404, "Not Found")
        e = envs.get(name)
        if pol:
            if e is None:
                return err(404, "Not Found")
            if method == "POST":
                e["policies"].append({"id": new_id(), "name": body["name"], "type": body.get("type", "branch")})
                return out(e["policies"][-1])
            return out({"total_count": len(e["policies"]), "branch_policies": e["policies"]})
        if method == "PUT":
            if e is None:
                e = envs[name] = {"name": name, "deployment_branch_policy": None, "protection_rules": [], "policies": []}
            e["deployment_branch_policy"] = body.get("deployment_branch_policy")
            if "reviewers" in body:
                e["protection_rules"] = [{"type": "required_reviewers", "prevent_self_review": body.get("prevent_self_review", False),
                                          "reviewers": [{"type": x["type"], "reviewer": {"id": x["id"]}} for x in body["reviewers"]]}]
        if e is None:
            return err(404, "Not Found")
        return out({k: v for k, v in e.items() if k != "policies"})

    sets = st["rulesets"].setdefault(full, {})
    if rest == "/rulesets":
        if method == "POST":
            rid = str(new_id())
            sets[rid] = body
            return out(dict(body, id=int(rid)))
        return out([{"id": int(k), "name": v["name"]} for k, v in sets.items()])
    m2 = re.fullmatch(r"/rulesets/(\d+)", rest)
    if m2:
        rid = m2.group(1)
        if rid not in sets:
            return err(404, "Not Found")
        if method == "PUT":
            sets[rid] = body
        live = json.loads(json.dumps(sets[rid]))
        # What GitHub adds on read-back: ids, source, extra default parameters.
        live.update({"id": int(rid), "source": full, "source_type": "Repository", "node_id": "RRS_x",
                     "current_user_can_bypass": "never", "_links": {"self": {"href": "https://example.com/"}}})
        for rule in live.get("rules", []):
            if rule["type"] == "pull_request":
                rule["parameters"]["automatic_copilot_code_review_enabled"] = False
            if rule["type"] == "required_status_checks":
                rule["parameters"]["do_not_enforce_on_create"] = False
        for a in live.get("bypass_actors", []):
            a.setdefault("actor_type", "Integration")
        if os.environ.get("GH_STUB_DRIFT") == "1" and live.get("rules"):
            live["rules"] = live["rules"][:-1]
        return out(live)
    return err(404, "Not Found")


def repo_values(kind, sub, args, repo):
    """Repository-level variables (no --env): stored as their text, since a
    variable is not secret and verify.sh compares it; secrets as a SHA-256."""
    if repo not in st["repos"]:
        return err(404, "Not Found")
    store = st["values"].setdefault(f"{repo}||{kind}", {})
    if sub == "list":
        fields = (opt(args, "--json") or "name").split(",")
        return out([{f: (n if f == "name" else store[n]) for f in fields} for n in sorted(store)])
    if sub == "set":
        if "--body" in args or "-b" in args:
            print("stub: a value was passed in argv", file=sys.stderr)
            sys.exit(2)
        data = sys.stdin.buffer.read()
        store[args[1]] = data.decode("utf-8") if kind == "variable" else hashlib.sha256(data).hexdigest()
        save()
        sys.exit(0)
    return err(400, "bad")


def values(kind, args):
    sub = args[0]
    env, repo = opt(args, "--env"), opt(args, "--repo")
    if env is None:
        return repo_values(kind, sub, args, repo)
    if sub == "list" and os.environ.get("GH_STUB_ENV_LIST_STATUS"):
        return err(int(os.environ["GH_STUB_ENV_LIST_STATUS"]), "Forbidden")
    key = f"{repo}|{env}|{kind}"
    r = st["repos"].get(repo)
    if r is None or (r["private"] and os.environ.get("GH_STUB_PLAN", "free") == "free"):
        return err(404, "Not Found")
    if env not in st["envs"].get(repo, {}):
        return err(404, "Not Found")
    store = st["values"].setdefault(key, {})
    if sub == "list":
        return out([{"name": n} for n in sorted(store)])
    if sub == "set":
        if "--body" in args or "-b" in args:
            print("stub: a value was passed in argv", file=sys.stderr)
            sys.exit(2)
        data = sys.stdin.buffer.read()
        store[args[1]] = hashlib.sha256(data).hexdigest()
        save()
        sys.exit(0)
    return err(400, "bad")


def pr(args):
    sub, repo = args[0], opt(args, "--repo")
    prs = st["prs"].setdefault(repo, [])
    if sub == "list":
        head = opt(args, "--head")
        return out([{"url": p["url"]} for p in prs if p["head"] == head and p["state"] == "open"])
    if sub == "create":
        sys.stdin.read()
        p = {"head": opt(args, "--head"), "base": opt(args, "--base"), "state": "open",
             "url": f"https://github.com/{repo}/pull/{len(prs) + 1}"}
        prs.append(p)
        save()
        print(p["url"])
        sys.exit(0)
    return err(400, "bad")


if not argv:
    sys.exit(2)
cmd = argv[0]
if cmd == "api":
    api(argv[1:])
elif cmd == "repo" and argv[1] == "create":
    full = argv[2]
    vis = "private" if "--private" in argv else "public"
    if full in st["repos"]:
        err(422, "Name already exists")
    new_repo(full, vis, "--add-readme" in argv)
    save()
    print(f"https://github.com/{full}")
elif cmd in ("variable", "secret"):
    values(cmd, argv[1:])
elif cmd == "pr":
    pr(argv[1:])
else:
    print("stub: unsupported command", file=sys.stderr)
    sys.exit(2)
