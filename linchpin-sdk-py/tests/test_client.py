"""Tests for LinchpinClient with a fake transport (no real network)."""

import io
import json

import pytest

from linchpin_sdk.client import HttpResponse, LinchpinClient
from linchpin_sdk.errors import (
    APIError,
    NetworkError,
    NotFoundError,
    ServerError,
)

BASE = "http://localhost:8000"

SESSION = {
    "id": "s-1",
    "agent_id": "a-1",
    "agent_version": 1,
    "environment_id": "e-1",
    "status": "running",
}


class CallRecorder:
    def __init__(self):
        self.calls = []

    @property
    def last(self):
        return self.calls[-1]

    def urls_and_methods(self):
        return [(call["url"], call["method"]) for call in self.calls]


def make_client(handler):
    recorder = CallRecorder()

    def transport(method, url, headers, body, timeout):
        recorder.calls.append(
            {
                "method": method,
                "url": url,
                "headers": dict(headers or {}),
                "body": body,
                "timeout": timeout,
            }
        )
        return handler(method, url, headers, body, timeout)

    client = LinchpinClient(base_url=BASE, api_key="test-key", transport=transport)
    return client, recorder


def json_response(payload, status=200):
    body = json.dumps(payload).encode("utf-8")
    return HttpResponse(status, {"content-type": "application/json"}, io.BytesIO(body))


def text_response(text, status=200):
    return HttpResponse(status, {}, io.BytesIO(text.encode("utf-8")))


def sse_response(frames):
    return HttpResponse(
        200, {"content-type": "text/event-stream"}, io.BytesIO("".join(frames).encode("utf-8"))
    )


def ndjson_response(lines):
    return HttpResponse(200, {}, io.BytesIO(("\n".join(lines) + "\n").encode("utf-8")))


class TestTransport:
    def test_sends_the_bearer_token_and_a_json_body_on_create(self):
        client, recorder = make_client(lambda *args: json_response(SESSION, status=201))

        session = client.sessions.create(
            {
                "agent_id": "a-1",
                "environment_id": "e-1",
                "title": "hello",
                "metadata": {"k": "v"},
            }
        )

        assert session["id"] == "s-1"
        call = recorder.last
        assert call["url"] == f"{BASE}/v1/sessions"
        assert call["method"] == "POST"
        assert call["headers"]["Authorization"] == "Bearer test-key"
        assert call["headers"]["Content-Type"] == "application/json"
        assert json.loads(call["body"]) == {
            "agent_id": "a-1",
            "environment_id": "e-1",
            "title": "hello",
            "metadata": {"k": "v"},
        }

    def test_normalizes_a_trailing_slash_on_base_url(self):
        client, recorder = make_client(lambda *args: json_response(SESSION))
        client.sessions.get("s-1")
        assert recorder.last["url"] == f"{BASE}/v1/sessions/s-1"

    def test_serializes_list_query_params_and_omits_none(self):
        client, recorder = make_client(
            lambda *args: json_response({"data": [], "next_cursor": None, "has_more": False})
        )

        client.sessions.list({"limit": 10, "offset": 5})
        assert recorder.calls[0]["url"] == f"{BASE}/v1/sessions?limit=10&offset=5"

        client.usage.get({"from_": "2026-01-01", "to": None, "group_by": "day"})
        assert recorder.calls[1]["url"] == f"{BASE}/v1/usage?from=2026-01-01&group_by=day"

    def test_lowercases_boolean_query_values(self):
        client, recorder = make_client(lambda *args: json_response({}))
        client.request("GET", "/v1/nothing", {"query": {"flag": True, "other": False}})
        assert recorder.last["url"] == f"{BASE}/v1/nothing?flag=true&other=false"

    def test_url_encodes_path_segments_and_query_values(self):
        client, recorder = make_client(lambda *args: json_response(SESSION))
        client.sessions.get("a b/c")
        assert recorder.last["url"] == f"{BASE}/v1/sessions/a%20b%2Fc"

    def test_returns_none_for_an_empty_body(self):
        client, _ = make_client(lambda *args: HttpResponse(204, {}, io.BytesIO(b"")))
        assert client.request("GET", "/v1/nothing") is None

    def test_throws_typed_errors_from_the_unified_envelope(self):
        client, _ = make_client(
            lambda *args: json_response(
                {"error": "not_found", "message": "Session x not found", "detail": None},
                status=404,
            )
        )
        with pytest.raises(NotFoundError) as excinfo:
            client.sessions.get("x")
        assert excinfo.value.status == 404
        assert excinfo.value.code == "not_found"
        assert excinfo.value.message == "Session x not found"

    def test_falls_back_to_server_error_for_non_json_error_bodies(self):
        client, _ = make_client(lambda *args: text_response("boom", status=500))
        with pytest.raises(ServerError) as excinfo:
            client.sessions.get("x")
        assert excinfo.value.message == "boom"

    def test_falls_back_to_api_error_for_unmapped_statuses(self):
        client, _ = make_client(lambda *args: text_response("weird", status=418))
        with pytest.raises(APIError):
            client.sessions.get("x")

    def test_wraps_transport_failures_in_network_error(self):
        client, _ = make_client(
            lambda *args: (_ for _ in ()).throw(OSError("fetch failed"))
        )
        with pytest.raises(NetworkError) as excinfo:
            client.sessions.get("x")
        assert isinstance(excinfo.value.__cause__, OSError)

    def test_does_not_rewrap_linchpin_errors_from_transports(self):
        marker = NetworkError(OSError("already wrapped"))

        def transport(*args):
            raise marker

        client = LinchpinClient(base_url=BASE, api_key="k", transport=transport)
        with pytest.raises(NetworkError) as excinfo:
            client.sessions.get("x")
        assert excinfo.value is marker

    def test_rejects_a_client_with_missing_base_url_or_api_key(self):
        with pytest.raises(TypeError):
            LinchpinClient(base_url="", api_key="k")
        with pytest.raises(TypeError):
            LinchpinClient(base_url=BASE, api_key="")

    def test_invalid_json_success_body_raises_invalid_response(self):
        client, _ = make_client(lambda *args: text_response("<html>not json</html>"))
        with pytest.raises(APIError) as excinfo:
            client.sessions.get("x")
        assert excinfo.value.code == "invalid_response"
        assert excinfo.value.status == 200


