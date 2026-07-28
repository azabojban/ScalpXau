# ScalpXau watchdog — MT5 + bot 5 мин сайын тексеру
# Іске қосу: powershell -ExecutionPolicy Bypass -File C:\ScalpXau\watchdog.ps1
# Немесе Task Scheduler: 5 минут сайын

$Root = Split-Path -Parent $MyInvocation.MyCommand.Path
$Log = Join-Path $Root "data\watchdog.log"
$EnvFile = Join-Path $Root ".env"

function Log($msg) {
    $line = "$(Get-Date -Format 'yyyy-MM-dd HH:mm:ss') $msg"
    Add-Content -Path $Log -Value $line -Encoding UTF8
    Write-Host $line
}

function Read-EnvValue($name) {
    if (-not (Test-Path $EnvFile)) { return "" }
    foreach ($line in Get-Content $EnvFile -Encoding UTF8) {
        if ($line -match "^\s*$name=(.*)$") {
            return $Matches[1].Trim().Trim('"')
        }
    }
    return ""
}

$mt5Path = Read-EnvValue "MT5_TERMINAL_PATH"
if (-not $mt5Path) {
    $candidates = @(
        "${env:ProgramFiles}\MetaTrader 5\terminal64.exe",
        "${env:ProgramFiles(x86)}\MetaTrader 5\terminal64.exe"
    )
    foreach ($c in $candidates) {
        if (Test-Path $c) { $mt5Path = $c; break }
    }
}

$mt5Running = Get-Process -Name "terminal64" -ErrorAction SilentlyContinue
if (-not $mt5Running) {
    if ($mt5Path -and (Test-Path $mt5Path)) {
        Log "MT5 жабык — qayta ishke qosu: $mt5Path"
        Start-Process -FilePath $mt5Path
        Start-Sleep -Seconds 15
    } else {
        Log "MT5 жабык — MT5_TERMINAL_PATH .env-te qoyynyz"
    }
} else {
    Log "MT5 OK (pid $($mt5Running.Id))"
}

$botRunning = Get-CimInstance Win32_Process -Filter "Name='python.exe'" -ErrorAction SilentlyContinue |
    Where-Object { $_.CommandLine -like "*xau_scalp_main.py*" }

if (-not $botRunning) {
    Log "Bot жабык — start.bat ishke qosu"
    $startBat = Join-Path $Root "start.bat"
    Start-Process -FilePath "cmd.exe" -ArgumentList "/c `"$startBat`"" -WorkingDirectory $Root -WindowStyle Minimized
} else {
    Log "Bot OK (pid $($botRunning.ProcessId))"
}
