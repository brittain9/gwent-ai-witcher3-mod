# Learning direction and open questions

Recorded November 10, 2026. This is a design discussion and source audit, not
a measured strength result. The aim is a strong Witcher 3 Gwent player with
learned judgment. More games and rented GPUs are options to assess, not a
selected experiment or an approved purchase.

The [player information inventory](player-information-inventory.md) expands the
information requirements into observed facts, memory, exact deductions, beliefs,
effect coverage and proposed acceptance checks. Its later audit distinguishes
the mod's network inputs from the separate research system's existing search
and public-history handling.

## Decisions from the discussion

- Use explicit faction information in future training and matching inference.
- Preserve information a player can legitimately remember, including cards
  publicly returned to a hidden hand. Represent uncertainty when later events
  make identity or location uncertain. Do not recover that knowledge by reading
  hidden engine state.
- Audit all relevant player information, including own deck composition without
  deck order. A board snapshot alone need not describe everything a player knows.
- Develop learned judgment. Search plus a network is a preferred direction to
  investigate, not yet a demonstrated improvement over the current policy.
- Examine the 2018 Gwent implementation for reusable ideas. Its rules, weights,
  encodings and measured results do not transfer directly to Witcher 3 Gwent.

Alex raised 50 million simulated games, compact storage, the local RTX 3080,
and a possible week of rented GPU time. No job, rental or training run was
started in this discussion.

## What the current mod does

Source inspected at `2d82b2527050ce4326cf38aa2c8938ab01466ca7`.

The [encoder](../learning/encoding.py) supplies own hand, visible board cards,
current board strength, weather, modifiers, graveyards, leaders, scores, lives,
round results, hand/deck counts, passing and leader-use status. Legal actions
are encoded separately. It has no explicit own-deck composition or sequence
of earlier actions. The [model](../learning/model.py) has no recurrent memory.
There is no dedicated remembered-opponent-hand input.

The [input contract](../scripts/policy_format.py) identifies the original
shared-model checkpoint as trained without explicit factions. The build keeps
that input contract for those weights. Visible leaders and cards can still
provide indirect faction clues. Future training should use real factions;
changing inputs under existing weights alone is not a validated fix.

The [controller](../mod/ai/as3/GwaiSharedController.as) runs the network and
accepts its selected move, apart from an immediate-match-win passing guard.
It does not search future positions. The outcome estimate is not a comparison
of simulated candidate moves. This checkout does not contain the training-side
rule simulator or PPO optimization loop, so this audit does not diagnose their
correctness or training quality.

## Useful examples in the 2018 project

The requested `git pull --ff-only origin main` completed in
`gwent-beta-ai`, reaching
`57660e68ab3f6dae525e961309dcfdcc924b906e`. The checkout was clean afterward.
The following are implementation capabilities, not claims that each caused
the stronger play Alex reports:

| Source in the 2018 checkout | Useful idea |
|---|---|
| `GwentAI/Learn/StateEncoder.cs` | Own deck as an unordered collection, explicit factions, and an opponent-known-hand zone |
| `GwentAI/Search/Belief.cs`, `Remember` | Retain identities observed by the player across later turns |
| `scripts/train_policy.py` | Train move preferences against search visit distributions |
| `scripts/train_value.py` | Train position judgment from match results |
| Policy network research note | Use network preferences to guide search and simulated continuations |
| `scripts/train_damped.py` | Alternate fresh no-search self-play with controlled policy/value updates |
| `scripts/encode_cache.py` | Versioned NumPy binary caches avoid repeated JSON parsing |
| `scripts/bench_pack.py` | Compress retained records in zstd archives |

Memory handling must be adapted to Witcher 3 events and tested for information
leaks. The 2018 implementation is a design reference, not an automatic fairness
proof for a port. Its current STATE also locates Witcher 3 research in a separate
`witcher3-gwent-ai-research` sibling. That directory exists locally at
`witcher3-gwent-ai-research`. The subsequent information
inventory inspected its rules and information paths. It already has search-side
memory and a viewer-scoped evidence ledger; this is separate from the network's
limited input schema. The causes of unsuccessful training remain unestablished.

## Product approaches discussed on November 10

The shipped mod must respond within a reasonable time on player hardware.
Development-time search, data generation and training can take much longer.
An illustrative target discussed was most decisions within one second and hard
decisions within a few seconds. This is a proposed target, not an agreed hard
limit or a measured capability. Measure latency in the actual mod.

