"""Turn entries back into file text, changing as little of the original file as possible.

Frontmatter is edited with ruamel.yaml in round-trip mode, so key order, comments, quote
style, and flow style (``{ ... }`` items) survive. New values are *merged* into the loaded
document instead of replacing it, so untouched values keep their exact formatting. The body
is never re-generated: it is written back verbatim (or replaced wholesale by an edit).

Every write path in the app goes through :func:`update_entry_text`, so a no-op edit always
produces a byte-identical file.
"""

from __future__ import annotations

import datetime as _dt
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from decimal import Decimal
from io import StringIO
from typing import Any

from ruamel.yaml import YAML
from ruamel.yaml.comments import CommentedMap, CommentedSeq
from ruamel.yaml.representer import RoundTripRepresenter
from ruamel.yaml.scalarstring import DoubleQuotedScalarString, SingleQuotedScalarString

FENCE = "---"


class SerializeError(ValueError):
    """The file cannot be edited safely (no frontmatter, or invalid YAML)."""


def _represent_decimal(representer: RoundTripRepresenter, value: Decimal) -> Any:
    return representer.represent_scalar("tag:yaml.org,2002:float", format(value, "f"))


def _yaml() -> YAML:
    yaml = YAML()  # round-trip
    yaml.preserve_quotes = True
    yaml.width = 4096  # never re-wrap long lines
    yaml.indent(mapping=2, sequence=4, offset=2)
    yaml.representer.add_representer(Decimal, _represent_decimal)
    return yaml


@dataclass
class EntryText:
    """An entry file split into its raw parts. ``compose()`` rebuilds the exact original."""

    frontmatter: str
    body: str
    newline: str = "\n"

    @classmethod
    def parse(cls, text: str) -> EntryText:
        newline = "\r\n" if "\r\n" in text else "\n"
        normalized = text.replace("\r\n", "\n")
        lines = normalized.split("\n")
        if not lines or lines[0].strip() != FENCE:
            raise SerializeError("file has no '---' frontmatter block")
        for index in range(1, len(lines)):
            if lines[index].strip() in (FENCE, "..."):
                front = "".join(line + "\n" for line in lines[1:index])
                body = "\n".join(lines[index + 1 :])
                return cls(front, body, newline)
        raise SerializeError("frontmatter block is never closed with '---'")

    @property
    def body_line(self) -> int:
        """1-based file line number of the body's first line."""
        return self.frontmatter.count("\n") + 3

    def compose(self) -> str:
        text = f"{FENCE}\n{self.frontmatter}{FENCE}\n{self.body}"
        return text.replace("\n", self.newline) if self.newline != "\n" else text

    # -- frontmatter ----------------------------------------------------------------------

    def load(self) -> CommentedMap:
        try:
            data = _yaml().load(self.frontmatter)
        except Exception as exc:  # ruamel raises several unrelated error types
            raise SerializeError(f"invalid YAML: {exc}") from exc
        if data is None:
            return CommentedMap()
        if not isinstance(data, CommentedMap):
            raise SerializeError("frontmatter must be a mapping")
        return data

    def dump(self, data: CommentedMap) -> None:
        stream = StringIO()
        _yaml().dump(data, stream)
        self.frontmatter = stream.getvalue()


# -- merging new values into loaded YAML ---------------------------------------------------


def _coerce_like(old: Any, new: Any) -> Any:
    """Keep a value's YAML type when the editor sends it back as text (dates, numbers)."""
    if not isinstance(new, str) or isinstance(old, str | bool):
        return new
    try:
        if isinstance(old, _dt.date):
            return _dt.date.fromisoformat(new.strip())
        if isinstance(old, int):
            return int(new.strip())
        if isinstance(old, float):
            return Decimal(new.strip())
    except ValueError:
        return new
    return new


def _same(old: Any, new: Any) -> bool:
    if isinstance(old, bool) or isinstance(new, bool):
        return type(old) is type(new) and old == new
    if isinstance(old, int | float | Decimal) and isinstance(new, int | float | Decimal):
        return Decimal(str(old)) == Decimal(str(new))
    if isinstance(old, str) and isinstance(new, str):
        return str(old) == new
    return type(old) is type(new) and bool(old == new)


