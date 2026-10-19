import base64
import os

import pytest
from cryptography.exceptions import InvalidTag

from scripts.backup_archive import decrypt, encrypt


def test_backup_round_trip_and_tamper_detection(tmp_path, monkeypatch):
    monkeypatch.setenv("BACKUP_ENCRYPTION_KEY", base64.b64encode(os.urandom(32)).decode())
    original = tmp_path / "original.dump"
    encrypted = tmp_path / "backup.enc"
    restored = tmp_path / "restored.dump"
    payload = os.urandom(1024 * 1024 + 17)
    original.write_bytes(payload)

    encrypt(original, encrypted)
    assert payload not in encrypted.read_bytes()
    decrypt(encrypted, restored)
    assert restored.read_bytes() == payload

    tampered = bytearray(encrypted.read_bytes())
    tampered[30] ^= 1
    encrypted.write_bytes(tampered)
    with pytest.raises(InvalidTag):
        decrypt(encrypted, tmp_path / "tampered.dump")
    assert not (tmp_path / "tampered.dump").exists()


def test_wrong_key_fails_without_plaintext(tmp_path, monkeypatch):
    monkeypatch.setenv("BACKUP_ENCRYPTION_KEY", base64.b64encode(os.urandom(32)).decode())
    source = tmp_path / "source"
    encrypted = tmp_path / "encrypted"
    destination = tmp_path / "destination"
    source.write_bytes(b"example database data")
    encrypt(source, encrypted)
    monkeypatch.setenv("BACKUP_ENCRYPTION_KEY", base64.b64encode(os.urandom(32)).decode())
    with pytest.raises(InvalidTag):
        decrypt(encrypted, destination)
    assert not destination.exists()
