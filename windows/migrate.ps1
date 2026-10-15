# Migration locale, appelée par install.ps1. Les données et les clés ne sont jamais recréées.
function Move-LegacyInstallation {
    $oldRoot = Join-Path $env:ProgramData 'local-repo'
    $oldProgram = Join-Path $env:ProgramFiles 'local-repo'
    $oldConfig = Join-Path $oldRoot 'etc\local-repo.json'
    if (-not (Test-Path $oldConfig)) { return }
    if ((Test-Path $Root) -or (Test-Path $Program)) {
        Fail 'local-repo et un dossier loco existent ensemble : migration refusée sans écrasement.'
    }
    Say 'Migration de local-repo vers loco : sauvegarde, arrêt et déplacement des données'
    $old = Get-Content -Raw -Encoding UTF8 $oldConfig | ConvertFrom-Json
    $oldPython = Join-Path $oldProgram 'python\python.exe'
    if (Test-Path $oldPython) {
        $saved = $env:LR_CONFIG
        $env:LR_CONFIG = $oldConfig
        & $oldPython -m lrwin backup create
        $result = $LASTEXITCODE
        $env:LR_CONFIG = $saved
        if ($result -ne 0) { Fail 'La sauvegarde préalable a échoué ; installation existante conservée.' }
    }
    $legacyService = Get-Service local-repo -ErrorAction SilentlyContinue
    if ($legacyService) {
        if ($legacyService.Status -ne 'Stopped') {
            Stop-Service local-repo -Force
            (Get-Service local-repo).WaitForStatus('Stopped', '00:01:00')
        }
        & (Join-Path $oldProgram 'winsw\local-repo.exe') uninstall | Out-Null
        if ($LASTEXITCODE -ne 0) { Fail 'Impossible de retirer le service local-repo.' }
    }
    Move-Item -LiteralPath $oldRoot -Destination $Root
    if (Test-Path $oldProgram) { Move-Item -LiteralPath $oldProgram -Destination $Program }
    foreach ($key in 'program_dir', 'data_dir', 'backup_dir', 'state_dir', 'etc_dir', 'log_dir', 'nginx_dir') {
        $path = [string]$old.$key
        if ($path.Equals($oldRoot, [StringComparison]::OrdinalIgnoreCase) -or
            $path.StartsWith($oldRoot + '\', [StringComparison]::OrdinalIgnoreCase)) {
            $old.$key = $Root + $path.Substring($oldRoot.Length)
        } elseif ($path.Equals($oldProgram, [StringComparison]::OrdinalIgnoreCase) -or
                  $path.StartsWith($oldProgram + '\', [StringComparison]::OrdinalIgnoreCase)) {
            $old.$key = $Program + $path.Substring($oldProgram.Length)
        }
    }
    [IO.File]::WriteAllText($ConfigPath, ($old | ConvertTo-Json -Depth 5), (New-Object Text.UTF8Encoding($false)))
    Remove-Item (Join-Path $Etc 'local-repo.json')
    foreach ($file in Get-ChildItem (Join-Path $Root 'logs') -Filter 'local-repo*' -ErrorAction SilentlyContinue) {
        Rename-Item $file.FullName ($file.Name.Replace('local-repo', 'loco'))
    }
    Get-NetFirewallRule -Group 'local-repo' -ErrorAction SilentlyContinue | Remove-NetFirewallRule
    Say 'Données migrées ; installation du service loco'
}
