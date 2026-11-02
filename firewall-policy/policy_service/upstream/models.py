from __future__ import annotations

from enum import Enum
from typing import Any

from pydantic import BaseModel, Field, SecretStr, field_validator, model_validator
from typing import Literal
import ipaddress


class Vendor(str, Enum):
    cisco_asa = "cisco_asa"
    palo_alto = "palo_alto"
    fortinet = "fortinet"
    fortigate = "fortigate"
    huawei = "huawei"


class FirewallObject(BaseModel):
    name: str
    type: str
    value: str
    vendor: Vendor
    device_name: str = "imported"


class PolicyRule(BaseModel):
    id: str
    vendor: Vendor
    device_name: str = "imported"
    rule_name: str
    source_zones: list[str] = Field(default_factory=list)
    destination_zones: list[str] = Field(default_factory=list)
    source_addresses: list[str] = Field(default_factory=list)
    destination_addresses: list[str] = Field(default_factory=list)
    services: list[str] = Field(default_factory=list)
    applications: list[str] = Field(default_factory=list)
    users: list[str] = Field(default_factory=list)
    action: str = "unknown"
    enabled: bool = True
    raw: str = ""
    line_no: int | None = None
    tags: list[str] = Field(default_factory=list)
    owner: str | None = None
    expires_at: str | None = None
    hit_count: int | None = None
    last_hit_at: str | None = None


class NatRule(BaseModel):
    id: str
    vendor: Vendor
    device_name: str = "imported"
    rule_name: str
    nat_type: str = "unknown"
    source_zones: list[str] = Field(default_factory=list)
    destination_zones: list[str] = Field(default_factory=list)
    original_sources: list[str] = Field(default_factory=list)
    original_destinations: list[str] = Field(default_factory=list)
    original_services: list[str] = Field(default_factory=list)
    translated_sources: list[str] = Field(default_factory=list)
    translated_destinations: list[str] = Field(default_factory=list)
    translated_services: list[str] = Field(default_factory=list)
    action: str = "nat"
    enabled: bool = True
    raw: str = ""
    line_no: int | None = None


class ParseResult(BaseModel):
    network: dict[str, Any] | None = None
    vendor: Vendor
    device_name: str
    policies: list[PolicyRule]
    objects: list[FirewallObject]
    nat_rules: list[NatRule] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)


class ImportRequest(BaseModel):
    vendor: Vendor
    device_name: str = "imported"
    config_text: str


class DeviceConnection(BaseModel):
    id: int | None = None
    name: str
    vendor: Vendor
    host: str
    port: int = 22
    protocol: str = "ssh"
    username: str
    enable_password_required: bool = False
    notes: str | None = None
    last_status: str | None = None
    last_error: str | None = None
    last_fetched_at: str | None = None
    created_at: str | None = None
    updated_at: str | None = None


class DeviceConnectionRequest(BaseModel):
    name: str
    vendor: Vendor
    host: str
    port: int = 22
    protocol: str = "ssh"
    username: str
    enable_password_required: bool = False
    notes: str | None = None


class DeviceCredentialRequest(BaseModel):
    password: str
    enable_password: str | None = None


class TopologyDiscoveryCredential(BaseModel):
    device_id: int = Field(gt=0)
    password: SecretStr = Field(min_length=1)
    enable_password: SecretStr | None = None


class TopologyDiscoveryRequest(BaseModel):
    credentials: list[TopologyDiscoveryCredential] = Field(min_length=1, max_length=32)

    @field_validator('credentials')
    @classmethod
    def unique_devices(cls, value):
        if len({c.device_id for c in value}) != len(value):
            raise ValueError('Select each device only once.')
        return value


class TopologyNetworkScanRequest(BaseModel):
    network: str
    vendor: Vendor
    protocol: Literal['ssh', 'telnet', 'api_https', 'netconf'] = 'ssh'
    port: int = Field(default=22, ge=1, le=65535)
    username: str = Field(min_length=1, max_length=128)
    password: SecretStr = Field(min_length=1)
    enable_password: SecretStr | None = None
    timeout_ms: int = Field(default=350, ge=100, le=2000)

    @field_validator('network')
    @classmethod
    def bounded_network(cls, value):
        network = ipaddress.IPv4Network(value, strict=False)
        if network.num_addresses > 512:
            raise ValueError('Use an IPv4 subnet of /23 or smaller (at most 512 addresses).')
        return str(network)


