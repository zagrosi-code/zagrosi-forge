"""Prepare committed contracts as checkout-private, immutable plan snapshots."""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import posixpath
import re
import stat
import tempfile
from urllib.parse import unquote, urlsplit

from . import actions, artifacts, context_links, markdown, policy, team_git, team_state

MARKER = '.forge-team-plan.json'
FILE_LIMIT = 128 * 1024
BUNDLE_LIMIT = 512 * 1024
FILE_COUNT = 128
_OID = re.compile(r'(?:[a-f0-9]{40}|[a-f0-9]{64})\Z')
_ARTIFACTS = {'codex-plan.md', 'claude-plan.md', 'codex-spec.md', 'claude-spec.md',
              'codex-plan-tdd.md', 'claude-plan-tdd.md', 'codex-research.md', 'claude-research.md',
              'codex-integration-notes.md', 'claude-integration-notes.md', 'codex-evidence.md',
              'claude-evidence.md', 'decisions.md', 'risk-register.md', 'quality-gates.md'}
_MUTABLE_FILES = {'traceability.md', 'forge-report.md', 'assumption-ledger.md',
                  '.forge/scores/history.jsonl', '.forge/report.html'}
_MUTABLE_DIRS = {'implementation', '.forge/packets', '.forge/tdd-skeletons'}
_EXCLUDED_DIRS = {'implementation', '.git', '.zagrosi-project', '.deep-project', '__pycache__',
                  '.cache', 'cache', 'compatibility', 'baselines', '.prompts'}


def _fail(message, code='team-plan-invalid'):
    raise team_state.TeamError(code, message)


def _json(value):
    return json.dumps(value, sort_keys=True, ensure_ascii=True, separators=(',', ':'))


def _digest(files):
    return hashlib.sha256(_json(files).encode()).hexdigest()


def _key(canonical, digest, target):
    return hashlib.sha256(_json([canonical, digest, target]).encode()).hexdigest()


def _name(value):
    if not isinstance(value, str) or team_state.normalize_paths([value]) != [value] or value == '.':
        _fail('Shared contract paths must be portable relative file names.')
    return value


def _mutable(name):
    return name in _MUTABLE_FILES or any(name == base or name.startswith(base + '/') for base in _MUTABLE_DIRS)


def mutable_path(planning, destination):
    """Restrict explicit generated output to the same private execution workspace."""
    root, path = Path(planning).absolute(), Path(destination).absolute()
    try:
        name = path.relative_to(root).as_posix()
        if not _mutable(name) or '..' in path.relative_to(root).parts or not path.resolve().is_relative_to(root.resolve()):
            return False
        current = path
        while current != root:
            if current.is_symlink() or current.is_file() and current.stat().st_nlink != 1:
                return False
            current = current.parent
        return not root.is_symlink()
    except (OSError, RuntimeError, ValueError):
        return False


def _no_links(path, base):
    current = base
    if current.is_symlink():
        _fail('Shared plan directories cannot be symbolic links.')
    for part in path.relative_to(base).parts:
        current /= part
        if current.is_symlink():
            _fail('Shared plan paths cannot contain symbolic links.')


def _read(path, limit=FILE_LIMIT):
    observed = path.lstat()
    if not stat.S_ISREG(observed.st_mode) or observed.st_nlink != 1 or observed.st_size > limit:
        _fail('Shared plan metadata must be bounded regular files without links.')
    descriptor = os.open(path, os.O_RDONLY | getattr(os, 'O_NOFOLLOW', 0) | getattr(os, 'O_NONBLOCK', 0))
    with os.fdopen(descriptor, 'rb') as stream:
        opened = os.fstat(stream.fileno())
        if ((observed.st_dev, observed.st_ino) != (opened.st_dev, opened.st_ino)
                or not stat.S_ISREG(opened.st_mode) or opened.st_nlink != 1):
            _fail('Shared plan metadata changed while opening it.')
        raw = stream.read(limit + 1)
    if len(raw) > limit:
        _fail('Shared plan metadata exceeds its size limit.')
    return raw


def _tree(repo, canonical, commit):
    output = team_git._run(repo.root, 'ls-tree', '-rlz', commit, '--', canonical)['stdout']
    rows = output.split('\0')
    if rows[-1] or len(rows) - 1 > FILE_COUNT:
        _fail('The canonical plan tree is malformed or exceeds 128 files.')
    result, portable_paths = {}, {}
    prefix = '' if canonical == '.' else canonical + '/'
    for row in rows[:-1]:
        header, path = row.split('\t', 1)
        mode, kind, oid, size = header.split()
        if not path.startswith(prefix) or not _OID.fullmatch(oid):
            _fail('Git returned an invalid shared plan tree.')
        name = _name(path[len(prefix):])
        parts = name.split('/')
        for length in range(1, len(parts) + 1):
            component_path = '/'.join(parts[:length])
            identity = (component_path, length == len(parts))
            portable = team_state._portable(component_path)
            previous = portable_paths.get(portable)
            # Reused directories keep one spelling and cannot also be files.
            if previous is not None and (previous != identity or identity[1]):
                _fail('Shared plan paths must not collide on portable filesystems.')
            portable_paths[portable] = identity
        result[name] = {'mode': mode, 'oid': oid, 'size': int(size) if kind == 'blob' else -1}
    return result


