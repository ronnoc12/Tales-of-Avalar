"""Edit forms generated from the entry schemas, so a form can never drift from validation.

:func:`field_specs` turns a pydantic model plus an entry's current values into a tree of
:class:`FieldSpec`. Each spec says how to edit the value (its *kind*), the choices for
fixed lists, number ranges, and whether the key was present in the file. The browser
sends back only keys that were present or now differ from the schema default, so a save
never adds default values the file did not have.

Anything the form cannot model (free-form mappings, mixed types, unknown extra keys) is
edited as a small YAML box for that one field.
"""

from __future__ import annotations

import datetime as _dt
import json
import types
import typing
from collections.abc import Mapping
from dataclasses import dataclass, field
from decimal import Decimal
from io import StringIO
from typing import Any, Literal, Union

from annotated_types import Ge, Le
from pydantic import BaseModel, BeforeValidator
from pydantic_core import PydanticUndefined
from ruamel.yaml import YAML

from chronicler.render import label_for
from chronicler.schema import (
    ALIGNMENTS,
    ENTRY_MODELS,
    LINK_FIELDS,
    LINK_LIST_FIELDS,
    Alias,
    Entry,
    check_alignment,
    to_change,
    to_score,
)
from chronicler.serialize import to_plain

Kind = Literal[
    "readonly",
    "enum",
    "score",
    "change",
    "int",
    "number",
    "decimal",
    "bool",
    "tristate",
    "text",
    "link",
    "list",
    "link-list",
    "aliases",
    "rows",
    "group",
    "yaml",
]
QUICK_KINDS = frozenset(
    {"enum", "score", "change", "int", "number", "decimal", "bool", "tristate", "text", "link"}
)
READONLY_KEYS = ("id", "type", "name")
HINTS = {
    "secret": "Hidden page: players never see this entry",
    "secret_fields": "Fields to hide from players on this entry",
    "party_view": "What the party believes, field by field",
    "status": "Only canon entries are shown to players",
}


@dataclass
class FieldSpec:
    key: str
    label: str
    kind: Kind
    value: Any = None
    present: bool = False
    default: Any = None
    options: tuple[str, ...] = ()
    minimum: float | None = None
    maximum: float | None = None
    step: str = "any"
    children: list[FieldSpec] = field(default_factory=list)  # group fields / a row template
    rows: list[list[FieldSpec]] = field(default_factory=list)  # existing rows
    hint: str = ""
    path: str = ""  # dotted path in the form, e.g. "goals.0.text"

    @property
    def quick(self) -> bool:
        return self.kind in QUICK_KINDS

    @property
    def default_json(self) -> str:
        return json.dumps(_jsonable(self.default))

    @property
    def yaml_text(self) -> str:
        if self.value is None or not self.present:
            return ""
        return dump_yaml(self.value)


def _jsonable(value: Any) -> Any:
    if isinstance(value, Decimal):
        return float(value)
    if isinstance(value, _dt.date):
        return value.isoformat()
    if isinstance(value, BaseModel):
        return _jsonable(value.model_dump(by_alias=True, exclude_defaults=True))
    if isinstance(value, Mapping):
        return {str(k): _jsonable(v) for k, v in value.items()}
    if isinstance(value, list | tuple):
        return [_jsonable(v) for v in value]
    return value


def dump_yaml(value: Any) -> str:
    """A value as YAML text for a raw YAML box."""
    yaml = YAML()
    yaml.default_flow_style = False
    stream = StringIO()
    yaml.dump(_jsonable(to_plain(value)), stream)
    text = stream.getvalue()
    return text.removesuffix("...\n").rstrip("\n")


# -- annotation analysis ---------------------------------------------------------------------


def _without_none(annotation: Any) -> tuple[Any, bool]:
    origin = typing.get_origin(annotation)
    if origin in (Union, types.UnionType):
        args = [a for a in typing.get_args(annotation) if a is not type(None)]
        optional = len(args) < len(typing.get_args(annotation))
        if len(args) == 1:
            return args[0], optional
        return Union[tuple(args)], optional  # noqa: UP007 - rebuilt dynamically
    return annotation, False


