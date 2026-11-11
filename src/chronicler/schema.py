"""Entry schemas: shared fields, every entry type, scores, and per-type view rules.

Models use ``extra="allow"`` so a typo never loses data; the validator reports unknown
top-level fields as warnings instead.
"""

from __future__ import annotations

import datetime as _dt
from decimal import Decimal, InvalidOperation
from typing import Annotated, Any, Literal

from pydantic import BaseModel, BeforeValidator, ConfigDict, Field, field_validator

# -- scores ------------------------------------------------------------------------------

SCORE_MIN = Decimal("-10.00")
SCORE_MAX = Decimal("10.00")
_CENT = Decimal("0.01")


def _to_decimal(value: Any, *, low: Decimal, high: Decimal) -> Decimal:
    if isinstance(value, bool) or value is None:
        raise ValueError("must be a number")
    try:
        number = Decimal(str(value).strip())
    except InvalidOperation as exc:
        raise ValueError(f"not a number: {value!r}") from exc
    if not number.is_finite():
        raise ValueError("must be a finite number")
    if number != number.quantize(_CENT):
        raise ValueError("use at most two decimal places")
    if not low <= number <= high:
        raise ValueError(f"must be between {low} and {high}")
    return number.quantize(_CENT)


def to_score(value: Any) -> Decimal:
    return _to_decimal(value, low=SCORE_MIN, high=SCORE_MAX)


def to_change(value: Any) -> Decimal:
    return _to_decimal(value, low=SCORE_MIN - SCORE_MAX, high=SCORE_MAX - SCORE_MIN)


Score = Annotated[Decimal, BeforeValidator(to_score)]
ScoreChange = Annotated[Decimal, BeforeValidator(to_change)]

# -- vocabularies --------------------------------------------------------------------------

Status = Literal["canon", "draft", "idea", "alternate", "retired"]
LifeState = Literal["alive", "dead", "missing", "undead", "unknown"]
PartyRole = Literal["ally", "friendly", "neutral", "rival", "antagonist", "nemesis"]
ALIGNMENTS = (
    "Lawful Good",
    "Neutral Good",
    "Chaotic Good",
    "Lawful Neutral",
    "True Neutral",
    "Chaotic Neutral",
    "Lawful Evil",
    "Neutral Evil",
    "Chaotic Evil",
    "Unaligned",
)
ID_PATTERN = r"^[a-z0-9][a-z0-9-]*$"


def check_alignment(value: Any) -> Any:
    if value is None:
        return value
    for alignment in ALIGNMENTS:
        if str(value).strip().casefold() == alignment.casefold():
            return alignment
    raise ValueError(f"unknown alignment {value!r}; use one of: {', '.join(ALIGNMENTS)}")


Alignment = Annotated[str, BeforeValidator(check_alignment)]
Text = str | int | float


class Model(BaseModel):
    model_config = ConfigDict(extra="allow", populate_by_name=True)


# -- shared building blocks ------------------------------------------------------------------


class Alias(Model):
    name: str
    used_by: str | None = None
    search_only: bool = False
    secret: bool = False


class Goal(Model):
    text: str
    status: Literal["active", "achieved", "abandoned"] = "active"
    secret: bool = False


class Relationship(Model):
    to: str
    type: str | None = None
    note: str | None = None
    secret: bool = False


class PlotInvolvement(Model):
    plot: str
    role: str | None = None
    wants: str | None = None
    knows: str | None = None
    opinion: str | None = None
    status: str | None = None
    plans: str | None = None
    secret: bool = False


class EventInvolvement(Model):
    event: str
    role: str | None = None
    opinion: str | None = None
    secret: bool = False


class FactionMembership(Model):
    faction: str
    rank: str | None = None
    branch: str | None = None
    standing: Score | None = None
    secret: bool = False


class LocationStint(Model):
    location: str
    from_: str | None = Field(default=None, alias="from")
    to: str | None = None


class FirstMet(Model):
    location: str | None = None
    session: str | None = None