def _eligible(name, entries):
    portable = team_state._portable(name)
    parts = portable.split('/')
    if (not name.endswith('.md') or any(part in _EXCLUDED_DIRS for part in parts)
            or 'interview' in parts[-1] or portable in _MUTABLE_FILES or portable.startswith('.forge/')):
        _fail('A shared contract links to an excluded private or generated artifact.')
    item = entries.get(name)
    if item is None or item['mode'] not in {'100644', '100755'} or not 0 <= item['size'] <= FILE_LIMIT:
        _fail('Every shared contract dependency must be a committed regular Markdown file of at most 128 KiB.')
    return item


def _seeds(entries):
    plans = set(entries) & {'codex-plan.md', 'claude-plan.md'}
    if len(plans) != 1:
        _fail('Shared preparation requires exactly one committed physical compact plan.')
    selected = (set(entries) & _ARTIFACTS) | {'sections/index.md'}
    selected.update(name for name in entries if name.startswith('reviews/') and name.count('/') == 1 and name.endswith('.md'))
    return next(iter(plans)), selected


def _blobs(repo, names, entries):
    objects = dict.fromkeys(entries[name]['oid'] for name in names)
    raw = team_git._run(repo.root, 'cat-file', '--batch', prompt=''.join(oid + '\n' for oid in objects))['stdout'].encode('utf-8')
    offset, found = 0, {}
    for expected in objects:
        ending = raw.find(b'\n', offset)
        if ending < 0:
            _fail('Git returned an incomplete shared contract object.')
        fields = raw[offset:ending].decode('ascii').split()
        if len(fields) != 3 or fields[:2] != [expected, 'blob'] or not fields[2].isdigit():
            _fail('Git returned an unexpected shared contract object.')
        size = int(fields[2])
        if size > FILE_LIMIT:
            _fail('A shared contract object exceeds 128 KiB.')
        start, end = ending + 1, ending + 1 + size
        content = raw[start:end]
        if len(content) != size or raw[end:end + 1] != b'\n':
            _fail('Git returned malformed shared contract framing.')
        algorithm = hashlib.sha1 if len(expected) == 40 else hashlib.sha256
        if algorithm(b'blob ' + str(size).encode() + b'\0' + content).hexdigest() != expected:
            _fail('Shared contract bytes do not match their Git object identity.')
        content.decode('utf-8')
        found[expected], offset = content, end + 1
    if offset != len(raw):
        _fail('Git returned trailing shared contract data.')
    return {name: found[entries[name]['oid']] for name in names}


def _relative(base, value):
    link = urlsplit(value)
    if link.scheme in {'https', 'http', 'mailto'} or not link.path and link.fragment:
        return None
    if link.scheme or link.netloc or not link.path or link.path.startswith(('/', '\\')):
        _fail('Shared contract file links must remain inside the canonical directory.')
    path = posixpath.normpath(posixpath.join(base, unquote(link.path)))
    if path == '..' or path.startswith('../'):
        _fail('Shared contract file links cannot leave the canonical directory.')
    return _name(path)


def _links(name, content):
    # Adapt definitions for the existing lexer so comments, fences and inline
    # code retain the same meaning as normal contract-link discovery.
    text = re.sub(r'(?m)^([ \t]{0,3})\[([^\]\n]+)\]:[ \t]*(.+)$',
                  lambda match: f'{match[1]}[{match[2]}]({match[3]})', content.decode('utf-8'))
    return {target for link in context_links.local_links(text)
            if (target := _relative(posixpath.dirname(name), link)) is not None}


