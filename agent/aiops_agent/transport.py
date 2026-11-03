"""Keep platform identity separate from the CLI transport used by Netmiko."""
TELNET_DRIVERS = {'arista_eos': 'arista_eos_telnet', 'paloalto_panos': 'paloalto_panos_telnet'}


class UnsupportedTransport(ValueError):
    pass


def cli_driver(device):
    if device.use_ssh:
        return device.platform
    try:
        return TELNET_DRIVERS[device.platform]
    except KeyError:
        raise UnsupportedTransport('Telnet is not supported for this platform; select SSH') from None
