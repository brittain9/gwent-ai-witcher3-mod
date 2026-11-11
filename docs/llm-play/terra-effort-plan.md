# Terra effort panel — November 10, 2026

Alex authorized 15–20 more varied Terra games. The frozen plan is 18 games:
six scenarios at each of medium, high and xhigh, using gpt-6.1-terra. Each game
uses a fresh agent context and the player-v3, mechanics-v2 and review-v2 pack.
The session command supplies connection credentials automatically.

| Player / opposing faction | Seat | Deck recipes and variants, in seat order | Opponent |
|---|---|---|---|
| Northern Realms / Nilfgaard | 0 | NKHard2, NilfHard1 | Stock native |
| Nilfgaard / Monsters | 1 | NMLHard1, NilfTournament2-1 | Direct league32k-5c |
| Scoia’tael / Skellige | 0 | ScoiaTournament2-1, SkelHard1 | league32k-5c tree,64 rollouts |
| Monsters / Northern Realms | 1 | NKHard1, NMLHard2 | Stock native |
| Skellige / Scoia’tael | 0 | SkelHard2, ScoiaHard2 | original-hard search,32 rollouts |
| Nilfgaard / Skellige | 1 | SkelTournament2-1, NilfTournament2-2 | original-hard tree,64 rollouts |

Each scenario freezes the initial seed and configured opponent seeds across
all efforts. Search uses explicit faction-pool beliefs and a one-second deadline;
search timing can still change choices. Stock reproduces native behavior and
reads both hands. The LLM receives only its assigned player's view.

Limits: 15 minutes per host,12 minutes or100 player moves per agent,512 engine
actions. Record failed and partial attempts. Keep timeouts distinct from losses.
Do not change rules, prompts, models or opponent budgets during this panel.

Report wins, losses, draws, incomplete games, start-to-terminal wall time,
accepted moves, command errors and post-game hypotheses. Time includes tools,
opponent computation and scheduling. Six scenarios offer descriptive evidence;
they do not establish a statistically reliable effort ranking. Related games
share a scenario/deal-family ID and must stay together in dataset splits.

Replay every complete journal. Retain source hashes, engine/model identities,
prompt hashes and decision-linked reviews. Reviews remain unvalidated. Check
specific alternatives before treating them as expert targets. No automatic
transcript-access audit is available.

For the Linux handoff, retain curated JSON action records in the repo. These
records reference the matching local card catalog instead of embedding it or
machine paths. They are offline game evidence, not live player context and not
yet encoded training batches. Full privileged engine journals remain local.
The previously deferred SQLite/compression/training-format decision remains
future work.