def _bundle(repo, canonical):
    entries = _tree(repo, canonical, repo.head)
    plan, pending = _seeds(entries)
    content = {}
    while pending:
        wanted = sorted(pending - set(content))
        if not wanted:
            break
        for name in wanted:
            _eligible(name, entries)
        if len(set(content) | set(wanted)) > FILE_COUNT or sum(entries[name]['size'] for name in set(content) | set(wanted)) > BUNDLE_LIMIT:
            _fail('Shared contracts are limited to 128 files and 512 KiB total.')
        content.update(_blobs(repo, wanted, entries))
        pending = set()
        for name in wanted:
            pending.update(_links(name, content[name]))
            if name == plan:
                meta, errors = markdown.parse_forge_meta(content[name].decode('utf-8'))
                if (errors or not meta or meta.get('artifact_type') != 'compact_plan'
                        or not isinstance(meta.get('depth_mode'), str) or meta['depth_mode'] not in policy.DEPTH_MODES):
                    _fail('Shared preparation requires explicit supported compact-plan metadata.')
                source = meta.get('source')
                if not isinstance(source, str) or not (source := _relative('', source)):
                    _fail('A shared compact plan requires an internal source specification.')
                pending.add(source)
            if name == 'sections/index.md':
                sections, errors = markdown.parse_numbered_manifest(content[name].decode('utf-8'), 'SECTION_MANIFEST', policy.SECTION_RE, prefix='section-')
                if errors or not sections:
                    _fail('The shared plan section manifest is incomplete or invalid.')
                pending.update(f'sections/{section}.md' for section in sections)
    files = {name: entries[name] for name in sorted(content)}
    index = {}
    output = team_git._run(repo.root, 'ls-files', '--stage', '-z', '--', canonical)['stdout']
    prefix = '' if canonical == '.' else canonical + '/'
    rows = output.split('\0')
    if rows[-1] or len(rows) - 1 > FILE_COUNT:
        _fail('The shared plan index is malformed or exceeds its bound.')
    for row in rows[:-1]:
        header, path = row.split('\t', 1)
        mode, oid, stage = header.split()
        name = _name(path[len(prefix):])
        if not path.startswith(prefix) or stage != '0' or name in index:
            _fail('Resolve shared plan index conflicts before preparation.', 'team-plan-stale')
        index[name] = {'mode': mode, 'oid': oid}
    if _seeds(index)[1] != _seeds(entries)[1]:
        _fail('Commit shared contract additions before preparation.', 'team-plan-stale')
    directory = repo.root / canonical
    _no_links(directory, repo.root)
    for name in _ARTIFACTS:
        if (directory / name).exists() and name not in entries:
            _fail('An uncommitted artifact would shadow the shared contract.', 'team-plan-stale')
    for name, item in files.items():
        if index.get(name) != {key: item[key] for key in ('mode', 'oid')}:
            _fail('Commit shared contract index changes before preparation.', 'team-plan-stale')
        path = directory / name
        _no_links(path, directory)
        if _read(path).replace(b'\r\n', b'\n') != content[name].replace(b'\r\n', b'\n'):
            _fail('Commit shared contract worktree changes before preparation.', 'team-plan-stale')
    return files, content


def _contract(planning):
    findings = artifacts.compact_plan_findings(planning)
    descriptor = artifacts.compact_plan_descriptor(planning)
    if descriptor is None or findings:
        _fail('The committed compact plan needs a complete source, sections, ownership, tests and passing embedded review.')


def _workspace_files(planning, files):
    count = 0
    for directory, directories, names in os.walk(planning, followlinks=False):
        for name in directories + names:
            count += 1
            if count > 8192:
                _fail('The prepared workspace exceeds its bounded file inventory.')
            path = Path(directory) / name
            relative = path.relative_to(planning).as_posix()
            observed = path.lstat()
            if stat.S_ISLNK(observed.st_mode):
                _fail('Prepared workspaces cannot contain symbolic links.')
            if stat.S_ISDIR(observed.st_mode):
                allowed = _mutable(relative) or any(item.startswith(relative + '/') for item in set(files) | _MUTABLE_FILES | _MUTABLE_DIRS)
            else:
                allowed = stat.S_ISREG(observed.st_mode) and observed.st_nlink == 1 and (relative in files or relative == MARKER or _mutable(relative))
            if not allowed:
                _fail('An added or linked artifact could shadow the prepared contract; preserve it before recovery.')


