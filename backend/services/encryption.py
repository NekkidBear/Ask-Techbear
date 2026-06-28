"""
backend/services/encryption.py — Application-layer PII encryption
Ask TechBear — Gymnarctos Studios LLC

Provides symmetric Fernet encryption for PII fields (currently
attendee_email) before values are written to the database.

The database column stores ciphertext. Even direct DB access
(psql, TablePlus, pgAdmin) without the application key returns
unusable bytes.

Key management:
    - Key is a URL-safe base64-encoded 32-byte value.
    - Stored in .env as ENCRYPTION_KEY.
    - Generate a new key once and store it:

        python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"

    - Never commit the key to git.
    - Never log the key.
    - If the key is lost, encrypted values are unrecoverable.
      Back up the key separately from the database.
    - Key rotation: decrypt all values with the old key, re-encrypt
      with the new key, swap ENCRYPTION_KEY in .env. A rotation
      helper is a future enhancement.

Usage:
    from backend.services.encryption import encrypt_email, decrypt_email

    # On submission — encrypt before writing to DB
    encrypted = encrypt_email("user@example.com")
    question.attendee_email = encrypted   # stores ciphertext string

    # On approval — decrypt only at send time
    plaintext = decrypt_email(question.attendee_email)
    await email_service.send(to=plaintext, ...)

Column type note:
    attendee_email is VARCHAR(254) in the migration (0003).
    Fernet ciphertext is base64-encoded and longer than the plaintext —
    a 254-char email produces ~400 chars of ciphertext. The column
    was intentionally sized to TEXT-compatible length in the migration;
    if VARCHAR(254) proves too short after testing, migration 0004
    should widen it to VARCHAR(512) or TEXT. See _MAX_EMAIL_LENGTH below.

Error handling:
    - encrypt_email: raises EncryptionError on bad key or empty input.
    - decrypt_email: raises DecryptionError on bad key, tampered
      ciphertext, or a value that was never encrypted (e.g. a legacy
      plaintext row). The caller (approve endpoint) catches
      DecryptionError and aborts the send rather than delivering to
      a garbled address.
    - Both functions return None for None input (null DB values).
"""

import logging
import os
from typing import Optional

from cryptography.fernet import Fernet, InvalidToken

logger = logging.getLogger(__name__)

# Fernet ciphertext of a 254-char email is ~400 chars.
# Used in encrypt_email to guard against oversized inputs.
_MAX_EMAIL_LENGTH = 254


class EncryptionError(Exception):
    """Raised when encryption fails (bad key, empty input, etc.)."""


class DecryptionError(Exception):
    """
    Raised when decryption fails.

    Possible causes:
        - Wrong key (key was rotated without re-encrypting values)
        - Tampered or corrupted ciphertext
        - Value was stored as plaintext (legacy row before encryption
          was introduced — migration did not back-fill existing rows)
    """


def _get_fernet() -> Fernet:
    """
    Load and return a Fernet instance from ENCRYPTION_KEY env var.

    Called at use time (not import time) so .env changes don't
    require restart and the key is never held in module-level state.

    Raises EncryptionError if ENCRYPTION_KEY is missing or invalid.
    """
    key = os.getenv("ENCRYPTION_KEY", "").strip()
    if not key:
        raise EncryptionError(
            "ENCRYPTION_KEY is not set. "
            "Generate one with: "
            "python -c \"from cryptography.fernet import Fernet; "
            "print(Fernet.generate_key().decode())\""
        )
    try:
        return Fernet(key.encode())
    except (ValueError, Exception) as exc:
        raise EncryptionError(
            f"ENCRYPTION_KEY is invalid: {exc}. "
            "Key must be a URL-safe base64-encoded 32-byte value."
        ) from exc


def encrypt_email(plaintext: Optional[str]) -> Optional[str]:
    """
    Encrypt an email address for storage.

    Args:
        plaintext: email address string, or None.

    Returns:
        URL-safe base64 ciphertext string, or None if input is None.

    Raises:
        EncryptionError: if key is missing/invalid, or input exceeds
            the maximum email length.
    """
    if plaintext is None:
        return None

    plaintext = plaintext.strip()
    if not plaintext:
        return None

    if len(plaintext) > _MAX_EMAIL_LENGTH:
        raise EncryptionError(
            f"Email address exceeds maximum length of {_MAX_EMAIL_LENGTH} characters."
        )

    fernet = _get_fernet()
    ciphertext = fernet.encrypt(plaintext.encode("utf-8"))
    return ciphertext.decode("utf-8")


def decrypt_email(ciphertext: Optional[str]) -> Optional[str]:
    """
    Decrypt a stored email address ciphertext.

    Args:
        ciphertext: the encrypted value from the database, or None.

    Returns:
        Plaintext email address string, or None if input is None.

    Raises:
        DecryptionError: if the key is wrong, the ciphertext is
            tampered, or the value is plaintext (not encrypted).
            Callers should catch this and abort the send.
    """
    if ciphertext is None:
        return None

    fernet = _get_fernet()
    try:
        plaintext = fernet.decrypt(ciphertext.encode("utf-8"))
        return plaintext.decode("utf-8")
    except InvalidToken as exc:
        raise DecryptionError(
            "Failed to decrypt email address. The value may be corrupted, "
            "the key may have changed, or this row predates encryption."
        ) from exc
    except Exception as exc:
        raise DecryptionError(
            f"Unexpected decryption error: {exc}"
        ) from exc


def has_encrypted_email(ciphertext: Optional[str]) -> bool:
    """
    Return True if the column contains a non-null, non-empty value.

    Does NOT attempt decryption — safe to call without the key.
    Used by serializers that need to expose 'has_email: bool'
    without touching the plaintext address.
    """
    return bool(ciphertext and ciphertext.strip())
