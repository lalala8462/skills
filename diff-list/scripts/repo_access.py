#!/usr/bin/env python3
"""Resolve a GitHub URL to an isolated, automatically maintained Git cache."""
import json
import os
import re
import shutil
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


def local_repo_path(value):
    """Resolve an existing local Git worktree without reading or changing its worktree files."""
    candidate = Path(value).expanduser()
    if not candidate.exists():
        return None
    try:
        root = run_git('-C', candidate, 'rev-parse', '--show-toplevel')
    except ValueError as exc:
        raise ValueError('Local repository path is not a Git worktree: ' + str(candidate)) from exc
    return Path(root).resolve()


def _local_repo_name(root):
    name = root.name
    try:
        origin = run_git('-C', root, 'config', '--get', 'remote.origin.url')
        if origin:
            try:
                _, _, name, _ = parse_repo_url(origin)
            except ValueError:
                pass
    except ValueError:
        pass
    return name


def repository_name(value):
    """Return a stable project name from a URL or local worktree origin."""
    local_root = local_repo_path(value)
    if local_root is not None:
        return _local_repo_name(local_root)
    return parse_repo_url(value)[2]


def prepare_repo(url):
    """Create a fresh isolated clone without checking out repository files."""
    local_root = local_repo_path(url)
    if local_root is not None:
        repo = _local_repo_name(local_root)
        source = str(local_root)
    else:
        _, _, repo, source = parse_repo_url(url)
    base = Path(os.getenv('LOCALAPPDATA') or tempfile.gettempdir()) / 'diff-list' / 'runs'
    base.mkdir(parents=True, exist_ok=True)
    run_dir = Path(tempfile.mkdtemp(prefix='run-', dir=str(base)))
    target = run_dir / 'repo'
    try:
        run_git('-c', 'core.longpaths=true', 'clone', '--quiet', '--no-checkout', source, target)
    except ValueError:
        # clone 失败时无证据可复用，清理由本次调用创建的空目录。
        shutil.rmtree(str(run_dir))
        raise
    return target, repo


def cleanup_analysis_clones(analysis_path):
    """交付成功后只删除本工具创建的隔离 clone，不触碰源仓库及其它目录。"""
    analysis=json.loads(Path(analysis_path).read_text(encoding='utf-8-sig'))
    projects=analysis.get('projects') or []
    if not projects:
        raise ValueError('analysis.json missing projects for clone cleanup')
    root=(Path(os.getenv('LOCALAPPDATA') or tempfile.gettempdir())/'diff-list'/'runs').resolve()
    directories=[]
    for project in projects:
        original=project.get('analysis_clone')
        if not isinstance(original,str) or not original:
            raise ValueError('analysis project missing analysis_clone')
        raw=Path(original)
        path=raw.resolve()
        parent=path.parent
        if (raw.is_symlink() or parent.is_symlink() or path.name!='repo'
                or parent.parent!=root or not parent.name.startswith('run-')
                or not (path/'.git').is_dir()
                or {part.name for part in parent.iterdir()}!={'repo'}):
            raise ValueError('refuse unsafe clone cleanup: '+str(original))
        directories.append(parent)
    for directory in sorted(set(directories)):
        shutil.rmtree(str(directory))
    return len(set(directories))