class Portrayal(Model):
    mannerisms: str | None = None
    voice: str | None = None
    quotes: list[str] = Field(default_factory=list)
    audio: str | None = None


class ClassLevel(Model):
    name: str
    subclass: str | None = None
    level: int = Field(ge=1, le=20)


class StatBlockRef(Model):
    ref: dict[str, Any] | str | None = None
    url: str | None = None
    mods: str | None = None


class Agenda(Model):
    ultimate_goal: str | None = None
    motivation: str | None = None
    location: str | None = None
    influence: list[str] = Field(default_factory=list)
    resources: list[str] = Field(default_factory=list)
    allies: list[str] = Field(default_factory=list)
    enemies: list[str] = Field(default_factory=list)
    weaknesses: str | None = None
    schemes: list[dict[str, Any]] = Field(default_factory=list)


# -- base entry ------------------------------------------------------------------------------


class Entry(Model):
    id: str = Field(pattern=ID_PATTERN)
    type: str
    name: str
    aliases: list[str | Alias] = Field(default_factory=list)
    status: Status = "canon"
    tags: list[str] = Field(default_factory=list)
    parent: str | None = None
    secret: bool | None = None
    secret_fields: list[str] = Field(default_factory=list)
    party_view: dict[str, Any] = Field(default_factory=dict)
    alternate_of: str | None = None
    used_in: list[str] = Field(default_factory=list)

    @field_validator("tags", mode="before")
    @classmethod
    def _tags_as_strings(cls, value: Any) -> Any:
        return [str(v) for v in value] if isinstance(value, list) else value


# -- entry types -----------------------------------------------------------------------------


class Npc(Entry):
    portrait: str | None = None
    race: str | None = None
    age: Text | None = None
    height: str | None = None
    pronouns: str | None = None
    role: str | None = None
    alignment: Alignment | None = None
    party_role: PartyRole | None = None
    disposition: Score | None = None
    self_view: str | None = None
    class_: list[ClassLevel] = Field(default_factory=list, alias="class")
    stat_block: StatBlockRef | str | None = None
    character_sheet: str | None = None
    life_state: LifeState = "alive"
    portrayal: Portrayal | None = None
    first_met: FirstMet | None = None
    location: str | None = None
    location_history: list[LocationStint] = Field(default_factory=list)
    factions: list[FactionMembership] = Field(default_factory=list)
    relationships: list[Relationship] = Field(default_factory=list)
    plots: list[PlotInvolvement] = Field(default_factory=list)
    events: list[EventInvolvement] = Field(default_factory=list)
    goals: list[Goal] = Field(default_factory=list)
    agenda: Agenda | None = None


class MapPin(Model):
    map: str
    x: float = Field(ge=0, le=1)
    y: float = Field(ge=0, le=1)


class MagicStance(Model):
    stance: str | None = None
    trained: str | None = None
    untrained: str | None = None


class Market(Model):
    size: Literal["hamlet", "village", "town", "city", "metropolis"] | None = None
    price_mods: dict[str, Decimal] = Field(default_factory=dict)
    banned: list[str] = Field(default_factory=list)
    allowed: list[str] = Field(default_factory=list)

    @field_validator("price_mods", mode="before")
    @classmethod
    def _positive_mods(cls, value: Any) -> Any:
        if isinstance(value, dict):
            result: dict[str, Decimal] = {}
            for key, raw in value.items():
                number = Decimal(str(raw))
                if number <= 0:
                    raise ValueError(f"price modifier for {key!r} must be positive")
                result[str(key)] = number
            return result
        return value


class PoliticalRelation(Model):
    to: str
    type: str | None = None
    note: str | None = None
    secret: bool = False


class FactionPresence(Model):
    faction: str
    presence: str | None = None
    site: str | None = None
    secret: bool = False


