"""Vrai PTY Linux : saisie acquittée, taille, sortie binaire et arrêt du shell."""
import base64
import importlib.util
import os
from pathlib import Path
import time
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
