# Controlled, one-shot first installation of the ALLWE classic 5176 route/gate.
# Explicit -Apply, temporary inbound firewall closure, live quiescence check,
# verified backup, original task ID preservation, and best-effort rollback.
[CmdletBinding()]
param([switch]$Apply, [string]$ExpectedCandidate = '')
Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'

$live = 'E:\Projects\libtv-classic-0e3d1c8-main'
$candidate = 'E:\Projects\allwe-classic-v2-release-rehearsal-20261121'
$py = Join-Path $live '.venv\Scripts\python.exe'
$npm = 'D:\New Folder\npm.cmd'
$freezeScript = Join-Path $candidate 'scripts\classic_first_install_ingress.ps1'
$admission = Join-Path $live 'scripts\classic_new_video_admission.py'
$runtime = Join-Path $live 'var\classic-lan\runtime'
$db = Join-Path $live 'var\classic-lan\classic.db'
$backupRoot = Join-Path $runtime 'release-backups'
$originalHead = ''
$changedGit = $false
$stackStopped = $false
$firewallHeld = $false
$applicationHeld = $false

function RunExternal([string]$file, [string[]]$argv) {
  & $file @argv
  if ($LASTEXITCODE -ne 0) { throw "$file failed (exit code $LASTEXITCODE)" }
}
function ConfirmStableAPI {
  $state = Get-Content (Join-Path $runtime 'stack.json') -Raw | ConvertFrom-Json
  $record = @($state.processes | Where-Object { $_.name -eq 'api' })
  if ($record.Count -ne 1) { throw 'Production API identity is ambiguous' }
  $process = Get-Process -Id $record[0].pid -ErrorAction SilentlyContinue
  if (-not $process) { throw 'Production API process not running' }
  $startedEpoch = [DateTimeOffset]::new($process.StartTime).ToUnixTimeSeconds()
  if ([Math]::Abs($startedEpoch - [double]$record[0].created) -gt 5) {
    throw 'Production API PID reuse or stale supervisor record detected'
  }
  $age = ((Get-Date) - $process.StartTime).TotalSeconds
  if ($age -lt 600) {
    throw "Production API uptime below safe 10-minute threshold: $([math]::Round($age)) seconds"
  }
  return $record[0].pid
}
function ConfirmCurrentTasks {
  $raw = & $py (Join-Path $candidate 'scripts\classic_v2_canary_preflight.py') --database $db --baseline-unknown 15
  if ($LASTEXITCODE -ne 0) { throw 'Live task preflight failed or provider/CUT work in flight' }
  $check = ($raw -join "`n") | ConvertFrom-Json
  if (-not $check.quiescentForVersionSwitch) { throw 'New task attempts detected during freeze' }
  if ($check.counts.uncertainPaidHistorical -ne 15) { throw 'Historical paid UNKNOWN baseline changed' }
  return $check
}
function ConfirmLiveSite {
  $response = Invoke-RestMethod -Uri 'http://127.0.0.1:5176/api/v1/health/ready' -TimeoutSec 8
  if ($response.status -ne 'ok') { throw 'Classic web/API not ready' }
}
function ConfirmNewContract([bool]$expectedPaused) {
  ConfirmLiveSite
  $web = Invoke-RestMethod -Uri 'http://127.0.0.1:5176/__classic_api_route/status' -TimeoutSec 8
  $gate = Invoke-RestMethod -Uri 'http://127.0.0.1:8002/api/v1/system/classic-new-video-admission' -TimeoutSec 8
  if ($web.hotRouteVersion -ne 1 -or $web.configured -ne $true -or $web.activeApiPort -ne 8002) {
    throw 'Web server has not installed the actual JSON hot-route contract'
  }
  if ($gate.gateVersion -ne 1 -or $gate.configured -ne $true -or
      $gate.deploymentId -ne 'libtv-classic-lan-0e3d1c8' -or
      $gate.newVideoAdmissionsPaused -ne $expectedPaused) {
    throw 'API has not confirmed the expected new-video admission state'
  }
  return @{ route = $web; gate = $gate }
}
function ConfirmProcesses {
  $state = Get-Content (Join-Path $runtime 'stack.json') -Raw | ConvertFrom-Json
  $names = @($state.processes | ForEach-Object { $_.name })
  if (@($names|Sort-Object -Unique).Count -ne 4) { throw 'Expected exactly one API/Web/Runner/CUT record each' }
  foreach($required in @('api','web','runner','cutflow')) {
    $proc = @($state.processes | Where-Object { $_.name -eq $required })
    if ($proc.Count -ne 1 -or -not (Get-Process -Id $proc[0].pid -ErrorAction SilentlyContinue)) {
      throw "Classic $required process missing or not unique"
    }
  }
}
function ConfirmBuild {
  $front = Invoke-WebRequest -Uri 'http://127.0.0.1:5176/' -UseBasicParsing -TimeoutSec 8
  if ($front.StatusCode -ne 200 -or $front.Content -notmatch '/assets/([a-zA-Z0-9_.-]+\.js)') {
    throw 'Frontend entry is unavailable or has no hashed JS asset'
  }
  $asset = $Matches[1]
  $js = Invoke-WebRequest -Uri "http://127.0.0.1:5176/assets/$asset" -UseBasicParsing -TimeoutSec 8
  if ($js.StatusCode -ne 200) { throw 'New frontend hashed JavaScript asset unavailable' }
}
function RestoreOriginalFrontend([string]$backupDirectory) {
  $archive = Join-Path $backupDirectory 'frontend-dist-before-v2.zip'
  if (-not (Test-Path $archive)) { throw 'Frontend rollback archive unavailable' }
  Expand-Archive -LiteralPath $archive -DestinationPath (Join-Path $live 'frontend\dist') -Force
}
$gitBefore = (& git -C $live rev-parse HEAD).Trim()
$gitCandidate = (& git -C $candidate rev-parse HEAD).Trim()
if ($Apply -and -not $ExpectedCandidate) { throw 'Apply requires an explicitly pinned candidate commit' }
if ($ExpectedCandidate -and $gitCandidate -ne $ExpectedCandidate) { throw 'Pinned candidate commit changed; refusing deployment' }
if ((& git -C $live status --porcelain) -or (& git -C $candidate status --porcelain)) { throw 'Source worktrees must be clean' }
& git -C $candidate merge-base --is-ancestor $gitBefore $gitCandidate
if ($LASTEXITCODE -ne 0) { throw 'Current main must be ancestor of approved release candidate' }
if (-not (Test-Path (Join-Path $candidate 'frontend\dist\index.html'))) { throw 'Candidate UI build missing' }
if (-not (Test-Path $npm)) { throw 'npm executable unavailable' }
$preflight = & $py (Join-Path $candidate 'scripts\classic_first_install_gate.py') --live-root $live --candidate-root $candidate
if ($LASTEXITCODE -ne 0) { throw 'First-install read-only preflight is blocked' }
$plan = ($preflight -join "`n") | ConvertFrom-Json
if ($plan.readyForControlledMaintenance -ne $true) { throw 'First-install readiness not confirmed' }
$stableApiPid = ConfirmStableAPI
$backup = @(
  Get-ChildItem $backupRoot -Directory | Where-Object {
    (Test-Path (Join-Path $_.FullName 'frontend-dist-before-v2.zip')) -and
    (Test-Path (Join-Path $_.FullName 'manifest.json'))
  } | Sort-Object LastWriteTime -Descending
)[0].FullName
Write-Output ("FIRST_INSTALL_PLAN: "+(@{
  liveRevision=$gitBefore; candidateRevision=$gitCandidate; backup=$backup
  activePaid=0; cutActive=0; expectedNetworkFreeze='TCP/5176 only'
} | ConvertTo-Json -Compress))
if (-not $Apply) {
  Write-Output 'DRY_RUN_ONLY: No user traffic, process, database or source changed.'
  exit 0
}
$originalHead = $gitBefore
try {
  # Old API ignores the new pause flag. Windows firewall closes external ingress
  # before the old supervisor or any app files can change.
  & $freezeScript -Action pause
  $status = (& $freezeScript -Action status) | ConvertFrom-Json
  if ($status.frozen -ne $true) { throw 'External TCP/5176 freeze did not take effect' }
  $firewallHeld = $true
  Start-Sleep -Seconds 3
  $null = ConfirmCurrentTasks
  ConfirmLiveSite
  if ((ConfirmStableAPI) -ne $stableApiPid) { throw 'Production API changed during ingress freeze' }
  if ((& git -C $live rev-parse HEAD).Trim() -ne $originalHead -or
      (& git -C $live status --porcelain) -or
      (& git -C $candidate status --porcelain)) {
    throw 'Source changed during ingress freeze; cancelling deployment'
  }

  $stackStopped = $true
  RunExternal $py @((Join-Path $live 'scripts\classic_autostart.py'), 'stop')
  # Only the approved fast-forward release is allowed; never overwrite somebody
  # else's uncommitted work. The old revision is retained for rollback.
  RunExternal 'git' @('-C', $live, 'merge', '--ff-only', $gitCandidate)
  $changedGit = $true
  Push-Location (Join-Path $live 'frontend')
  try { RunExternal $npm @('run','build') } finally { Pop-Location }

  # Running new API requires the pause file to be visible before opening 5176.
  $flag = Join-Path $runtime 'pause-new-video-tasks.flag'
  if (Test-Path $flag) { throw 'Unexpected pre-existing intake flag' }
  [IO.File]::WriteAllText($flag, "allwe-classic-new-video-admissions-v1`n", [Text.UTF8Encoding]::new($false))
  $applicationHeld = $true
  RunExternal $py @((Join-Path $live 'scripts\classic_autostart.py'), 'start')
  $null = ConfirmNewContract $true
  ConfirmProcesses
  ConfirmBuild
  $null = ConfirmCurrentTasks

  # Validate the application-level admission freeze before re-opening the
  # outside world; then resume creation only after network access is restored.
  & $freezeScript -Action resume
  $status = (& $freezeScript -Action status) | ConvertFrom-Json
  if ($status.installed) { throw 'Maintenance firewall rule was not cleared' }
  $firewallHeld = $false
  RunExternal $py @($admission, 'resume')
  $applicationHeld = $false
  $null = ConfirmNewContract $false
  ConfirmProcesses
  ConfirmBuild
  Write-Output ('DEPLOYED: '+(@{ liveRevision=((& git -C $live rev-parse --short HEAD).Trim()); hotRouteVersion=1; activeApiPort=8002; newTaskAdmission='open'; databaseRestored=$false; providerPaidTest='not_run'; externalFirewall='removed' } | ConvertTo-Json -Compress))
 } catch {
  Write-Output ("DEPLOYMENT_FAILED: "+$_.Exception.Message)
  # A firewall creation may have succeeded just before verification failed;
  # detect and remove only the still-valid rule during safe recovery.
  if (-not $firewallHeld) {
    try {
      $probe = (& $freezeScript -Action status) | ConvertFrom-Json
      if ($probe.frozen -eq $true) { $firewallHeld = $true }
    } catch { Write-Output ("FIREWALL_STATUS_ERROR: "+$_.Exception.Message) }
  }
  $rollbackOK = $false
  if (-not $firewallHeld -and $changedGit) {
    try {
      & $freezeScript -Action pause
      $firewallHeld = ((& $freezeScript -Action status) | ConvertFrom-Json).frozen
      if (-not $firewallHeld) { throw "Cannot refreeze existing user traffic" }
      $null = ConfirmCurrentTasks
    } catch {
      Write-Output ("NEW_TRAFFIC_OR_FREEZE_ERROR: "+$_.Exception.Message)
      Write-Output "Manually review new API and any in-flight tasks; unsafe code rollback refused."
      exit 3
    }
  }
  try {
    if ($changedGit) {
      # New supervisor must stop before tracked code is restored.
      try { RunExternal $py @((Join-Path $live 'scripts\classic_autostart.py'), 'stop') }
      catch { Write-Output ("NEW_STACK_STOP_ERROR: "+$_.Exception.Message) }
      $current = (& git -C $live rev-parse HEAD).Trim()
      if ($current -ne $gitCandidate -or (& git -C $live status --porcelain)) {
        throw 'Source has changed concurrently: refusing destructive rollback'
      }
      RunExternal 'git' @('-C', $live, 'reset', '--hard', $originalHead)
      RestoreOriginalFrontend $backup
    }
    if ($applicationHeld) {
      $flag = Join-Path $runtime 'pause-new-video-tasks.flag'
      if ((Test-Path $flag) -and ((Get-Content $flag -Raw).Trim() -eq 'allwe-classic-new-video-admissions-v1')) {
        Remove-Item $flag -Force
      }
    }
    if ($stackStopped) {
      RunExternal $py @((Join-Path $live 'scripts\classic_autostart.py'), 'start')
    }
    ConfirmLiveSite
    ConfirmProcesses
    $rollbackOK = $true
  } catch { Write-Output ("ROLLBACK_ERROR: "+$_.Exception.Message) }
  if ($rollbackOK -and $firewallHeld) {
    try {
      & $freezeScript -Action resume
      $firewallHeld = $false
    } catch { Write-Output ("FIREWALL_RELEASE_ERROR: "+$_.Exception.Message) }
  }
  Write-Output ('RECOVERY_STATUS: '+(@{oldSiteHealthy=$rollbackOK; ingressFreezeStillPresent=$firewallHeld; databaseRestored=$false; lastKnownRevision=((& git -C $live rev-parse --short HEAD).Trim())} | ConvertTo-Json -Compress))
  exit 2
}