def _validator_kind(annotation: Any) -> tuple[Kind, tuple[str, ...]] | None:
    if typing.get_origin(annotation) is not typing.Annotated:
        return None
    for meta in typing.get_args(annotation)[1:]:
        if isinstance(meta, BeforeValidator):
            if meta.func is to_score:
                return "score", ()
            if meta.func is to_change:
                return "change", ()
            if meta.func is check_alignment:
                return "enum", ALIGNMENTS
    return None


def _model_of(annotation: Any) -> type[BaseModel] | None:
    return (
        annotation if isinstance(annotation, type) and issubclass(annotation, BaseModel) else None
    )


def classify(key: str, annotation: Any) -> tuple[Kind, tuple[str, ...], type[BaseModel] | None]:
    """(kind, enum options, nested model) for one field."""
    inner, optional = _without_none(annotation)
    by_validator = _validator_kind(inner)
    if by_validator:
        return by_validator[0], by_validator[1], None
    if typing.get_origin(inner) is Literal:
        return "enum", tuple(str(a) for a in typing.get_args(inner)), None
    if inner is bool:
        return ("tristate" if optional else "bool"), (), None
    if inner is int:
        return "int", (), None
    if inner is float:
        return "number", (), None
    if inner is Decimal:
        return "decimal", (), None
    if inner is str:
        return ("link" if key in LINK_FIELDS else "text"), (), None
    model = _model_of(inner)
    if model is not None:
        return "group", (), model
    origin = typing.get_origin(inner)
    args = typing.get_args(inner)
    if origin in (Union, types.UnionType) and set(args) <= {str, int, float, _dt.date}:
        return "text", (), None
    if origin is list and args:
        item, _ = _without_none(args[0])
        if item is str:
            return ("link-list" if key in LINK_LIST_FIELDS else "list"), (), None
        if _model_of(item) is not None:
            return "rows", (), _model_of(item)
        if typing.get_origin(item) in (Union, types.UnionType) and Alias in typing.get_args(item):
            return "aliases", (), Alias
    return "yaml", (), None


def _default(info: Any) -> Any:
    if info.default_factory is not None:
        return info.default_factory()
    return None if info.default is PydanticUndefined else info.default


def _limits(info: Any) -> tuple[float | None, float | None]:
    low = high = None
    for meta in info.metadata:
        if isinstance(meta, Ge):
            low = float(meta.ge)  # type: ignore[arg-type]
        elif isinstance(meta, Le):
            high = float(meta.le)  # type: ignore[arg-type]
    return low, high


# -- building specs ----------------------------------------------------------------------------


def _row_value(kind: Kind, item: Any) -> Mapping[str, Any]:
    if kind == "aliases" and isinstance(item, str):
        return {"name": item}
    return item if isinstance(item, Mapping) else {}


def _fits(kind: Kind, value: Any) -> bool:
    """Can this widget show the value without losing anything?"""
    if value is None:
        return True
    if kind == "rows":
        return isinstance(value, list) and all(isinstance(v, Mapping) for v in value)
    if kind == "aliases":
        return isinstance(value, list) and all(isinstance(v, str | Mapping) for v in value)
    if kind in ("list", "link-list"):
        return isinstance(value, list) and all(isinstance(v, str | int | float) for v in value)
    if kind == "group":
        return isinstance(value, Mapping)
    if kind in ("yaml", "readonly"):
        return True
    return not isinstance(value, Mapping | list)


