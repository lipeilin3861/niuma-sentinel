[CmdletBinding()]
param(
    [Parameter(Mandatory)][string]$MakeNsis,
    [Parameter(Mandatory)][string]$YuNetNotice,
    [Parameter(Mandatory)][string]$BuildDirectory
)

$ErrorActionPreference = 'Stop'
$project = Split-Path -Parent $PSScriptRoot
$python = Join-Path $project '.venv/Scripts/python.exe'
$compiler = (Resolve-Path -LiteralPath $MakeNsis).Path
$compilerNotice = Join-Path (Split-Path -Parent $compiler) 'COPYING'
$build = (New-Item -ItemType Directory -Force -Path $BuildDirectory).FullName
$licenses = Join-Path $build 'licenses'
$dist = Join-Path $build 'dist'
$work = Join-Path $build 'work'
$setup = Join-Path $build 'NiuMaSentinel-Setup.exe'

Push-Location -LiteralPath $project
$previousLicensePath = $env:NIUMA_RELEASE_LICENSES
try {
    $metadata = & $python -X utf8 -c 'import json, release_info as r; print(json.dumps([r.APP_NAME, r.APP_VERSION, r.COMPANY, r.WEBSITE_URL]))'
    if ($LASTEXITCODE -ne 0) { throw 'Could not load release information.' }
    $name, $version, $company, $website = $metadata | ConvertFrom-Json
    & $python (Join-Path $PSScriptRoot 'prepare_licenses.py') $licenses $compilerNotice $YuNetNotice
    if ($LASTEXITCODE -ne 0) { throw 'Could not collect release license notices.' }
    $env:NIUMA_RELEASE_LICENSES = $licenses
    & $python -m PyInstaller --noconfirm --distpath $dist --workpath $work (Join-Path $PSScriptRoot 'NiuMaSentinel.spec')
    if ($LASTEXITCODE -ne 0) { throw 'The Windows app could not be frozen.' }
    $payload = Join-Path $dist 'NiuMaSentinel'
    $icon = Join-Path $project 'assets/niuma.ico'
    & $compiler '/INPUTCHARSET' 'UTF8' "/DPRODUCT_NAME=$name" "/DAPP_VERSION=$version" "/DCOMPANY=$company" `
        "/DWEBSITE_URL=$website" "/DPRODUCT_ICON=$icon" "/DPAYLOAD_DIR=$payload" "/DSETUP_OUTPUT=$setup" `
        (Join-Path $PSScriptRoot 'niuma.nsi')
    if ($LASTEXITCODE -ne 0) { throw 'The Windows installer could not be compiled.' }
    Get-Item -LiteralPath $setup
}
finally {
    $env:NIUMA_RELEASE_LICENSES = $previousLicensePath
    Pop-Location
}
