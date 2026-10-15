"""Validation réseau avant l'effacement du disque et la reconfiguration de l'appliance."""
from pathlib import Path
import subprocess
import unittest

LIB = Path(__file__).resolve().parents[1] / 'linux/lib/appliance.sh'


class TestNetworkAnswers(unittest.TestCase):
    def accepts(self, function, *values):
        return subprocess.run(['sh', '-c', '. "$1"; shift; "$@"', 'iso-test', str(LIB), function, *values],
                              capture_output=True).returncode == 0

    def test_static_addresses_are_ipv4_with_a_prefix(self):
        self.assertTrue(self.accepts('appliance_cidr_ok', '192.168.43.10/24'))
        for value in ('192.168.43.10', '2001:db8::10/64', '192.168.999.10/24', '$(id)/24'):
            self.assertFalse(self.accepts('appliance_cidr_ok', value), value)

    def test_gateway_must_match_the_configured_network(self):
        self.assertTrue(self.accepts('appliance_gateway_ok', '192.168.43.10/24', '192.168.43.1'))
        for gateway in ('192.168.42.1', '2001:db8::1', 'not-an-address'):
            self.assertFalse(self.accepts('appliance_gateway_ok', '192.168.43.10/24', gateway), gateway)