| Approach | Training/development | Shipped player | Main tradeoff |
|---|---|---|---|
| Longer direct self-play | Alternate fresh games and network updates | Network chooses directly | Simple deployment; progress and coverage must be measured |
| Network plus search | Learn preferences and outcomes; improve fair search | Bounded search guided by network | Tactical checking costs player time and integration work |
| Search teacher and fast student | Stronger search generates move targets; matches provide outcome targets | Fast network, optionally short search | Moves expensive thinking offline; student can inherit teacher errors |
| Handwritten judgment plus search | Write scoring rules and explore continuations | Search with those scores | Explainable but difficult to cover all strategic interactions |

Recommendation: build and verify a stronger search teacher, train a fast student,
then compare the student alone and with bounded search inside the mod. Keep longer
direct self-play as a real comparison; adopt it if it gives the better product.
Do not presume copying a teacher's choices proves stronger match play.
This teacher/student cycle is commonly called expert iteration.
[Original research](https://proceedings.neurips.cc/paper/2017/hash/d8e1344e27a5b08cdfd5d027d9b8d6de-Abstract.html).

Alex also requested an LLM connector for the headless client, modeled on the
2018 project's agent-play tools and improved for Witcher 3. It should support
session subagents and LLMs against configured opponents, retain games, and help
find weaknesses and propose labels. Alex explicitly requested implementation
in this chat. LLM play and judgments are candidate evidence, not automatically
high-quality training labels or a replacement for a verified search teacher.
The [next-session plan](next-session-plan.md) owns the proposed follow-on work.

## Training loops

**Fresh self-play updates:** play a batch with the current network, learn from
it, then generate another batch with the updated network. Standard PPO is
on-policy: it uses experience from the policy being updated. Generating all
50 million games first and repeatedly fitting them is not the normal PPO loop.
Retaining old games is still useful for audits and other training methods.
[PPO reference](https://spinningup.openai.com/en/latest/algorithms/ppo.html).

**Learning from search:** use search to produce move targets and completed
games to produce outcome targets. Train the network to guide later search.
Refresh the data as the player improves. This is the useful principle behind
AlphaZero-style loops, but Gwent needs hidden-information handling rather than
a direct copy of perfect-information search.
[OpenSpiel explanation](https://github.com/google-deepmind/open_spiel/blob/master/docs/alpha_zero.md).

Neither method guarantees improvement. More games cannot restore information
discarded by an encoder. They can also repeat weaknesses of a narrow opponent
or deck pool. Compare independent match outcomes as training progresses.

## Storage and time for 50 million games

These are arithmetic examples, not measurements of Witcher 3 records.
Units below are decimal. Total size equals games times stored bytes per game.

| Average stored record per game | Total for 50 million games |
|---|---:|
| 10 KB | 500 GB |
| 100 KB | 5 TB |
| 1 MB | 50 TB |

At an assumed 50 recorded decisions per game, 50 million games produce
2.5 billion decision records. At 1 KB per decision that is 2.5 TB before
additional metadata, copies, caches or checkpoints. Compression varies by data.

| Aggregate completed games per second | Generation time |
|---|---:|
| 10 | 57.9 days |
| 100 | 5.8 days |
| 1,000 | 13.9 hours |

These exclude separate training and evaluation costs. Search budget changes
generation speed substantially. Measure bytes and decisions per game, aggregate
games per second, CPU use, GPU use and training throughput on the actual pipeline.

Keep compact, versioned game/action records for reproducibility and sharded
numeric arrays for training. Avoid storing every dense observation if it can
be reconstructed accurately and cheaply from retained records. Preserve seeds,
initial conditions, stochastic outcomes as needed, rule/encoder/model versions,
legal actions and training targets. PPO additionally needs the generating
policy probabilities and rollout data required by its update. Replays must
reproduce observations, legal moves and outcomes before replacing source data.
Do not assume an uncompressed NPZ cache minimizes disk usage; the 2018 cache
primarily improves loading speed. Compression and fast training access solve
different problems.

## Proposed next steps

1. Locate the current Witcher 3 research source, checkpoints and training records.
2. Specify the complete fair-player information contract. Test memory across
   returns, plays, discards and any shuffle or identity-changing effects.
3. Train new weights with matching faction and memory inputs. Preserve the
   current model as a comparison baseline.
4. Compare policy-only play with bounded search using the same network, deals
   and independent opponents. Reproduce concrete poor decisions as diagnostics.
5. Profile a bounded local run on the RTX 3080 machine. Measure both learning
   progress and where time is spent before selecting larger compute.
6. If scaling is justified, size the rental from measured throughput, storage,
   utilization and a spending/stop limit. Recheck current provider prices then.

A GPU accelerates network computation; it does not automatically accelerate
a CPU rule simulator or tree search. A week-long rental is only useful if the
pipeline feeds it efficiently and additional training is improving play.
