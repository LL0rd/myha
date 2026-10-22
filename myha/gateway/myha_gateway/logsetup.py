"""Logging setup with a filter that keeps secrets out of the add-on log."""

import logging
import re

_FORMAT = "%(asctime)s %(levelname)s %(name)s: %(message)s"
_RULES = (
    # Must run first: in odd formats like "authorization=[Bearer x]" the key rule alone leaks x.
    (re.compile(r"(bearer\s+)\S+", re.IGNORECASE), r"\1<redacted>"),
    # key=value, key: value, key = value, quoted keys, Bearer/Basic scheme; key and scheme stay.
    # shortcut: only the first token of a value is redacted ("a=1; b=2" keeps b=2);
    # widen the value pattern once cookie headers get logged
    (
        re.compile(
            r"""((?:secret|token|password|authorization|ticket|cookie)["']?\s*[=:]\s*["']?"""
            r"(?:(?:bearer|basic)\s+)?)\S+",
            re.IGNORECASE,
        ),
        r"\1<redacted>",
    ),
    # base64url runs at least as long as a 256-bit secret from ids.new_secret().
    # shortcut: redacts any 43+ char base64url run (also long entity ids and SPKI pins);
    # revisit if logs become unreadable
    (re.compile(r"[A-Za-z0-9_-]{43,}"), "<redacted>"),
)


def _redact(text: str) -> str:
    for pattern, replacement in _RULES:
        text = pattern.sub(replacement, text)
    return text


class RedactionFilter(logging.Filter):
    """Formats message, traceback and stack once; replaces secrets with ``<redacted>``."""

    def filter(self, record: logging.LogRecord) -> bool:
        try:
            message = record.getMessage()
        except Exception:  # malformed log call: drop the args instead of raising into the caller
            message = str(record.msg)
        record.msg, record.args = _redact(message), ()
        if record.exc_info and not record.exc_text:
            # Formatter.format() reuses a cached exc_text instead of rendering exc_info again.
            record.exc_text = logging.Formatter().formatException(record.exc_info)
        record.exc_text = record.exc_text and _redact(record.exc_text)
        record.stack_info = record.stack_info and _redact(record.stack_info)
        return True


def setup_logging(level: str) -> None:
    """Route all records through one redacting stderr handler on the root logger."""
    handler = logging.StreamHandler()
    # On the handler, because logger filters never see records propagated from child loggers.
    handler.addFilter(RedactionFilter())
    logging.basicConfig(level=level.upper(), format=_FORMAT, handlers=[handler], force=True)
