---
id: casfer-ferndance
type: npc
name: Casfer Ferndance
aliases: [Cas, {name: Casfr, search_only: true}]
tags: [use]
portrait: "![[img:casfer-portrait]]"
race: Halfling
age: 38
height: 3'1"
pronouns: he/him
role: Leader of the USE Midcross branch
alignment: Neutral Good
party_role: ally
disposition: 2.50
self_view: CANARY-SELF-VIEW
life_state: alive
portrayal:
  mannerisms: Bad jokes
  voice: New England accent
first_met: {location: "[[Midcross]]", session: "[[Session 1]]"}
location: "[[Midcross]]"
factions:
  - {faction: "[[USE]]", rank: Branch Leader, standing: 8.00}
relationships:
  - {to: "[[Cassie Ferndance]]", type: twin, note: Runs USE HQ in Valoria}
  - {to: "[[Lord Burgenroot]]", type: informant, note: CANARY-SECRET-RELATIONSHIP, secret: true}
plots:
  - {plot: "[[Ravenwood Silence]]", role: Quest giver, plans: CANARY-PLOT-PLANS}
goals:
  - {text: Keep USE neutral}
  - {text: CANARY-SECRET-GOAL, secret: true}
party_view:
  role: Runs a consulting firm
---

## Pivotal Decisions
- Took over the branch at 30.

## Personality
Good-natured. {secret}CANARY-INLINE-PERSONALITY{/secret} Loves bad jokes.
{revealed [[Session 2]]}He and [[Cassie Ferndance]] were recruits at 12.{/revealed}

## Inner Dealings {secret}
CANARY-SECRET-DEALINGS with [[Vhallion]].

## Field Notes
- Public bullet one.
- {secret}CANARY-MULTI-BULLET-A
- CANARY-MULTI-BULLET-B{/secret}
- Public bullet two.

{secret reveal=cas-letter about=[[Ravenwood Silence]]}CANARY-MULTI-PARA-A
CANARY-MULTI-PARA-B

```text
CANARY-CODE-IN-SECRET
```
CANARY-MULTI-PARA-C{/secret}

{revealed [[Session 2]] reveal=cas-twins}Known now: the twins
share a {secret}CANARY-SECRET-IN-REVEAL
still hidden{/secret} past.{/revealed}

{secret}CANARY-OUTER {prep}CANARY-PREP-IN-SECRET{/prep} tail{/secret}

## Ledger {secret reveal=cas-ledger about=[[USE]]}
CANARY-SECTION-WITH-ATTRS
