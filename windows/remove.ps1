#Requires -Version 5.1
#Requires -RunAsAdministrator
<#
.SYNOPSIS
    Désinstalle loco (serveur Windows). Par défaut, garde les données, la configuration, les comptes
    et les clés pour une réinstallation ; -Purge efface aussi tout cela (dépôts, caches, sauvegardes).
.EXAMPLE
    powershell -ExecutionPolicy Bypass -File "C:\Program Files\loco\windows\remove.ps1"
.EXAMPLE
    powershell -ExecutionPolicy Bypass -File "C:\Program Files\loco\windows\remove.ps1" -Purge
#>
[CmdletBinding()]
param([switch]$Purge, [switch]$Yes)

$ErrorActionPreference = 'Stop'
$Program = Join-Path $env:ProgramFiles 'loco'
$Root = Join-Path $env:ProgramData 'loco'
$ConfigPath = Join-Path $Root 'etc\loco.json'
$ServiceName = 'loco'

function Say([string]$Text) { Write-Host "==> $Text" -ForegroundColor Cyan }

$cfg = $null
if (Test-Path $ConfigPath) { $cfg = Get-Content -Raw -Encoding UTF8 $ConfigPath | ConvertFrom-Json }
if ($Purge -and -not $Yes) {
    $answer = Read-Host 'Effacer aussi les dépôts, caches, comptes, clés et sauvegardes ? Tapez OUI pour confirmer'
    if ($answer -cne 'OUI') { Write-Host 'Abandon.'; exit 1 }
}

Say 'Arrêt et suppression du service'
$service = Get-Service $ServiceName -ErrorAction SilentlyContinue
if ($service) {
    if ($service.Status -ne 'Stopped') {
        Stop-Service $ServiceName -Force
        (Get-Service $ServiceName).WaitForStatus('Stopped', '00:01:00')
    }
    $exe = Join-Path $Program 'winsw\loco.exe'
    if (Test-Path $exe) { & $exe uninstall | Out-Null } else { & sc.exe delete $ServiceName | Out-Null }
}
Get-Process nginx -ErrorAction SilentlyContinue | Where-Object { $_.Path -like "$Program\*" } | Stop-Process -Force

Say 'Règles du pare-feu'
Get-NetFirewallRule -Group 'loco' -ErrorAction SilentlyContinue | Remove-NetFirewallRule

Say 'Programme'
if (Test-Path $Program) {
    # Ce script peut se trouver dans le dossier supprimé : PowerShell l'a déjà chargé.
    Remove-Item -Recurse -Force $Program -ErrorAction SilentlyContinue
}

if ($Purge) {
    Say 'Données, configuration et sauvegardes'
    $dirs = @($Root)
    if ($cfg) { $dirs += $cfg.data_dir, $cfg.backup_dir }
    foreach ($d in $dirs | Select-Object -Unique) {
        if ($d -and (Test-Path $d)) { Remove-Item -Recurse -Force $d }
    }
} else {
    Write-Host "    Conservé : $Root (configuration, comptes, clés)"
    if ($cfg) { Write-Host "    Conservé : $($cfg.data_dir) (dépôts et caches), $($cfg.backup_dir) (sauvegardes)" }
    Write-Host '    Pour tout effacer : remove.ps1 -Purge'
}
Say 'loco est désinstallé'
