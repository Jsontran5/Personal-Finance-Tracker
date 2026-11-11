# Run in the current PowerShell terminal so Ctrl+C stops Python directly.
$ErrorActionPreference = 'Stop'
Push-Location -LiteralPath $PSScriptRoot
try {
    if (-not (Test-Path -LiteralPath 'dist\index.html') -or
        -not (Test-Path -LiteralPath '.venv\Scripts\python.exe')) {
        throw 'Run Setup Finance.cmd first.'
    }
    & '.\.venv\Scripts\python.exe' -m finance
}
finally {
    Pop-Location
}
