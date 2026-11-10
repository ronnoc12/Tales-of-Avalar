---
id: reverse-medusa-guardian
type: creature
name: Reverse Medusa Guardian
aliases: [Phylactery Guardian]
size: Gargantuan
creature_type: monstrosity
alignment: chaotic evil
ac: 17 (natural scales)
hp: 285 (30d12 + 90)
speed: 40 ft., climb 20 ft., burrow 20 ft.
abilities: { str: 22, dex: 14, con: 17, int: 10, wis: 14, cha: 16 }
saves: { dex: 6, con: 7, wis: 6 }
skills: { perception: 6, intimidation: 6 }
senses: darkvision 120 ft., tremorsense 60 ft., passive Perception 16
languages: understands Abyssal and Draconic but cannot speak
cr: 10 (5,900 XP)
traits:
  - { name: Multi-Headed Horror, text: "The guardian has 6 petrified screaming heads. If it takes 50 or more damage in a single round, one head (chosen randomly) is shattered. Destroying all heads removes its Gaze of Torment, reducing its CR to 8." }
  - { name: Terrifying Presence, text: "Any creature that starts its turn within 30 ft. must succeed on a DC 16 Wisdom save or be frightened until the end of its turn." }
  - { name: Legendary Resistance (3/Day), text: "If it fails a saving throw, it can choose to succeed instead." }
  - { name: "Damage Resistances / Condition Immunities", text: "Resistant to fire and necrotic. Immune to charmed, frightened, paralyzed." }
actions:
  - { name: Multiattack, text: "One constrict and one bite." }
  - { name: Constrict, text: "Melee Weapon Attack: +9 to hit, reach 15 ft., one target. Hit: 21 (3d10 + 6) bludgeoning, and the target is grappled (escape DC 17) and restrained until the grapple ends." }
  - { name: Bite, text: "Melee Weapon Attack: +9 to hit, reach 10 ft., one target. Hit: 18 (3d8 + 6) piercing plus 10 (3d6) necrotic." }
  - { name: Gaze of Torment (Recharge 5-6), text: "Roll d6 for which head's gaze lashes out; creatures of its choice within 30 ft. make a DC 16 save. 1 Petrifying (CON): restrained, then petrified on a second failure at the end of its next turn. 2 Weeping (WIS): stunned until the end of its next turn. 3 Searing (DEX): 21 (6d6) fire. 4 Despairing (WIS): prone and frightened until the end of its next turn. 5 Withering (CON): max HP reduced by 10 (2d10) until a long rest. 6 Shattering (STR): pushed 20 ft. and 14 (4d6) force." }
legendary_actions:
  - { name: Tail Lash, text: "Melee attack, +9 to hit, reach 15 ft., 14 (3d6 + 6) bludgeoning." }
  - { name: Gaze Pulse (Costs 2 Actions), text: "Activate one Gaze of Torment effect at random (roll d6)." }
  - { name: Move, text: "Slithers up to half its speed without provoking opportunity attacks." }
tags: [ashvault, boss, unsighted-order]
---

## Overview
A custom boss: the horror the [[Unsighted Order]] brought to the [[Ashvault Sanctum]] with old, forbidden magic to guard [[Vhallion's Phylactery]]. Built as a Deadly encounter for five level-6 characters. In its lair, the faces of women twisted in agony are half-buried in the walls, their stone eyes turning toward intruders. Modern wards keep people out and the monster in.

## Lair Actions
On initiative count 20 (losing ties), one of:
1. **Shifting Shadows:** the walls ripple with shadow, heavily obscuring vision until the next round.
2. **Stone Scream:** a petrified head in the walls wails; all creatures make a DC 15 Wisdom save or take 11 (2d10) psychic damage and are deafened for 1 round.
3. **Ashfall:** volcanic ash falls, creating difficult terrain until the next round.
