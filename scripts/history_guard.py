"""Validate every reachable documentation tree, not merely the final checkout."""
import argparse
import importlib.util
import json
import os
from pathlib import Path, PurePosixPath
import re
import subprocess
import sys
import tempfile

ROOT = Path(__file__).resolve().parents[1]
DESTINATIONS = {
    'https://gitlab.com/samueladamson37/imsel-proxy-utility.git',
    'https://github.com/samueladamson37-web/IMSEL-Proxy-Utility.git',
}
ZERO = '0' * 40
BASE_FILES = {
    '.gitattributes', '.gitignore', '.gitlab-ci.yml', '.githooks/pre-push',
    'mkdocs.yml', 'public-pages.json', 'update-config.json',
}


def git(*args, root=ROOT):
    return subprocess.check_output(['git', *args], cwd=root)


def validator():
    spec = importlib.util.spec_from_file_location('public_validator', ROOT / 'scripts/validate-docs.py')
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def allowed(name, pages):
    path = PurePosixPath(name)
    return (name in BASE_FILES or name in pages or
            (len(path.parts) == 2 and path.parts[0] == 'scripts' and path.suffix == '.py') or
            (len(path.parts) == 2 and path.parts[0] == 'tests' and
             path.name.startswith('test_') and path.suffix == '.py') or
            (len(path.parts) == 3 and path.parts[:2] == ('.github', 'workflows') and
             path.suffix in {'.yml', '.yaml'}))


def validate_history(revision='HEAD', root=ROOT):
    revision = git('rev-parse', '--verify', f'{revision}^{{commit}}', root=root).decode().strip()
    if git('rev-parse', '--is-shallow-repository', root=root).strip() != b'false':
        raise ValueError('Full history is required; shallow checkout refused')
    commits = git('rev-list', revision, root=root).decode().splitlines()
    checked = set()
    policy = validator()
    for commit in commits:
        tree = git('rev-parse', f'{commit}^{{tree}}', root=root).strip()
        if tree in checked:
            continue
        checked.add(tree)
        entries = {}
        for row in git('ls-tree', '-rz', commit, root=root).split(b'\0'):
            if not row:
                continue
            info, raw_name = row.split(b'\t', 1)
            mode, kind, oid = info.split()
            name = raw_name.decode('utf-8')
            path = PurePosixPath(name)
            if (mode not in {b'100644', b'100755'} or kind != b'blob' or
                    path.is_absolute() or '..' in path.parts or '\\' in name or
                    ':' in name or '.git' in path.parts):
                raise ValueError(f'Unsafe entry in {commit}: {name}')
            entries[name] = oid.decode()
        if 'public-pages.json' not in entries:
            raise ValueError(f'Not a documentation tree: {commit}')
        pages = json.loads(git('cat-file', 'blob', entries['public-pages.json'], root=root))
        if not isinstance(pages, list) or not all(isinstance(page, str) for page in pages):
            raise ValueError(f'Invalid page manifest in {commit}')
        for name in entries:
            if not allowed(name, pages):
                raise ValueError(f'Unapproved historical file in {commit}: {name}')
        with tempfile.TemporaryDirectory(prefix='docs-history-') as temporary:
            snapshot = Path(temporary)
            for name, oid in entries.items():
                content = git('cat-file', 'blob', oid, root=root)
                if b'\0' in content:
                    raise ValueError(f'Binary content in documentation tree {commit}: {name}')
                content.decode('utf-8')
                target = snapshot / name
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_bytes(content)
            errors = policy.validate(snapshot)
            if errors:
                raise ValueError(f'Historical tree {commit} rejected: {errors[0]}')
    return len(commits), len(checked)


def check_destination(name, location):
    if location not in DESTINATIONS:
        raise ValueError('Push destination is not a documentation repository')
    for option in ((), ('--push',)):
        urls = git('remote', 'get-url', *option, '--all', name).decode().splitlines()
        if urls != [location]:
            raise ValueError('Fetch/push destination mismatch')
    config = git('config', '--list').decode().splitlines()
    prefix = f'remote.{name}.'.lower()
    for line in config:
        key, _, value = line.partition('=')
        if key.lower() == prefix + 'mirror' and value.lower() == 'true':
            raise ValueError('Mirror pushes are forbidden')
        if key.lower() == prefix + 'push':
            raise ValueError('Configured push refspecs are forbidden')


def check_push(name, location, rows):
    check_destination(name, location)
    tips = set()
    for row in rows.splitlines():
        local_ref, local_oid, remote_ref, remote_oid = row.split()
        if local_oid == ZERO:
            expected = os.environ.get('DOCS_RESET_DELETE_SHA')
            if (remote_ref != 'refs/heads/docs/ping-concurrency-settings' or
                    expected != remote_oid):
                raise ValueError('Unapproved branch deletion')
            continue
        if remote_ref != 'refs/heads/main':
            raise ValueError('Only the documentation main branch may be published')
        if not re.fullmatch('[0-9a-f]{40}', local_oid):
            raise ValueError('Invalid outgoing commit')
        tips.add(local_oid)
    for tip in tips:
        validate_history(tip)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--pre-push', nargs=2, metavar=('REMOTE', 'URL'))
    parser.add_argument('--revision', default='HEAD')
    args = parser.parse_args()
    if args.pre_push:
        check_push(*args.pre_push, sys.stdin.read())
    else:
        commits, trees = validate_history(args.revision)
        print(f'[OK] Full documentation history: {commits} commits, {trees} trees')


if __name__ == '__main__':
    try:
        main()
    except (ValueError, OSError, UnicodeError, subprocess.CalledProcessError) as error:
        print(f'[FAIL] {error}', file=sys.stderr)
        raise SystemExit(1)
