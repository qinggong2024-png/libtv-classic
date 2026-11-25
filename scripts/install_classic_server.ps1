param([string]$Uv = "uv")
$ErrorActionPreference = "Stop"
$root = Split-Path -Parent $PSScriptRoot
Set-Location -LiteralPath $root
$runtime = Join-Path $root "var\classic-lan"
$dist = Join-Path $root "frontend\dist"
$stage = Join-Path $root "frontend\dist-build"
# First installation only: never run over an existing deployment.
foreach ($path in @($runtime, (Join-Path $root "var\classic"), $dist, $stage, (Join-Path $root ".venv"), (Join-Path $root "frontend\node_modules"), (Join-Path $root ".env"))) {
    if (Test-Path -LiteralPath $path) { throw "Existing installation or previous attempt: $path. Use a new clean checkout; do not delete live data." }
}
foreach ($tool in @($Uv, "node", "npm.cmd", "ffmpeg", "ffprobe")) {
    if (-not (Get-Command $tool -ErrorAction SilentlyContinue)) { throw "Required tool not found: $tool" }
}
if (@(Get-ChildItem -LiteralPath (Join-Path $root "frontend") -Filter ".env*" -Force).Count) {
    throw "Remove environment overrides from this fresh checkout before installation."
}
& $Uv sync --frozen --extra dev --python 3.11
if ($LASTEXITCODE -ne 0) { throw "Python dependency installation failed." }
& npm.cmd --prefix frontend ci
if ($LASTEXITCODE -ne 0) { throw "Frontend dependency installation failed." }
& npm.cmd --prefix frontend run build
if ($LASTEXITCODE -ne 0) { throw "Frontend build failed." }
if (-not (Test-Path -LiteralPath (Join-Path $stage "index.html") -PathType Leaf) -or
    -not (Test-Path -LiteralPath (Join-Path $stage "assets") -PathType Container) -or
    -not @(Get-ChildItem -LiteralPath (Join-Path $stage "assets") -File).Count) {
    throw "Incomplete frontend bundle."
}
if ((Test-Path -LiteralPath $runtime) -or (Test-Path -LiteralPath $dist)) {
    throw "Deployment appeared during installation. Refusing to publish."
}
# Atomic directory rename, only in a fresh checkout. Existing releases retain the
# release-manager lock and publication path.
[IO.Directory]::Move($stage, $dist)
Write-Host "Installation complete. Run start.cmd with this server's private IPv4 address."
