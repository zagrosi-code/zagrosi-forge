"""Release artifact receipts travel between producer and receiver without the ledger."""
from concurrent.futures import ThreadPoolExecutor
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
from types import SimpleNamespace
import zipfile

import pytest

from forge_test_helpers import SCRIPT, load_zagrosi_module
from test_delivery import git


def forge(cwd, *args, succeeds=True):
    result = subprocess.run([sys.executable, str(SCRIPT), 'delivery', *map(str, args)], cwd=cwd,
                            capture_output=True, text=True, timeout=20)
    assert result.returncode == int(not succeeds), (result.stdout, result.stderr)
    payload = json.loads(result.stdout)
    assert payload['success'] is succeeds, payload
    return payload


@pytest.fixture
def tracked(tmp_path):
    repo = tmp_path / 'producer'
    repo.mkdir()
    git(repo, 'init', '-b', 'main')
    git(repo, 'config', 'user.name', 'Builder')
    git(repo, 'config', 'user.email', 'builder@example.invalid')
    git(repo, 'commit', '--allow-empty', '-m', 'base')
    git(repo, 'checkout', '-b', 'feature')
    git(repo, 'commit', '--allow-empty', '-m', 'release')
    for pr in ('42', '99'):
        forge(repo, 'track', '--pr', pr, '--head-ref', 'refs/heads/feature', '--base-ref', 'refs/heads/main')
    return repo, git(repo, 'rev-parse', 'HEAD')


def record(tracked, *subject, env='uat', sha=None, succeeds=True):
    repo, head = tracked
    return forge(repo, 'artifact', 'record', '--pr', '42', '--sha', sha or head, '--stage', 'build',
                 '--environment', env, '--name', 'runtime', '--actor', 'builder', '--source', 'builds/4',
                 *subject, succeeds=succeeds)


def make_zip(path, contents):
    with zipfile.ZipFile(path, 'w') as out:
        out.writestr('module.py', contents)


def test_verify_different_builds_of_the_same_commit(tracked, tmp_path):
    repo, sha = tracked
    package = tmp_path / 'engine.zip'
    make_zip(package, 'return 1')
    original = package.read_bytes()
    handoff = record(tracked, '--file', package)
    receipt = handoff['artifact']
    assert receipt['sha'] == sha and receipt['filename'] == 'engine.zip'
    assert receipt['size_bytes'] == len(original)
    assert receipt['digest'] == 'sha256:' + hashlib.sha256(original).hexdigest()
    assert receipt['pr'] == '42' and receipt['environment'] == 'uat' and receipt['stage'] == 'build'
    assert receipt['source'] == 'builds/4' and receipt['actor'] == 'builder'
    assert str(tmp_path) not in json.dumps(handoff)
    assert record(tracked, '--file', package, env=' uat ') == handoff
    receiver = tmp_path / 'receiver'
    receiver.mkdir()
    receipt_path = receiver / 'receipt.json'
    receipt_path.write_text(json.dumps(handoff))
    local = receiver / 'renamed.zip'
    local.write_bytes(original)
    verified = forge(receiver, 'artifact', 'verify', '--receipt', receipt_path, '--file', local)
    assert verified['matched'] and verified['verification'] == 'file_bytes'
    make_zip(local, 'return 2')
    assert local.stat().st_size == receipt['size_bytes']
    mismatch = forge(receiver, 'artifact', 'verify', '--receipt', receipt_path, '--file', local, succeeds=False)
    assert not mismatch['matched'] and mismatch['actual']['digest'] != receipt['digest']
    assert record(tracked, '--file', local)['artifact']['receipt_id'] != receipt['receipt_id']
    git(repo, 'worktree', 'add', '--detach', str(tmp_path / 'other'), sha)
    listed = forge(tmp_path / 'other', 'status', '--environment', 'uat')['prs']
    assert {row['digest'] for row in listed['42']['artifacts']} == {receipt['digest'], mismatch['actual']['digest']}
    assert all(row['status'] == 'current' for row in listed['42']['artifacts'])
    assert not listed['99'].get('artifacts')
    assert not forge(repo, 'status', '--pr', '42', '--environment', 'prod')['prs']['42'].get('artifacts')
    git(repo, 'commit', '--allow-empty', '-m', 'restack')
    stale = forge(repo, 'status', '--pr', '42', '--environment', 'uat')['prs']['42']
    assert all(row['status'] == 'stale' for row in stale['artifacts'])
    assert stale['stages']['deploy']['outcome'] == 'unknown'


