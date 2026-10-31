param(
    [string]$Python = "",
    [string]$OutputDirectory = "",
    [string]$Version = "2026.10.31.1",
    [ValidateSet("Online", "Bundled")]
    [string]$VectorPayload = "Online",
    [string]$CertificateThumbprint = $env:MITOM_SIGN_CERT_SHA1,
    [string]$TimestampUrl = $env:MITOM_SIGN_TIMESTAMP_URL,
    [switch]$RequireSigned,
    [switch]$BuildOnly
)
$ErrorActionPreference = "Stop"
$sourceRoot = $PSScriptRoot
$workspaceRoot = Split-Path -Parent $sourceRoot
if (-not $Python) { $Python = Join-Path $workspaceRoot "prompt-studio\.venv\Scripts\python.exe" }
if ($Version -notmatch '^\d{4}\.\d{2}\.\d{2}\.\d+$') { throw "Invalid launcher release version." }
if (-not $OutputDirectory) { $OutputDirectory = Join-Path $workspaceRoot ("releases\mitom-launcher\" + $Version) }
$OutputDirectory = [IO.Path]::GetFullPath($OutputDirectory)
$buildRoot = Join-Path $workspaceRoot "build\mitom-launcher"
New-Item -ItemType Directory -Path $OutputDirectory, $buildRoot -Force | Out-Null
& $Python -m unittest discover -s (Join-Path $sourceRoot "tests") -v
if ($LASTEXITCODE -ne 0) { throw "Launcher regression tests failed." }
$arguments = @(
    "-m", "PyInstaller", "--noconfirm", "--clean", "--onefile", "--windowed",
    "--name", ("MITOM-Launcher-Setup-" + $Version), "--noupx",
    "--distpath", $OutputDirectory, "--workpath", (Join-Path $buildRoot "work"),
    "--specpath", $buildRoot, "--paths", $sourceRoot,
    "--icon", (Join-Path $sourceRoot "assets\favicon.ico"),
    "--add-data", ((Join-Path $sourceRoot "assets") + ";assets"),
    "--add-data", ((Join-Path $sourceRoot "legacy") + ";legacy"),
    "--add-data", ((Join-Path $sourceRoot "licenses") + ";licenses"),
    "--add-data", ((Join-Path $sourceRoot "vector-release.json") + ";."),
    "--add-data", ((Join-Path $sourceRoot "mifont-release.json") + ";."),
    "--add-data", ((Join-Path $sourceRoot "recovery-manifest.json") + ";."),
    "--add-data", ((Join-Path $sourceRoot "launcher-release.json") + ";."),
    "--add-data", ((Join-Path $sourceRoot "release-public-key.json") + ";."),
    "--add-data", ((Join-Path $sourceRoot "launcher-update-public-key.json") + ";.")
)
if (Test-Path -LiteralPath (Join-Path $sourceRoot "product-video-release.json")) {
    $arguments += @("--add-data", ((Join-Path $sourceRoot "product-video-release.json") + ";."))
}
if ($VectorPayload -eq "Bundled") {
    $arguments += @("--add-data", ((Join-Path $sourceRoot "apps") + ";apps"))
}
$arguments += (Join-Path $sourceRoot "main.py")
& $Python @arguments
if ($LASTEXITCODE -ne 0) { throw "PyInstaller build failed." }
$executable = Join-Path $OutputDirectory ("MITOM-Launcher-Setup-" + $Version + ".exe")
if ($CertificateThumbprint) {
    $signTool = Get-Command signtool.exe -ErrorAction SilentlyContinue
    if (-not $signTool) { throw "Signing requested, but signtool.exe is unavailable." }
    if (-not $TimestampUrl) { throw "Set MITOM_SIGN_TIMESTAMP_URL to your signing provider's timestamp URL." }
    & $signTool.Source sign /sha1 $CertificateThumbprint /fd SHA256 /tr $TimestampUrl /td SHA256 $executable
    if ($LASTEXITCODE -ne 0) { throw "Authenticode signing failed." }
    & $signTool.Source verify /pa $executable
    if ($LASTEXITCODE -ne 0) { throw "Authenticode verification failed." }
} elseif ($RequireSigned) {
    throw "A trusted code-signing certificate is required for this release. No signed artifact was produced."
}
if ($BuildOnly) {
    # Permit security review of fresh bytes before any packaged code executes.
    # This does not suppress or alter Windows security scanning.
    $signature = Get-AuthenticodeSignature -LiteralPath $executable
    $digest = (Get-FileHash -LiteralPath $executable -Algorithm SHA256).Hash.ToLowerInvariant()
    Set-Content -LiteralPath (Join-Path $OutputDirectory "SHA256SUMS.txt") -Value ($digest + "  " + [IO.Path]::GetFileName($executable)) -Encoding ascii
    Write-Output ("Built only; not executed: " + $executable)
    Write-Output ("Authenticode: " + $signature.Status)
    return
}
$smokePath = Join-Path $OutputDirectory "runtime-smoke.json"
$smoke = Start-Process -FilePath $executable -ArgumentList @("--self-test", "--result-json", ('"' + $smokePath + '"')) -WindowStyle Hidden -PassThru -Wait
if ($smoke.ExitCode -ne 0) { throw "Packaged launcher smoke test failed with exit code $($smoke.ExitCode)." }
$result = Get-Content -LiteralPath $smokePath -Raw | ConvertFrom-Json
if (-not $result.ok) { throw "Packaged launcher reported a failed self-test." }
$uiSmokePath = Join-Path $OutputDirectory "ui-smoke.json"
$uiSmoke = Start-Process -FilePath $executable -ArgumentList @("--ui-smoke", "--result-json", ('"' + $uiSmokePath + '"')) -WindowStyle Hidden -PassThru -Wait
if ($uiSmoke.ExitCode -ne 0) { throw "Packaged launcher UI smoke test failed with exit code $($uiSmoke.ExitCode)." }
$uiResult = Get-Content -LiteralPath $uiSmokePath -Raw | ConvertFrom-Json
if (-not $uiResult.ok) { throw "Packaged launcher reported a failed UI self-test." }
$networkSmokePath = Join-Path $OutputDirectory "network-smoke.json"
$networkSmoke = Start-Process -FilePath $executable -ArgumentList @("--network-smoke", "--result-json", ('"' + $networkSmokePath + '"')) -WindowStyle Hidden -PassThru -Wait
if ($networkSmoke.ExitCode -ne 0) { throw "Packaged launcher TLS/signed-feed smoke test failed. See $networkSmokePath" }
$networkResult = Get-Content -LiteralPath $networkSmokePath -Raw | ConvertFrom-Json
if (-not $networkResult.ok) { throw "Packaged launcher reported a failed TLS/signed-feed smoke test." }
$signature = Get-AuthenticodeSignature -LiteralPath $executable
$digest = (Get-FileHash -LiteralPath $executable -Algorithm SHA256).Hash.ToLowerInvariant()
Set-Content -LiteralPath (Join-Path $OutputDirectory "SHA256SUMS.txt") -Value ($digest + "  " + [IO.Path]::GetFileName($executable)) -Encoding ascii
Write-Output ("Built: " + $executable)
Write-Output ("Vector payload: " + $VectorPayload)
Write-Output ("Authenticode: " + $signature.Status)
