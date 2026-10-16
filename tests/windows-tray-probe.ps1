#Requires -Version 5.1
#Requires -RunAsAdministrator
# Machine jetable uniquement : état indicatif et accès d'un véritable compte standard.
$ErrorActionPreference = 'Stop'
$ProgressPreference = 'SilentlyContinue'
$exe = Join-Path $env:ProgramFiles 'loco-client\loco-tray.exe'
$state = Join-Path $env:ProgramData 'loco-client-status\status.json'
$private = Join-Path $env:ProgramData 'loco-client\client.json'
if (-not (Test-Path $exe)) { throw 'Compagnon systray absent du MSI.' }
$startup = (Get-ItemProperty 'HKLM:\SOFTWARE\Microsoft\Windows\CurrentVersion\Run').loco
if ($startup -ne ('"' + $exe + '"')) { throw 'Démarrage de session absent.' }
for ($i = 0; $i -lt 60 -and -not (Test-Path $state); $i++) { Start-Sleep 1 }
if (-not (Test-Path $state)) { throw 'Le service ne publie pas son état.' }
$original = [IO.File]::ReadAllBytes($state)
Stop-Service loco-client
try {
    foreach ($color in 'green', 'orange', 'red') {
        @{color=$color; reason='État de test'; updated_at=[DateTime]::UtcNow.ToString('o')} | ConvertTo-Json | Set-Content -Encoding UTF8 $state
        $read = (& $exe --status | Out-String) | ConvertFrom-Json
        if ($read.Color -ne $color) { throw "Mauvaise couleur : $color" }
    }
    @{color='green'; reason='Ancien état'; updated_at=[DateTime]::UtcNow.AddMinutes(-11).ToString('o')} | ConvertTo-Json | Set-Content -Encoding UTF8 $state
    if (((& $exe --status | Out-String) | ConvertFrom-Json).Color -ne 'grey') { throw 'État ancien affiché au vert.' }
    @{color='green'; updated_at=[DateTime]::UtcNow.ToString('o')} | ConvertTo-Json | Set-Content -Encoding UTF8 $state
    if (((& $exe --status | Out-String) | ConvertFrom-Json).Color -ne 'grey') { throw 'État incomplet affiché au vert.' }
    & $exe --update | Out-Null
    if ($LASTEXITCODE -ne 2) { throw 'Le compagnon accepte une commande de mise à jour.' }
} finally {
    [IO.File]::WriteAllBytes($state, $original)
    Start-Service loco-client
}


# Impersonation sur le thread de test : vérifie réellement les accès d'un utilisateur standard,
# sans dépendre d'un bureau ouvert ou du planificateur de tâches du runner.
$name = 'locoTrayProbe'
$plain = 'Tray-Aa9-' + [guid]::NewGuid().ToString('N')
$created = $false
try {
    New-LocalUser -Name $name -Password (ConvertTo-SecureString $plain -AsPlainText -Force) -AccountNeverExpires | Out-Null
    $created = $true
    Add-LocalGroupMember -SID 'S-1-5-32-545' -Member $name
    Add-Type -TypeDefinition @'
using System;
using System.ComponentModel;
using System.IO;
using System.Runtime.InteropServices;
using System.Security.Principal;
public sealed class LocoTrayProbeResult {
    public bool Standard, PrivateDenied, WriteDenied;
    public string State;
}
public static class LocoTrayProbeAccess {
    [DllImport("advapi32.dll",CharSet=CharSet.Unicode,SetLastError=true)] static extern bool LogonUser(string user,string domain,string password,int type,int provider,out IntPtr token);
    [DllImport("kernel32.dll")] static extern bool CloseHandle(IntPtr handle);
    public static LocoTrayProbeResult Check(string user,string password,string state,string secret) {
        IntPtr token;
        if(!LogonUser(user,".",password,2,0,out token)) throw new Win32Exception(Marshal.GetLastWin32Error());
        try {
            using(WindowsIdentity.Impersonate(token))
            using(var identity=WindowsIdentity.GetCurrent()) {
                var result=new LocoTrayProbeResult();
                result.Standard=!identity.IsSystem && !new WindowsPrincipal(identity).IsInRole(WindowsBuiltInRole.Administrator);
                result.State=File.ReadAllText(state);
                try {File.ReadAllText(secret);} catch(UnauthorizedAccessException) {result.PrivateDenied=true;}
                try {using(var file=File.Open(state,FileMode.Open,FileAccess.Write)) {}} catch(UnauthorizedAccessException) {result.WriteDenied=true;}
                return result;
            }
        } finally {CloseHandle(token);}
    }
}
'@
    $result = [LocoTrayProbeAccess]::Check($name, $plain, $state, $private)
    $read = $result.State | ConvertFrom-Json
    if (-not $result.Standard -or -not $result.PrivateDenied -or -not $result.WriteDenied -or $read.color -notin @('green','orange','red','grey')) {
        throw 'Les droits de lecture seule du compte standard sont incorrects.'
    }
    Write-Host 'Systray : couleurs, état ancien, démarrage de session et lecture seule en compte standard validés ; jeton inaccessible.'
} finally {
    if ($created) { Remove-LocalUser -Name $name }
}
