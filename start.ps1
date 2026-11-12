param([switch]$Demo)

$ErrorActionPreference = 'Stop'
Push-Location -LiteralPath $PSScriptRoot
try {
    $python = Join-Path $PSScriptRoot '.venv\Scripts\python.exe'
    if (-not (Test-Path -LiteralPath $python)) {
        python -m venv .venv
        if ($LASTEXITCODE -ne 0) { throw 'Could not create the Python virtual environment.' }
        & $python -m pip install -e .
        if ($LASTEXITCODE -ne 0) { throw 'Could not install Python dependencies.' }
    }
    if (-not (Test-Path -LiteralPath 'web\dist\index.html')) {
        npm install --prefix web --no-audit --no-fund
        if ($LASTEXITCODE -ne 0) { throw 'Could not install dashboard dependencies.' }
        npm run build --prefix web
        if ($LASTEXITCODE -ne 0) { throw 'Could not build the dashboard.' }
    }
    Write-Host 'Folio is opening at http://127.0.0.1:8000. Press Ctrl+C to stop.'
    if ($Demo) { & $python -m portfolio serve --demo }
    else { & $python -m portfolio serve }
    if ($LASTEXITCODE -ne 0) { throw 'Folio could not start. Check whether port 8000 is already in use.' }
}
finally { Pop-Location }
