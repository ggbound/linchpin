"""Atlas B8 — unit tests for envelope encryption of event payloads (FR-10.3).

Validates: x-events-encrypted sessions seal business-content payloads,
control events stay plaintext, and the data-key lifecycle (generate →
wrap → persist → unwrap → cache → drop).
"""

from __future__ import annotations

import json
import uuid
from unittest.mock import AsyncMock, patch

import pytest
from cryptography.fernet import Fernet

from app import event_crypto
from app.encryption import get_encryption_service
from app.event_crypto import (
    byok_credential_id,
    cache_session_data_key,
    decrypt_payload,
    drop_session_data_key,
    encrypt_payload,
    generate_data_key,
    get_kek,
    get_session_data_key,
    is_control_event,
    unwrap_data_key,
    wrap_data_key,
)


@pytest.fixture(autouse=True)
def _set_encryption_key(monkeypatch):
    """Set a valid Fernet master key for the encryption service."""
    key = Fernet.generate_key().decode()
    monkeypatch.setenv("VAULT_ENCRYPTION_KEY", key)
    from app.encryption import reset_encryption_service

    reset_encryption_service()
    yield
    reset_encryption_service()


@pytest.fixture(autouse=True)
def _fresh_key_cache():
    """Fresh in-process key cache per test (belt and braces — conftest
    also clears it, but this keeps the module readable standalone)."""
    event_crypto._data_key_cache.clear()
    yield
    event_crypto._data_key_cache.clear()


# ---------------------------------------------------------------------------
# is_control_event
# ---------------------------------------------------------------------------


class TestIsControlEvent:
    def test_session_events_are_control(self):
        for t in ("session.started", "session.error", "session.compacted"):
            assert is_control_event(t) is True

    def test_span_events_are_control(self):
        assert is_control_event("span.llm_call") is True

    def test_thread_events_are_control(self):
        assert is_control_event("thread.created") is True

    def test_business_events_are_not_control(self):
        for t in (
            "user.message",
            "user.interrupt",
            "user.tool_confirmation",
            "agent.message",
            "agent.thinking",
            "agent.tool_use",
            "agent.tool_result",
        ):
            assert is_control_event(t) is False


# ---------------------------------------------------------------------------
# Key lifecycle
# ---------------------------------------------------------------------------


class TestKeyLifecycle:
    def test_generate_data_key_is_fernet_key(self):
        key = generate_data_key()
        # Fernet keys are 32-byte urlsafe-base64 — Fernet() accepts them.
        Fernet(key.encode())  # must not raise

    def test_generate_data_key_unique(self):
        assert generate_data_key() != generate_data_key()

    def test_wrap_unwrap_roundtrip(self):
        key = generate_data_key()
        wrapped = wrap_data_key(key)
        assert wrapped != key  # wrapped under the master key
        assert unwrap_data_key(wrapped) == key

    def test_wrap_uses_master_key_from_env(self):
        key = generate_data_key()
        wrapped = wrap_data_key(key)
        # Decrypting with a different master key must fail.
        monkey_master = Fernet.generate_key().decode()
        from app.encryption import EncryptionService

        other = EncryptionService(monkey_master)
        with pytest.raises(Exception):
            other.decrypt(wrapped.encode())


# ---------------------------------------------------------------------------
# encrypt_payload / decrypt_payload
# ---------------------------------------------------------------------------


class TestPayloadEnvelope:
    def test_encrypt_roundtrip(self):
        key = generate_data_key()
        payload = {"content": "secret", "n": 1, "nested": {"a": [1, 2]}}
        sealed = encrypt_payload(payload, key)

        assert sealed["enc"] == "v1"
        assert isinstance(sealed["ct"], str)
        assert "secret" not in json.dumps(sealed)

        assert decrypt_payload(sealed, key) == payload

    def test_decrypt_with_none_key_passthrough(self):
        payload = {"content": "plain"}
        assert decrypt_payload(payload, None) is payload

    def test_decrypt_non_envelope_passthrough(self):
        """Plaintext control events / legacy rows pass through untouched."""
        key = generate_data_key()
        payload = {"summary": "s", "covered_seq_end": 5}
        assert decrypt_payload(payload, key) is payload

    def test_decrypt_non_dict_passthrough(self):
        key = generate_data_key()
        assert decrypt_payload("just a string", key) == "just a string"
        assert decrypt_payload(None, key) is None

    def test_decrypt_malformed_ct_raises(self):
        """An envelope whose ciphertext was sealed under a different key
        fails loudly rather than returning garbage."""
        key_a = generate_data_key()
        key_b = generate_data_key()
        sealed = encrypt_payload({"content": "x"}, key_a)
        with pytest.raises(Exception):
            decrypt_payload(sealed, key_b)


