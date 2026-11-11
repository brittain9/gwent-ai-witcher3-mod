# Versioned LLM play context

The v1 pack contains [player instructions](player-v1.md), a frozen
[mechanics reference](mechanics-v1.md), and a [post-game rubric](review-v1.md).
Supply all three to a fresh player, plus its endpoint, capability, seat, and
execution limits. The live structured setup supplies card-specific descriptions.

Freeze a pack for each batch. Change behavior or rules in a new version after
that batch starts. Record SHA-256 hashes of the exact files and retain copies
with the private batch manifest. The general mechanics guide may evolve; the
versioned snapshot records what a particular player was told.

For an initial connector pilot, use a few complete games against distinct
controllers, with explicit limits and all attempts retained. This tests the data
path; it does not rank opponents. A strength comparison needs many paired deals,
seat swaps, consistent decks and compute budgets, and uncertainty estimates.

The November 10 pilot requested Luna 6 at xhigh in fresh session subagents. It
uses one game each against stock, direct network, and bounded network tree
search. All use Northern Realms decks; this does not cover other factions or
all special choices. Journals remain private and reports remain unvalidated.

Store separately:

- Exact game events and replay verification.
- Model, engine, opponent configuration, prompt hashes, seeds and limits.
- Player decision summaries and post-game hypotheses.
- Completion/failure accounting and fair-access audit status.

Before admitting demonstrations to training, qualify both information access
and decision quality. Use verified alternatives or search comparisons for labels.
Keep entire related games together when creating dataset splits.

## Version 2 and review

The [v2 player prompt](player-v2.md), [mechanics](mechanics-v2.md), and
[review rubric](review-v2.md) clarify Tight Bond, faction counts, output limits,
and report decision IDs. They are a separate post-pilot pack. The original
three-model comparison uses v1 throughout; one later Luna game checks v2.
See the [readiness review](readiness-review.md) for verified fixes and limits.

The [session results](session-2026-11-10.md) compare outcomes, time and decision
quality for Luna, Terra and Sol.

## Additional Terra effort panel

The [v3 player prompt](player-v3.md) uses a session command with automatic
connection arguments. The [additional panel results](terra-effort-results.md)
cover 16 completed Terra games and one partial run, with medium/high/xhigh effort,
varied decks and opponents. The user closed the batch after existing games
finished. See the [curated corpus](../../records/llm/README.md) for all 29 complete
pilot/panel games and the partial game, including replay evidence and reviews.
