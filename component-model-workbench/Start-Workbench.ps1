param([int]$Port=8840)
$ErrorActionPreference='Stop'
$taskRoot=$PSScriptRoot
$taskPython=Join-Path $taskRoot '.venv\Scripts\python.exe'
if (-not (Test-Path -LiteralPath $taskPython)) {
    $taskPython=Join-Path (Split-Path $taskRoot -Parent) 'tmp\solar-stack-complete-20261011\.venv\Scripts\python.exe'
}
if (-not (Test-Path -LiteralPath $taskPython)) {
    Write-Host 'Create a Python environment first: python -m venv .venv'
    Write-Host 'Then install: .venv\Scripts\python.exe -m pip install -r requirements.txt'
    exit 1
}
$taskPrivate=Join-Path $taskRoot 'private'
New-Item -ItemType Directory -Path $taskPrivate -Force | Out-Null
try {
    $taskHealth=Invoke-RestMethod -Uri "http://127.0.0.1:$Port/health" -TimeoutSec 2
    if ($taskHealth.service -eq 'component-model-workbench') {
        Write-Host "Workbench already running: http://127.0.0.1:$Port"
        exit 0
    }
} catch {}
$taskProcess=Start-Process -FilePath $taskPython -ArgumentList @('-X','utf8','-m','uvicorn','server:app','--host','127.0.0.1','--port',"$Port") -WorkingDirectory $taskRoot -RedirectStandardOutput (Join-Path $taskPrivate 'server.out.log') -RedirectStandardError (Join-Path $taskPrivate 'server.err.log') -WindowStyle Hidden -PassThru
Set-Content -LiteralPath (Join-Path $taskPrivate 'server.pid') -Value $taskProcess.Id
Write-Host "Workbench starting: http://127.0.0.1:$Port"
Write-Host "Server process: $($taskProcess.Id)"
