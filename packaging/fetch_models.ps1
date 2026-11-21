$ErrorActionPreference = 'Stop'
$models = Join-Path (Split-Path -Parent $PSScriptRoot) 'models'
$sources = Get-Content -LiteralPath (Join-Path $models 'sources.json') -Raw -Encoding UTF8 | ConvertFrom-Json

foreach ($source in $sources) {
    $uri = [uri]$source.url
    if ($source.file -notmatch '^[a-zA-Z0-9_]+\.(onnx|task|tflite)$' -or
        $source.sha256 -notmatch '^[a-f0-9]{64}$' -or
        $uri.Scheme -ne 'https' -or
        $uri.Host -cnotin @('storage.googleapis.com', 'media.githubusercontent.com')) {
        throw "Invalid source entry: $($source.file)"
    }

    $model = Join-Path $models $source.file
    if ((Test-Path -LiteralPath $model -PathType Leaf) -and
        (Get-FileHash -LiteralPath $model -Algorithm SHA256).Hash -eq $source.sha256) {
        Write-Output "Verified $($source.file)"
        continue
    }

    $download = (New-TemporaryFile).FullName
    try {
        Invoke-WebRequest -UseBasicParsing -Uri $uri -OutFile $download -TimeoutSec 90
        if ((Get-FileHash -LiteralPath $download -Algorithm SHA256).Hash -ne $source.sha256) {
            throw "SHA-256 mismatch for $($source.file)"
        }
        Move-Item -LiteralPath $download -Destination $model -Force
        Write-Output "Downloaded and verified $($source.file)"
    }
    finally {
        if (Test-Path -LiteralPath $download) {
            Remove-Item -LiteralPath $download -Force
        }
    }
}