def _spec(key: str, info: Any, data: Mapping[str, Any], prefix: str) -> FieldSpec:
    kind, options, model = classify(key, info.annotation)
    if not _fits(kind, data.get(key)):
        kind, options, model = "yaml", (), None
    low, high = _limits(info)
    path = f"{prefix}{key}"
    spec = FieldSpec(
        key,
        label_for(key),
        kind,
        value=data.get(key),
        present=key in data,
        default=_default(info),
        options=options,
        minimum=low,
        maximum=high,
        hint=HINTS.get(key, ""),
        path=path,
    )
    if kind in ("score", "change", "decimal"):
        spec.step = "0.01"
        spec.minimum, spec.maximum = (-10.0, 10.0) if kind == "score" else (-20.0, 20.0)
        if kind == "decimal":
            spec.minimum = spec.maximum = None
    elif kind == "int":
        spec.step = "1"
    if model is not None and kind == "group":
        value = spec.value if isinstance(spec.value, Mapping) else {}
        spec.children = field_specs(model, value, prefix=f"{path}.")
    elif model is not None:
        items = spec.value if isinstance(spec.value, list) else []
        spec.rows = [
            field_specs(model, _row_value(kind, item), prefix=f"{path}.{i}.")
            for i, item in enumerate(items)
        ]
    return spec


def field_specs(
    model: type[BaseModel],
    data: Mapping[str, Any],
    *,
    prefix: str = "",
    include_extras: bool = True,
) -> list[FieldSpec]:
    """One spec per schema field, then any extra keys in ``data`` as YAML boxes."""
    specs: list[FieldSpec] = []
    known: set[str] = set()
    for name, info in model.model_fields.items():
        key = info.alias or name
        known.add(key)
        specs.append(_spec(key, info, data, prefix))
    if include_extras:
        for key, value in data.items():
            if key not in known:
                specs.append(
                    FieldSpec(
                        key,
                        label_for(key),
                        "yaml",
                        value=value,
                        present=True,
                        hint="extra",
                        path=f"{prefix}{key}",
                    )
                )
    return specs


def entry_model(meta: Mapping[str, Any]) -> type[BaseModel]:
    return ENTRY_MODELS.get(str(meta.get("type", "")), Entry)


def entry_form(meta: Mapping[str, Any]) -> list[FieldSpec]:
    """The edit form for one entry: its type's fields (id/type/name shown read-only)."""
    specs = field_specs(entry_model(meta), meta)
    for spec in specs:
        if spec.key in READONLY_KEYS:
            spec.kind = "readonly"
    return specs


def field_spec(meta: Mapping[str, Any], key: str) -> FieldSpec | None:
    for spec in entry_form(meta):
        if spec.key == key:
            return spec
    return None


def quick_spec(meta: Mapping[str, Any], key: str) -> FieldSpec | None:
    """The spec for one top-level field, if it can be edited in place."""
    spec = field_spec(meta, key)
    return spec if spec is not None and spec.quick else None


# -- reading a submitted form back ----------------------------------------------------------------
#
# Field values arrive as ``f:<path>`` (``f:goals.0.text``). ``p:<path>`` = "1" marks a key
# that was in the file, ``r:<path>`` = "1" marks each row (so a new, empty row survives a
# re-render), and ``k:<path>`` = "yaml" marks a field the form showed as a YAML box. A key
# comes back when it was in the file or now differs from the schema default, so a save
# never adds default values the file did not have.

VALUE, PRESENT, ROW, KIND = "f:", "p:", "r:", "k:"


def _is_blank(value: Any) -> bool:
    return value is None or value == "" or value == [] or value == {}


def _number(text: str, kind: Kind) -> Any:
    if not text.strip():
        return None
    try:
        number = float(text)
    except ValueError:
        return text  # keep what was typed; validation reports it
    if kind == "int" and number.is_integer():
        return int(number)
    return number


def _yaml(raw: str, path: str, problems: list[str]) -> Any:
    if not raw.strip():
        return None
    try:
        return YAML(typ="safe", pure=True).load(StringIO(raw))
    except Exception as exc:  # ruamel raises several unrelated error types
        problems.append(f"{path}: not valid YAML ({str(exc).splitlines()[0]})")
        return raw  # kept as text so nothing typed is lost


def _scalar(kind: Kind, raw: Any, path: str, problems: list[str]) -> Any:
    if kind == "bool":
        return raw is not None and str(raw) not in ("", "0", "false")
    text = "" if raw is None else str(raw).replace("\r\n", "\n")
    if kind == "tristate":
        return None if text == "" else text == "true"
    if kind in ("int", "number", "decimal", "score", "change"):
        return _number(text, kind)
    if kind in ("list", "link-list"):
        return [line.strip() for line in text.split("\n") if line.strip()]
    if kind == "yaml":
        return _yaml(text, path, problems)
    return text if text.strip() else None


