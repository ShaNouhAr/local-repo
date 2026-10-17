"""Vrai PTY Linux : saisie acquittée, taille, sortie binaire et arrêt du shell."""
import base64
import importlib.util
import http.server
import json
import os
from pathlib import Path
import time
import shutil
import ssl
import subprocess
import tempfile
import threading
from unittest import mock
import urllib.error
import unittest


@unittest.skipUnless(os.name == 'posix', 'PTY Linux')
class TestLinuxTerminal(unittest.TestCase):
    def test_real_shell_resize_and_input_retransmission(self):
        spec = importlib.util.spec_from_file_location('loco_remote_test', Path(__file__).resolve().parents[1] / 'portal/agents/loco-client.py')
        agent = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(agent)
        descriptor = {'id': 'test', 'kind': 'terminal', 'cols': 132, 'rows': 41, 'ack': 0, 'state': 'active', 'packets': []}
        process = agent.RemoteProcess(descriptor)
        self.addCleanup(process.close)
        self.assertEqual(process.state, 'active', process.error)
        child = process.process
        command = b"printf '\\122\\105\\115\\117\\124\\105\\137\\117\\113\\n'; stty size\n"
        descriptor['packets'] = [{'seq': 1, 'data': base64.b64encode(command).decode()}]
        process.update(descriptor)
        output = b''
        until = time.monotonic() + 8
        while time.monotonic() < until:
            payload = process.payload()
            output += base64.b64decode(payload['data'])
            descriptor['ack'] = payload['seq']
            process.update(descriptor)  # même paquet d'entrée : pas de deuxième commande
            if b'REMOTE_OK' in output and b'41 132' in output:
                break
            time.sleep(0.05)
        self.assertIn(b'REMOTE_OK', output)
        self.assertIn(b'41 132', output)
        self.assertEqual(output.count(b'REMOTE_OK'), 1)
        self.assertEqual(process.ack, 1)
        process.close()
        self.assertIsNotNone(child.poll(), 'Le shell survit à la fermeture')
        process.close()  # fermeture idempotente


@unittest.skipUnless(os.name == 'posix' and shutil.which('openssl'), 'Agent Linux et TLS local')
class TestRemoteTransport(unittest.TestCase):
    def test_reuses_https_connection_and_still_checks_ca_hostname_and_authorization(self):
        spec = importlib.util.spec_from_file_location('loco_transport_test', Path(__file__).resolve().parents[1] / 'portal/agents/loco-client.py')
        agent = importlib.util.module_from_spec(spec); spec.loader.exec_module(agent)
        clients = []
        class Handler(http.server.BaseHTTPRequestHandler):
            protocol_version = 'HTTP/1.1'
            def log_message(self, *_args):
                pass
            def do_POST(self):
                clients.append(self.client_address)
                data = json.loads(self.rfile.read(int(self.headers['Content-Length'])))
                good = self.headers.get('Authorization') == 'Bearer test-token'
                body = json.dumps({'sessions': data['sessions']}).encode()
                self.send_response(200 if good else 403)
                self.send_header('Content-Length', str(len(body))); self.end_headers(); self.wfile.write(body)
        with tempfile.TemporaryDirectory() as tmp:
            cert, key = Path(tmp, 'ca.crt'), Path(tmp, 'server.key')
            subprocess.run(['openssl', 'req', '-x509', '-newkey', 'rsa:2048', '-nodes', '-days', '1', '-subj', '/CN=remote-test',
                            '-addext', 'subjectAltName=IP:127.0.0.1', '-keyout', str(key), '-out', str(cert)],
                           check=True, capture_output=True)
            server = http.server.ThreadingHTTPServer(('127.0.0.1', 0), Handler); server.daemon_threads = True
            context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER); context.load_cert_chain(cert, key)
            server.socket = context.wrap_socket(server.socket, server_side=True)
            thread = threading.Thread(target=server.serve_forever, daemon=True); thread.start()
            transport = agent.RemoteTransport()
            try:
                with mock.patch.object(agent, 'CA', str(cert)):
                    base = 'https://127.0.0.1:' + str(server.server_port)
                    for _ in range(2):self.assertEqual(transport.exchange(base, {'sessions': []}, 'test-token'), {'sessions': []})
                    self.assertEqual(clients[0], clients[1], 'Nouvelle connexion TLS pour chaque paquet')
                    with self.assertRaises(urllib.error.HTTPError) as rejected:
                        transport.exchange(base, {'sessions': []}, 'wrong-token')
                    self.assertEqual(rejected.exception.code, 403)
                    self.assertIsNone(transport.connection)
                    with self.assertRaises(ssl.SSLCertVerificationError):
                        transport.exchange('https://localhost:' + str(server.server_port), {'sessions': []}, 'test-token')
                    self.assertIsNone(transport.connection)
                    self.assertEqual(transport.exchange(base, {'sessions': []}, 'test-token'), {'sessions': []})
            finally:
                transport.close(); server.shutdown(); server.server_close(); thread.join(2)
