#!/usr/bin/env python3
"""Resolve a GitHub URL to an isolated, automatically maintained Git cache."""
import os
import re
import subprocess
import tempfile
from pathlib import Path
from urllib.parse import urlsplit

_REPO = re.compile(r'^https://github\.com/([A-Za-z0-9-]+)/([A-Za-z0-9_.-]+?)(?:\.git)?/?$')


def parse_github_url(url):
    """Accept only ordinary HTTPS GitHub owner/repository URLs (no embedded credentials)."""
    match = _REPO.fullmatch(url.strip())
    if not match or match.group(2) in ('.', '..'):
        raise ValueError('Expected GitHub repository URL: https://github.com/owner/repo')
    owner, repo = match.groups()
    return owner, repo, 'https://github.com/{}/{}.git'.format(owner, repo)


def parse_repo_url(url):
    """Accept GitHub or GitLab HTTP(S) clone URLs, without credentials or URL options."""
    parts = urlsplit(url.strip())
    if parts.hostname == 'github.com':
        owner, repo, canonical = parse_github_url(url)
        return ('github.com', owner, repo, canonical)
    if (parts.scheme not in ('http', 'https') or not parts.hostname or
            parts.username or parts.password or parts.query or parts.fragment or
            parts.port is not None):
        raise ValueError('Expected GitLab HTTP(S) repository URL without credentials')
    segments = parts.path.strip('/').split('/')
    if len(segments) < 2 or any(not re.fullmatch(r'[A-Za-z0-9_][A-Za-z0-9_.-]*', s)
                                or s in ('.', '..') for s in segments):
        raise ValueError('Expected GitLab group/project repository path')
    segments[-1] = re.sub(r'\.git$', '', segments[-1])
    if not segments[-1] or segments[-1] in ('.', '..'):
        raise ValueError('Invalid GitLab project name')
    canonical = '{}://{}/{}.git'.format(parts.scheme, parts.hostname, '/'.join(segments))
    return (parts.hostname, '/'.join(segments[:-1]), segments[-1], canonical)


def run_git(*args):
    process = subprocess.run(['git', *map(str, args)], stdout=subprocess.PIPE,
                             stderr=subprocess.PIPE, encoding='utf-8',
                             errors='replace', check=False)
    if process.returncode:
        raise ValueError('Git failed: ' + ' '.join(map(str, args)) + '\n' + process.stderr.strip())
    return process.stdout.strip()


def prepare_repo(url, cache_dir=None):
    """Create a fresh isolated clone without checking out repository files."""
    if cache_dir is not None:
        raise ValueError('--cache-dir is no longer supported; isolated runs use the dedicated directory')
    _, _, repo, canonical = parse_repo_url(url)
    base = Path(os.getenv('LOCALAPPDATA') or tempfile.gettempdir()) / 'diff-list' / 'runs'
    base.mkdir(parents=True, exist_ok=True)
    run_dir = Path(tempfile.mkdtemp(prefix='run-', dir=str(base)))
    target = run_dir / 'repo'
    run_git('-c', 'core.longpaths=true', 'clone', '--quiet', '--no-checkout', canonical, target)
    return target, repo
