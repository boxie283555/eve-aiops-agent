"""Encrypted per-device credentials used by scheduled collection."""
from __future__ import annotations

import json
import os
import tempfile
import threading
from pathlib import Path

from cryptography.fernet import Fernet, InvalidToken


class CredentialVault:
    """Small encrypted key/value store; Nautobot contains only generated refs."""

    _lock = threading.RLock()

    def __init__(self, path: str | None = None, key: str | None = None):
        self.path = Path(path or os.getenv('AIOPS_CREDENTIAL_VAULT_PATH', '/app/data/credential-vault.enc'))
        raw_key = key or os.getenv('AIOPS_CREDENTIAL_VAULT_KEY')
        if not raw_key:
            raise RuntimeError('AIOPS_CREDENTIAL_VAULT_KEY is not configured')
        try:
            self.fernet = Fernet(raw_key.encode() if isinstance(raw_key, str) else raw_key)
        except (ValueError, TypeError):
            raise RuntimeError('AIOPS_CREDENTIAL_VAULT_KEY is invalid') from None

    def _read(self) -> dict[str, str]:
        if not self.path.exists():
            return {}
        try:
            encrypted = self.path.read_bytes()
            value = json.loads(self.fernet.decrypt(encrypted))
            if not isinstance(value, dict) or not all(isinstance(k, str) and isinstance(v, str) for k, v in value.items()):
                raise ValueError
            return value
        except (InvalidToken, ValueError, json.JSONDecodeError):
            raise RuntimeError('Credential vault cannot be decrypted; preserve the vault and key') from None

    def _write(self, values: dict[str, str]) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        payload = self.fernet.encrypt(json.dumps(values, ensure_ascii=False, sort_keys=True).encode())
        fd, temporary = tempfile.mkstemp(prefix='.credential-vault-', dir=self.path.parent)
        try:
            os.fchmod(fd, 0o600)
            with os.fdopen(fd, 'wb') as stream:
                stream.write(payload)
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(temporary, self.path)
            os.chmod(self.path, 0o600)
        finally:
            if os.path.exists(temporary):
                os.unlink(temporary)

    def get(self, reference: str | None) -> str | None:
        if not reference:
            return None
        with self._lock:
            return self._read().get(reference)

    def set_many(self, values: dict[str, str]) -> None:
        if not values:
            return
        with self._lock:
            current = self._read()
            current.update(values)
            self._write(current)

    def delete_many(self, references: list[str]) -> None:
        if not references:
            return
        with self._lock:
            current = self._read()
            for reference in references:
                current.pop(reference, None)
            self._write(current)
