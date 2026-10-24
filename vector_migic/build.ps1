param(
    [string]$Python = "",
    [string]$OutputDirectory = "",
    [switch]$SkipTests
)
$ErrorActionPreference = "Stop"
$sourceRoot = $PSScriptRoot
$workspaceRoot = Split-Path -Parent $sourceRoot
if (-not $Python) { $Python = Join-Path $workspaceRoot ".work\vector-migic-env\Scripts\python.exe" }
if (-not $OutputDirectory) { $OutputDirectory = Join-Path $workspaceRoot "releases\vector-migic\1.0.0" }
$OutputDirectory = [IO.Path]::GetFullPath($OutputDirectory)
$buildRoot = Join-Path $workspaceRoot "build\vector-migic\1.0.0"
New-Item -ItemType Directory -Path $buildRoot, $OutputDirectory -Force | Out-Null

if (-not $SkipTests) {
    & $Python -m unittest discover -s (Join-Path $sourceRoot "tests") -v
    if ($LASTEXITCODE -ne 0) { throw "Vector Migic source tests failed." }
    & $Python -m unittest discover -s (Join-Path $sourceRoot "packaging") -p "test_*.py" -v
    if ($LASTEXITCODE -ne 0) { throw "Vector Migic package safety tests failed." }
}
& $Python (Join-Path $sourceRoot "packaging\prepare_assets.py") --source $sourceRoot --output (Join-Path $buildRoot "assets")
if ($LASTEXITCODE -ne 0) { throw "Vector Migic asset/license preparation failed." }
$arguments = @(
    "-m", "PyInstaller", "--noconfirm", "--clean", "--onedir", "--windowed", "--noupx",
    "--name", "VectorMigic", "--contents-directory", "_internal",
    "--distpath", (Join-Path $buildRoot "dist"),
    "--workpath", (Join-Path $buildRoot "work"), "--specpath", $buildRoot,
    "--paths", $sourceRoot, "--paths", $workspaceRoot,
    "--icon", (Join-Path $buildRoot "assets\VectorMigic.ico"),
    "--add-data", ((Join-Path $sourceRoot "static") + ";static"),
    "--add-data", ((Join-Path $sourceRoot "assets") + ";assets"),
    "--add-data", ((Join-Path $buildRoot "assets\notices") + ";notices"),
    "--collect-all", "vtracer", "--collect-all", "reportlab", "--collect-all", "svglib",
    "--collect-all", "PIL", "--collect-all", "lxml", "--collect-all", "cssselect2",
    "--collect-all", "tinycss2", "--collect-all", "charset_normalizer",
    (Join-Path $sourceRoot "main.py")
)
& $Python @arguments
if ($LASTEXITCODE -ne 0) { throw "Vector Migic PyInstaller build failed." }
$appDirectory = Join-Path $buildRoot "dist\VectorMigic"
$smokePath = Join-Path $buildRoot "runtime-smoke.json"
$process = Start-Process -FilePath (Join-Path $appDirectory "VectorMigic.exe") -ArgumentList @("--self-test", "--result-json", ('"' + $smokePath + '"')) -WindowStyle Hidden -PassThru
if (-not $process.WaitForExit(120000)) { $process.Kill(); throw "Packaged Vector Migic self-test timed out." }
if ($process.ExitCode -ne 0) { throw "Packaged Vector Migic self-test failed: $($process.ExitCode)." }
if (-not (Test-Path -LiteralPath $smokePath)) { throw "Packaged self-test produced no report." }
$smoke = Get-Content -LiteralPath $smokePath -Raw | ConvertFrom-Json
if (-not $smoke.ok) { throw "Packaged Vector Migic reported a failed self-test." }
& $Python (Join-Path $sourceRoot "packaging\runtime_smoke.py") --executable (Join-Path $appDirectory "VectorMigic.exe") --result-json (Join-Path $buildRoot "http-worker-smoke.json")
if ($LASTEXITCODE -ne 0) { throw "Packaged Vector Migic HTTP/worker test failed." }
& $Python (Join-Path $sourceRoot "packaging\package.py") --app-dir $appDirectory --output $OutputDirectory --workspace $workspaceRoot --smoke-report $smokePath
if ($LASTEXITCODE -ne 0) { throw "Vector Migic archive validation failed." }
Write-Output ("Built Vector Migic 1.0.0: " + (Join-Path $OutputDirectory "VectorMigic-1.0.0.zip"))
