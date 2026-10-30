"""Immutable per-branch packet state; never share mutable NAT state across ECMP."""
from __future__ import annotations

from dataclasses import asdict, dataclass, replace
from ipaddress import ip_address


@dataclass(frozen=True)
class FlowState:
    source_ip: str
    destination_ip: str
    protocol: str = 'any'
    source_port: int | None = None
    destination_port: int | None = None
    application: str | None = None

    def __post_init__(self):
        if ip_address(self.source_ip).version != ip_address(self.destination_ip).version:
            raise ValueError('Mixed address families require a supported translation gateway')
        if self.protocol not in {'any', 'tcp', 'udp', 'icmp', 'icmpv6'}:
            raise ValueError('Unsupported protocol')
        if (self.protocol == 'icmp' and ip_address(self.source_ip).version != 4) or (self.protocol == 'icmpv6' and ip_address(self.source_ip).version != 6):
            raise ValueError('ICMP protocol does not match address family')
        for port in (self.source_port, self.destination_port):
            if port is not None and (not 0 <= port <= 65535 or self.protocol not in {'tcp', 'udp'}):
                raise ValueError('Ports require TCP/UDP and must be between 0 and 65535')

    def data(self):
        return asdict(self)

    def translated(self, **values):
        return replace(self, **values)
