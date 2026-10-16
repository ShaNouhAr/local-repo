"""Sonde réseau réelle : HTTP sur le port TLS doit rediriger sans perdre l'URL."""
import http.client
import sys
import urllib.parse


def check(base):
    url = urllib.parse.urlsplit(base)
    assert url.scheme == 'https' and url.hostname, base
    port = url.port or 443
    cases = (
        ('GET', '/', None, None),
        ('HEAD', '/login?next=%2Flinux%3Frepository%3Ddebian%2Fstable&probe=1', None, None),
        ('POST', '/login', 'probe=redirect', None),
        ('GET', '/login?next=%2Frpm', None, f'loco.example:{port}'),
    )
    for method, path, body, host in cases:
        connection = http.client.HTTPConnection(url.hostname, port, timeout=10)
        try:
            connection.request(method, path, body=body, headers={'Host': host} if host else {})
            response = connection.getresponse()
            expected = f'https://{host or url.netloc}{path}'
            assert response.status == 308, f'{method} {base}{path} : {response.status} au lieu de 308'
            assert response.getheader('Location') == expected, (response.getheader('Location'), expected)
            response.read()
        finally:
            connection.close()
    # Une requête mal formée reste refusée : seule l'erreur HTTP-sur-TLS est redirigée.
    connection = http.client.HTTPConnection(url.hostname, port, timeout=10)
    try:
        connection.putrequest('GET', '/', skip_host=True)
        connection.endheaders()  # HTTP/1.1 impose Host, y compris avec les anciennes versions de nginx.
        response = connection.getresponse()
        assert response.status == 400 and response.getheader('Location') is None, (
            response.status, response.getheader('Location'))
        response.read()
    finally:
        connection.close()
    print(f'HTTP sur le port HTTPS : GET/HEAD/POST, chemin, paramètres et hôte conservés : OK ({base})')


if __name__ == '__main__':
    check(sys.argv[1])