# ---------------------------------------------------------------------------
# In-process data-key cache
# ---------------------------------------------------------------------------


class TestDataKeyCache:
    def test_cache_and_drop(self):
        sid = str(uuid.uuid4())
        key = generate_data_key()
        cache_session_data_key(sid, key)
        assert event_crypto._data_key_cache[uuid.UUID(sid)] == key
        drop_session_data_key(sid)
        assert uuid.UUID(sid) not in event_crypto._data_key_cache

    def test_drop_missing_is_noop(self):
        drop_session_data_key(str(uuid.uuid4()))
        drop_session_data_key("not-a-uuid")  # must not raise

    @pytest.mark.asyncio
    async def test_get_session_data_key_uses_cache_without_db(self):
        """A cached key is returned without touching the DB."""
        sid = str(uuid.uuid4())
        key = generate_data_key()
        cache_session_data_key(sid, key)

        with patch(
            "app.event_crypto.fetch_one", new_callable=AsyncMock
        ) as mock_fetch:
            assert await get_session_data_key(uuid.UUID(sid)) == key
        mock_fetch.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_get_session_data_key_db_fallback_unwraps(self):
        """Cache miss → SELECT the wrapped key, unwrap, memoize."""
        key = generate_data_key()
        wrapped = wrap_data_key(key)

        with patch(
            "app.event_crypto.fetch_one", new_callable=AsyncMock,
            return_value={"data_key": wrapped},
        ) as mock_fetch:
            got = await get_session_data_key(uuid.UUID(str(uuid.UUID(int=1))))

        assert got == key
        # Memoized: a second call must not hit the DB again.
        assert (
            await get_session_data_key(uuid.UUID(int=1)) == key
        )
        assert mock_fetch.await_count == 1
        # ...and the memoized copy is in the cache.
        assert event_crypto._data_key_cache[uuid.UUID(int=1)] == key

    @pytest.mark.asyncio
    async def test_get_session_data_key_none_for_plaintext_session(self):
        """No row (plaintext session / missing key) → None, nothing cached."""
        result = await get_session_data_key(uuid.UUID(int=2))
        assert result is None
        assert uuid.UUID(int=2) not in event_crypto._data_key_cache


# ---------------------------------------------------------------------------
# Atlas M6 — BYOK: customer KEK wrap path
# ---------------------------------------------------------------------------


class TestByokWrapFormat:
    def test_wrap_byok_envelope_shape(self):
        key = generate_data_key()
        kek = Fernet.generate_key().decode()
        cid = str(uuid.uuid4())

        wrapped = wrap_data_key(key, kek=kek, kek_credential_id=cid)

        envelope = json.loads(wrapped)
        assert envelope["enc"] == "byok1"
        assert envelope["kid"] == cid
        assert isinstance(envelope["ct"], str)
        # The ct is the data key Fernet-sealed under the customer KEK.
        assert Fernet(kek).decrypt(envelope["ct"].encode()) == key.encode()

    def test_unwrap_byok_roundtrip(self):
        key = generate_data_key()
        kek = Fernet.generate_key().decode()

        wrapped = wrap_data_key(key, kek=kek, kek_credential_id=str(uuid.uuid4()))

        assert unwrap_data_key(wrapped, kek=kek) == key

    def test_unwrap_byok_without_kek_fails_500(self):
        """Honest-failure mode: a lost KEK surfaces as kek_unavailable."""
        from fastapi import HTTPException

        wrapped = wrap_data_key(
            generate_data_key(), kek=Fernet.generate_key().decode(),
            kek_credential_id=str(uuid.uuid4()),
        )
        with pytest.raises(HTTPException) as ei:
            unwrap_data_key(wrapped)
        assert ei.value.status_code == 500
        assert ei.value.detail["error"] == "kek_unavailable"

    def test_unwrap_byok_with_wrong_kek_fails(self):
        wrapped = wrap_data_key(
            generate_data_key(), kek=Fernet.generate_key().decode(),
            kek_credential_id=str(uuid.uuid4()),
        )
        with pytest.raises(Exception):
            unwrap_data_key(wrapped, kek=Fernet.generate_key().decode())

    def test_platform_path_unaffected_by_kek_arg(self):
        """A platform-wrapped key stays a bare Fernet token even if a kek
        is somehow passed — shape routing decides, not the argument."""
        key = generate_data_key()
        wrapped = wrap_data_key(key)
        assert byok_credential_id(wrapped) is None
        assert unwrap_data_key(
            wrapped, kek=Fernet.generate_key().decode()
        ) == key