class SeedDiscoveryRequest(BaseModel):
    seed: str
    username: str = Field(min_length=1, max_length=128)
    password: SecretStr | None = None
    neighbor_username: str | None = Field(default=None, min_length=1, max_length=128)
    neighbor_password: SecretStr | None = None
    enable_password: SecretStr | None = None
    use_ssh_agent: bool = False
    via_seed: bool = False
    include_arp: bool = False
    port: int = Field(default=22, ge=1, le=65535)
    platform: Literal['autodetect', 'cisco_ios', 'cisco_nxos', 'arista_eos', 'huawei', 'cisco_asa', 'paloalto_panos', 'fortinet'] = 'autodetect'
    allowed_networks: list[str] = Field(min_length=1, max_length=32)
    max_devices: int = Field(default=128, ge=1, le=512)
    max_depth: int = Field(default=8, ge=0, le=32)
    import_firewalls: bool = True

    @field_validator('seed')
    @classmethod
    def valid_seed(cls, value):
        return str(ipaddress.IPv4Address(value))

    @field_validator('allowed_networks')
    @classmethod
    def valid_scope(cls, values):
        return [str(n) for n in ipaddress.collapse_addresses([ipaddress.IPv4Network(v.strip(), strict=False) for v in values])]

    @model_validator(mode='after')
    def valid_seed_credentials(self):
        if self.via_seed and (self.platform != 'arista_eos' or self.port != 22 or not self.password or not self.password.get_secret_value()):
            raise ValueError('CLI jump mode currently requires an Arista EOS seed, port 22 and a password.')
        if self.via_seed:
            import re
            if not re.fullmatch(r'[A-Za-z0-9_][A-Za-z0-9_.-]*', self.username):
                raise ValueError('CLI jump username must contain only letters, digits, underscore, dot or hyphen.')
            if self.neighbor_username and not re.fullmatch(r'[A-Za-z0-9_][A-Za-z0-9_.-]*', self.neighbor_username):
                raise ValueError('Neighbor username must contain only letters, digits, underscore, dot or hyphen.')
        if not self.use_ssh_agent and (not self.password or not self.password.get_secret_value()):
            raise ValueError('Enter a password or select local SSH agent authentication.')
        if self.neighbor_username and not self.neighbor_password:
            raise ValueError('Neighbor username requires a neighbor password.')
        if self.neighbor_password and not self.neighbor_username:
            raise ValueError('Neighbor password requires a neighbor username.')
        if not any(ipaddress.IPv4Address(self.seed) in ipaddress.IPv4Network(n) for n in self.allowed_networks):
            raise ValueError('Seed must be within the allowed management networks.')
        return self


class TopologyPathRequest(BaseModel):
    source: str
    destination: str
    service: str = 'tcp/443'
    source_device: str | None = None
    destination_device: str | None = None

    @field_validator('source', 'destination')
    @classmethod
    def valid_ip(cls, value):
        return str(ipaddress.IPv4Address(value))

    @field_validator('service')
    @classmethod
    def valid_service(cls, value):
        import re
        value = value.strip().lower()
        if value == 'icmp':
            return value
        match = re.fullmatch(r'(tcp|udp)/(\d{1,5})', value)
        if not match or not 1 <= int(match[2]) <= 65535:
            raise ValueError('Use tcp/443, udp/53, or icmp.')
        return value


class AnalysisFinding(BaseModel):
    severity: str
    category: str
    title: str
    detail: str
    policy_ids: list[str] = Field(default_factory=list)
    recommendation: str


class PolicyQuery(BaseModel):
    vendor: str | None = None
    device_name: str | None = None
    source: str | None = None
    destination: str | None = None
    service: str | None = None
    application: str | None = None
    user: str | None = None
    source_zone: str | None = None
    destination_zone: str | None = None
    action: str | None = None
    enabled: bool | None = None
    query: str | None = None


class PolicyPathRequest(BaseModel):
    source: str | None = None
    destination: str | None = None
    service: str | None = None
    application: str | None = None
    user: str | None = None
    source_zone: str | None = None
    destination_zone: str | None = None
    vendor: str | None = None
    device_name: str | None = None


class PolicyPrecheckRequest(BaseModel):
    vendor: str | None = None
    device_name: str | None = None
    source_zone: str | None = None
    destination_zone: str | None = None
    source: str
    destination: str
    service: str
    application: str | None = None
    user: str | None = None
    action: str = "allow"


class PolicyPrecheckIssue(BaseModel):
    severity: str
    category: str
    title: str
    detail: str
    policy_ids: list[str] = Field(default_factory=list)
    recommendation: str


class PolicyPrecheckResult(BaseModel):
    verdict: str
    summary: str
    issues: list[PolicyPrecheckIssue]
    matched_policies: list[dict[str, Any]]
    placement: dict[str, Any]
    deployment: dict[str, Any]


class ChangeRequest(BaseModel):
    title: str
    requester: str
    business_reason: str
    vendor: str | None = None
    device_name: str | None = None
    source_zone: str | None = None
    destination_zone: str | None = None
    source: str
    destination: str
    service: str
    application: str | None = None
    user: str | None = None
    action: str = "allow"
    expires_at: str | None = None
    status: str = "draft"


class ChangeStatusRequest(BaseModel):
    status: str


class StoredChangeRequest(ChangeRequest):
    id: int
    created_at: str


class Dashboard(BaseModel):
    policy_count: int
    nat_rule_count: int = 0
    object_count: int
    change_request_count: int
    finding_count: int
    high_risk_count: int
    vendor_counts: dict[str, int]
    action_counts: dict[str, int]
    recent_audit: list[dict[str, Any]]
