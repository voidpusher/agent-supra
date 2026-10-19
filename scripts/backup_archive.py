"""Encrypt and decrypt PostgreSQL backups with an environment-held AES-256 key.

Archive format: 8-byte marker, 12-byte nonce, ciphertext, 16-byte GCM tag.
Only the encrypted archive should be uploaded or retained.
"""

from __future__ import annotations

import argparse
import base64
import binascii
import os
import tempfile
from pathlib import Path

from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes

MAGIC = b"ASBAK001"
NONCE_SIZE = 12
TAG_SIZE = 16
CHUNK_SIZE = 1024 * 1024


def _key() -> bytes:
    encoded = os.environ.get("BACKUP_ENCRYPTION_KEY", "")
    try:
        key = base64.b64decode(encoded, validate=True)
    except (ValueError, binascii.Error) as exc:
        raise ValueError("BACKUP_ENCRYPTION_KEY must be a base64-encoded 32-byte key") from exc
    if len(key) != 32:
        raise ValueError("BACKUP_ENCRYPTION_KEY must be a base64-encoded 32-byte key")
    return key


def encrypt(source: Path, destination: Path) -> None:
    if source.resolve() == destination.resolve():
        raise ValueError("Source and destination must differ")
    nonce = os.urandom(NONCE_SIZE)
    cipher = Cipher(algorithms.AES(_key()), modes.GCM(nonce)).encryptor()
    try:
        with source.open("rb") as plain, destination.open("xb") as encrypted:
            encrypted.write(MAGIC + nonce)
            while chunk := plain.read(CHUNK_SIZE):
                encrypted.write(cipher.update(chunk))
            encrypted.write(cipher.finalize())
            encrypted.write(cipher.tag)
    except BaseException:
        destination.unlink(missing_ok=True)
        raise


def decrypt(source: Path, destination: Path) -> None:
    if source.resolve() == destination.resolve():
        raise ValueError("Source and destination must differ")
    size = source.stat().st_size
    if size < len(MAGIC) + NONCE_SIZE + TAG_SIZE:
        raise ValueError("Backup archive is incomplete")
    with source.open("rb") as encrypted:
        if encrypted.read(len(MAGIC)) != MAGIC:
            raise ValueError("Unrecognized backup archive")
        nonce = encrypted.read(NONCE_SIZE)
        encrypted.seek(-TAG_SIZE, os.SEEK_END)
        tag = encrypted.read(TAG_SIZE)
        cipher = Cipher(algorithms.AES(_key()), modes.GCM(nonce, tag)).decryptor()
        remaining = size - len(MAGIC) - NONCE_SIZE - TAG_SIZE
        encrypted.seek(len(MAGIC) + NONCE_SIZE)
        with tempfile.NamedTemporaryFile(dir=destination.parent, delete=False) as temp:
            temp_path = Path(temp.name)
        try:
            with temp_path.open("wb") as temp:
                while remaining:
                    chunk = encrypted.read(min(CHUNK_SIZE, remaining))
                    if not chunk:
                        raise ValueError("Backup archive is incomplete")
                    remaining -= len(chunk)
                    temp.write(cipher.update(chunk))
                temp.write(cipher.finalize())
        except BaseException:
            temp_path.unlink(missing_ok=True)
            raise
    if destination.exists():
        temp_path.unlink(missing_ok=True)
        raise FileExistsError(destination)
    temp_path.replace(destination)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("operation", choices=("encrypt", "decrypt"))
    parser.add_argument("source", type=Path)
    parser.add_argument("destination", type=Path)
    args = parser.parse_args()
    if args.operation == "encrypt":
        encrypt(args.source, args.destination)
    else:
        decrypt(args.source, args.destination)


if __name__ == "__main__":
    main()