class TestByokCredentialId:
    def test_legacy_token_is_none(self):
        key = generate_data_key()
        wrapped = wrap_data_key(key)
        assert byok_credential_id(wrapped) is None

    def test_byok_envelope_yields_kid(self):
        cid = str(uuid.uuid4())
        wrapped = wrap_data_key(
            generate_data_key(), kek=Fernet.generate_key().decode(),
            kek_credential_id=cid,
        )
        assert byok_credential_id(wrapped) == cid

    def test_non_json_string_is_none(self):
        assert byok_credential_id("gAAAAA-not-json") is None

    def test_payload_envelope_is_none(self):
        """A sealed payload ({"enc": "v1"}) must not be mistaken for a
        wrapped data key."""
        assert byok_credential_id(json.dumps({"enc": "v1", "ct": "x"})) is None

    def test_json_without_ct_is_none(self):
        assert byok_credential_id(json.dumps({"enc": "byok1"})) is None


class TestGetKek:
    @pytest.mark.asyncio
    async def test_db_roundtrip_and_memoization(self):
        kek = Fernet.generate_key().decode()
        cid = uuid.uuid4()
        sealed = get_encryption_service().encrypt(json.dumps({"kek": kek}))

        with patch(
            "app.event_crypto.fetch_one", new_callable=AsyncMock,
            return_value={"encrypted_secrets": sealed},
        ) as mock_fetch:
            assert await get_kek(str(cid)) == kek
            assert await get_kek(str(cid)) == kek  # second call — cache hit

        assert mock_fetch.await_count == 1
        assert event_crypto._kek_cache[cid] == kek

    @pytest.mark.asyncio
    async def test_missing_credential_raises_500(self):
        from fastapi import HTTPException

        with pytest.raises(HTTPException) as ei:
            await get_kek(str(uuid.uuid4()))
        assert ei.value.status_code == 500
        assert ei.value.detail["error"] == "kek_unavailable"


class TestGetSessionDataKeyByok:
    @pytest.mark.asyncio
    async def test_byok_wrapped_key_unwraps_via_kek_lookup(self):
        """A byok1-shaped stored key triggers the KEK lookup, then the
        customer-KEK unwrap — cache-cold, both DB reads hit fetch_one."""
        kek = Fernet.generate_key().decode()
        cid = str(uuid.uuid4())
        key = generate_data_key()
        wrapped = wrap_data_key(key, kek=kek, kek_credential_id=cid)
        sealed = get_encryption_service().encrypt(json.dumps({"kek": kek}))
        # wrap_data_key warms the KEK cache; clear it to exercise the
        # cold path (e.g. post-restart replay).
        event_crypto._kek_cache.clear()

        with patch(
            "app.event_crypto.fetch_one", new_callable=AsyncMock,
            side_effect=[
                {"data_key": wrapped},          # sessions.data_key
                {"encrypted_secrets": sealed},  # credentials KEK lookup
            ],
        ) as mock_fetch:
            got = await get_session_data_key(uuid.UUID(int=3))

        assert got == key
        assert mock_fetch.await_count == 2
        assert event_crypto._data_key_cache[uuid.UUID(int=3)] == key

    @pytest.mark.asyncio
    async def test_byok_wrapped_key_uses_warm_kek_cache(self):
        """After session creation the KEK cache is pre-warmed — the
        unwrap needs only the sessions.data_key read."""
        kek = Fernet.generate_key().decode()
        key = generate_data_key()
        wrapped = wrap_data_key(
            key, kek=kek, kek_credential_id=str(uuid.uuid4())
        )

        with patch(
            "app.event_crypto.fetch_one", new_callable=AsyncMock,
            return_value={"data_key": wrapped},
        ) as mock_fetch:
            got = await get_session_data_key(uuid.UUID(int=4))

        assert got == key
        assert mock_fetch.await_count == 1
