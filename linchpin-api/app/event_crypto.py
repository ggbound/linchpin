"""Atlas B8 — envelope encryption for business-content event payloads (FR-10.3).

Sessions opted in via the ``x-events-encrypted: true`` request header get a
per-session data key (a fresh Fernet key), wrapped and stored in
``sessions.data_key``. Business-content payloads (user.* / agent.*) are
encrypted at rest as the self-describing envelope
``{"enc": "v1", "ct": "<fernet token>"}``; control events
(session.* / span.* / thread.*) stay plaintext so scheduling queries
(compaction boundary scan, interrupt dispatch, stream fan-out) never need
the key.

Two key-wrapping paths (Atlas M6 BYOK, FR-10.3 "customer key registration"):

- **Platform master key** (default) — ``wrap_data_key`` seals the data key
  under ``VAULT_ENCRYPTION_KEY``; the stored form is a bare Fernet token
  (legacy shape, unchanged).
- **Customer KEK (BYOK)** — the caller passes ``x-kek-credential-id``
  naming a ``byok_kek`` vault credential; the data key is sealed under
  the customer's key instead and the stored form is the self-describing
  JSON ``{"enc": "byok1", "kid": "<credential id>", "ct": "<token>"}``.
  Unwrapping routes on the shape: bare token → master key, JSON → the
  KEK credential named by ``kid`` (resolved via the credentials table,
  memoized in-process).

Plaintext data keys are memoized per session (in-process) so the hot
append/replay paths do one unwrap per session instead of per event.
"""

from __future__ import annotations

import json
import logging
import uuid
from typing import Any

from cryptography.fernet import Fernet
from fastapi import HTTPException

from app.db import fetch_one
from app.encryption import get_encryption_service

logger = logging.getLogger("linchpin-api")

# Control-plane prefixes stay plaintext (Atlas B8 spec): scheduling and
# observability queries can still filter/aggregate without the data key.
_CONTROL_PREFIXES: tuple[str, ...] = ("session.", "span.", "thread.")

_ENVELOPE_VERSION = "v1"

# BYOK key-wrapping envelope (Atlas M6). ``kid`` names the byok_kek
# vault credential whose ``kek`` secret unwraps ``ct``.
_BYOK_WRAP_VERSION = "byok1"

# In-process cache: session uuid → unwrapped data key.
_data_key_cache: dict[uuid.UUID, str] = {}

# In-process cache: KEK credential id → customer KEK (BYOK unwrap path).
_kek_cache: dict[uuid.UUID, str] = {}


def is_control_event(event_type: str) -> bool:
    """True for control-plane events that must stay plaintext."""
    return event_type.startswith(_CONTROL_PREFIXES)


def generate_data_key() -> str:
    """Generate a fresh per-session data key (base64 Fernet key)."""
    return Fernet.generate_key().decode()


def wrap_data_key(
    data_key: str,
    kek: str | None = None,
    kek_credential_id: str | None = None,
) -> str:
    """Wrap a data key for storage.

    Without ``kek``: seal under the platform master key — the legacy
    bare-Fernet-token shape. With ``kek`` (BYOK): seal under the
    customer's key and return the self-describing byok1 JSON envelope
    carrying ``kid`` (the credential to unwrap with later).
    """
    if kek is None:
        return get_encryption_service().encrypt(data_key).decode()
    ct = Fernet(kek.encode()).encrypt(data_key.encode()).decode()
    envelope = {"enc": _BYOK_WRAP_VERSION, "kid": kek_credential_id, "ct": ct}
    # Warm the KEK cache so the first unwrap needs no DB roundtrip.
    try:
        _kek_cache[uuid.UUID(kek_credential_id)] = kek
    except (TypeError, ValueError):
        pass
    return json.dumps(envelope)


def unwrap_data_key(wrapped: str, kek: str | None = None) -> str:
    """Unwrap a stored data key.

    BYOK shape (JSON with ``enc == byok1``) requires ``kek``; the legacy
    bare-token shape unwraps under the platform master key.
    """
    kid = byok_credential_id(wrapped)
    if kid is not None:
        if kek is None:
            raise HTTPException(
                status_code=500,
                detail={
                    "error": "kek_unavailable",
                    "message": (
                        "session data key is customer-wrapped but no KEK "
                        "was resolved"
                    ),
                },
            )
        ct = json.loads(wrapped)["ct"]
        return Fernet(kek.encode()).decrypt(ct.encode()).decode()
    return get_encryption_service().decrypt(wrapped.encode())


