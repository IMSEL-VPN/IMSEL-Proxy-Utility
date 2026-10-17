#!/usr/bin/env python3
"""Checked fast-forward publication to the two documentation remotes only."""
import argparse
import hashlib
import os
import re
import subprocess
import sys
from pathlib import Path

from history_guard import validate_history

ROOT = Path(__file__).resolve().parents[1]
REMOTES = {
    "origin": "https://github.com/samueladamson37-web/IMSEL-Proxy-Utility.git",
    "gitlab": "https://gitlab.com/samueladamson37/imsel-proxy-utility.git",
}


def git(*args):
    return subprocess.check_output(["git", *args], cwd=ROOT, text=True).strip()


def check_remote(name, url):
    for option in ((), ("--push",)):
        if git("remote", "get-url", *option, "--all", name).splitlines() != [url]:
            raise ValueError(f"Unexpected remote destination: {name}")


def check_update_config(tip, commit, allow):
    if not allow:
        changes = git("diff", "--name-only", tip, commit, "--", "update-config.json")
        history = git("log", "--format=%H", f"{tip}..{commit}", "--", "update-config.json")
        if changes or history:
            raise ValueError("update-config.json changed; separate explicit authorization required")


def reset_plan(expected, update_config_hash):
    """Explicit one-time reset; never infer leases or fetch old ancestry here."""
    commit = git("rev-parse", "HEAD")
    if git("rev-list", "--count", commit) != "1":
        raise ValueError("History reset requires exactly one independent root commit")
    published_config = subprocess.check_output(
        ["git", "show", f"{commit}:update-config.json"], cwd=ROOT)
    if hashlib.sha256(published_config).hexdigest() != update_config_hash:
        raise ValueError("Preserved update-config.json checksum mismatch")
    pending = []
    for name, url in REMOTES.items():
        check_remote(name, url)
        main, branch = expected[name]
        if not all(re.fullmatch(r"[0-9a-f]{40}", value) for value in (main, branch)):
            raise ValueError("Exact old main and docs-branch SHAs are required")
        actual = dict(line.split()[::-1] for line in
                      git("ls-remote", "--heads", "--tags", name).splitlines())
        if actual == {"refs/heads/main": commit}:
            continue
        if actual != {"refs/heads/main": main,
                      "refs/heads/docs/ping-concurrency-settings": branch}:
            raise ValueError(f"Remote refs changed or unknown refs exist: {name}")
        pending.append(name)
    return pending


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--execute", action="store_true")
    parser.add_argument("--allow-update-config", action="store_true")
    parser.add_argument("--reset-history", action="store_true")
    parser.add_argument("--expected-origin", nargs=2, metavar=("MAIN", "DOCS_BRANCH"))
    parser.add_argument("--expected-gitlab", nargs=2, metavar=("MAIN", "DOCS_BRANCH"))
    parser.add_argument("--preserved-update-config-sha256")
    args = parser.parse_args()
    if Path(git("rev-parse", "--show-toplevel")).resolve() != ROOT.resolve():
        raise ValueError("Not the documentation Git root")
    if git("branch", "--show-current") != "main" or git("status", "--porcelain"):
        raise ValueError("Publication requires clean main, including untracked files")
    commit = git("rev-parse", "HEAD")
    expected = {"origin": args.expected_origin, "gitlab": args.expected_gitlab}
    if args.reset_history:
        if (args.allow_update_config or not all(expected.values()) or
                not args.preserved_update_config_sha256):
            raise ValueError("Reset requires exact leases and preserved update-config checksum")
        pending = reset_plan(expected, args.preserved_update_config_sha256)
    else:
        if any(expected.values()) or args.preserved_update_config_sha256:
            raise ValueError("Reset options require --reset-history")
        for name, url in REMOTES.items():
            check_remote(name, url)
            subprocess.run(["git", "fetch", "--no-tags", name, "main"], cwd=ROOT, check=True)
            tip = git("rev-parse", "FETCH_HEAD")
            subprocess.run(["git", "merge-base", "--is-ancestor", tip, commit], cwd=ROOT, check=True)
            check_update_config(tip, commit, args.allow_update_config)
    validate_history(commit)
    subprocess.run([sys.executable, "scripts/validate-docs.py"], cwd=ROOT, check=True)
    subprocess.run([sys.executable, "-m", "unittest", "discover", "-s", "tests", "-v"], cwd=ROOT, check=True)
    subprocess.run([sys.executable, "scripts/build-docs.py"], cwd=ROOT, check=True)
    subprocess.run(["git", "diff", "--check"], cwd=ROOT, check=True)
    if git("status", "--porcelain") or git("rev-parse", "HEAD") != commit:
        raise ValueError("Repository changed during validation")
    if not args.execute:
        print(f"[READY] {commit}: validated for both remotes; nothing pushed")
        return
    completed = []
    try:
        if args.reset_history:
            pending = reset_plan(expected, args.preserved_update_config_sha256)
        for name, url in REMOTES.items():
            if args.reset_history and name not in pending:
                continue
            if git("status", "--porcelain") or git("rev-parse", "HEAD") != commit:
                raise ValueError("Repository changed before push")
            check_remote(name, url)
            command = ["git", "-c", "core.hooksPath=.githooks", "push"]
            environment = os.environ.copy()
            if args.reset_history:
                old_main, old_branch = expected[name]
                command += ["--atomic", f"--force-with-lease=refs/heads/main:{old_main}",
                            f"--force-with-lease=refs/heads/docs/ping-concurrency-settings:{old_branch}"]
                environment["DOCS_RESET_DELETE_SHA"] = old_branch
            command += [name, f"{commit}:refs/heads/main"]
            if args.reset_history:
                command += [":refs/heads/docs/ping-concurrency-settings"]
            subprocess.run(command, cwd=ROOT, env=environment, check=True)
            completed.append(name)
        for name in REMOTES:
            lines = git("ls-remote", "--heads", name, "refs/heads/main").splitlines()
            if len(lines) != 1 or lines[0].split()[0] != commit:
                raise ValueError(f"Remote changed or failed verification: {name}")
            if args.reset_history:
                refs = git("ls-remote", "--heads", "--tags", name).splitlines()
                if len(refs) != 1 or refs[0].split() != [commit, "refs/heads/main"]:
                    raise ValueError(f"Unexpected refs remain after reset: {name}")
    except Exception:
        print(f"[PARTIAL] Confirmed pushes: {completed}; rerun after resolving errors, never force-push", file=sys.stderr)
        raise
    print(f"[OK] Both documentation remotes contain {commit}")


if __name__ == "__main__":
    try:
        main()
    except (ValueError, subprocess.CalledProcessError, FileNotFoundError) as error:
        print(f"[FAIL] {error}", file=sys.stderr)
        raise SystemExit(1)
