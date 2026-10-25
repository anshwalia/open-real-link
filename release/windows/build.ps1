# Build OpenRealLink for Windows.
#
#   release\windows\build.ps1            # build the one-folder app
#   release\windows\build.ps1 -Zip       # ...and a versioned .zip beside it
#
# Uses the repository virtualenv (.venv) unless $env:PYTHON is set. Output lands
# in dist\windows\. See docs/packaging.md for signing.
[CmdletBinding()]
param(
    [switch]$Zip
)
$ErrorActionPreference = 'Stop'

$here = Split-Path -Parent $MyInvocation.MyCommand.Path
$root = (Resolve-Path (Join-Path $here '..\..')).Path
Set-Location $root

$python = if ($env:PYTHON) { $env:PYTHON } else { Join-Path $root '.venv\Scripts\python.exe' }
if (-not (Test-Path $python)) {
    $python = (Get-Command python -ErrorAction SilentlyContinue).Source
}
if (-not $python) { throw 'build: no Python interpreter found (set $env:PYTHON).' }
Write-Host "python: $python"

# If an icon source is present, (re)build icon.ico so the build picks it up.
# A missing or broken icon is not fatal: the build continues without one.
$iconSource = Join-Path $root 'release\icon.png'
if (Test-Path $iconSource) {
    & $python (Join-Path $here 'make_icon.py')
    if ($LASTEXITCODE -ne 0) { Write-Warning 'icon build failed; continuing without a custom icon' }
}

# PyInstaller, PySide6 and the Windows transport are all needed to freeze.
& $python -m pip install --quiet -e '.[gui,release]'
if ($LASTEXITCODE -ne 0) { throw 'build: pip install failed' }

Write-Host 'building the app...'
& $python -m PyInstaller `
    --noconfirm --clean `
    --distpath (Join-Path $root 'dist\windows') `
    --workpath (Join-Path $root 'build\windows') `
    (Join-Path $here 'open-real-link.spec')
if ($LASTEXITCODE -ne 0) { throw 'build: PyInstaller failed' }

$app = Join-Path $root 'dist\windows\OpenRealLink\OpenRealLink.exe'
Write-Host "built: $app"

if ($Zip) {
    $version = (Select-String -Path (Join-Path $root 'pyproject.toml') `
        -Pattern '^version\s*=\s*"([^"]+)"').Matches[0].Groups[1].Value
    $archive = Join-Path $root "dist\windows\OpenRealLink-$version-win64.zip"
    if (Test-Path $archive) { Remove-Item $archive }
    Write-Host 'zipping the app...'
    Compress-Archive -Path (Join-Path $root 'dist\windows\OpenRealLink\*') -DestinationPath $archive
    Write-Host "built: $archive"
}
