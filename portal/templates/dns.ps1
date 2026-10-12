# local-repo : redirige les domaines Windows Update vers le cache {{ host }}.
# A executer en administrateur sur le serveur DNS Active Directory.
#   .\local-repo-dns.ps1            -> cree les zones
#   .\local-repo-dns.ps1 -Remove    -> supprime les zones (retour arriere)
param([switch]$Remove)
$ErrorActionPreference = 'Stop'
$ip = '{{ host }}'
$domains = @({% for d in domains %}'{{ d }}'{% if not loop.last %}, {% endif %}{% endfor %})

foreach ($d in $domains) {
    $zone = Get-DnsServerZone -Name $d -ErrorAction SilentlyContinue
    if ($Remove) {
        if ($zone) { Remove-DnsServerZone -Name $d -Force; Write-Host "Zone supprimee : $d" }
        continue
    }
    if (-not $zone) { Add-DnsServerPrimaryZone -Name $d -ReplicationScope Forest }
    foreach ($name in '@', '*') {
        $existing = Get-DnsServerResourceRecord -ZoneName $d -Name $name -RRType A -ErrorAction SilentlyContinue
        if ($existing) { $existing | Remove-DnsServerResourceRecord -ZoneName $d -Force }
        Add-DnsServerResourceRecordA -ZoneName $d -Name $name -IPv4Address $ip
    }
    Write-Host "Zone $d -> $ip"
}
Clear-DnsServerCache -Force
