"""Tests for the SSE parser and stream adapters (ported from the TS SDK)."""

import codecs
import io

from linchpin_sdk.client import HttpResponse
from linchpin_sdk.sse import SseParser, stream_ndjson, stream_sse
from linchpin_sdk.types import SseEvent


def parse(*chunks):
    """Shorthand: feed chunks sequentially, collect all frames."""
    parser = SseParser()
    events = []
    for chunk in chunks:
        events.extend(parser.push(chunk))
    events.extend(parser.flush())
    return events


def fake_response(*chunks):
    return HttpResponse(200, {}, io.BytesIO(b"".join(chunks)))


class TestSseParser:
    def test_parses_a_basic_frame_with_id_event_data(self):
        events = parse('id: 7\nevent: session.message\ndata: {"seq":7}\n\n')
        assert events == [SseEvent(event="session.message", data='{"seq":7}', id="7")]

    def test_handles_crlf_terminators(self):
        events = parse("id: 1\r\nevent: ping\r\ndata: hi\r\n\r\n")
        assert events == [SseEvent(event="ping", data="hi", id="1")]

    def test_handles_bare_cr_terminators(self):
        events = parse("event: a\rdata: x\r\r")
        assert events == [SseEvent(event="a", data="x", id=None)]

    def test_joins_multi_line_data_with_newline(self):
        events = parse("data: line1\ndata: line2\n\n")
        assert events == [SseEvent(event="message", data="line1\nline2")]

    def test_ignores_comment_and_keep_alive_lines(self):
        events = parse(": keep-alive\ndata: real\n\n")
        assert events == [SseEvent(event="message", data="real")]

    def test_accepts_fields_without_a_space_after_the_colon(self):
        events = parse('data:{"a":1}\n\n')
        assert events == [SseEvent(event="message", data='{"a":1}')]

    def test_defaults_the_event_type_to_message(self):
        events = parse("data: x\n\n")
        assert events[0].event == "message"

    def test_carries_the_last_event_id_across_frames(self):
        events = parse("id: 1\ndata: a\n\ndata: b\n\n")
        assert events == [
            SseEvent(event="message", data="a", id="1"),
            SseEvent(event="message", data="b", id="1"),
        ]

    def test_clears_the_event_id_on_an_empty_id_field(self):
        events = parse("id: 1\ndata: a\n\nid:\ndata: b\n\n")
        assert events[0].id == "1"
        assert events[1].id is None

    def test_strips_a_leading_bom(self):
        events = parse("\ufeffdata: x\n\n")
        assert events == [SseEvent(event="message", data="x")]

    def test_reassembles_a_frame_split_across_chunks_mid_line(self):
        events = parse("dat", "a: hel", "lo\n", "\n")
        assert events == [SseEvent(event="message", data="hello")]

    def test_treats_a_crlf_pair_split_across_chunks_as_one_terminator(self):
        # The \r ends chunk 1; the \n opens chunk 2. Counting them as two
        # terminators would dispatch "a" early — per spec both data lines
        # belong to the same (still unfinished) event.
        events = parse("data: a\r", "\ndata: b\n\n")
        assert events == [SseEvent(event="message", data="a\nb")]

    def test_does_not_dispatch_frames_with_an_empty_data_buffer(self):
        events = parse("event: ping\n\nid: 3\n\n")
        assert events == []

    def test_flushes_a_final_unterminated_line_at_eof(self):
        parser = SseParser()
        assert parser.push("data: tail") == []
        assert parser.flush() == [SseEvent(event="message", data="tail")]

    def test_ignores_unknown_fields_such_as_retry(self):
        events = parse("retry: 5000\ndata: x\n\n")
        assert events == [SseEvent(event="message", data="x")]


class TestStreamSse:
    def test_streams_frames_from_a_response_body(self):
        response = fake_response(
            b'id: 1\ndata: {"seq":1}\n\n', b'id: 2\ndata: {"seq":2}\n\n'
        )
        events = list(stream_sse(response))
        assert [event.id for event in events] == ["1", "2"]
        assert _load_json(events[0].data) == {"seq": 1}

    def test_decodes_utf8_multibyte_split_across_chunks(self):
        # "é" is b"\xc3\xa9" — split the pair across two chunks.
        response = fake_response(b"data: \xc3", b"\xa9\n\n")
        events = list(stream_sse(response))
        assert events == [SseEvent(event="message", data="é")]

    def test_strips_a_bom_delivered_as_bytes(self):
        response = fake_response(b"\xef\xbb\xbfdata: x\n\n")
        events = list(stream_sse(response))
        assert events == [SseEvent(event="message", data="x")]

    def test_closes_the_response_after_the_stream_ends(self):
        stream = io.BytesIO(b"data: x\n\n")
        response = HttpResponse(200, {}, stream)
        list(stream_sse(response))
        assert stream.closed

    def test_raises_no_stream_body_without_iter_bytes(self):
        import pytest

        from linchpin_sdk.errors import LinchpinError

        with pytest.raises(LinchpinError) as excinfo:
            list(stream_sse(None))
        assert excinfo.value.code == "no_stream_body"


class TestStreamNdjson:
    def test_streams_parsed_records_including_a_final_unterminated_line(self):
        response = fake_response(b'{"action":"a"}\n{"action":"b"}\n{"action":"c"}')
        records = list(stream_ndjson(response))
        assert records == [{"action": "a"}, {"action": "b"}, {"action": "c"}]

    def test_skips_blank_lines(self):
        response = fake_response(b'{"a":1}\n\n{"b":2}\n')
        assert list(stream_ndjson(response)) == [{"a": 1}, {"b": 2}]

    def test_reassembles_records_split_across_chunks(self):
        response = fake_response(b'{"ac', b'tion":"x"}\n{"action":"y"}\n')
        assert list(stream_ndjson(response)) == [{"action": "x"}, {"action": "y"}]

    def test_decodes_utf8_multibyte_split_across_chunks(self):
        response = fake_response(b'{"text":"\xc3', b'\xa9"}\n')
        assert list(stream_ndjson(response)) == [{"text": "é"}]


def _load_json(text):
    import json

    return json.loads(text)


def test_incremental_decoder_replaces_bad_bytes():
    decoder = codecs.getincrementaldecoder("utf-8")("replace")
    assert decoder.decode(b"\xff") == "\ufffd"