def byok_credential_id(wrapped: str) -> str | None:
    """Return the KEK credential id for a BYOK-wrapped data key, else None.

    Legacy platform-wrapped keys are bare Fernet tokens — not JSON — so
    they route to the master-key path.
    """
    if not wrapped.startswith("{"):
        return None
    try:
        envelope = json.loads(wrapped)
    except ValueError:
        return None
    if (
        isinstance(envelope, dict)
        and envelope.get("enc") == _BYOK_WRAP_VERSION
        and isinstance(envelope.get("ct"), str)
    ):
        return str(envelope.get("kid"))
    return None


async def get_kek(kek_credential_id: str) -> str:
    """Resolve a customer KEK from its byok_kek vault credential.

    Cache-first; on miss, decrypts ``credentials.encrypted_secrets``
    (platform master key) and memoizes. Unknown/archived credentials or
    missing secret material surface as a 500 ``kek_unavailable`` — the
    session's payloads are ciphertext but no longer openable, which is
    the honest failure mode for a customer-managed key that's gone.
    """
    try:
        kid = uuid.UUID(kek_credential_id)
    except ValueError:
        raise HTTPException(
            status_code=500,
            detail={
                "error": "kek_unavailable",
                "message": f"invalid KEK credential id {kek_credential_id!r}",
            },
        )
    cached = _kek_cache.get(kid)
    if cached is not None:
        return cached
    row = await fetch_one(
        """
        SELECT encrypted_secrets FROM credentials
        WHERE id = $1 AND credential_type = 'byok_kek'
          AND archived_at IS NULL AND encrypted_secrets IS NOT NULL
        """,
        kid,
    )
    if row is None:
        raise HTTPException(
            status_code=500,
            detail={
                "error": "kek_unavailable",
                "message": (
                    f"KEK credential {kek_credential_id} is missing, "
                    "archived, or has no secret material"
                ),
            },
        )
    kek = json.loads(get_encryption_service().decrypt(row["encrypted_secrets"]))["kek"]
    _kek_cache[kid] = kek
    return kek


def encrypt_payload(payload: dict[str, Any], data_key: str) -> dict[str, Any]:
    """Seal a payload dict into the self-describing envelope."""
    token = Fernet(data_key.encode()).encrypt(json.dumps(payload).encode())
    return {"enc": _ENVELOPE_VERSION, "ct": token.decode()}


def decrypt_payload(payload: Any, data_key: str | None) -> Any:
    """Open an envelope payload; passthrough anything else.

    Handles plaintext control events, legacy rows, and non-encrypted
    sessions (``data_key=None``) uniformly.
    """
    if data_key is None:
        return payload
    if not (
        isinstance(payload, dict)
        and payload.get("enc") == _ENVELOPE_VERSION
        and isinstance(payload.get("ct"), str)
    ):
        return payload
    plain = Fernet(data_key.encode()).decrypt(payload["ct"].encode())
    return json.loads(plain)


def cache_session_data_key(session_id: str, data_key: str) -> None:
    """Memoize the plaintext data key at session-create time."""
    try:
        _data_key_cache[uuid.UUID(session_id)] = data_key
    except ValueError:
        pass


def drop_session_data_key(session_id: str) -> None:
    """Drop the cached key once the session is terminated/deleted."""
    try:
        _data_key_cache.pop(uuid.UUID(session_id), None)
    except ValueError:
        pass


async def get_session_data_key(session_id: uuid.UUID) -> str | None:
    """Return the plaintext data key for an encrypted session, else None.

    Cache-first; on miss, unwraps ``sessions.data_key`` from the DB and
    memoizes — routing platform-wrapped (bare token) and BYOK-wrapped
    (byok1 JSON) shapes to their respective key-encryption keys. Rows
    that are not encrypted (or missing the key) yield None.
    """
    cached = _data_key_cache.get(session_id)
    if cached is not None:
        return cached
    row = await fetch_one(
        """
        SELECT data_key FROM sessions
        WHERE id = $1 AND events_encrypted AND data_key IS NOT NULL
        """,
        session_id,
    )
    if row is None:
        return None
    wrapped = row["data_key"]
    kid = byok_credential_id(wrapped)
    if kid is not None:
        kek = await get_kek(kid)
        data_key = unwrap_data_key(wrapped, kek=kek)
    else:
        data_key = unwrap_data_key(wrapped)
    _data_key_cache[session_id] = data_key
    return data_key
