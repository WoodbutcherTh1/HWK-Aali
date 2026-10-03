# Finish the PC OpenSSH Server install AFTER a reboot.
#
# The capability installed as "Staged", which means Windows downloaded it but
# will not materialise sshd.exe until the machine has restarted once.
# Run this in an ELEVATED PowerShell (right-click -> Run as administrator):
#
#   powershell -ExecutionPolicy Bypass -File scripts/finish_pc_sshd.ps1
#
# Then the Pi can SSH in:
#   ssh-copy-id hmamk@192.168.1.13

$ErrorActionPreference = 'Continue'

Write-Host "== 1/3: capability state ==" -ForegroundColor Cyan
$cap = Get-WindowsCapability -Online -Name OpenSSH.Server~~~~0.0.1.0
Write-Host "   State: $($cap.State)"
if ($cap.State -eq 'NotPresent') {
    Write-Host "   Re-installing (this can take several minutes)..." -ForegroundColor Yellow
    Add-WindowsCapability -Online -Name OpenSSH.Server~~~~0.0.1.0 | Out-Null
    $cap = Get-WindowsCapability -Online -Name OpenSSH.Server~~~~0.0.1.0
    Write-Host "   State now: $($cap.State)"
}

Write-Host "`n== 2/3: service ==" -ForegroundColor Cyan
$sshdExe = "$env:SystemRoot\System32\OpenSSH\sshd.exe"
if (-not (Test-Path $sshdExe)) {
    Write-Host "   sshd.exe still missing -- reboot again and re-run." -ForegroundColor Red
    exit 1
}
Write-Host "   sshd.exe found: $sshdExe"

$svc = Get-Service sshd -ErrorAction SilentlyContinue
if (-not $svc) {
    Write-Host "   Service not registered yet; trying sc.exe create" -ForegroundColor Yellow
    & sc.exe create sshd binPath= "$env:SystemRoot\System32\OpenSSH\sshd.exe" start= auto DisplayName= "OpenSSH SSH Server" | Out-Null
}
Set-Service sshd -StartupType Automatic
if ((Get-Service sshd).Status -ne 'Running') { Start-Service sshd }
$svc = Get-Service sshd
Write-Host "   sshd => Status=$($svc.Status) StartType=$($svc.StartType)" -ForegroundColor Green

Write-Host "`n== 3/3: firewall + verify ==" -ForegroundColor Cyan
if (-not (Get-NetFirewallRule -Name 'sshd-jarvis' -ErrorAction SilentlyContinue)) {
    New-NetFirewallRule -Name 'sshd-jarvis' -DisplayName 'OpenSSH Server (sshd) - jarvis' `
        -Enabled True -Direction Inbound -Protocol TCP -Action Allow -LocalPort 22 | Out-Null
    Write-Host "   firewall rule created"
} else {
    Write-Host "   firewall rule already present"
}

$listen = Get-NetTCPConnection -State Listen -LocalPort 22 -ErrorAction SilentlyContinue
if ($listen) {
    foreach ($l in $listen) { Write-Host "   LISTENING $($l.LocalAddress):$($l.LocalPort) pid $($l.OwningProcess)" -ForegroundColor Green }
} else {
    Write-Host "   nothing listening on 22 yet" -ForegroundColor Red
}

Write-Host "`nDone. From the Pi you can now run: ssh-copy-id hmamk@192.168.1.13" -ForegroundColor Green