def _flow_like(siblings: Sequence[Any]) -> bool:
    for item in siblings:
        if isinstance(item, CommentedMap | CommentedSeq):
            return bool(item.fa.flow_style())
    return False


def _fresh(value: Any, *, flow: bool = False) -> Any:
    """A plain Python value as round-trip YAML nodes."""
    if isinstance(value, Mapping):
        result = CommentedMap()
        for key, item in value.items():
            result[str(key)] = _fresh(item)
        if flow:
            result.fa.set_flow_style()
        return result
    if isinstance(value, list | tuple):
        seq = CommentedSeq()
        for item in value:
            seq.append(_fresh(item, flow=flow or isinstance(item, Mapping)))
        if flow:
            seq.fa.set_flow_style()
        return seq
    return value


def _replace_scalar(old: Any, new: Any) -> Any:
    new = _coerce_like(old, new)
    if _same(old, new):
        return old
    if isinstance(new, str) and isinstance(old, DoubleQuotedScalarString):
        return DoubleQuotedScalarString(new)
    if isinstance(new, str) and isinstance(old, SingleQuotedScalarString):
        return SingleQuotedScalarString(new)
    return _fresh(new)


def merge(old: Any, new: Any) -> Any:
    """Return ``new`` built from ``old``'s YAML nodes wherever the two agree.

    Mappings are updated in place (keys removed, changed, or appended), lists element by
    element; untouched scalars keep their original node, so quoting and number formats
    survive. New list items copy the flow/block style of their siblings.
    """
    if isinstance(old, CommentedMap) and isinstance(new, Mapping):
        for key in [k for k in old if k not in new]:
            del old[key]
        for key, value in new.items():
            old[str(key)] = merge(old[key], value) if key in old else _fresh(value)
        return old
    if isinstance(old, CommentedSeq) and isinstance(new, list | tuple):
        flow = _flow_like(old)
        for i, value in enumerate(new):
            if i < len(old):
                old[i] = merge(old[i], value)
            else:
                old.append(_fresh(value, flow=flow and isinstance(value, Mapping)))
        del old[len(new) :]
        return old
    if isinstance(new, Mapping | list | tuple):
        return _fresh(new)
    return _replace_scalar(old, new)


# -- public API -------------------------------------------------------------------------------


def to_plain(value: Any) -> Any:
    """Round-trip YAML nodes as plain dicts/lists/scalars (what the parser produces)."""
    if isinstance(value, Mapping):
        return {str(k): to_plain(v) for k, v in value.items()}
    if isinstance(value, list | tuple):
        return [to_plain(v) for v in value]
    if isinstance(value, str):
        return str(value)
    if isinstance(value, bool) or value is None:
        return value
    if isinstance(value, float):
        return float(value)
    if isinstance(value, int):
        return int(value)
    return value


def update_entry_text(
    text: str, *, meta: Mapping[str, Any] | None = None, body: str | None = None
) -> str:
    """Rewrite an entry file with new frontmatter values and/or a new body.

    ``meta`` is the complete new frontmatter (it is merged, so untouched values keep their
    formatting); ``body`` replaces everything after the frontmatter. Line endings follow
    the original file.
    """
    parts = EntryText.parse(text)
    if meta is not None:
        loaded = parts.load()
        if to_plain(loaded) != to_plain(meta):
            parts.dump(merge(loaded, meta))
    if body is not None:
        parts.body = body.replace("\r\n", "\n")
    return parts.compose()


def normalize_entry_text(text: str) -> str:
    """The text a first save produces: frontmatter re-dumped, body untouched."""
    parts = EntryText.parse(text)
    parts.dump(parts.load())
    return parts.compose()


def new_entry_text(meta: Mapping[str, Any], body: str = "") -> str:
    """Text for a brand-new entry file."""
    parts = EntryText("", body.replace("\r\n", "\n"))
    parts.dump(_fresh(dict(meta)))
    return parts.compose()
