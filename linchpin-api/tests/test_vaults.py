"""Atlas M6 BYOK — vault registration face for customer-managed KEKs.

Validates: credential_type "byok_kek" accepts a Fernet key-encryption-key
(the customer-key registration half of the B8 spec), stores it encrypted
at rest via the vault master key, rejects non-Fernet input at the model
boundary (422), and the PATCH flow rotates the stored KEK. Secrets never
appear in API responses.
"""

from __future__ import annotations

import json
import uuid
from datetime import datetime, timezone
from unittest.mock import AsyncMock, patch

import pytest
from cryptography.fernet import Fernet

from app.encryption import get_encryption_service

AUTH = {"Authorization": "Bearer test-secret-key"}
DT = datetime(2025, 6, 1, tzinfo=timezone.utc)
DT2 = datetime(2025, 6, 2, tzinfo=timezone.utc)


@pytest.fixture(autouse=True)
def _set_encryption_key(monkeypatch):
    """Valid Fernet master key for the encryption service (same pattern
    as test_event_crypto.py)."""
    key = Fernet.generate_key().decode()
    monkeypatch.setenv("VAULT_ENCRYPTION_KEY", key)
    from app.encryption import reset_encryption_service

    reset_encryption_service()
    yield
    reset_encryption_service()


def _vault_row(vault_id: str):
    return {
        "id": uuid.UUID(vault_id),
        "project_id": uuid.uuid4(),
        "display_name": "customer-keys",
        "metadata": "{}",
        "created_at": DT,
        "updated_at": DT,
        "archived_at": None,
    }


def _cred_row(cred_id: str, vault_id: str, updated_at=None):
    return {
        "id": uuid.UUID(cred_id),
        "vault_id": uuid.UUID(vault_id),
        "credential_type": "byok_kek",
        "mcp_server_url": None,
        "provider": None,
        "client_id": None,
        "created_at": DT,
        "updated_at": updated_at or DT,
        "archived_at": None,
    }


@patch("app.routes.vaults.fetch_one", new_callable=AsyncMock)
def test_create_byok_kek_credential_encrypts_kek(mock_fetch, client):
    """byok_kek credential stores an encrypted {"kek": ...} blob; the
    response never exposes secrets."""
    vault_id = str(uuid.uuid4())
    kek = Fernet.generate_key().decode()
    cred_id = str(uuid.uuid4())

    mock_fetch.side_effect = [
        _vault_row(vault_id),           # _get_vault_or_404
        {"cnt": 0},                     # 20-credential limit check
        _cred_row(cred_id, vault_id),   # INSERT ... RETURNING *
    ]

    resp = client.post(
        f"/v1/vaults/{vault_id}/credentials",
        json={"credential_type": "byok_kek", "kek": kek},
        headers=AUTH,
    )

    assert resp.status_code == 201
    body = resp.json()
    assert body["id"] == cred_id
    assert body["credential_type"] == "byok_kek"
    assert "encrypted_secrets" not in body  # secrets omitted from responses

    sql, *params = mock_fetch.call_args_list[2].args
    assert "INSERT INTO credentials" in sql
    assert params[2] == "byok_kek"
    assert params[3] is None  # no mcp_server_url on a KEK credential
    assert json.loads(get_encryption_service().decrypt(params[5])) == {"kek": kek}


def test_create_byok_kek_rejects_non_fernet_key(client):
    """A non-Fernet kek is rejected at the model boundary (422) — before
    any DB round-trip or encryption."""
    vault_id = str(uuid.uuid4())
    resp = client.post(
        f"/v1/vaults/{vault_id}/credentials",
        json={"credential_type": "byok_kek", "kek": "not-a-fernet-key"},
        headers=AUTH,
    )
    assert resp.status_code == 422


@patch("app.routes.vaults.fetch_one", new_callable=AsyncMock)
def test_update_byok_kek_rotates_stored_key(mock_fetch, client):
    """PATCH with a new kek re-encrypts and stores {"kek": <new>}."""
    vault_id = str(uuid.uuid4())
    cred_id = str(uuid.uuid4())
    new_kek = Fernet.generate_key().decode()

    mock_fetch.side_effect = [
        _vault_row(vault_id),                          # _get_vault_or_404
        _cred_row(cred_id, vault_id),                  # SELECT credential
        _cred_row(cred_id, vault_id, updated_at=DT2),  # UPDATE RETURNING *
    ]

    resp = client.patch(
        f"/v1/vaults/{vault_id}/credentials/{cred_id}",
        json={"kek": new_kek},
        headers=AUTH,
    )

    assert resp.status_code == 200
    assert resp.json()["id"] == cred_id

    sql, *params = mock_fetch.call_args_list[2].args
    assert "UPDATE credentials" in sql
    assert json.loads(get_encryption_service().decrypt(params[0])) == {"kek": new_kek}


@patch("app.routes.vaults.fetch_one", new_callable=AsyncMock)
def test_update_credential_empty_body_skips_update(mock_fetch, client):
    """PATCH with no secret fields returns current state and never
    rewrites encrypted_secrets."""
    vault_id = str(uuid.uuid4())
    cred_id = str(uuid.uuid4())

    mock_fetch.side_effect = [
        _vault_row(vault_id),
        _cred_row(cred_id, vault_id),
    ]

    resp = client.patch(
        f"/v1/vaults/{vault_id}/credentials/{cred_id}",
        json={},
        headers=AUTH,
    )

    assert resp.status_code == 200
    assert mock_fetch.await_count == 2  # vault + credential lookups only
