---
name: pc-commands
description: Aali's Windows command library — the everyday commands for files, networking, processes, and diagnostics, with safe/partially-safe flags and when to use each. Load before writing any shell command for the user.
---

## Command categories

### Files & folders
| Task | Command |
|---|---|
| List with sizes | `Get-ChildItem -Recurse \| Sort-Object Length -Descending \| Select-Object -First 20` |
| Find a file | `Get-ChildItem -Path C:\ -Recurse -Filter "name*.ext" -ErrorAction SilentlyContinue` |
| Folder tree size | `"{0:N2} GB" -f ((Get-ChildItem -Recurse \| Measure-Object Length -Sum).Sum/1GB)` |
| Newest files | `Get-ChildItem -Recurse \| Sort-Object LastWriteTime -Descending \| Select-Object -First 10` |
| Compare folders | `Compare-Object (Get-ChildItem A).Name (Get-ChildItem B).Name` |

### Networking
| Task | Command |
|---|---|
| connectivity | `Test-Connection google.com -Count 2` |
| port listening? | `Test-NetConnection localhost -Port 5055` |
| my IPs | `Get-NetIPAddress -AddressFamily IPv4 \| Select IPAddress, InterfaceAlias` |
| who's on my wifi | `netstat -an \| findstr ESTABLISHED` |
| http check | `Invoke-WebRequest -Uri <url> -UseBasicParsing -TimeoutSec 10` |

### Processes & services
| Task | Command |
|---|---|
| top memory | `Get-Process \| Sort-Object WS -Descending \| Select -First 10 Name, @{N='MB';E={[int](\$_.WS/1MB)}}` |
| find a process | `Get-Process -Name python -ErrorAction SilentlyContinue` |
| what runs on port | `Get-NetTCPConnection -LocalPort 5055 -State Listen \| Select OwningProcess` |
| service status | `Get-Service \| Where-Object {\$_.Status -eq 'Running'}` |

### Diagnostics
| Task | Command |
|---|---|
| disk free | `Get-PSDrive -PSProvider FileSystem \| Select Name, @{N='FreeGB';E={[int](\$_.Free/1GB)}}` |
| RAM total/free | `Get-CimInstance Win32_OperatingSystem \| Select @{N='TotalGB';E={[int](\$_.TotalVisibleMemorySize/1MB)}}, @{N='FreeGB';E={[int](\$_.FreePhysicalMemory/1MB)}}` |
| GPU load | `nvidia-smi --query-gpu=utilization.gpu,memory.used,temperature.gpu --format=csv,noheader` |
| uptime | `(Get-Date) - (Get-CimInstance Win32_OperatingSystem).LastBootUpTime` |
| battery | `Get-CimInstance Win32_Battery \| Select EstimatedChargeRemaining` |

## Safety rules when composing commands

1. **Read-only by default**: `Get-*`, `Test-*`, `netstat`, `nvidia-smi` are safe
   to run freely. Anything with `Set-`, `Remove-`, `Stop-`, `New-`, `>`, or
   `del` CHANGES the system - explain it first and get a yes.
2. **Never suggest**: `Remove-Item -Recurse -Force` on a path you haven't
   listed first, `Stop-Process` on python/node (Aali's own runtimes), or
   `format`/diskpart on ANY drive.
3. **Escaping**: inside double-quoted PowerShell strings, `$` must be escaped
   as `` `$ `` or the variable expands BEFORE the command runs - this is the
   most common broken-command bug (verify output, never assume).
4. run_command's allow-list accepts `powershell` - the env-dump guard
   (os.environ/printenv/$VAR patterns) still applies inside it.