class TestSessionsResource:
    def test_maps_get_update_delete_archive_onto_the_right_verbs(self):
        client, recorder = make_client(lambda *args: json_response(SESSION))

        client.sessions.get("s-1")
        client.sessions.update("s-1", {"title": "new"})
        client.sessions.delete("s-1")
        client.sessions.archive("s-1")

        assert recorder.urls_and_methods() == [
            (f"{BASE}/v1/sessions/s-1", "GET"),
            (f"{BASE}/v1/sessions/s-1", "POST"),
            (f"{BASE}/v1/sessions/s-1", "DELETE"),
            (f"{BASE}/v1/sessions/s-1/archive", "POST"),
        ]

    def test_posts_event_batches_inside_an_events_envelope(self):
        client, recorder = make_client(lambda *args: json_response([]))

        client.sessions.post_events("s-1", [{"type": "user.message", "payload": {"text": "hi"}}])

        call = recorder.last
        assert call["url"] == f"{BASE}/v1/sessions/s-1/events"
        assert call["method"] == "POST"
        assert json.loads(call["body"]) == {
            "events": [{"type": "user.message", "payload": {"text": "hi"}}]
        }

    def test_spawns_threads_under_threads(self):
        client, recorder = make_client(
            lambda *args: json_response({**SESSION, "parent_session_id": "p-1"}, status=201)
        )

        client.sessions.spawn_thread("p-1", {"agent_id": "worker", "input_message": "go dig"})

        call = recorder.last
        assert call["url"] == f"{BASE}/v1/sessions/p-1/threads"
        assert call["method"] == "POST"
        assert json.loads(call["body"]) == {"agent_id": "worker", "input_message": "go dig"}

    def test_lists_threads(self):
        client, recorder = make_client(lambda *args: json_response({"data": [SESSION]}))
        client.sessions.list_threads("p-1")
        assert recorder.last["url"] == f"{BASE}/v1/sessions/p-1/threads"
        assert recorder.last["method"] == "GET"

    def test_lists_events_with_params(self):
        client, recorder = make_client(lambda *args: json_response({"events": []}))
        client.sessions.list_events("s-1", {"after_cursor": "c-1", "limit": 5})
        assert recorder.last["url"] == f"{BASE}/v1/sessions/s-1/events?after_cursor=c-1&limit=5"


