"""Incremental SSE frame parser + response-body stream adapter (Atlas A3).

Implements the WHATWG server-sent-events wire format without any
dependency: ``id`` / ``event`` / ``data`` fields, ``\\r\\n`` / ``\\r`` /
``\\n`` terminators, ``:`` comments, multi-line ``data`` joined with
``\\n``, a leading BOM, and chunk boundaries split mid-line (including a
``\\r\\n`` pair split across two chunks).

The stream helpers accept any response object exposing ``iter_bytes()``
(the SDK's ``HttpResponse``); they duck-type it so tests can feed fakes.
"""

from __future__ import annotations

import codecs
import json
import re
from typing import Any, Iterator, List, Optional

from .errors import LinchpinError
from .types import SseEvent

__all__ = ["SseParser", "stream_sse", "stream_ndjson"]

_LINE_SPLIT = re.compile(r"\r\n|\r|\n")


class SseParser:
    """Push-parser for the WHATWG server-sent-events wire format."""

    def __init__(self) -> None:
        self._pending = ""
        self._last_event_id: Optional[str] = None
        self._event_type = ""
        self._data_lines: List[str] = []
        self._trailing_cr = False
        self._seen_first_chunk = False

    def push(self, chunk: str) -> List[SseEvent]:
        """Feed one text chunk; returns every complete frame it terminated."""
        if not self._seen_first_chunk:
            self._seen_first_chunk = True
            if chunk.startswith("\ufeff"):
                chunk = chunk[1:]

        # A \r\n pair split across chunks must stay ONE terminator.
        if self._trailing_cr:
            self._trailing_cr = False
            if chunk.startswith("\n"):
                chunk = chunk[1:]
        self._trailing_cr = chunk.endswith("\r")

        events: List[SseEvent] = []
        lines = _LINE_SPLIT.split(self._pending + chunk)
        # The last element is the (possibly empty) unfinished line.
        self._pending = lines.pop() if lines else ""

        for line in lines:
            self._process_line(line, events)
        return events

    def flush(self) -> List[SseEvent]:
        """Flush at EOF: a final unterminated line still counts, then dispatch."""
        events: List[SseEvent] = []
        if self._pending != "":
            line = self._pending
            self._pending = ""
            self._process_line(line, events)
        self._dispatch(events)
        return events

    def _process_line(self, line: str, out: List[SseEvent]) -> None:
        # Blank line -> dispatch whatever the buffered fields describe.
        if line == "":
            self._dispatch(out)
            return
        if line.startswith(":"):
            return  # comment / keep-alive

        colon = line.find(":")
        if colon == -1:
            field, value = line, ""
        else:
            field, value = line[:colon], line[colon + 1:]
        if value.startswith(" "):
            value = value[1:]

        if field == "event":
            self._event_type = value
        elif field == "data":
            self._data_lines.append(value)
        elif field == "id":
            # Spec: an empty (NUL-free) id clears the last-event-id buffer.
            if "\0" not in value:
                self._last_event_id = None if value == "" else value
        # unknown fields (e.g. retry) are ignored

    def _dispatch(self, out: List[SseEvent]) -> None:
        data = "\n".join(self._data_lines)
        self._data_lines = []
        event = self._event_type or "message"
        self._event_type = ""
        if data == "":
            # Spec: an event with an empty data buffer is not dispatched.
            return
        out.append(SseEvent(event=event, data=data, id=self._last_event_id))


def _incremental_decoder() -> Any:
    return codecs.getincrementaldecoder("utf-8")("replace")


def _ensure_body(response: Any) -> None:
    if response is None or not hasattr(response, "iter_bytes"):
        raise LinchpinError(0, "no_stream_body", "Response has no body to stream", None)


def _close(response: Any) -> None:
    close = getattr(response, "close", None)
    if close is not None:
        close()


def stream_sse(response: Any) -> Iterator[SseEvent]:
    """Consume a response body as a stream of parsed SSE frames."""
    _ensure_body(response)
    parser = SseParser()
    decoder = _incremental_decoder()
    try:
        for chunk in response.iter_bytes():
            text = decoder.decode(chunk)
            if text:
                yield from parser.push(text)
        tail = decoder.decode(b"", final=True)
        if tail:
            yield from parser.push(tail)
        yield from parser.flush()
    finally:
        _close(response)


def stream_ndjson(response: Any) -> Iterator[Any]:
    """Consume a response body as newline-delimited JSON records."""
    _ensure_body(response)
    decoder = _incremental_decoder()
    buffer = ""
    try:
        for chunk in response.iter_bytes():
            buffer += decoder.decode(chunk)
            while True:
                newline = buffer.find("\n")
                if newline == -1:
                    break
                line = buffer[:newline].strip()
                buffer = buffer[newline + 1:]
                if line != "":
                    yield json.loads(line)
        tail = buffer.strip()
        if tail:
            yield json.loads(tail)
    finally:
        _close(response)