class Location(Entry):
    image: str | None = None
    kind: (
        Literal[
            "plane",
            "continent",
            "region",
            "nation",
            "city",
            "town",
            "district",
            "site",
            "building",
            "dungeon",
        ]
        | None
    ) = None
    population: Text | None = None
    ruler: str | None = None
    government: str | None = None
    terrain: str | None = None
    map_pin: MapPin | None = None
    info_tier: int | None = Field(default=None, ge=1, le=5)
    magic: MagicStance | None = None
    market: Market | None = None
    relations: list[PoliticalRelation] = Field(default_factory=list)
    ruler_approval: Score | None = None
    factions: list[FactionPresence] = Field(default_factory=list)
    first_visited: str | None = None
    plots: list[PlotInvolvement] = Field(default_factory=list)


class PublicOpinion(Model):
    default: Score | None = None
    by_location: dict[str, Score] = Field(default_factory=dict)


class InternalApproval(Model):
    score: Score | None = None
    note: str | None = None
    by_group: dict[str, Score] = Field(default_factory=dict)


class FactionRelation(Model):
    to: str
    feeling: Score | None = None
    type: str | None = None
    note: str | None = None
    secret: bool = False


class Presence(Model):
    location: str
    role: str | None = None
    secret: bool = False


class Faction(Entry):
    symbol: str | None = None
    kind: str | None = None
    motto: str | None = None
    leader: str | None = None
    headquarters: str | None = None
    size: str | None = None
    alignment: Alignment | None = None
    party_role: PartyRole | None = None
    reputation: Score | None = None
    public_face: str | None = None
    true_purpose: str | None = None
    public_opinion: PublicOpinion | None = None
    internal_approval: InternalApproval | None = None
    relations: list[FactionRelation] = Field(default_factory=list)
    ranks: list[str] = Field(default_factory=list)
    presence: list[Presence] = Field(default_factory=list)
    plots: list[PlotInvolvement] = Field(default_factory=list)
    events: list[EventInvolvement] = Field(default_factory=list)
    goals: list[Goal] = Field(default_factory=list)
    agenda: Agenda | None = None


class ActionRoll(Model):
    dice: str = "d20"
    dc: int
    mod: int = 0


class PlotAction(Model):
    name: str
    date: str | None = None
    location: str | None = None
    actor: str | None = None
    roll: ActionRoll | None = None
    result: Literal["pending", "success", "failure", "delayed"] = "pending"
    if_ignored: str | None = None
    notes: str | None = None


class Plotline(Entry):
    state: Literal["planned", "active", "dormant", "resolved", "failed", "abandoned"] = "active"
    pace: Literal["quick", "short", "medium", "arc", "saga"] | None = None
    summary: str | None = None
    locations: list[str] = Field(default_factory=list)
    hooks: list[str] = Field(default_factory=list)
    actions: list[PlotAction] = Field(default_factory=list)
    if_ignored: str | None = None
    related: list[str] = Field(default_factory=list)


class InWorldSpan(Model):
    start: str | None = None
    end: str | None = None


class SessionChange(Model):
    who: str
    disposition: ScoreChange | None = None
    reputation: ScoreChange | None = None
    standing: ScoreChange | None = None
    reason: str | None = None


class Session(Entry):
    number: int = Field(ge=0)
    real_date: _dt.date | str | None = None
    in_world: InWorldSpan | None = None
    party_location: str | None = None
    recap_audio: str | None = None
    appeared: list[str] = Field(default_factory=list)
    spotlight: list[str] | None = None
    changes: list[SessionChange] = Field(default_factory=list)
    reveals: list[str] = Field(default_factory=list)
    decisions_resolved: list[str] = Field(default_factory=list)
    plot_actions_resolved: list[str] = Field(default_factory=list)
    rolls: list[dict[str, Any]] = Field(default_factory=list)


class DecisionOption(Model):
    name: str
    impact: str | None = None
    effects: list[dict[str, Any]] = Field(default_factory=list)


