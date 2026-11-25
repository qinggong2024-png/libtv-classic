# ALLWE Classic 5176 first-install network ingress barrier.
# Deliberately blocks ONLY inbound TCP/5176 on Windows. It does not stop
# localhost API 8002, remote CUT, or the unrelated 5173 app.
# Run elevated. A specific, validated firewall rule is removed on resume.
[CmdletBinding()]
param(
  [ValidateSet('status','pause','resume')][string]$Action = 'status'
)
Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'
$ruleName = 'ALLWE_CLASSIC_5176_FIRST_INSTALL_FREEZE_20261122'
function Inspect-Freeze {
  $profiles = @(Get-NetFirewallProfile)
  $rule = @(Get-NetFirewallRule -Name $ruleName -ErrorAction SilentlyContinue)
  if ($rule.Count -gt 1) { throw 'Ambiguous firewall rule identity' }
  if ($rule.Count -eq 0) {
    return @{ installed = $false; frozen = $false; firewallProfilesEnabled = (@($profiles|Where-Object { $_.Enabled -ne $true }).Count -eq 0) }
  }
  $value = $rule[0]
  $ports = @(Get-NetFirewallPortFilter -AssociatedNetFirewallRule $value)
  $addresses = @(Get-NetFirewallAddressFilter -AssociatedNetFirewallRule $value)
  if ($ports.Count -ne 1 -or $addresses.Count -ne 1) { throw 'Unexpected firewall rule filters' }
  $p = $ports[0]
  $a = $addresses[0]
  if (
    [string]$value.Direction -ne 'Inbound' -or
    [string]$value.Action -ne 'Block' -or
    [string]$value.Profile -ne 'Any' -or
    [string]$p.Protocol -ne 'TCP' -or
    [string]$p.LocalPort -ne '5176' -or
    [string]$p.RemotePort -ne 'Any' -or
    [string]$a.RemoteAddress -ne 'Any'
  ) { throw 'Unexpected firewall rule scope; manual review required' }
  $allEnabled = @($profiles | Where-Object { $_.Enabled -ne $true }).Count -eq 0
  return @{ installed = $true; frozen = ([string]$value.Enabled -eq 'True' -and $allEnabled); firewallProfilesEnabled = $allEnabled }
}
if ($Action -ne 'status') {
  $identity = [Security.Principal.WindowsIdentity]::GetCurrent()
  $principal = [Security.Principal.WindowsPrincipal]::new($identity)
  if (-not $principal.IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)) {
    throw 'Administrator privileges required; no firewall changes made'
  }
}
$before = Inspect-Freeze
switch ($Action) {
  'pause' {
    if (-not $before.firewallProfilesEnabled) { throw 'All firewall profiles must be enabled' }
    if (-not $before.installed) {
      New-NetFirewallRule -Name $ruleName -DisplayName 'ALLWE Classic 5176 first-install ingress freeze' -Direction Inbound -Protocol TCP -LocalPort 5176 -RemotePort Any -Profile Any -Action Block -Enabled True | Out-Null
    } elseif (-not $before.frozen) { throw 'Existing maintenance firewall rule is disabled; manual review required' }
  }
  'resume' {
    if ($before.installed) {
      # Inspect-Freeze validated this exact rule and its port before deletion.
      Remove-NetFirewallRule -Name $ruleName
    }
  }
}
$after = Inspect-Freeze
if ($Action -eq 'pause' -and -not $after.frozen) { throw 'Firewall pause not verified' }
if ($Action -eq 'resume' -and $after.installed) { throw 'Firewall resume not verified' }
[pscustomobject]@{
  action = $Action
  frozen = [bool]$after.frozen
  installed = [bool]$after.installed
  firewallProfilesEnabled = [bool]$after.firewallProfilesEnabled
  ruleName = $ruleName
  scope = 'inbound TCP 5176 only; leaves 5173 and localhost API untouched'
} | ConvertTo-Json -Compress
