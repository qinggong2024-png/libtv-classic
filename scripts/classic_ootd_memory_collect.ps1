# Independent read-only classic OOTD shadow collection.
# Scheduled every 5 minutes; no production service restarts or model API calls.
[CmdletBinding()]
param()
Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'

$root = (Resolve-Path (Join-Path $PSScriptRoot '..')).Path
$py = Join-Path $root '.venv\Scripts\python.exe'
$collector = Join-Path $root 'scripts\classic_ootd_memory_collect.py'
$db = Join-Path $root 'var\classic-lan\classic.db'
$output = Join-Path $root 'var\classic-lan\runtime\director-memory-collection'

if (-not (Test-Path -LiteralPath $py -PathType Leaf)) { throw 'Classic Python venv not found' }
if (-not (Test-Path -LiteralPath $db -PathType Leaf)) { throw 'Classic DB not found' }
& $py $collector --database $db --output $output --limit 3000
if ($LASTEXITCODE -ne 0) { throw "Read-only shadow collection failed: $LASTEXITCODE" }
