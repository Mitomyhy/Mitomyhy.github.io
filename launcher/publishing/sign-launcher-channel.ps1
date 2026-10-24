param(
    [switch]$Initialize,
    [string]$PayloadFile = "",
    [string]$OutputFile = "",
    [string]$PublicKeyFile = (Join-Path (Split-Path -Parent $PSScriptRoot) "launcher-update-public-key.json")
)
$ErrorActionPreference = "Stop"
$keyDirectory = Join-Path ([Environment]::GetFolderPath("LocalApplicationData")) "MITOM\Publishing"
$keyFile = Join-Path $keyDirectory "launcher-update-20261024.dpapi"
$keyId = "mitom-launcher-20261024"
$rsa = [Security.Cryptography.RSA]::Create(4096)
$privateBytes = $null
try {
    if (Test-Path -LiteralPath $keyFile) {
        $privateBytes = [Security.Cryptography.ProtectedData]::Unprotect(
            [IO.File]::ReadAllBytes($keyFile), $null,
            [Security.Cryptography.DataProtectionScope]::CurrentUser)
        $consumed = 0
        $rsa.ImportRSAPrivateKey($privateBytes, [ref]$consumed)
        if ($consumed -ne $privateBytes.Length) { throw "Invalid protected publisher key." }
    } elseif ($Initialize) {
        New-Item -ItemType Directory -Path $keyDirectory -Force | Out-Null
        $privateBytes = $rsa.ExportRSAPrivateKey()
        $protected = [Security.Cryptography.ProtectedData]::Protect(
            $privateBytes, $null, [Security.Cryptography.DataProtectionScope]::CurrentUser)
        $stream = [IO.File]::Open($keyFile, [IO.FileMode]::CreateNew, [IO.FileAccess]::Write)
        try { $stream.Write($protected, 0, $protected.Length) } finally { $stream.Dispose() }
    } else {
        throw "Publisher key is unavailable on this Windows account; refusing a silent key rotation."
    }
    $parameters = $rsa.ExportParameters($false)
    $modulus = [Convert]::ToHexString($parameters.Modulus).ToLowerInvariant()
    $public = [ordered]@{ key_id = $keyId; n = $modulus; e = 65537 }
    if (Test-Path -LiteralPath $PublicKeyFile) {
        $existing = Get-Content -LiteralPath $PublicKeyFile -Raw | ConvertFrom-Json
        if ($existing.key_id -ne $keyId -or $existing.n -ne $modulus -or $existing.e -ne 65537) {
            throw "Protected publisher key does not match the existing public key."
        }
    } else {
        if (-not $Initialize) { throw "Initialize the public verification key explicitly." }
        [IO.File]::WriteAllText($PublicKeyFile, (($public | ConvertTo-Json) + "`n"), [Text.UTF8Encoding]::new($false))
    }
    if ($PayloadFile -or $OutputFile) {
        if (-not $PayloadFile -or -not $OutputFile) { throw "Provide both payload and output paths." }
        $payload = [IO.File]::ReadAllBytes([IO.Path]::GetFullPath($PayloadFile))
        $signature = $rsa.SignData($payload, [Security.Cryptography.HashAlgorithmName]::SHA256,
                                  [Security.Cryptography.RSASignaturePadding]::Pkcs1)
        $envelope = [ordered]@{ schema = 1; key_id = $keyId;
            payload = [Convert]::ToBase64String($payload); signature = [Convert]::ToBase64String($signature) }
        [IO.File]::WriteAllText([IO.Path]::GetFullPath($OutputFile),
            (($envelope | ConvertTo-Json) + "`n"), [Text.UTF8Encoding]::new($false))
    }
    [ordered]@{ ok = $true; key_id = $keyId; rsa_bits = $rsa.KeySize;
        protected_key = $keyFile; public_key = $PublicKeyFile; signed_output = $OutputFile } | ConvertTo-Json -Compress
} finally {
    if ($null -ne $privateBytes) { [Array]::Clear($privateBytes, 0, $privateBytes.Length) }
    $rsa.Dispose()
}