class Decision(Entry):
    state: Literal["pending", "resolved", "expired"] = "pending"
    plot: str | None = None
    deadline: str | None = None
    context: str | None = None
    options: list[DecisionOption] = Field(default_factory=list)
    chosen: str | None = None
    resolved_in: str | None = None


class Abilities(Model):
    str_: int | None = Field(default=None, alias="str", ge=1, le=30)
    dex: int | None = Field(default=None, ge=1, le=30)
    con: int | None = Field(default=None, ge=1, le=30)
    int_: int | None = Field(default=None, alias="int", ge=1, le=30)
    wis: int | None = Field(default=None, ge=1, le=30)
    cha: int | None = Field(default=None, ge=1, le=30)


class Pc(Entry):
    player: str | None = None
    image: str | None = None
    race: str | None = None
    class_: list[ClassLevel] = Field(default_factory=list, alias="class")
    ac: int | None = None
    hp_max: int | None = None
    speed: Text | None = None
    abilities: Abilities | None = None
    saves: dict[str, int] = Field(default_factory=dict)
    passives: dict[str, int] = Field(default_factory=dict)
    spell_dc: int | None = None
    languages: list[str] = Field(default_factory=list)
    character_sheet: str | None = None
    life_state: LifeState = "alive"
    inspiration: bool | int = False
    conditions: list[str] = Field(default_factory=list)
    exhaustion: int = Field(default=0, ge=0, le=6)
    goals: list[Goal] = Field(default_factory=list)
    relationships: list[Relationship] = Field(default_factory=list)
    plots: list[PlotInvolvement] = Field(default_factory=list)
    events: list[EventInvolvement] = Field(default_factory=list)


class Charges(Model):
    max: int = Field(ge=0)
    recharge: str | None = None


class Sentience(Model):
    int_: int | None = Field(default=None, alias="int")
    wis: int | None = None
    cha: int | None = None
    alignment: Alignment | None = None
    communication: str | None = None
    senses: str | None = None
    purpose: str | None = None


class Item(Entry):
    image: str | None = None
    kind: str | None = None
    rarity: Literal["common", "uncommon", "rare", "very rare", "legendary", "artifact"] | None = (
        None
    )
    attunement: bool | str = False
    state: Literal["active", "dormant", "awakened", "empowered", "destroyed", "lost"] = "active"
    holder: str | None = None
    location: str | None = None
    stats: dict[str, Any] = Field(default_factory=dict)
    charges: Charges | None = None
    sentient: Sentience | None = None


class Feature(Model):
    name: str
    text: str | None = None


class Creature(Entry):
    image: str | None = None
    size: str | None = None
    creature_type: str | None = None
    alignment: str | None = None
    ac: Text | None = None
    hp: Text | None = None
    speed: Text | None = None
    abilities: Abilities | None = None
    saves: dict[str, int] = Field(default_factory=dict)
    skills: dict[str, int] = Field(default_factory=dict)
    senses: str | None = None
    languages: str | list[str] | None = None
    cr: Text | None = None
    traits: list[Feature] = Field(default_factory=list)
    actions: list[Feature] = Field(default_factory=list)
    reactions: list[Feature] = Field(default_factory=list)
    legendary_actions: list[Feature] = Field(default_factory=list)


class Deity(Entry):
    symbol: str | None = None
    domains: list[str] = Field(default_factory=list)
    alignment: Alignment | None = None
    relations: list[Relationship] = Field(default_factory=list)


class Lore(Entry):
    kind: Literal["era", "event", "people", "concept", "term"] | None = None
    date: str | None = None
    era: str | None = None
    start: str | None = None
    end: str | None = None


class Scene(Entry):
    kind: Literal["read-aloud", "dream", "handout", "description"] | None = None
    location: str | None = None
    plot: str | None = None


class Prep(Entry):
    state: Literal["open", "in-progress", "done"] = "open"
    target_session: int | str | None = None
    target_date: str | None = None
    links: list[str] = Field(default_factory=list)


class RollTable(Entry):
    trigger: dict[str, Any] | None = None
    result: dict[str, Any] | None = None


