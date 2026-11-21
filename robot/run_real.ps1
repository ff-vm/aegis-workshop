# Windows version of run_real.sh: run a skill on the REAL Aegis, with the
# simulator's live view and rule checks on this laptop. Commands go to FF's
# control gateway on the robot through an ssh tunnel.
#
#   powershell -ExecutionPolicy Bypass -File robot\run_real.ps1 firefly spin_and_wave.py
#
# Join the robot's Wi-Fi hotspot first (AEGIS-2A-...). A second window opens
# for ssh: type the robot's password there. 1 m of clear floor all round,
# someone on the e-stop. Ctrl+C here stops the dog (passive).
#
# Env: ROBOT_IP (default 192.168.234.1), GW_LOCAL_PORT (default 18768),
#      FF_GW_YAW_SIGN=-1 if the dog turns the wrong way.
param(
    [Parameter(Mandatory = $true)][string]$User,
    [Parameter(Mandatory = $true)][string]$Skill
)
$ErrorActionPreference = 'Stop'
$RobotIp = if ($env:ROBOT_IP) { $env:ROBOT_IP } else { '192.168.234.1' }
$LocalPort = if ($env:GW_LOCAL_PORT) { [int]$env:GW_LOCAL_PORT } else { 18768 }

Set-Location (Join-Path $PSScriptRoot '..')
$Skill = Split-Path $Skill -Leaf
if (-not (Test-Path $Skill)) { Write-Host "No such skill file: $Skill"; exit 1 }
if (-not (Get-Command ssh -ErrorAction SilentlyContinue)) {
    Write-Host "ssh not found. Settings > System > Optional features > add 'OpenSSH Client'."
    exit 1
}

function Test-Port([string]$HostName, [int]$Port) {
    $c = New-Object System.Net.Sockets.TcpClient
    try { return $c.ConnectAsync($HostName, $Port).Wait(3000) } catch { return $false } finally { $c.Dispose() }
}

Write-Host "==> checking the robot at $RobotIp"
if (-not (Test-Port $RobotIp 22)) {
    Write-Host "Robot not reachable at $RobotIp. Are you on its hotspot (AEGIS-2A-...)?"
    exit 1
}

Write-Host "==> opening a tunnel to FF's gateway (ssh $User@$RobotIp)"
Write-Host "    A new window opens: type the robot's password there, then come back here."
$ssh = Start-Process ssh -PassThru -ArgumentList @(
    '-N', '-o', 'ExitOnForwardFailure=yes', '-o', 'ServerAliveInterval=2',
    '-L', "$($LocalPort):127.0.0.1:8768", "$User@$RobotIp")

$deadline = (Get-Date).AddSeconds(90)
while (-not (Test-Port '127.0.0.1' $LocalPort)) {
    if ($ssh.HasExited) { Write-Host "ssh closed (wrong password?). Run this again."; exit 1 }
    if ((Get-Date) -gt $deadline) { Write-Host "Tunnel didn't open in 90 s."; Stop-Process $ssh.Id -ErrorAction SilentlyContinue; exit 1 }
    Start-Sleep -Milliseconds 500
}
Write-Host "==> tunnel open"

$py = if (Test-Path '.venv\Scripts\python.exe') { '.venv\Scripts\python.exe' } else { 'py' }
$env:FF_TARGET = 'gateway'
if (-not $env:FF_SIM_OPEN) { $env:FF_SIM_OPEN = '1' }
$env:AEGIS_GATEWAY = "http://127.0.0.1:$LocalPort"
try {
    & $py $Skill
} finally {
    Remove-Item Env:FF_TARGET, Env:AEGIS_GATEWAY -ErrorAction SilentlyContinue
    if (-not $ssh.HasExited) { Stop-Process $ssh.Id -ErrorAction SilentlyContinue }
}
