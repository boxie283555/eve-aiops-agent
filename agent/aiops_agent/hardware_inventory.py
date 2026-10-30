"""Component identity from EOS sectioned and NX-OS named inventories.

Slot numbers are only unique within a component family. Fan/optic records must
never supply a PSU or line-card serial, even when they share the same slot.
"""
import re
from dataclasses import dataclass


@dataclass(frozen=True)
class InventoryPart:
    kind: str
    slot: str
    model: str
    serial: str


def known(value):
    return bool(value and str(value).strip().lower() not in {
        'unknown', 'n/a', 'na', 'none', '-', '--', 'not applicable', 'not_applicable',
    })


def slot_id(value):
    value = str(value).strip().lower()
    match = re.fullmatch(r'(?:power\s*supply|power-supply|psu?|pwr|slot|module|mod|supervisor|linecard)?\s*(\d+(?:[/.-]\d+)*)', value)
    return match.group(1) if match else value


def _kind(name):
    if re.search(r'fan|transceiver|sfp|qsfp|ethernet', name, re.I):
        return None
    if re.search(r'power[ -]?suppl|\bpsu?\s*\d|\bpwr[- ]', name, re.I):
        return 'power'
    if re.search(r'chassis|system information', name, re.I):
        return 'chassis'
    if re.search(r'\b(slots?|module|supervisor|line[ -]?card)\b', name, re.I):
        return 'module'
    return None


def parse_inventory(output, structured=None):
    parts = []
    # NX-OS and IOS-style named FRU records.
    for match in re.finditer(r'NAME:\s*"([^"]+)"[^\n]*\n\s*PID:\s*([^,\n]*),\s*VID:[^,\n]*,\s*SN:[^\S\r\n]*([^\r\n]*)', output, re.I):
        name, model, serial = (x.strip() for x in match.groups())
        kind = _kind(name)
        if kind:
            number = re.search(r'\d+(?:[/.-]\d+)*', name)
            parts.append(InventoryPart(kind, 'chassis' if kind == 'chassis' else number.group() if number else name.lower(), model, serial))

    # EOS inventory headings distinguish PSU/fan/optic slot namespaces.
    section, model, serial_next = None, '', False
    for line in output.splitlines():
        text = line.strip()
        if re.match(r'System (information|has|contains)', text, re.I):
            section = _kind(text)
            model, serial_next = '', False
            continue
        if not text or re.match(r'^[-\s]+$', text):
            continue
        if section == 'chassis':
            if re.search(r'Serial Number', text, re.I):
                serial_next = True
            elif serial_next:
                # HW version can be blank; serial is the token before Mfg Date.
                tokens = text.split()
                date_index = next((i for i, token in enumerate(tokens) if re.match(r'\d{4}-\d\d-\d\d', token)), len(tokens))
                if date_index and not re.fullmatch(r'\d{1,3}(?:\.\d+)+', tokens[date_index - 1]):
                    parts.append(InventoryPart('chassis', 'chassis', model, tokens[date_index - 1]))
                serial_next = False
            elif not text.lower().startswith(('model', 'hw version')):
                model = text.split()[0]
        elif section in {'power', 'module'}:
            fields = text.split()
            if re.search(r'\bNot (?:Inserted|Present|Installed)\b', text, re.I):
                continue
            if len(fields) >= 3 and re.fullmatch(r'(?:Supervisor|Linecard|Fabric|Switchcard)?\d+(?:[/.-]\d+)*', fields[0], re.I):
                serial_index = 3 if section == 'module' else 2
                if len(fields) > serial_index:
                    parts.append(InventoryPart(section, slot_id(fields[0]), fields[1], fields[serial_index]))

    for record in structured if isinstance(structured, list) else []:
        if not isinstance(record, dict):
            continue
        values = {k.lower(): str(v).strip() for k, v in record.items() if v is not None}
        name = values.get('name', '')
        model = values.get('pid', values.get('model', ''))
        if model.lower() == 'not' or values.get('sn', '').lower() in {'inserted', 'present', 'installed'}:
            continue
        kind = _kind(name) or _kind(values.get('descr', '')) or _kind(model)
        if not kind:
            continue  # Bare PORT=1 is ambiguous without a component family.
        slot = values.get('slot', values.get('port', ''))
        if not slot:
            number = re.search(r'\d+(?:[/.-]\d+)*', name)
            slot = number.group() if number else name.lower()
        parts.append(InventoryPart(kind, 'chassis' if kind == 'chassis' else slot_id(slot), model, values.get('sn', values.get('serial', ''))))
    return list(dict.fromkeys(parts))


def identity(parts, kind, slot, model='unknown'):
    candidates = [p for p in parts if p.kind == kind and p.slot == slot_id(slot)]
    if known(model):
        candidates = [p for p in candidates if not known(p.model) or p.model == model]
    # Do not arbitrarily pick a serial when the source is contradictory.
    models = {p.model for p in candidates if known(p.model)}
    serials = {p.serial for p in candidates if known(p.serial)}
    return (next(iter(models)) if len(models) == 1 else 'unknown',
            next(iter(serials)) if len(serials) == 1 else 'unknown')
