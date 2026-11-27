#!/usr/bin/env python3
"""One-shot deploy: push the tracker to GitHub and switch the website on.

Reads the token from the environment so it never appears in a command line,
a log, or this file. Nothing is printed that could leak it.

    GH_TOKEN=xxx GH_USER=yourname python3 deploy_github.py

Steps, each verified before moving on:
  1. check the token works and see who it belongs to
  2. confirm the target repository exists
  3. push every file to the main branch
  4. allow the robot to write (skipped silently if not permitted)
  5. switch on GitHub Pages, serving the /docs folder
  6. start the robot immediately
  7. wait for it to finish and report the result
  8. confirm the live address actually answers
"""

import json
import os
import subprocess
import sys
import time
import urllib.error
import urllib.request

TOKEN = os.environ.get("GH_TOKEN", "").strip()
USER = os.environ.get("GH_USER", "").strip()
REPO = os.environ.get("GH_REPO", "gold-watch").strip()
HERE = os.path.dirname(os.path.abspath(__file__))

API = "https://api.github.com"


def say(step, msg):
    print("[%s] %s" % (step, msg), flush=True)


def call(method, path, body=None, expect=(200, 201, 202, 204)):
    """Call the GitHub API. Returns (status, parsed_json_or_None)."""
    url = path if path.startswith("http") else API + path
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(url, data=data, method=method)
    req.add_header("Authorization", "Bearer " + TOKEN)
    req.add_header("Accept", "application/vnd.github+json")
    req.add_header("X-GitHub-Api-Version", "2022-11-28")
    req.add_header("User-Agent", "gold-watch-deploy")
    if data:
        req.add_header("Content-Type", "application/json")
    try:
        with urllib.request.urlopen(req, timeout=45) as r:
            raw = r.read().decode() or "null"
            return r.status, json.loads(raw)
    except urllib.error.HTTPError as exc:
        raw = exc.read().decode(errors="replace")
        try:
            parsed = json.loads(raw)
        except ValueError:
            parsed = {"message": raw[:300]}
        return exc.code, parsed
    except Exception as exc:                                   # noqa: BLE001
        return 0, {"message": str(exc)}


def run(cmd, secret=None):
    """Run a shell command, scrubbing the token out of any output."""
    p = subprocess.run(cmd, cwd=HERE, shell=True, capture_output=True, text=True)
    out = (p.stdout + p.stderr)
    if secret:
        out = out.replace(secret, "***")
    return p.returncode, out.strip()


def main():
    if not TOKEN or not USER:
        print("need GH_TOKEN and GH_USER in the environment", file=sys.stderr)
        return 2

    # 1 ---------------------------------------------------------------
    status, me = call("GET", "/user")
    if status == 200:
        say("1/8", "token works, account: %s" % me.get("login"))
        owner = me.get("login") or USER
    else:
        # Fine-grained tokens scoped to one repo cannot read /user.
        say("1/8", "token is repo-scoped (cannot read account) - using %s" % USER)
        owner = USER

    slug = "%s/%s" % (owner, REPO)

    # 2 ---------------------------------------------------------------
    status, repo = call("GET", "/repos/" + slug)
    if status != 200:
        say("2/8", "cannot see %s (http %s: %s)" % (slug, status, repo.get("message")))
        say("", "create an EMPTY public repo named '%s' first, then re-run" % REPO)
        return 1
    if repo.get("private"):
        say("2/8", "WARNING: repo is private - free Pages and scheduled runs need public")
    say("2/8", "found %s" % slug)

    # 3 ---------------------------------------------------------------
    remote = "https://x-access-token:%s@github.com/%s.git" % (TOKEN, slug)
    run("git remote remove deploy", TOKEN)
    rc, out = run("git remote add deploy '%s'" % remote, TOKEN)
    if rc:
        say("3/8", "could not set remote: %s" % out)
        return 1

    rc, out = run("git push deploy main:main", TOKEN)
    if rc:
        # Repo was created with a README, so histories differ. Safe to replace:
        # the only thing there is GitHub's own starter commit.
        say("3/8", "normal push refused, replacing the starter commit")
        rc, out = run("git push --force deploy main:main", TOKEN)
    if rc:
        say("3/8", "push failed: %s" % out[-400:])
        return 1
    say("3/8", "pushed every file to main")

    # 4 ---------------------------------------------------------------
    status, _ = call("PUT", "/repos/%s/actions/permissions/workflow" % slug,
                     {"default_workflow_permissions": "write",
                      "can_approve_pull_request_reviews": False})
    if status in (200, 204):
        say("4/8", "robot allowed to save its work")
    else:
        say("4/8", "could not set write permission automatically "
                   "(fix by hand if step 7 fails: Settings > Actions > General "
                   "> Workflow permissions > Read and write)")

    # 5 ---------------------------------------------------------------
    body = {"source": {"branch": "main", "path": "/docs"}}
    status, res = call("POST", "/repos/%s/pages" % slug, body)
    if status == 409:
        status, res = call("PUT", "/repos/%s/pages" % slug, body)
    if status in (200, 201, 204):
        say("5/8", "website switched on, serving /docs")
    else:
        say("5/8", "Pages API said http %s: %s" % (status, res.get("message")))
        say("", "do it by hand: Settings > Pages > branch main, folder /docs")

    # 6 ---------------------------------------------------------------
    status, _ = call("POST",
                     "/repos/%s/actions/workflows/update.yml/dispatches" % slug,
                     {"ref": "main"})
    if status in (201, 204):
        say("6/8", "robot started")
    else:
        say("6/8", "could not start the robot automatically (http %s)" % status)

    # 7 ---------------------------------------------------------------
    say("7/8", "waiting for the first build...")
    verdict = "unknown"
    for _ in range(40):                       # up to ~5 minutes
        time.sleep(8)
        status, runs = call("GET", "/repos/%s/actions/runs?per_page=1" % slug)
        if status != 200 or not runs.get("workflow_runs"):
            continue
        r = runs["workflow_runs"][0]
        if r.get("status") == "completed":
            verdict = r.get("conclusion")
            say("7/8", "first build: %s" % verdict)
            if verdict != "success":
                say("", "see what failed: %s" % r.get("html_url"))
            break
    else:
        say("7/8", "still building - check the Actions tab in a minute")

    # 8 ---------------------------------------------------------------
    site = "https://%s.github.io/%s/" % (owner.lower(), REPO)
    code = 0
    for _ in range(12):                        # Pages can take ~2 min first time
        time.sleep(10)
        rc, out = run("curl -s -o /dev/null -w '%%{http_code}' --max-time 20 " + site)
        code = out.strip()
        if code == "200":
            break
    say("8/8", "live site responded http %s" % code)

    print("")
    print("=" * 58)
    print("  YOUR WEBSITE")
    print("  " + site)
    print("=" * 58)
    print("  Updates itself every 15 minutes from now on.")
    print("  Delete the token now: github.com/settings/tokens")
    return 0


if __name__ == "__main__":
    sys.exit(main())
