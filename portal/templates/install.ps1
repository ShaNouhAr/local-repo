# local-repo : installation depuis le catalogue {{ base }}
$ErrorActionPreference = 'Stop'
$ProgressPreference = 'SilentlyContinue'

$principal = New-Object Security.Principal.WindowsPrincipal([Security.Principal.WindowsIdentity]::GetCurrent())
if (-not $principal.IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)) {
    Write-Warning 'Relancez PowerShell en tant qu''administrateur.'
    return
}
{% for s in missing %}
Write-Warning 'Absent du catalogue (ou pas encore pret) : {{ s }}'
{% endfor %}
$apps = @(
{% for a in apps %}    @{ Name = {{ q(a.name) }}; Url = {{ q(base ~ '/win/files/' ~ a.slug ~ '/' ~ quote(a.filename)) }}; File = {{ q(a.filename) }}; Sha = {{ q(a.sha256) }}; Kind = {{ q(a.kind) }}; Args = {{ q(a.args) }} }{% if not loop.last %},{% endif %}
{% endfor %})

$dir = Join-Path $env:TEMP ('local-repo-' + [guid]::NewGuid().ToString('N'))
New-Item -ItemType Directory -Path $dir | Out-Null
$failed = 0

foreach ($a in $apps) {
    Write-Host "==> $($a.Name)" -ForegroundColor Cyan
    try {
        $dest = Join-Path $dir $a.File
        Invoke-WebRequest -UseBasicParsing -Uri $a.Url -OutFile $dest
        if ((Get-FileHash $dest -Algorithm SHA256).Hash -ne $a.Sha) { throw 'somme SHA256 invalide' }
        $p = $null
        switch ($a.Kind) {
            'msi' { $p = Start-Process msiexec.exe -ArgumentList "/i `"$dest`" $($a.Args)" -Wait -PassThru }
            'msu' { $p = Start-Process wusa.exe -ArgumentList "`"$dest`" $($a.Args)" -Wait -PassThru }
            'ps1' { $p = Start-Process powershell.exe -ArgumentList "-NoProfile -ExecutionPolicy Bypass -File `"$dest`" $($a.Args)" -Wait -PassThru }
            'exe' {
                if ($a.Args) { $p = Start-Process $dest -ArgumentList $a.Args -Wait -PassThru }
                else { $p = Start-Process $dest -Wait -PassThru }
            }
            default {
                $target = Join-Path $env:PUBLIC 'Downloads'
                Copy-Item $dest $target -Force
                Write-Host "    Fichier copie dans $target" -ForegroundColor Green
            }
        }
        if ($p) {
            $code = $p.ExitCode
            if ($code -eq 3010 -or $code -eq 1641) { Write-Host '    OK (redemarrage necessaire)' -ForegroundColor Yellow }
            elseif ($code -eq 2359302) { Write-Host '    Deja installe' -ForegroundColor Green }
            elseif ($code -ne 0) { throw "code de sortie $code" }
            else { Write-Host '    OK' -ForegroundColor Green }
        }
    } catch {
        Write-Warning "$($a.Name) : $_"
        $failed++
    }
}

Remove-Item $dir -Recurse -Force -ErrorAction SilentlyContinue
if ($failed) { Write-Warning "$failed installation(s) en echec." } else { Write-Host 'Termine.' -ForegroundColor Green }
