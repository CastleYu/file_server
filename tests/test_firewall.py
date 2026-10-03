import unittest
from unittest.mock import patch

from abyssfs.ui import firewall
from abyssfs.ui.constants import NetCategory


class FirewallProfileTests(unittest.TestCase):
    def test_parse_profile_aliases(self):
        output = """
Private Profile:
----------------------------------------------------------------------
castle

Public Profile:
----------------------------------------------------------------------
Radmin VPN
lmclient
"""
        profiles = firewall._parse_profile_aliases(output)

        self.assertEqual(NetCategory.PRIVATE, profiles["castle"])
        self.assertEqual(NetCategory.PUBLIC, profiles["Radmin VPN"])
        self.assertEqual(NetCategory.PUBLIC, profiles["lmclient"])

    def test_wildcard_host_uses_default_interface_not_any_public_profile(self):
        with patch("abyssfs.ui.firewall.is_windows", return_value=True), \
             patch("abyssfs.ui.firewall._default_aliases", return_value={"castle"}), \
             patch(
                 "abyssfs.ui.firewall._active_profiles",
                 return_value={
                     "castle": NetCategory.PRIVATE,
                     "Radmin VPN": NetCategory.PUBLIC,
                     "lmclient": NetCategory.PUBLIC,
                 },
             ):
            self.assertFalse(firewall.should_offer_firewall_access("0.0.0.0"))

    def test_wildcard_host_offers_when_default_interface_is_public(self):
        with patch("abyssfs.ui.firewall.is_windows", return_value=True), \
             patch("abyssfs.ui.firewall._default_aliases", return_value={"lmclient"}), \
             patch(
                 "abyssfs.ui.firewall._active_profiles",
                 return_value={"lmclient": NetCategory.PUBLIC},
             ):
            self.assertTrue(firewall.should_offer_firewall_access("0.0.0.0"))

    def test_deferred_request_uses_actual_access_interface(self):
        with patch("abyssfs.ui.firewall.is_windows", return_value=True), \
             patch("abyssfs.ui.firewall._local_host_for_remote", return_value="192.168.3.7"), \
             patch("abyssfs.ui.firewall._aliases_for_host", return_value={"castle"}), \
             patch(
                 "abyssfs.ui.firewall._active_profiles",
                 return_value={"castle": NetCategory.PRIVATE},
             ):
            self.assertFalse(
                firewall.should_request_firewall_access("0.0.0.0", "192.168.3.13")
            )

    def test_deferred_request_triggers_for_public_access_interface(self):
        with patch("abyssfs.ui.firewall.is_windows", return_value=True), \
             patch("abyssfs.ui.firewall._local_host_for_remote", return_value="26.1.2.3"), \
             patch("abyssfs.ui.firewall._aliases_for_host", return_value={"Radmin VPN"}), \
             patch(
                 "abyssfs.ui.firewall._active_profiles",
                 return_value={"Radmin VPN": NetCategory.PUBLIC},
             ):
            self.assertTrue(
                firewall.should_request_firewall_access("0.0.0.0", "26.4.5.6")
            )


if __name__ == "__main__":
    unittest.main()