def _row_indexes(form: Mapping[str, Any], path: str) -> list[int]:
    found: set[int] = set()
    for name in form:
        for marker in (VALUE, PRESENT, ROW, KIND):
            head = f"{marker}{path}."
            if name.startswith(head):
                index = name[len(head) :].split(".", 1)[0]
                if index.isdigit():
                    found.add(int(index))
    return sorted(found)


def _decode_value(
    kind: Kind,
    model: type[BaseModel] | None,
    form: Mapping[str, Any],
    path: str,
    problems: list[str],
    *,
    keep_blank_rows: bool = False,
) -> Any:
    if form.get(f"{KIND}{path}") == "yaml":
        kind, model = "yaml", None  # the form showed a YAML box for an odd value
    if kind == "group" and model is not None:
        return decode_fields(model, form, f"{path}.", problems)
    if kind in ("rows", "aliases") and model is not None:
        rows: list[Any] = []
        for index in _row_indexes(form, path):
            item: Any = decode_fields(model, form, f"{path}.{index}.", problems)
            if kind == "aliases" and set(item) == {"name"}:
                item = item["name"]
            if not _is_blank(item):
                rows.append(item)
            elif keep_blank_rows:
                rows.append({})
        return rows
    return _scalar(kind, form.get(f"{VALUE}{path}"), path, problems)


def decode_fields(
    model: type[BaseModel],
    form: Mapping[str, Any],
    prefix: str = "",
    problems: list[str] | None = None,
    *,
    extras: bool = False,
) -> dict[str, Any]:
    """Values for every field of ``model`` under ``prefix`` in a submitted form."""
    problems = problems if problems is not None else []
    result: dict[str, Any] = {}
    known: set[str] = set()
    for name, info in model.model_fields.items():
        key = info.alias or name
        known.add(key)
        if key in READONLY_KEYS and not prefix:
            continue
        path = f"{prefix}{key}"
        kind, _options, child = classify(key, info.annotation)
        value = _decode_value(kind, child, form, path, problems)
        if form.get(f"{PRESENT}{path}") == "1" or (
            not _is_blank(value)
            and value is not False
            and json.dumps(_jsonable(value)) != json.dumps(_jsonable(_default(info)))
        ):
            result[key] = value
    if extras:
        for name in form:
            if not name.startswith(f"{VALUE}{prefix}"):
                continue
            key = name[len(VALUE) + len(prefix) :]
            if "." in key or key in known:
                continue
            result[key] = _yaml(str(form[name]), key, problems)
    return result


def decode_form(
    meta: Mapping[str, Any], form: Mapping[str, Any]
) -> tuple[dict[str, Any], list[str]]:
    """An entry's new frontmatter from the edit-mode form (id, type, name never change here).

    Returns (meta, problems); problems name fields that could not be read (bad YAML).
    """
    problems: list[str] = []
    values = decode_fields(entry_model(meta), form, "", problems, extras=True)
    fixed = {key: meta[key] for key in READONLY_KEYS if key in meta}
    return {**fixed, **values}, problems


def decode_field(
    meta: Mapping[str, Any], form: Mapping[str, Any], key: str, *, keep_blank_rows: bool = False
) -> tuple[Any, list[str]]:
    """One top-level field's value from a form (quick edit and the row tools; the row
    tools keep blank rows so a just-added row survives the re-render)."""
    problems: list[str] = []
    model = entry_model(meta)
    info = next((i for n, i in model.model_fields.items() if (i.alias or n) == key), None)
    if info is None:  # an extra key: always a YAML box
        return _yaml(str(form.get(f"{VALUE}{key}", "")), key, problems), problems
    kind, _options, child = classify(key, info.annotation)
    value = _decode_value(kind, child, form, key, problems, keep_blank_rows=keep_blank_rows)
    return value, problems