class TestOtherResources:
    def test_creates_pauses_updates_and_deletes_deployments(self):
        client, recorder = make_client(lambda *args: json_response({"id": "d-1"}))

        client.deployments.create({"agent_id": "a", "environment_id": "e", "cron": "0 9 * * *"})
        client.deployments.pause("d-1")
        client.deployments.update("d-1", {"cron": "30 9 * * *"})
        client.deployments.delete("d-1")

        assert recorder.urls_and_methods() == [
            (f"{BASE}/v1/deployments", "POST"),
            (f"{BASE}/v1/deployments/d-1/pause", "POST"),
            (f"{BASE}/v1/deployments/d-1", "PATCH"),
            (f"{BASE}/v1/deployments/d-1", "DELETE"),
        ]

    def test_gets_and_resumes_deployments(self):
        client, recorder = make_client(lambda *args: json_response({"id": "d-1"}))
        client.deployments.get("d-1")
        client.deployments.resume("d-1")
        client.deployments.list({"limit": 3})
        assert recorder.urls_and_methods() == [
            (f"{BASE}/v1/deployments/d-1", "GET"),
            (f"{BASE}/v1/deployments/d-1/resume", "POST"),
            (f"{BASE}/v1/deployments?limit=3", "GET"),
        ]

    def test_fetches_artifact_metadata_and_raw_content(self):
        def handler(method, url, headers, body, timeout):
            if url.endswith("/content"):
                return text_response("file-contents")
            return json_response({"id": "f-1", "path": "/tmp/out.txt"})

        client, recorder = make_client(handler)

        artifact = client.artifacts.get("f-1")
        content = client.artifacts.content("f-1")

        assert artifact["id"] == "f-1"
        assert content == "file-contents"
        assert recorder.calls[1]["url"] == f"{BASE}/v1/artifacts/f-1/content"

    def test_lists_artifacts_filtered_by_session(self):
        client, recorder = make_client(lambda *args: json_response({"data": []}))
        client.artifacts.list({"session_id": "s-1"})
        assert recorder.last["url"] == f"{BASE}/v1/artifacts?session_id=s-1"

    def test_gets_usage_with_group_by(self):
        client, recorder = make_client(
            lambda *args: json_response(
                {
                    "group_by": "day",
                    "from": None,
                    "to": None,
                    "total": {"bucket": "total", "sessions": 3, "input_tokens": 10},
                    "buckets": [],
                }
            )
        )
        response = client.usage.get({"group_by": "day"})
        assert response["group_by"] == "day"
        assert recorder.last["url"] == f"{BASE}/v1/usage?group_by=day"

    def test_usage_from_alias_maps_to_from_query(self):
        client, recorder = make_client(lambda *args: json_response({"group_by": "day"}))
        client.usage.get({"from_": "2026-01-01T00:00:00Z", "to": "2026-01-31T23:59:59Z"})
        url = recorder.last["url"]
        assert "from=2026-01-01T00%3A00%3A00Z" in url
        assert "to=2026-01-31T23%3A59%3A59Z" in url

    def test_streams_the_audit_log_as_parsed_ndjson_records(self):
        client, _ = make_client(
            lambda *args: ndjson_response(
                [
                    '{"action":"session.create","resource_type":"session"}',
                    '{"action":"session.archive","resource_type":"session"}',
                ]
            )
        )

        actions = [
            entry["action"]
            for entry in client.audit.export({"resource_type": "session"})
        ]
        assert actions == ["session.create", "session.archive"]

    def test_audit_from_alias_maps_to_from_query(self):
        client, recorder = make_client(lambda *args: ndjson_response([]))
        list(client.audit.export({"from_": "2026-01-01", "action": "session.create"}))
        assert "from=2026-01-01" in recorder.last["url"]
        assert "action=session.create" in recorder.last["url"]


class TestSseStreaming:
    def test_parses_stream_frames_and_sends_last_event_id_for_resume(self):
        client, recorder = make_client(
            lambda *args: sse_response(
                [
                    'id: 1\nevent: session.message\ndata: {"seq":1,"type":"session.message","payload":{"text":"hi"}}\n\n',
                    'id: 2\nevent: session.status\ndata: {"seq":2,"type":"session.status"}\n\n',
                ]
            )
        )

        events = list(client.sessions.stream("s-1", {"last_event_id": "0"}))

        assert len(events) == 2
        assert events[0].id == "1"
        assert events[0].event == "session.message"
        assert events[0].payload["seq"] == 1
        assert events[1].payload["type"] == "session.status"

        call = recorder.last
        assert call["url"] == f"{BASE}/v1/sessions/s-1/stream"
        assert call["headers"]["Last-Event-ID"] == "0"

    def test_passes_cursor_as_a_query_param_when_no_last_event_id_is_given(self):
        client, recorder = make_client(lambda *args: sse_response([]))

        events = list(client.sessions.stream("s-1", {"cursor": "c-1"}))

        assert events == []
        assert recorder.last["url"] == f"{BASE}/v1/sessions/s-1/stream?cursor=c-1"

    def test_omits_cursor_query_when_absent(self):
        client, recorder = make_client(lambda *args: sse_response([]))
        list(client.sessions.stream("s-1"))
        assert recorder.last["url"] == f"{BASE}/v1/sessions/s-1/stream"

    def test_tolerates_non_json_stream_payloads(self):
        client, _ = make_client(
            lambda *args: sse_response(['id: 1\nevent: session.ping\ndata: pong\n\n'])
        )
        events = list(client.sessions.stream("s-1"))
        assert len(events) == 1
        assert events[0].payload is None
        assert events[0].data == "pong"

    def test_raises_not_found_error_when_the_stream_endpoint_404s(self):
        client, _ = make_client(
            lambda *args: json_response(
                {"error": "not_found", "message": "nope"}, status=404
            )
        )
        with pytest.raises(NotFoundError):
            next(iter(client.sessions.stream("missing")))

    def test_generic_client_stream_consumes_sse(self):
        client, recorder = make_client(
            lambda *args: sse_response(['id: 9\ndata: {"x":1}\n\n'])
        )
        frames = list(client.stream("/v1/sessions/s-1/stream"))
        assert frames[0].id == "9"
        assert recorder.last["url"] == f"{BASE}/v1/sessions/s-1/stream"
