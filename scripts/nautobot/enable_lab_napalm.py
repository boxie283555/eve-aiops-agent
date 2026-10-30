"""Run with nautobot-server shell; configure only lab devices, without probing them."""

import json, os
from pathlib import Path
from django.db import transaction
from nautobot.dcim.models import Device, Platform, Interface
from nautobot.ipam.models import IPAddress, Prefix, Namespace
from nautobot.extras.models import Secret, SecretsGroup, SecretsGroupAssociation, Status
from nautobot.extras.choices import (
    SecretsGroupAccessTypeChoices as Access,
    SecretsGroupSecretTypeChoices as Type,
)

lab = list(Device.objects.filter(location__name="lab").select_related("platform"))
assert lab, "No devices in site/location lab"
before = [
    {
        "id": str(d.id),
        "name": d.name,
        "platform_id": str(d.platform_id),
        "primary_ip4_id": str(d.primary_ip4_id) if d.primary_ip4_id else None,
        "secrets_group_id": str(d.secrets_group_id) if d.secrets_group_id else None,
    }
    for d in lab
]
p = Path("/opt/nautobot/media/lab-napalm-before.json")
if not p.exists():
    p.write_text(json.dumps(before, indent=2))
    p.chmod(0o600)
with transaction.atomic():
    active = Status.objects.get(name="Active")
    namespace, _ = Namespace.objects.get_or_create(
        name="lab-management",
        defaults={"description": "Management host records for lab NAPALM"},
    )
    for d in lab:
        assert d.location.name == "lab"
        is_pa = d.platform.name in ["Palo Alto PAN-OS", "Palo Alto PAN-OS (lab NAPALM)"]
        assert is_pa or d.platform.name in ["Arista EOS", "Arista EOS (lab NAPALM)"]
        key = "PANOS" if is_pa else "EOS"
        base = "Palo Alto PAN-OS" if is_pa else "Arista EOS"
        driver = "paloalto_panos" if is_pa else "arista_eos"
        platform, _ = Platform.objects.get_or_create(
            name=base + " (lab NAPALM)",
            defaults={
                "manufacturer": d.platform.manufacturer,
                "network_driver": driver,
                "napalm_driver": "panos" if is_pa else "eos",
                "napalm_args": {"ssl_verify": False} if is_pa else {"transport": "ssh"},
            },
        )
        assert not platform.devices.exclude(
            location__name="lab"
        ).exists(), "Refuse to change a platform used outside lab"
        platform.network_driver = driver
        platform.napalm_driver = (
            "aiops_napalm.panos_lab" if is_pa else "aiops_napalm.eos_lab"
        )
        platform.napalm_args = {"ssl_verify": False} if is_pa else {"transport": "ssh"}
        platform.full_clean()
        platform.save()
        group, _ = SecretsGroup.objects.get_or_create(name="lab NAPALM " + key)
        for typ, suffix in [
            (Type.TYPE_USERNAME, "USERNAME"),
            (Type.TYPE_PASSWORD, "PASSWORD"),
        ]:
            variable = "NAPALM_LAB_" + key + "_" + suffix
            assert os.getenv(variable), "Missing secret environment"
            secret, _ = Secret.objects.get_or_create(
                name="lab NAPALM " + key + " " + suffix.lower(),
                defaults={
                    "provider": "environment-variable",
                    "parameters": {"variable": variable},
                },
            )
            secret.full_clean()
            secret.save()
            SecretsGroupAssociation.objects.get_or_create(
                secrets_group=group,
                access_type=Access.TYPE_GENERIC,
                secret_type=typ,
                defaults={"secret": secret},
            )
        host = d.cf.get("management_ip")
        assert host
        if d.primary_ip4:
            assert str(d.primary_ip4.host) == host
        else:
            prefix = Prefix.objects.filter(
                namespace=namespace, network=host, prefix_length=32
            ).first()
            if not prefix:
                prefix = Prefix(
                    prefix=host + "/32",
                    namespace=namespace,
                    status=active,
                    description="lab management host",
                )
                prefix.validated_save()
            ip = IPAddress.objects.filter(parent=prefix, host=host).first()
            if not ip:
                ip = IPAddress(
                    address=host + "/32",
                    parent=prefix,
                    status=active,
                    description=d.name + " management",
                )
                ip.validated_save()
            iface, _ = Interface.objects.get_or_create(
                device=d,
                name="management" if is_pa else "Management1",
                defaults={"type": "other", "status": active, "mgmt_only": True},
            )
            iface.ip_addresses.add(ip)
            d.primary_ip4 = ip
        d.platform = platform
        d.secrets_group = group
        d.validated_save()
        print(
            "Enabled",
            d.name,
            "site",
            d.location.name,
            "driver",
            platform.napalm_driver,
            "primary",
            str(d.primary_ip4),
        )
print("Lab-only NAPALM configuration complete")
