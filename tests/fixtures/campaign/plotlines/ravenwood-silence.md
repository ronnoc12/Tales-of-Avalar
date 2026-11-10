---
id: ravenwood-silence
type: plotline
name: Ravenwood Silence
state: active
pace: short
summary: The lumber town went quiet.
hooks: [CANARY-HOOK]
if_ignored: CANARY-IF-IGNORED
actions:
  - { name: Bounty posted, date: 1 Seedmoon 1500 AS, result: success }
  - { name: Scouts vanish, date: 2 Seedmoon 1500 AS }
  - { name: Mill burns, date: 14 Seedmoon 1500 AS, roll: { dc: 14, mod: 3 } }
  - { name: Town abandoned, date: 30 Seedmoon 1500 AS, if_ignored: CANARY-ACTION-IGNORED }
---
Investigate the missing lumber from [[Midcross]].
