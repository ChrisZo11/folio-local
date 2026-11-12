# Privacy and repository safety

Folio is designed to keep your portfolio on your own machine. A private source repository is an additional access boundary; it is not a place to store financial records, passwords, or credentials.

## What belongs in this repository

- Application source, dependency manifests, tests, and setup instructions.
- Explicitly labeled, independently invented demo data and example imports.
- The reviewed demo-only screenshot in `docs/demo-preview.jpg`.
- A Claude MCP template containing placeholder paths only.

## What stays on your machine

Databases and their WAL/SHM files, backups, real CSV exports, statements, PDFs, spreadsheets, personal requirements/context documents, local MCP settings, environment files, credentials, logs, dependency folders, caches, and build outputs are excluded by the default-deny `.gitignore`.

The original personal PRD is intentionally not versioned. No original broker records or account-specific path configuration are needed to run a fresh checkout.

## Checks before commit and push

Enable the bundled hooks once in each clone:

```sh
git config core.hooksPath .githooks
python scripts/check_repository.py --history
```

The commit check reads **staged Git blobs**, so it checks what will be committed rather than only your working files. The push check also scans all reachable historical blobs and commit metadata. It rejects paths outside the source/documentation allowlist, common token/private-key patterns, credential URLs, personal filesystem paths/emails, symlinks, unexpected binaries, and snapshot EXIF/XMP metadata. Errors identify the path/rule without echoing detected secret values.

When adding a new source path, review it and update both `.gitignore` and the guard allowlist if necessary. Do not use `git add -f` for private data. Lockfiles are intentional source dependencies and should remain tracked.

These checks supplement human review. They cannot recognize every possible secret or distinguish a real balance from an invented number in otherwise valid source. Always inspect staged changes and screenshots. Only capture README images from a fresh isolated synthetic demo; never replace the approved screenshot with a live account capture. Git hooks can be bypassed or disabled and are not a server-side security boundary.

## Local runtime boundaries

- The web server binds to `127.0.0.1`, validates Host and browser mutation origins, and has no authentication for remote access. Do not expose it to your LAN or the internet.
- Portfolio records remain in SQLite. Only public ticker symbols are sent to the market-data provider. Prices and FX are delayed/cached, not a streaming broker feed.
- Read-only MCP tools can report financial data to the client you deliberately connect. Review that client's data-handling policies before using it with real holdings. Refresh tools update public market caches, not trades.
- SQLite databases and local backups are **not encrypted** by this app. Protect the device, filesystem permissions, and backup destination.
- Do not paste portfolio data, credentials, or logs containing them into GitHub issues, pull requests, Actions artifacts, or screenshots.

If a credential is ever committed, revoke/rotate it immediately; deleting the current file does not remove Git history. Remove the sensitive history before pushing, and review access if it was already uploaded. Report suspected vulnerabilities privately to the repository owner without attaching financial records.
