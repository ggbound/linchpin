"""Tests for the error hierarchy and envelope extraction (FR-12.1 / §6.4)."""

import pytest

from linchpin_sdk.errors import (
    APIError,
    AuthenticationError,
    ConflictError,
    LinchpinError,
    NetworkError,
    NotFoundError,
    PermissionDeniedError,
    QuotaExceededError,
    RateLimitedError,
    ServerError,
    ValidationError,
    error_from_status,
)


@pytest.mark.parametrize(
    ("status", "expected"),
    [
        (401, AuthenticationError),
        (402, QuotaExceededError),
        (403, PermissionDeniedError),
        (404, NotFoundError),
        (409, ConflictError),
        (422, ValidationError),
        (429, RateLimitedError),
        (500, ServerError),
        (503, ServerError),
        (418, APIError),
    ],
)
def test_error_from_status_mapping(status, expected):
    error = error_from_status(status, None)
    assert isinstance(error, expected)
    assert isinstance(error, LinchpinError)
    assert isinstance(error, Exception)
    assert error.status == status


class TestEnvelopeExtraction:
    def test_reads_the_unified_envelope(self):
        error = error_from_status(
            404, {"error": "not_found", "message": "Session abc not found", "detail": None}
        )
        assert error.status == 404
        assert error.code == "not_found"
        assert error.message == "Session abc not found"
        assert error.detail is None

    def test_keeps_a_structured_detail(self):
        detail = {"conflict": "environment_in_use"}
        error = error_from_status(409, {"error": "conflict", "message": "busy", "detail": detail})
        assert error.detail == detail

    def test_unified_envelope_with_non_string_error_falls_back(self):
        error = error_from_status(400, {"error": 42, "message": "bad input"})
        assert error.code == "unknown_error"
        assert error.message == "bad input"

    def test_unified_envelope_message_only(self):
        error = error_from_status(400, {"message": "bad input"})
        assert error.code == "unknown_error"
        assert error.message == "bad input"

    def test_falls_back_to_fastapi_legacy_detail_string_shape(self):
        error = error_from_status(422, {"detail": "Validation failed"})
        assert error.code == "unknown_error"
        assert error.message == "Validation failed"
        assert error.detail == "Validation failed"

    def test_summarizes_fastapi_pydantic_detail_arrays(self):
        error = error_from_status(
            422,
            {"detail": [{"loc": ["body", "agent_id"], "msg": "field required"}]},
        )
        assert "field required" in error.message

    def test_joins_multiple_pydantic_msgs_with_semicolons(self):
        error = error_from_status(
            422,
            {
                "detail": [
                    {"msg": "field required"},
                    {"msg": "invalid cron"},
                ]
            },
        )
        assert error.message == "field required; invalid cron"

    def test_uses_a_raw_text_body_as_the_message(self):
        error = error_from_status(500, "boom")
        assert isinstance(error, ServerError)
        assert error.message == "boom"
        assert error.detail is None

    def test_never_throws_on_null_bodies(self):
        error = error_from_status(502, None)
        assert error.message == "Request failed"
        assert error.code == "unknown_error"

    def test_blank_text_body_uses_the_fallback_message(self):
        error = error_from_status(500, "   ")
        assert error.message == "Request failed"

    def test_default_message_when_message_is_not_a_string(self):
        error = error_from_status(404, {"error": "not_found", "message": {"x": 1}})
        assert error.code == "not_found"
        assert error.message == "Request failed"


class TestNetworkError:
    def test_preserves_the_cause_and_carries_status_0(self):
        cause = OSError("connection refused")
        error = NetworkError(cause)
        assert error.status == 0
        assert error.code == "network_error"
        assert "connection refused" in error.message
        assert error.__cause__ is cause
        assert isinstance(error, LinchpinError)

    def test_describe_falls_back_to_str(self):
        error = NetworkError("disk on fire")
        assert "disk on fire" in error.message
