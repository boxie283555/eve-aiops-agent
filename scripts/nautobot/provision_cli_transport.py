"""Provision the CLI transport selector without modifying device records."""
from aiops_agent.settings import load_settings
from aiops_agent.nautobot import NautobotClient

s = load_settings()
c = NautobotClient(s.nautobot_url, s.nautobot_token, 30)
fields = c._get_all('extras/custom-fields/?limit=100')
field = next((f for f in fields if f['key'] == 'use_ssh'), None)
if field:
    kind = field['type']['value'] if isinstance(field['type'], dict) else field['type']
    if kind != 'boolean' or 'dcim.device' not in field['content_types']:
        raise ValueError('Existing use_ssh field is incompatible; schema left unchanged')
    print('Existing CLI transport field retained')
else:
    c._request('POST', 'extras/custom-fields/', {
        'key': 'use_ssh', 'label': 'AIOps · Use SSH (off = Telnet)',
        'type': 'boolean', 'content_types': ['dcim.device'], 'default': True,
        'required': False,
        'description': 'CLI transport: true or unset uses SSH; false uses Telnet. connection_port overrides the default 22/23. Telnet drivers support EOS and PAN-OS. PA HTTPS API is independent.'})
    print('CLI transport field created with SSH default; no device records edited')
