$ErrorActionPreference = 'Continue'
$root = 'C:\Users\HmamK\OneDrive\Desktop\HWK-Aali-main'
# kill any previous instance on 5055
$conns = Get-NetTCPConnection -LocalPort 5055 -State Listen -ErrorAction SilentlyContinue
foreach ($c in $conns) { Stop-Process -Id $c.OwningProcess -Force -ErrorAction SilentlyContinue }
Start-Sleep -Seconds 2

$env:PORT = '5055'
$env:HWK_ALLOW_COMMANDS = '1'
$env:PYTHONIOENCODING = 'utf-8'
$env:PYTHONPATH = 'file-agent'

Start-Process -FilePath "$root\.venv\Scripts\python.exe" `
  -ArgumentList 'file-agent/app.py' `
  -WorkingDirectory $root -WindowStyle Hidden

Start-Sleep -Seconds 8
try {
  $r = Invoke-WebRequest -UseBasicParsing -TimeoutSec 5 http://127.0.0.1:5055/api/health
  Write-Output ("HEALTH: " + $r.Content)
} catch {
  Write-Output ("HEALTH FAILED: " + $_.Exception.Message)
}
