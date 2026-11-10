---
id: new-npc                     # permanent; lowercase-with-dashes; never change it
type: npc
name: New NPC
aliases: []                     # nicknames, misspellings: [Cas, { name: Casfr, search_only: true }]
status: canon                   # canon | draft | idea | alternate | retired
tags: []

# -- quick card --
portrait:                       # "![[img:asset-id]]"
race:
age:
height:
pronouns:
role:                           # occupation / position
portrayal:
  mannerisms:
  voice:
  quotes: []
  audio:                        # "[[audio:clip-id]]"
alignment:                      # e.g. Neutral Good
party_role:                     # ally | friendly | neutral | rival | antagonist | nemesis
disposition: 0.00               # -10.00 to +10.00 toward the party
self_view:                      # how they justify themselves
class: []                       # [{ name: Rogue, subclass: Mastermind, level: 7 }]
stat_block:                     # { ref: { source: Monster Manual, name: Spy, page: 330 }, mods: "" } (empty = Commoner)
character_sheet:
life_state: alive               # alive | dead | missing | undead | unknown

# -- where --
first_met: { location: "", session: "" }
location:                       # "[[Midcross]]"
location_history: []            # [{ location: "[[Valoria]]", from: 3 Month-1 1500 AS, to: 20 Month-1 1500 AS }]

# -- who --
factions: []                    # [{ faction: "[[USE]]", rank: Branch Leader, standing: 0.00 }]
relationships: []               # [{ to: "[[Someone]]", type: sibling, note: "", secret: false }]

# -- story --
plots: []                       # [{ plot: "[[Plot]]", role: "", wants: "", knows: "", opinion: "", status: active }]
events: []                      # [{ event: "[[Event]]", role: "", opinion: "" }]
goals: []                       # [{ text: "", status: active, secret: false }]
agenda: {}                      # optional: ultimate_goal, resources, allies, enemies, weaknesses, schemes
party_view: {}                  # only fields the party believes differently, e.g. { role: "..." }
---

## Pivotal Decisions

## Appearance

## Personality

## Backstory

## GM Notes {secret}
