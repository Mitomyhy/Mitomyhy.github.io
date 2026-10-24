param(
    [string]$Python = "",
    [string]$OutputDirectory = "",
    [switch]$SkipTests
)
$ErrorActionPreference = "Stop"
$sourceRoot = $PSScriptRoot
$workspaceRoot = Split-Path -Parent $sourceRoot
if (-not $Python) { $Python = Join-Path $workspaceRoot ".work\mifont-env\Scripts\python.exe" }
$Version = (& $Python -c "import sys; sys.path.insert(0, sys.argv[1]); from app_version import VERSION; print(VERSION)" $sourceRoot).Trim()
if ($LASTEXITCODE -ne 0 -or $Version -notmatch '^\d+\.\d+\.\d+$') { throw "Invalid shared MiFont release version." }
if (-not $OutputDirectory) { $OutputDirectory = Join-Path $workspaceRoot ("releases\mifont\" + $Version) }
$OutputDirectory = [IO.Path]::GetFullPath($OutputDirectory)
$buildRoot = Join-Path $workspaceRoot ("build\mifont\" + $Version)
New-Item -ItemType Directory -Path $buildRoot, $OutputDirectory -Force | Out-Null

if (-not $SkipTests) {
    & $Python -m unittest discover -s (Join-Path $sourceRoot "tests") -v
    if ($LASTEXITCODE -ne 0) { throw "MiFont source tests failed." }
    & $Python -m unittest discover -s (Join-Path $sourceRoot "packaging") -p "test_*.py" -v
    if ($LASTEXITCODE -ne 0) { throw "MiFont package safety tests failed." }
}
& $Python (Join-Path $sourceRoot "packaging\prepare_assets.py") --source $sourceRoot --output (Join-Path $buildRoot "assets")
if ($LASTEXITCODE -ne 0) { throw "MiFont asset/license preparation failed." }
$arguments = @(
    "-m", "PyInstaller", "--noconfirm", "--clean", "--onedir", "--windowed", "--noupx",
    "--name", "MiFont", "--contents-directory", "_internal",
    "--distpath", (Join-Path $buildRoot "dist"),
    "--workpath", (Join-Path $buildRoot "work"), "--specpath", $buildRoot,
    "--paths", $sourceRoot, "--paths", $workspaceRoot,
    "--icon", (Join-Path $buildRoot "assets\MiFont.ico"),
    "--add-data", ((Join-Path $sourceRoot "static") + ";static"),
    "--add-data", ((Join-Path $sourceRoot "assets") + ";assets"),
    "--add-data", ((Join-Path $sourceRoot "vendor") + ";vendor"),
    "--add-data", ((Join-Path $sourceRoot "helpers") + ";helpers"),
    "--add-data", ((Join-Path $buildRoot "assets\notices") + ";notices"),
    "--collect-all", "fontTools", "--collect-all", "PIL",
    (Join-Path $sourceRoot "main.py")
)
& $Python @arguments
if ($LASTEXITCODE -ne 0) { throw "MiFont PyInstaller build failed." }
$appDirectory = Join-Path $buildRoot "dist\MiFont"
$smokePath = Join-Path $buildRoot "runtime-smoke.json"
$process = Start-Process -FilePath (Join-Path $appDirectory "MiFont.exe") -ArgumentList @("--self-test", "--result-json", ('"' + $smokePath + '"')) -WindowStyle Hidden -PassThru
if (-not $process.WaitForExit(120000)) { $process.Kill(); throw "Packaged MiFont self-test timed out." }
if ($process.ExitCode -ne 0) { throw "Packaged MiFont self-test failed: $($process.ExitCode)." }
if (-not (Test-Path -LiteralPath $smokePath)) { throw "Packaged self-test produced no report." }
$smoke = Get-Content -LiteralPath $smokePath -Raw | ConvertFrom-Json
if (-not $smoke.ok) { throw "Packaged MiFont reported a failed self-test." }
& $Python (Join-Path $sourceRoot "packaging\runtime_smoke.py") --executable (Join-Path $appDirectory "MiFont.exe") --online-reference --result-json (Join-Path $buildRoot "http-worker-smoke.json")
if ($LASTEXITCODE -ne 0) { throw "Packaged MiFont HTTP/worker test failed." }
& $Python (Join-Path $sourceRoot "packaging\package.py") --app-dir $appDirectory --output $OutputDirectory --workspace $workspaceRoot --smoke-report $smokePath
if ($LASTEXITCODE -ne 0) { throw "MiFont archive validation failed." }
Write-Output ("Built MiFont " + $Version + ": " + (Join-Path $OutputDirectory ("MiFont-" + $Version + ".zip")))
