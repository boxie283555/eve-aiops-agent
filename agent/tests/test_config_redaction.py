"""The file viewer keeps file structure and redacts raw credential material."""
import unittest
from aiops_agent.config_redaction import redacted_file_text


class ConfigRedactionTests(unittest.TestCase):
    def test_cli_file_order_indentation_comments_blank_lines_and_end_survive(self):
        raw='! saved config\r\nhostname demo\r\n\r\ninterface Ethernet9\r\n   description last-port\r\n!\r\ninterface Ethernet1\r\n   description first-port\r\nusername admin secret private-value\r\nend\r\n'
        self.assertEqual(redacted_file_text(raw,'arista_eos'),raw.replace('username admin secret private-value','[REDACTED]'))

    def test_cli_multiline_keys_and_sensitive_hierarchies_are_redacted(self):
        raw='hostname demo\nmanagement security\n   private key\n      wrapped-private-value\nsnmp-server user test auth sha private-auth priv aes private-encryption\nradius-server host 192.0.2.1 key 7 private-radius\n-----BEGIN RSA PRIVATE KEY-----\nbase64-private-material\n-----END RSA PRIVATE KEY-----\ninterface Ethernet1\n   description public\nend\n'
        result=redacted_file_text(raw,'arista_eos')
        for value in ['wrapped-private','private-auth','private-encryption','private-radius','base64-private']:
            self.assertNotIn(value,result)
        self.assertIn('interface Ethernet1\n   description public\nend\n',result)
        self.assertEqual(result.count('[REDACTED]'),7)

    def test_xml_order_and_config_structure_survive_with_multiline_secret_redaction(self):
        raw='<config><devices><entry name="lab" password="attr-private"><hostname>lab</hostname><phash>hash-private</phash><private-key value="value-private"><part>nested-private</part></private-key><key>\nmultiline-private\n</key><interfaces><entry name="ethernet1/2"/><entry name="ethernet1/1"/></interfaces></entry></devices></config>'
        result=redacted_file_text(raw,'paloalto_panos')
        for value in ['attr-private','hash-private','value-private','nested-private','multiline-private']:
            self.assertNotIn(value,result)
        self.assertTrue(result.startswith('<config>\n'))
        self.assertIn('<hostname>lab</hostname>',result)
        self.assertLess(result.index('ethernet1/2'),result.index('ethernet1/1'))
        self.assertIn('[REDACTED]',result)

    def test_invalid_xml_and_unsupported_formats_fail_closed(self):
        for raw in ['<config>', '<!DOCTYPE config [<!ENTITY x "private">]><config>&x;</config>','<other/>']:
            with self.assertRaises(ValueError):redacted_file_text(raw,'paloalto_panos')
        with self.assertRaises(ValueError):redacted_file_text('private','unknown')
