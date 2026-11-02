"""Display saved configuration as text without exposing credential material."""
from __future__ import annotations

import re
import xml.etree.ElementTree as ET

from .config_history import SECRET

CLI_SECRET = re.compile(
    SECRET.pattern + r'|\bkey\b|\bkey-string\b|\bpassphrase\b|\bsnmp-server\s+user\b', re.I)
XML_SECRET = re.compile(SECRET.pattern + r'|^(?:key|key-string|passphrase)$', re.I)


def redacted_file_text(raw: str, platform: str) -> str:
    if platform == 'paloalto_panos':
        # Parse XML so multiline secrets and nested secret elements are covered.
        # Do not expand document-defined entities into the public preview.
        if re.search(r'<!DOCTYPE|<!ENTITY', raw, re.I):
            raise ValueError('XML declarations are not supported in configuration preview')
        try:
            root = ET.fromstring(raw)
        except ET.ParseError:
            raise ValueError('Invalid XML configuration backup') from None
        if root.tag != 'config':
            raise ValueError('Expected running config XML')

        def redact(node):
            sensitive = bool(XML_SECRET.search(node.tag.rsplit('}', 1)[-1]))
            for key in node.attrib:
                if sensitive or XML_SECRET.search(key.rsplit('}', 1)[-1]):
                    node.attrib[key] = '[REDACTED]'
            if sensitive:
                for child in list(node):
                    node.remove(child)
                node.text = '[REDACTED]'
            else:
                for child in node:
                    redact(child)
        redact(root)
        # PAN-OS API backups are often one long line; retain element order and
        # indent it as a single readable XML document.
        ET.indent(root, space='  ')
        return ET.tostring(root, encoding='unicode') + '\n'

    if platform not in {'arista_eos', 'cisco_nxos'}:
        raise ValueError('Unsupported configuration preview platform')
    output = []
    secret_indent = None
    pem = False
    for line in raw.splitlines(keepends=True):
        text = line.rstrip('\r\n')
        ending = line[len(text):]
        prefix = text[:len(text) - len(text.lstrip())]
        value = text.strip()
        if re.search(r'-----BEGIN .*PRIVATE KEY-----', value, re.I):
            pem = True
        if pem:
            output.append(prefix + '[REDACTED]' + ending)
            if re.search(r'-----END .*PRIVATE KEY-----', value, re.I):
                pem = False
            continue
        if value and secret_indent is not None and len(prefix) <= secret_indent:
            secret_indent = None
        sensitive = bool(CLI_SECRET.search(value))
        if sensitive or (value and secret_indent is not None):
            output.append(prefix + '[REDACTED]' + ending)
            if sensitive and secret_indent is None:
                secret_indent = len(prefix)
        else:
            output.append(line)
    return ''.join(output)
