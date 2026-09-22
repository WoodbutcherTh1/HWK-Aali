# Rebuild the Aali desktop shortcuts (owner convenience, 2026-09-22).
# Run:  powershell -NoProfile -ExecutionPolicy Bypass -File scripts\make_desktop_shortcuts.ps1
# Creates: Aali Desktop (app window), Aali CLI (terminal), Aali Status
# (GPU/disks/training). All use Aali's own icon.

$ErrorActionPreference = "Stop"
$root    = Split-Path -Parent $PSScriptRoot          # repo root
$scripts = Join-Path $root "scripts"
$icon    = Join-Path $root "build-desktop\icon.ico"
$desk    = [Environment]::GetFolderPath("Desktop")
$ws      = New-Object -ComObject WScript.Shell

function New-AaliShortcut {
    param([string]$Name, [string]$Bat, [string]$Description, [int]$WindowStyle)
    $path = Join-Path $desk ($Name + ".lnk")
    $sc = $ws.CreateShortcut($path)
    $sc.TargetPath      = (Join-Path $scripts $Bat)
    $sc.WorkingDirectory = $scripts
    $sc.Description      = $Description
    $sc.WindowStyle      = $WindowStyle
    if (Test-Path $icon) { $sc.IconLocation = "$icon,0" }
    $sc.Save()
    Write-Host ("created/updated: " + $path)
}

New-AaliShortcut -Name "Aali Desktop" -Bat "aali_desktop.bat" `
    -Description "Open Aali in his own app window" -WindowStyle 7
New-AaliShortcut -Name "Aali CLI" -Bat "aali_cli.bat" `
    -Description "Talk to Aali in the terminal (live tool traces)" -WindowStyle 1
New-AaliShortcut -Name "Aali Status" -Bat "status.bat" `
    -Description "Quick status: GPU, disks, latest training lines" -WindowStyle 1

Write-Host ("icon used: " + $icon + " (present: " + (Test-Path $icon) + ")")
