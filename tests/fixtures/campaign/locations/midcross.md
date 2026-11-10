---
id: midcross
type: location
name: Midcross
aliases: [Mid Cross]
parent: "[[The Continent]]"
kind: city
population: Transient
ruler: "[[Lord Burgenroot]]"
ruler_approval: -2.50
info_tier: 2
magic:
  stance: Tolerant if profitable
  trained: Legal
  untrained: "Ignored for a bribe {secret}CANARY-MAGIC-BRIBE{/secret}"
market:
  size: city
  price_mods: { lumber: 0.70 }
relations:
  - { to: "[[Valoria]]", type: ally, note: Close trade partner }
  - { to: "[[Valoria]]", type: rival, note: CANARY-SECRET-RELATION, secret: true }
factions:
  - { faction: "[[USE]]", presence: Branch outpost }
plots:
  - { plot: "[[Ravenwood Silence]]", role: Where the bounty is posted }
---

## Overview
A trade hub. Ally of [[Valoria]]. {prep}Remind them the bridge is out{/prep}

## Governance
Run by [[Lord Burgenroot]]. {secret}CANARY-INLINE-GOVERNANCE{/secret}
{revealed [[Session 1]]}The council can remove him.{/revealed}

## The Undercity {secret}
CANARY-SECRET-SECTION lives here.

### Deeper Vaults
CANARY-NESTED-SECRET-SECTION

## Places
### The Gilded Anchor {inn}
Cheap rooms, expensive gossip. Owner: [[Casfer Ferndance]].

### Smugglers' Den {secret}
CANARY-SECRET-PLACE
