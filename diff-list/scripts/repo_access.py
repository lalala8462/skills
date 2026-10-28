#!/usr/bin/env python3
"""Resolve a GitHub URL to an isolated, automatically maintained Git cache."""
import os
import re
import subprocess
import tempfile
from pathlib import Path

_REPO = re.compile(r'^https://github\.com/([A-Za-z0-9-]+)/([A-Za-z0-9_.-]+?)(?:\.git)?/?$')


def parse_github_url(url):
    """Accept only ordinary HTTPS GitHub owner/repository URLs (no embedded credentials)."""
    match = _REPO.fullmatch(url.strip())
    if not match or match.group(2) in ('.', '..'):
        raise ValueError('Expected GitHub repository URL: https://github.com/owner/repo')
    owner, repo = match.groups()
    return owner, repo, 'https://github.com/{}/{}.git'.format(owner, repo)


def run_git(*args):
    process = subprocess.run(['git', *map(str, args)], stdout=subprocess.PIPE,
                             stderr=subprocess.PIPE, encoding='utf-8',
                             errors='replace', check=False)
    if process.returncode:
        raise ValueError('Git failed: ' + ' '.join(map(str, args)) + '\n' + process.stderr.strip())
    return process.stdout.strip()


def prepare_repo(url, cache_dir=None):
    """Clone/fetch only in the dedicated cache; never checkout/edit the user's code."""
    owner, repo, canonical = parse_github_url(url)
    base = Path(cache_dir) if cache_dir else Path(os.getenv('LOCALAPPDATA') or tempfile.gettempdir()) / 'diff-list' / 'git-cache'
    target = base / owner / repo
    if target.exists():
        if not target.is_dir() or not (target / '.git' / 'HEAD').is_file():
            raise ValueError('Cache path exists but is not a Git cache: ' + str(target))
        actual = run_git('-C', target, 'remote', 'get-url', 'origin')
        if actual != canonical:
            raise ValueError('Git cache origin mismatch for ' + str(target))
        run_git('-C', target, 'fetch', '--quiet', '--prune', '--tags', 'origin')
    else:
        target.parent.mkdir(parents=True, exist_ok=True)
        run_git('clone', '--quiet', canonical, target)
    return target, repo
