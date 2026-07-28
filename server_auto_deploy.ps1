# Сервер: GitHub-тан auto pull + bot restart (код өзгерсе)
# Task Scheduler: install_deploy_task.bat

$Root = Split-Path -Parent $MyInvocation.MyCommand.Path
$Log = Join-Path $Root "data\deploy.log"
Set-Location $Root

function Log($msg) {
    $line = "$(Get-Date -Format 'yyyy-MM-dd HH:mm:ss') $msg"
    New-Item -ItemType Directory -Force -Path (Split-Path $Log) | Out-Null
    Add-Content -Path $Log -Value $line -Encoding UTF8
}

if (-not (Test-Path (Join-Path $Root ".git"))) {
    Log "QATe: .git joq"
    exit 1
}

$before = (git rev-parse HEAD 2>$null).Trim()
if (-not $before) {
    Log "QATe: git rev-parse"
    exit 1
}

git fetch origin main 2>&1 | Out-Null
git reset --hard origin/main 2>&1 | Out-Null
$after = (git rev-parse HEAD).Trim()

if ($before -eq $after) {
    exit 0
}

Log "Jańa kod: $before -> $after"

Get-CimInstance Win32_Process -Filter "Name='python.exe'" -ErrorAction SilentlyContinue |
    Where-Object { $_.CommandLine -like "*xau_scalp_main.py*" } |
    ForEach-Object {
        Log "Bot toqtatu pid $($_.ProcessId)"
        Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue
    }

Start-Sleep -Seconds 2

$startBat = Join-Path $Root "start.bat"
Start-Process -FilePath "cmd.exe" -ArgumentList "/c `"$startBat`"" -WorkingDirectory $Root -WindowStyle Minimized
Log "Bot qayta ishke qosyldy"