def test_image_receipt_compares_the_qualified_reference(tracked, tmp_path):
    image = 'registry.example:5000/product/app@sha256:' + 'a' * 64
    release = record(tracked, '--image', image)
    receipt = release['artifact']
    assert receipt['kind'] == 'image' and receipt['digest'] == 'sha256:' + 'a' * 64
    receipt_path = tmp_path / 'image.json'
    receipt_path.write_text(json.dumps(release))
    verified = forge(tmp_path, 'artifact', 'verify', '--receipt', receipt_path, '--image', image)
    assert verified['verification'] == 'reported_reference' and verified['matched']
    changed = image.replace('/product/', '/other/')
    result = forge(tmp_path, 'artifact', 'verify', '--receipt', receipt_path, '--image', changed, succeeds=False)
    assert not result['matched']
    for invalid in ('repo:latest', 'repo@sha256:abc', 'registry.example /app@sha256:' + 'a' * 64):
        assert record(tracked, '--image', invalid, succeeds=False)['error']


def test_invalid_source_and_receipt_leave_ledger_unchanged(tracked, tmp_path):
    repo, _ = tracked
    ledger = repo / '.git' / 'forge-delivery.json'
    previous = ledger.read_bytes()
    assert record(tracked, '--file', tmp_path / 'missing', succeeds=False)['error']
    assert record(tracked, '--file', tmp_path, succeeds=False)['error']
    stale_sha = git(repo, 'rev-parse', 'main')
    data = tmp_path / 'data'
    data.write_bytes(b'package')
    assert record(tracked, '--file', data, sha=stale_sha, succeeds=False)['error']
    assert ledger.read_bytes() == previous
    report = record(tracked, '--file', data)
    previous = ledger.read_bytes()
    report['artifact']['stage'] = 'deploy'
    saved = tmp_path / 'reported.json'
    saved.write_text(json.dumps(report))
    assert forge(repo, 'artifact', 'verify', '--receipt', saved, '--file', data, succeeds=False)['error']
    assert ledger.read_bytes() == previous


def test_concurrent_captures_preserve_other_scopes(tracked, tmp_path):
    package = tmp_path / 'engine.zip'
    make_zip(package, 'return 2')
    environments = ['review', 'uat', 'production']
    with ThreadPoolExecutor(max_workers=len(environments)) as pool:
        receipts = list(pool.map(lambda env: record(tracked, '--file', package, env=env)['artifact'], environments))
    repo, _ = tracked
    all_artifacts = forge(repo, 'status', '--pr', '42')['prs']['42']['artifacts']
    assert {row['receipt_id'] for row in all_artifacts} == {receipt['receipt_id'] for receipt in receipts}
    assert {row['environment'] for row in all_artifacts} == set(environments)


def test_declared_image_type_is_validated_before_parsing(tracked, tmp_path):
    image = 'sha256:' + 'b' * 64
    report = record(tracked, '--image', image)
    report['artifact']['image'] = 123
    receipt_path = tmp_path / 'invalid.json'
    receipt_path.write_text(json.dumps(report))
    assert forge(tmp_path, 'artifact', 'verify', '--receipt', receipt_path, '--image', image, succeeds=False)['error']


def test_concurrent_file_edit_fails_the_hash_observation(tmp_path, monkeypatch):
    package = tmp_path / 'package.zip'
    package.write_bytes(b'original')
    artifacts = load_zagrosi_module().delivery_artifacts
    original_stat = Path.stat
    observed = False

    def edit_at_final_stat(path, *args, **kwargs):
        nonlocal observed
        if path == package:
            if observed:
                with package.open('ab') as writer:
                    writer.write(b'more')
            observed = True
        return original_stat(path, *args, **kwargs)

    monkeypatch.setattr(Path, 'stat', edit_at_final_stat)
    with pytest.raises(ValueError, match='changed'):
        artifacts._file_identity(package)


def test_byte_count_describes_the_stream_even_when_stat_reports_an_advisory_size(tmp_path, monkeypatch):
    # Some Linux virtual regular files report a placeholder size such as 0 or 4096.
    path = tmp_path / 'version'
    contents = b'contents of virtual regular file'
    path.write_bytes(contents)
    artifacts = load_zagrosi_module().delivery_artifacts
    original_stat, original_fstat = Path.stat, os.fstat
    source = path.stat()

    def advisory_size(info):
        if not os.path.samestat(source, info):
            return info
        return SimpleNamespace(st_size=4096, st_ino=info.st_ino, st_dev=info.st_dev, st_mode=info.st_mode,
                               st_mtime_ns=info.st_mtime_ns, st_ctime_ns=info.st_ctime_ns)

    monkeypatch.setattr(os, 'fstat', lambda fd: advisory_size(original_fstat(fd)))
    monkeypatch.setattr(Path, 'stat', lambda p, **options: advisory_size(original_stat(p, **options)))
    evidence = artifacts._file_identity(path)
    assert evidence['size_bytes'] == len(contents)
    assert evidence['digest'] == 'sha256:' + hashlib.sha256(contents).hexdigest()
