# Сторож OzonFFHelper: держит сервер запущенным постоянно.
# Стартует автоматически при входе в Windows (ярлык в папке "Автозагрузка").
# Каждую минуту проверяет сервер и поднимает его заново, если тот упал.

$ErrorActionPreference = 'SilentlyContinue'

$port = 8503
$root = 'D:\OzonFFHelper'
$py   = Join-Path $root '.venv\Scripts\python.exe'
$logs = Join-Path $root 'logs'

if (-not (Test-Path $logs)) { New-Item -ItemType Directory -Path $logs | Out-Null }

function Write-Log($msg) {
    "$(Get-Date -Format 'yyyy-MM-dd HH:mm:ss') $msg" |
        Out-File -Append -Encoding utf8 (Join-Path $logs 'serve_guard.log')
}

if (-not (Test-Path $py)) { Write-Log "НЕТ python: $py"; exit 1 }

Write-Log "сторож запущен"

while ($true) {
    $alive = $false
    try {
        $r = Invoke-WebRequest "http://localhost:$port/_stcore/health" -UseBasicParsing -TimeoutSec 5
        if ($r.StatusCode -eq 200) { $alive = $true }
    } catch { }

    if (-not $alive) {
        $streamlitArgs = @(
            '-m', 'streamlit', 'run', 'app.py',
            '--server.port', "$port",
            '--server.headless', 'true',
            '--browser.gatherUsageStats', 'false'
        )
        Start-Process -FilePath $py `
            -ArgumentList $streamlitArgs `
            -WorkingDirectory $root `
            -WindowStyle Hidden `
            -RedirectStandardOutput (Join-Path $logs 'serve_out.log') `
            -RedirectStandardError  (Join-Path $logs 'serve_err.log')
        Write-Log "сервер был недоступен - запущен заново"
        Start-Sleep -Seconds 20
    }

    Start-Sleep -Seconds 60
}
