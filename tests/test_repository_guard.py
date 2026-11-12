import importlib.util
from pathlib import Path

module_path = Path(__file__).resolve().parents[1] / 'scripts' / 'check_repository.py'
spec = importlib.util.spec_from_file_location('repository_guard', module_path)
guard = importlib.util.module_from_spec(spec)
spec.loader.exec_module(guard)


def test_only_reviewed_source_and_synthetic_examples_are_allowed():
    for path in ('portfolio/db.py', 'tests/test_ledger.py', 'web/src/main.tsx', 'examples/transactions.csv', 'docs/demo-preview.jpg'):
        assert guard.allowed(path)
    for path in ('data/portfolio.db', '.env', 'web/.env.production', 'statements/broker.pdf', 'docs/live-holdings.jpg', 'exports/trades.csv', '../outside.py', 'portfolio/private.csv'):
        assert not guard.allowed(path)
        assert guard.inspect_blob(path, b'private data')


def test_tokens_paths_and_emails_are_blocked_without_echoing_contents():
    token = 'gh' + 'p_' + 'a' * 30
    path = 'C:' + '\\Users\\' + 'sample-person' + '\\Documents\\file'
    email = 'sample-person' + '@' + 'mail.invalid'
    for value in (token, path, email):
        findings = guard.inspect_blob('portfolio/example.py', value.encode())
        assert findings
        assert all(value not in finding for finding in findings)
    assert not guard.inspect_blob('claude-desktop.example.json', b'{"command":"C:\\\\path\\\\to\\\\folio"}')


def test_symlinks_and_unexpected_binaries_are_blocked():
    assert guard.inspect_blob('portfolio/db.py', b'../../private', '120000')
    assert guard.inspect_blob('portfolio/db.py', b'\x00\xff\x00')
    assert guard.inspect_blob('docs/demo-preview.jpg', b'not an image')
    assert guard.inspect_blob('docs/demo-preview.jpg', b'\xff\xd8Exif\x00\x00hidden\xff\xd9')


def test_history_scan_catches_secrets_in_prior_commits(tmp_path, monkeypatch, capsys):
    import subprocess
    def git(*args):
        return subprocess.run(['git', '-C', str(tmp_path), *args], check=True, capture_output=True)
    git('init', '-b', 'main')
    git('config', 'user.name', 'Repository test')
    git('config', 'user.email', 'test@example.com')
    (tmp_path / 'README.md').write_text('gh' + 'p_' + 'b' * 30)
    git('add', 'README.md')
    git('commit', '-m', 'Synthetic secret fixture')
    (tmp_path / 'README.md').write_text('Clean current content')
    git('add', 'README.md')
    git('commit', '-m', 'Remove synthetic fixture')
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr('sys.argv', ['check_repository.py', '--history'])
    guard.read_object.cache_clear()
    assert guard.main() == 1
    output = capsys.readouterr().err
    assert 'history' in output
    assert 'GitHub credential' in output
    assert ('gh' + 'p_' + 'b' * 30) not in output