class Asset(Entry):
    kind: Literal["image", "audio", "map"] = "image"
    file: str
    caption: str | None = None
    source: str | None = None
    linked: list[str] = Field(default_factory=list)


# -- registry --------------------------------------------------------------------------------

ENTRY_MODELS: dict[str, type[Entry]] = {
    "npc": Npc,
    "location": Location,
    "faction": Faction,
    "plotline": Plotline,
    "session": Session,
    "decision": Decision,
    "pc": Pc,
    "item": Item,
    "creature": Creature,
    "deity": Deity,
    "lore": Lore,
    "scene": Scene,
    "prep": Prep,
    "rolltable": RollTable,
    "asset": Asset,
}

TYPE_FOLDERS: dict[str, str] = {
    "npc": "npcs",
    "location": "locations",
    "faction": "factions",
    "plotline": "plotlines",
    "session": "sessions",
    "decision": "decisions",
    "pc": "pcs",
    "item": "items",
    "creature": "creatures",
    "deity": "deities",
    "lore": "lore",
    "scene": "scenes",
    "prep": "prep",
    "rolltable": "roll-tables",
    "asset": "assets",
}
FOLDER_TYPES = {folder: kind for kind, folder in TYPE_FOLDERS.items()}

TYPE_LABELS: dict[str, str] = {
    "npc": "NPCs",
    "location": "Locations",
    "faction": "Factions",
    "plotline": "Plotlines",
    "session": "Sessions",
    "decision": "Decisions",
    "pc": "Player Characters",
    "item": "Items",
    "creature": "Creatures",
    "deity": "Deities",
    "lore": "Lore",
    "scene": "Scenes",
    "prep": "Prep",
    "rolltable": "Roll Tables",
    "asset": "Assets",
}

# Types whose entries are GM-only unless the entry sets ``secret: false``.
DEFAULT_SECRET_TYPES = frozenset({"pc", "decision", "creature", "prep", "rolltable", "scene"})

# Fields never shown in the Player view (unless overridden through ``party_view``).
GM_ONLY_COMMON = frozenset(
    {"status", "secret", "secret_fields", "party_view", "alternate_of", "used_in", "agenda", "tags"}
)
GM_ONLY_FIELDS: dict[str, frozenset[str]] = {
    "npc": frozenset(
        {
            "alignment",
            "disposition",
            "party_role",
            "self_view",
            "stat_block",
            "character_sheet",
            "portrayal",
            "plots",
            "events",
        }
    ),
    "location": frozenset({"info_tier", "ruler_approval", "plots", "market"}),
    "faction": frozenset(
        {
            "alignment",
            "true_purpose",
            "reputation",
            "public_opinion",
            "internal_approval",
            "party_role",
            "plots",
            "events",
        }
    ),
    "plotline": frozenset({"actions", "pace", "if_ignored", "hooks", "related"}),
    "session": frozenset(
        {
            "changes",
            "rolls",
            "plot_actions_resolved",
            "decisions_resolved",
            "reveals",
            "spotlight",
            "recap_audio",
        }
    ),
    "item": frozenset({"sentient"}),
}
# Nested keys stripped from list items / mappings in the Player view.
GM_ONLY_NESTED = frozenset({"standing", "feeling", "plans", "knows", "wants"})

