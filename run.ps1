$ErrorActionPreference = "Stop"
$root = Split-Path -Parent $MyInvocation.MyCommand.Path
Set-Location $root
$venvPython = Join-Path $root ".venv\Scripts\python.exe"

function Get-Python311 {
    $commands = @(
        @{ Cmd = "py"; Args = @("-3.11") },
        @{ Cmd = "python3.11"; Args = @() },
        @{ Cmd = "python"; Args = @() },
        @{ Cmd = "py"; Args = @("-3") }
    )
    foreach ($item in $commands) {
        $resolved = Get-Command $item.Cmd -ErrorAction SilentlyContinue
        if (-not $resolved) { continue }
        try {
            $exe = & $item.Cmd @($item.Args) -c "import sys; print(sys.executable)" 2>$null
            if (-not $exe) { continue }
            $ver = & $item.Cmd @($item.Args) -c "import sys; print('%d.%d' % (sys.version_info.major, sys.version_info.minor))" 2>$null
            if ($ver -and $ver.Trim() -eq "3.11") {
                return $exe.Trim()
            }
        } catch {
            continue
        }
    }
    throw "Python 3.11을 찾지 못했습니다. Python 3.11을 설치하고 PATH에 추가한 뒤 다시 실행하세요."
}

if (-not (Test-Path $venvPython)) {
    $py = Get-Python311
    & $py -m venv (Join-Path $root ".venv")
    if (-not (Test-Path $venvPython)) {
        throw ".venv 생성에 실패했습니다."
    }
}

& $venvPython -m uvicorn app.main:app --reload --app-dir "$root\backend" --host 127.0.0.1 --port 8000
