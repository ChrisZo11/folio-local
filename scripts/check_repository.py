"""Check staged files or reachable Git history without printing secret contents."""
import argparse
from functools import lru_cache
from pathlib import PurePosixPath
import re
import subprocess
import sys

FILES = {
    '.gitignore', '.gitattributes', 'README.md', 'SECURITY.md', 'pyproject.toml',
    'start.ps1', 'claude-desktop.example.json',
    '.githooks/pre-commit', '.githooks/pre-push', 'scripts/check_repository.py',
    'web/package.json', 'web/package-lock.json', 'web/index.html',
    'web/tsconfig.json', 'web/vite.config.ts', 'web/public/favicon.svg',
    'docs/USAGE.md', 'docs/demo-preview.jpg',
    'examples/transactions.csv', 'examples/holdings-only.csv', 'examples/manual-transaction.json',
}
RULES = {
    'GitHub credential': re.compile(r'\b(?:gh[pousr]_[A-Za-z0-9]{20,}|github_pat_[A-Za-z0-9_]{30,})\b'),
    'API secret': re.compile(r'\bsk-(?:proj-|ant-api\d+-)?[A-Za-z0-9_\-]{20,}\b'),
    'AWS access key': re.compile(r'\b(?:AKIA|ASIA)[A-Z0-9]{16}\b'),
    'private key': re.compile(r'-----BEGIN (?:[A-Z]+ )?PRIVATE KEY-----'),
    'credential in URL': re.compile(r'https?://[^\s/@:]+:[^\s/@]+@'),
    'personal Windows path': re.compile(r'(?i)\b[A-Z]:[\\/]+Users[\\/]+[^\\/\s"\']+'),
    'personal Unix path': re.compile(r'/(?:Users|home)/[A-Za-z0-9_.-]+/'),
}
EMAIL = re.compile(r'\b[A-Za-z0-9._%+\-]+@[A-Za-z0-9.\-]+\.[A-Za-z]{2,}\b')
ASSIGNMENT = re.compile(r'''(?i)\b(?:api[_-]?key|access[_-]?token|secret[_-]?key|password|client[_-]?secret)\s*[=:]\s*["']([^"'\r\n]{8,})["']''')


def allowed(path):
    p = PurePosixPath(path)
    if p.is_absolute() or '..' in p.parts or '\\' in path:
        return False
    if path in FILES:
        return True
    return len(p.parts) == 2 and p.parts[0] in {'portfolio', 'tests'} and p.suffix == '.py' or (
        len(p.parts) == 3 and p.parts[:2] == ('web', 'src') and p.suffix in {'.ts', '.tsx', '.css'})


def content_findings(content):
    findings = []
    for line, text in enumerate(content.splitlines(), 1):
        for name, pattern in RULES.items():
            if pattern.search(text):
                findings.append((line, name))
        for match in EMAIL.finditer(text):
            address = match.group().lower()
            if not address.endswith(('@users.noreply.github.com', '@example.com', '@example.invalid')):
                findings.append((line, 'personal email'))
        for match in ASSIGNMENT.finditer(text):
            value = match.group(1)
            if not value.upper().startswith(('YOUR_', 'REPLACE_', 'EXAMPLE_', '<')):
                findings.append((line, 'hardcoded credential assignment'))
    return findings


def inspect_blob(path, content, mode='100644'):
    if not allowed(path):
        return ['path is outside the reviewed source/documentation allowlist']
    if mode not in {'100644', '100755'}:
        return ['symlinks, submodules, and conflicted entries are not allowed']
    if len(content) > 2_000_000:
        return ['file exceeds the 2 MB review limit']
    if path == 'docs/demo-preview.jpg':
        if not content.startswith(b'\xff\xd8') or not content.endswith(b'\xff\xd9'):
            return ['snapshot must be a JPEG captured from the synthetic demo']
        if b'Exif\x00\x00' in content or b'http://ns.adobe.com/xap/' in content:
            return ['snapshot contains EXIF/XMP metadata; use a clean browser screenshot']
        return []
    try:
        text = content.decode('utf-8')
    except UnicodeDecodeError:
        return ['unexpected binary or non-UTF-8 file']
    return [f'line {line}: {name}' for line, name in content_findings(text)]


def git(*args):
    result = subprocess.run(['git', *args], capture_output=True, check=True)
    return result.stdout


@lru_cache(maxsize=None)
def read_object(oid):
    kind = git('cat-file', '-t', oid).decode().strip()
    return kind, git('cat-file', '-p', oid) if kind in {'blob', 'commit'} else b''


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--history', action='store_true', help='also inspect all reachable commits and file blobs')
    args = parser.parse_args()
    findings, checked = [], set()
    for entry in git('ls-files', '--stage', '-z').split(b'\x00'):
        if not entry:
            continue
        meta, raw_path = entry.split(b'\t', 1)
        mode, oid, stage = meta.decode().split()
        path = raw_path.decode('utf-8')
        if stage != '0':
            findings.append(f'{path}: unresolved merge conflict')
            continue
        kind, content = read_object(oid)
        if kind != 'blob':
            findings.append(f'{path}: unexpected Git object type')
            continue
        findings.extend(f'{path}: {message}' for message in inspect_blob(path, content, mode))
        checked.add((path, oid))
    if args.history:
        for line in git('rev-list', '--objects', '--all').decode('utf-8').splitlines():
            oid, _, path = line.partition(' ')
            kind, content = read_object(oid)
            if kind == 'commit':
                findings.extend(f'commit {oid[:12]}: line {n}: {name}' for n, name in content_findings(content.decode('utf-8')))
            elif kind == 'blob' and (path, oid) not in checked:
                findings.extend(f'{path} (history): {message}' for message in inspect_blob(path, content))
                checked.add((path, oid))
    if findings:
        print('Repository check BLOCKED. Sensitive contents are not displayed.', file=sys.stderr)
        for finding in findings:
            print(f'  {finding}', file=sys.stderr)
        return 1
    if not checked:
        print('Repository check BLOCKED: no staged or committed source files.', file=sys.stderr)
        return 1
    print(f'Repository check passed: {len(checked)} reviewed file versions' + (' plus reachable commit metadata.' if args.history else '.'))
    return 0


if __name__ == '__main__':
    try:
        sys.exit(main())
    except (OSError, subprocess.CalledProcessError, ValueError) as exc:
        print(f'Repository check could not finish ({type(exc).__name__}); refusing to continue.', file=sys.stderr)
        sys.exit(1)