def _validate(planning, target):
    marker = planning / MARKER
    if not marker.exists() and not marker.is_symlink():
        if planning.parent.name != 'forge-plans':
            return None
        administrative = planning.parent.parent
        detected = team_git._run(administrative, 'rev-parse', '--absolute-git-dir', check=False)
        if detected['returncode'] or Path(detected['stdout'].strip()).resolve() != administrative.resolve():
            return None
        _fail('The prepared plan marker is missing; preserve the workspace before recovery.')
    value = team_git.decode_json(_read(marker, BUNDLE_LIMIT))
    fields = {'version', 'root', 'git_dir', 'target', 'canonical', 'commit', 'digest', 'files'}
    if (not isinstance(value, dict) or set(value) != fields or type(value['version']) is not int or value['version'] != 1
            or any(not isinstance(value[key], str) for key in fields - {'version', 'files'})
            or not _OID.fullmatch(value['commit']) or not re.fullmatch(r'[a-f0-9]{64}', value['digest'])
            or not isinstance(value['files'], dict) or not 1 <= len(value['files']) <= FILE_COUNT):
        _fail('The prepared plan marker is malformed; preserve it before recovery.')
    for name, item in value['files'].items():
        _name(name)
        if (not isinstance(item, dict) or set(item) != {'mode', 'oid', 'size'}
                or not isinstance(item['mode'], str) or item['mode'] not in {'100644', '100755'}
                or not isinstance(item['oid'], str) or not _OID.fullmatch(item['oid'])
                or type(item['size']) is not int or not 0 <= item['size'] <= FILE_LIMIT):
            _fail('The prepared plan file identities are malformed; preserve the workspace before recovery.')
    target = Path(target or value['target']).resolve()
    repo = team_git.Repository.discover(target)
    if (value['root'] != str(repo.root) or value['git_dir'] != str(repo.git_dir) or value['target'] != str(target)
            or value['canonical'] != team_state.normalize_paths([value['canonical']])[0]):
        _fail('The prepared plan belongs to a different repository, checkout or target.')
    key = _key(value['canonical'], value['digest'], target.relative_to(repo.root).as_posix())
    if planning != repo.git_dir / 'forge-plans' / key:
        _fail('The prepared plan is outside its bound private workspace.')
    _no_links(planning, repo.git_dir)
    files, content = _bundle(repo, value['canonical'])
    if value['files'] != files or value['digest'] != _digest(files):
        _fail('The shared contract changed; prepare its reviewed current version.', 'team-plan-stale')
    original = _tree(repo, value['canonical'], value['commit'])
    if any(original.get(name) != item for name, item in files.items()):
        _fail('The prepared marker does not identify its recorded source commit.')
    _workspace_files(planning, files)
    for name in files:
        if _read(planning / name) != content[name]:
            _fail('An immutable prepared contract copy changed; preserve the workspace before recovery.')
    _contract(planning)
    return {'path': value['canonical'], 'digest': value['digest'], 'commit': value['commit'], 'section': None}


def validate(planning, target=None):
    """Recognize ordinary paths cheaply; authenticate every prepared contract copy."""
    try:
        return _validate(Path(os.path.abspath(planning)), target)
    except (OSError, UnicodeError, ValueError, RuntimeError) as exc:
        raise team_state.TeamError('team-plan-invalid', 'Could not validate the prepared plan; preserve its files before recovery.') from exc


def prepare(planning, target):
    """Read reviewed Git blobs and atomically publish a private execution workspace."""
    try:
        target = Path(target).resolve()
        repo = team_git.Repository.discover(target)
        planning = Path(os.path.abspath(planning))
        canonical = planning.relative_to(repo.root).as_posix()
        if canonical != team_state.normalize_paths([canonical])[0]:
            _fail('Choose a canonical plan directory inside the target repository.')
        _no_links(planning, repo.root)
        files, content = _bundle(repo, canonical)
        digest = _digest(files)
        base = repo.git_dir / 'forge-plans'
        workspace = base / _key(canonical, digest, target.relative_to(repo.root).as_posix())
        if workspace.exists() or workspace.is_symlink():
            descriptor = validate(workspace, target)
        else:
            if base.is_symlink() or base.exists() and not base.is_dir():
                _fail('The private prepared-plan directory cannot be redirected.')
            with tempfile.TemporaryDirectory(prefix='forge-plan-', dir=repo.git_dir) as temporary:
                stage = Path(temporary) / 'workspace'
                stage.mkdir()
                for name, raw in content.items():
                    path = stage / name
                    path.parent.mkdir(parents=True, exist_ok=True)
                    path.write_bytes(raw)
                _contract(stage)
                record = {'version': 1, 'root': str(repo.root), 'git_dir': str(repo.git_dir), 'target': str(target),
                          'canonical': canonical, 'commit': repo.head, 'digest': digest, 'files': files}
                (stage / MARKER).write_text(_json(record) + '\n', encoding='utf-8')
                base.mkdir(exist_ok=True)
                try:
                    stage.rename(workspace)
                except OSError:
                    if not workspace.exists():
                        raise
                descriptor = validate(workspace, target)
        return {'success': True, 'status': 'prepared', 'planning_dir': str(workspace),
                'sections_dir': str(workspace / 'sections'), 'source': descriptor,
                'commands': {
                    'team_start': actions.command('team', 'start', '--target-dir', str(target), '--task', '<task>',
                                                  '--planning-dir', str(workspace)),
                    'implement_setup': actions.command('implement-setup', '--target-dir', str(target),
                                                       '--sections-dir', str(workspace / 'sections'))},
                'next_action': 'Reserve a task for this prepared plan, then run the normal implementation admission checks.'}
    except (OSError, UnicodeError, ValueError, RuntimeError) as exc:
        raise team_state.TeamError('team-plan-invalid', 'Could not prepare the committed plan; preserve its files and inspect the canonical contract.') from exc