# Fields shown on the quick card, in order (everything else goes under "Details").
QUICK_CARD: dict[str, tuple[str, ...]] = {
    "npc": (
        "race",
        "age",
        "height",
        "pronouns",
        "role",
        "portrayal",
        "alignment",
        "party_role",
        "disposition",
        "life_state",
        "location",
        "class",
        "stat_block",
        "character_sheet",
        "self_view",
    ),
    "pc": (
        "player",
        "race",
        "class",
        "ac",
        "hp_max",
        "speed",
        "spell_dc",
        "passives",
        "saves",
        "abilities",
        "inspiration",
        "exhaustion",
        "conditions",
        "languages",
        "life_state",
        "character_sheet",
    ),
    "location": (
        "kind",
        "population",
        "ruler",
        "ruler_approval",
        "government",
        "terrain",
        "info_tier",
    ),
    "faction": (
        "kind",
        "motto",
        "leader",
        "headquarters",
        "size",
        "alignment",
        "party_role",
        "reputation",
        "public_face",
        "true_purpose",
    ),
    "plotline": ("state", "pace", "summary", "locations"),
    "session": ("number", "real_date", "in_world", "party_location"),
    "decision": ("state", "plot", "deadline", "chosen", "resolved_in"),
    "item": ("kind", "rarity", "attunement", "state", "holder", "location"),
    "creature": ("size", "creature_type", "alignment", "ac", "hp", "speed", "cr"),
    "deity": ("domains", "alignment", "symbol"),
    "lore": ("kind", "date", "era"),
    "scene": ("kind", "location", "plot"),
    "prep": ("state", "target_session", "target_date"),
    "asset": ("kind", "file", "caption", "source"),
}
IMAGE_FIELDS = ("portrait", "image", "symbol")

# Fields holding in-world dates, as (field path) where "*" means "each list item".
DATE_FIELDS: dict[str, tuple[tuple[str, ...], ...]] = {
    "npc": (("location_history", "*", "from"), ("location_history", "*", "to")),
    "plotline": (("actions", "*", "date"),),
    "session": (("in_world", "start"), ("in_world", "end")),
    "decision": (("deadline",),),
    "prep": (("target_date",),),
}
FUZZY_DATE_FIELDS: dict[str, tuple[tuple[str, ...], ...]] = {
    "lore": (("date",), ("start",), ("end",)),
}


def is_secret_entry(meta: dict[str, Any]) -> bool:
    explicit = meta.get("secret")
    if isinstance(explicit, bool):
        return explicit
    return str(meta.get("type", "")) in DEFAULT_SECRET_TYPES


def known_fields(entry_type: str) -> set[str]:
    model = ENTRY_MODELS.get(entry_type, Entry)
    names: set[str] = set()
    for name, info in model.model_fields.items():
        names.add(info.alias or name)
    return names


# Fields whose value is one [[link]] (any entry type, any nesting level); the editor offers
# name autocomplete for them. LINK_LIST_FIELDS hold a list of links.
LINK_FIELDS = frozenset(
    {
        "actor",
        "alternate_of",
        "branch",
        "event",
        "faction",
        "headquarters",
        "holder",
        "leader",
        "location",
        "map",
        "parent",
        "plot",
        "ruler",
        "session",
        "site",
        "to",
    }
)
LINK_LIST_FIELDS = frozenset(
    {"allies", "appeared", "enemies", "locations", "related", "spotlight", "used_in"}
)


# ``secret: true`` hides any list item from players (see redact.py), so it is always known.
GENERIC_ITEM_KEYS = frozenset({"secret"})


def nested_unknown_fields(value: Any, path: str = "") -> list[str]:
    """Paths of keys inside nested items that no schema field covers.

    Top-level keys of an entry are checked separately (with :func:`known_fields`); this
    finds the ones below, e.g. ``goals[1].discreet`` from an unquoted comma in YAML.
    """
    found: list[str] = []
    if isinstance(value, BaseModel):
        if path:
            for key in value.model_extra or {}:
                if key not in GENERIC_ITEM_KEYS:
                    found.append(f"{path}.{key}")
        for name, info in type(value).model_fields.items():
            child = f"{path}.{info.alias or name}" if path else (info.alias or name)
            found.extend(nested_unknown_fields(getattr(value, name), child))
    elif isinstance(value, list):
        for i, item in enumerate(value):
            found.extend(nested_unknown_fields(item, f"{path}[{i}]"))
    elif isinstance(value, dict):
        for key, item in value.items():
            found.extend(nested_unknown_fields(item, f"{path}.{key}"))
    return found
