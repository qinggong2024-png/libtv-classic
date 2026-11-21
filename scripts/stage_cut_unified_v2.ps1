param(
    [string]$Storage = "",
    [switch]$Send,
    [switch]$Replace
)

$ErrorActionPreference = "Stop"
$repoRoot = Split-Path -Parent $PSScriptRoot
if (-not $Storage) {
    $Storage = Join-Path $repoRoot "var\classic-lan"
}
$storagePath = [IO.Path]::GetFullPath($Storage)
$configPath = Join-Path $storagePath "runtime\cutflow-config.json"
if (-not (Test-Path -LiteralPath $configPath -PathType Leaf)) {
    throw "CUT flow configuration is missing: $configPath"
}
$utf8 = [Text.UTF8Encoding]::new($false)
$config = [IO.File]::ReadAllText($configPath, $utf8) | ConvertFrom-Json
$root = [IO.Path]::GetFullPath([string]$config.handoffRoot)
$coord = [IO.Path]::GetFullPath((Join-Path $root "COORD"))
if (-not $coord.StartsWith($root, [StringComparison]::OrdinalIgnoreCase)) {
    throw "COORD path escapes the configured handoff root."
}
if (-not (Test-Path -LiteralPath $root -PathType Container)) {
    throw "Configured handoff root is unavailable: $root"
}

$contractSource = Join-Path $repoRoot "docs\CLASSIC_CUTFLOW_UNIFIED_V2_20261029.md"
$taskSource = Join-Path $repoRoot "docs\CLASSIC_CUTFLOW_UNIFIED_V2_CUT_TASK_20261029.md"
foreach ($source in @($contractSource, $taskSource)) {
    if (-not (Test-Path -LiteralPath $source -PathType Leaf)) {
        throw "Required contract document is missing: $source"
    }
}

$contractTarget = Join-Path $coord "CLASSIC_CUTFLOW_UNIFIED_V2_20261029.md"
$taskTarget = Join-Path $coord "CLASSIC_CUTFLOW_UNIFIED_V2_CUT_TASK_20261029.md"
$instructionTarget = Join-Path $coord "instr\0035-unified-v2.json"
$issuedAt = (Get-Date).ToString("o")
$instruction = [ordered]@{
    instructionId = "0035"
    issuedAt = $issuedAt
    issuedBy = "classic"
    kind = "cutflow-business"
    title = "Adopt unified CUT task contract v2"
    state = "open"
    authorization = [ordered]@{
        granted = $true
        grantedBy = "Reel"
        scope = @(
            "Modify CUT source code to support libtv-cut-lan-multi/2",
            "Run isolated tests, lint and build",
            "Do not modify image/video generation prompts or yiying-ootd/2 content",
            "Deployment, real editing, paid execution and replacement batches are not authorized"
        )
    }
    context = [ordered]@{
        protocol = "CLASSIC_CUTFLOW_UNIFIED_V2_20261029.md"
        executionTask = "CLASSIC_CUTFLOW_UNIFIED_V2_CUT_TASK_20261029.md"
        contractVersion = "libtv-cut-lan-multi/2"
        statusSchema = "yiying-cut-status/2"
        statusAuthority = "nas-status"
        receiptSchema = "yiying-classic-import/1"
    }
    actions = @(
        "Read both contract documents under COORD and inspect the CUT repository state.",
        "Preserve existing uncommitted changes and do not modify generation prompts or policy content.",
        "Implement unified sources[] intake, authoritative NAS status, revision fencing and optional POST wake-up.",
        "Run single-source, multi-source, concurrency, restart, legacy-contract, lint and build tests.",
        "Do not create real or paid editing jobs.",
        "Write source and test results to COORD/ack/0035-unified-v2.json."
    )
    acceptance = @(
        "Single-source and multi-source jobs use one intake and one status file",
        "POST responses are not treated as accepted state",
        "Lower revisions and same-revision rewrites cannot replace newer state",
        "READY scanner still accepts jobs when HTTP is unavailable",
        "Legacy /1, Bridge and historical jobs still pass"
    )
    reply = "COORD/ack/0035-unified-v2.json"
}
$instructionJson = $instruction | ConvertTo-Json -Depth 10

if (-not $Send) {
    [ordered]@{
        preview = $true
        root = $root
        contract = $contractTarget
        task = $taskTarget
        instruction = $instructionTarget
        instructionPreview = $instruction
    } | ConvertTo-Json -Depth 10
    return
}

New-Item -ItemType Directory -Force -Path (Join-Path $coord "instr") | Out-Null
$writeInstruction = $true
if (Test-Path -LiteralPath $instructionTarget) {
    try {
        $existing = [IO.File]::ReadAllText($instructionTarget, $utf8) | ConvertFrom-Json
    }
    catch {
        $existing = $null
    }
    $sameIdentity = (
        $null -ne $existing -and
        $existing.instructionId -eq "0035" -and
        $existing.context.contractVersion -eq "libtv-cut-lan-multi/2" -and
        $existing.reply -eq "COORD/ack/0035-unified-v2.json"
    )
    if ($sameIdentity -and -not $Replace) {
        $writeInstruction = $false
    }
    elseif (-not $Replace) {
        throw "Instruction already exists with different content. Use -Replace only after review."
    }
}
Copy-Item -LiteralPath $contractSource -Destination $contractTarget -Force
Copy-Item -LiteralPath $taskSource -Destination $taskTarget -Force
if ($writeInstruction) {
    $temporary = "$instructionTarget.$([Guid]::NewGuid().ToString('N')).tmp"
    try {
        [IO.File]::WriteAllText($temporary, $instructionJson, $utf8)
        Move-Item -LiteralPath $temporary -Destination $instructionTarget -Force
    }
    finally {
        Remove-Item -LiteralPath $temporary -Force -ErrorAction SilentlyContinue
    }
}
else {
    Write-Output $instructionTarget
    return
}
$pointerTarget = Join-Path $coord "INSTRUCTION"
$pointerText = @(
    "0035"
    "updatedAt: $issuedAt"
    "file: instr/0035-unified-v2.json"
    "note: Adopt libtv-cut-lan-multi/2 with NAS-authoritative status; implementation and tests only, no paid execution."
) -join [Environment]::NewLine
$pointerTemporary = "$pointerTarget.$([Guid]::NewGuid().ToString('N')).tmp"
try {
    [IO.File]::WriteAllText(
        $pointerTemporary,
        $pointerText + [Environment]::NewLine,
        $utf8
    )
    Move-Item -LiteralPath $pointerTemporary -Destination $pointerTarget -Force
}
finally {
    Remove-Item -LiteralPath $pointerTemporary -Force -ErrorAction SilentlyContinue
}
Write-Output $instructionTarget
