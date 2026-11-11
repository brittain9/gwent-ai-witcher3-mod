# Recommended next session

Recorded November 10, 2026. This is a proposed engineering and research order,
not authorization for a timed research session, training run or rental. Alex
explicitly authorized implementing the headless LLM connector in the current
chat. Keep the existing mod and client installations unchanged while qualifying
new tooling.

## Objective

Create stronger learned judgment that runs quickly in the shipped mod. Use
slower development-time search as a potential teacher. Compare a fast student
alone and with bounded search; retain longer direct self-play as a competing
approach. The four [product approaches](learning-direction.md) remain options,
not an assumption that expert iteration must win.

## First use of the LLM connector

Use the connector to run fresh, isolated player agents against explicitly
configured opponents. Start with complete exploratory games, then replay them
and extract decisions worth investigating. The player sees its own information,
legal choices, public rules and permitted memory, not raw engine responses or
the other player's private state.

Use LLMs for:

- Producing diverse game trajectories and opponents.
- Finding apparent tactical and strategic weaknesses.
- Proposing alternative legal moves and concise evidence-backed labels.
- Reviewing a player's own decisions, not just criticizing its opponent.
- Supplying candidate demonstrations after qualification.

An LLM win does not make each move correct. A plausible explanation is not a
verified label. Store reports as unvalidated hypotheses linked to exact decisions.
Separate legality/replay checks, fair-view status, opponent validity, and strength
evidence. Failures and abandoned attempts remain part of the accounting.

The 2018 audit recommends a trusted engine adapter, an atomic decision interface,
one fresh player agent per game, and journals separate from subjective reports.
Improve on its pitfalls: no truncated rules, no stale option reuse, no permissive
unknown-transcript audit, no automatic eligibility for resource-starved opponents,
and no strategy advice disguised as mechanics. A session subagent with filesystem
tools is not OS-sandboxed merely because its prompt says to use the connector.
Audit actual access before accepting that game as fair training/evaluation data.

## Recommended order

1. **Qualify connector use on the real client engine.** Exercise each configured
   opponent mode, both seats and all pending choice types. Check clean shutdown,
   stale requests, partial failures, private reveals, game completion and replay.
   Do not call a model configuration the shipped opponent unless its actual
   selector, guard, model and search settings match.
2. **Collect a bounded exploratory LLM panel.** Freeze model/prompt/rules versions,
   opponents, deals and resource limits before the panel. Use fresh agents with
   no inherited private match context. Keep diagnostics distinct from a strength
   benchmark. Choose the game count from cost and question, not a standing quota.
3. **Verify candidate weaknesses.** Reproduce the recorded move and continuation.
   Test alternatives under the same fair information. Keep uncertain judgments
   uncertain; use privileged diagnostics only with an explicit oracle label.
4. **Select the information changes.** Use the [inventory](player-information-inventory.md)
   to implement matching faction, deck and memory inputs across training and
   deployment. Preserve a baseline and version the new input contract.
5. **Compare learning routes.** Test whether additional fresh self-play or a
   stronger search teacher improves independent full-match play. If a teacher
   is demonstrably useful, train a fast student and measure what strength survives.
6. **Profile compute and latency.** Measure generation, encoding, loading,
   optimization and actual mod response times. Use the local RTX 3080 first;
   select rented compute only for a demonstrated bottleneck and explicit budget.

## Storage and training data

Alex requested evaluating a more efficient format than JSON for volume, training
and LLM use. This is worth doing, but these consumers need different forms:

| Consumer | Proposed representation | Purpose |
|---|---|---|
| Replay and durable evidence | Versioned compact event records in compressed shards | Preserve exact reproducibility and provenance |
| Training | Sharded numeric arrays or columnar batches | Avoid repeated JSON parsing and support efficient batches |
| LLM player/reviewer | Concise readable seat-specific JSON/text generated on demand | Preserve clarity and prevent hidden-state exposure |

Do not send opaque binary records to an LLM. Do not store a new copy of the entire
game snapshot for every consumer when one authoritative record can reconstruct
their views. Preserve necessary initial state, randomness, actions, public/private
visibility, model/rules identities and training targets.

Compare a small representative corpus in current JSONL, compressed JSONL, and
one compact candidate such as Arrow/Parquet or typed array shards. Choose the
candidate after inspecting actual record shape and training access patterns;
do not implement all formats. The 2018 project already has uncompressed NumPy
caches for faster loading and zstd archives for storage. Those are useful
references, not proof of the best format at Witcher 3 scale.

Measure bytes/game and bytes/decision; encode/decode time; sequential and sampled
batch throughput; peak memory; compression cost; exact replay; schema evolution;
partial-write recovery; and seat-view reconstruction. Report original and derived
storage separately. Retain originals until conversion and replay are verified.
Keep train/validation/evaluation splits by source game/deal family, not individual
rows from the same match. Preserve source links for every LLM or search label.

## Deliverables

- Usable LLM-player commands with exact opponent identity and replayable journals.
- A small reviewed set of weaknesses and labels with evidence status.
- A chosen information-contract change with behavioral tests.
- A storage comparison and one justified format decision.
- A measured learning/compute plan, and eventually a fast playable candidate.

The first connector does not need a provider-specific paid API SDK. A session
subagent or external LLM runner can use the same observe/act commands. The shipped
mod remains local; an LLM subscription is not a player requirement.

## Current-session update

Alex subsequently authorized a bounded Luna 6 xhigh pilot through the CLI.
The [v1 context pack](llm-play/README.md) freezes the player instructions,
mechanics, and post-game rubric for three exploratory games against stock,
direct-network, and network-tree controllers. Treat this as connector and data
quality work. Follow up by validating reported weaknesses, extending faction
coverage, and measuring storage on the actual journals before scaling generation.

## Deferred local storage decision

Alex prefers local storage on the Linux training machine. LLM and human-played
games may be retained in the repository for portability between Mac and Linux.
He explicitly deferred storage implementation to a later session.

Recommended starting point: a local SQLite index for game metadata, results,
model/prompt identities, provenance and review status; compressed immutable game
records for replay; and derived numeric shards for training. Keep the live
SQLite database and bulk self-play corpus outside Git. Prepare portable curated
LLM/human records without bundling the game catalog or machine-specific paths.
The current session retains raw journals locally; that is not yet cross-machine
synchronization.

SQLite supports local application storage but permits one writer at a time.
Use batched ingestion through one writer if many generators run concurrently.
See [SQLite appropriate uses](https://www.sqlite.org/whentouse.html). Database
queries help selection and bookkeeping; they do not establish optimal GPU batch
throughput. Benchmark compressed JSON records against a compact alternative and
numeric training shards. [NumPy memory mapping](https://numpy.org/doc/stable/reference/generated/numpy.load.html)
can load array slices without loading an entire file; it does not provide
compression. [Parquet](https://parquet.apache.org/) is a columnar, compressed
candidate for tabular analysis, not a mandatory extra layer.

A preliminary measurement on six completed journals in this session was
3,189,793 JSON bytes versus 103,492 gzip bytes (about 31 times smaller). This is
an in-memory compression measurement on a tiny repetitive corpus, not a training
throughput benchmark or a large-scale capacity estimate. Preserve replay and
provenance when testing any conversion. Do not start a migration in this session.

## Session handoff

See the [13-game results](llm-play/session-2026-11-10.md) and
[readiness review](llm-play/readiness-review.md). The connector and versioned
prompts are implemented. All games completed and replayed; hosts were stopped.
At that handoff, storage and portable record export were deferred. No training,
rental, mod installation or publication was started. The later Terra panel
below adds curated portable action records; database migration stays deferred.

## Additional Terra effort panel

Alex authorized an additional 18-game panel after the initial handoff. The
[frozen plan](llm-play/terra-effort-plan.md) compares medium, high and xhigh
across six varied scenarios. Preserve its results and portable action records
for Linux. The panel does not authorize training, rentals or database migration.

## Batch closure and recurring weaknesses

Alex then requested finishing only the already running games and reporting the
results. No further primary game or reserve retry was launched after that
instruction. Retain the partial medium run and label unstarted slots explicitly.

The recurring candidate weaknesses are round-resource planning, passing under
match-loss risk, anticipating Scorch/Medic replies, and planning for large
Muster or resurrection continuations. In several losing runs the pass rationale
focused on forcing the opponent to spend cards despite the player having only
one life. The observed consequence is clear; the optimal alternative still
needs fair counterfactual analysis. Xhigh did not consistently outperform
medium or high in these small paired samples.

The player context provides general mechanics and known-card definitions, not a
complete public faction-card reference. Test an on-demand public rules/card
lookup before attributing every surprise to model judgment. This can expand
public knowledge without exposing an opponent deck. The next data-quality work
is to reconstruct fair views, validate useful alternative lines, and implement
the selected faction/history input changes. Do not train imitation targets
directly from every winning LLM move or post-game explanation.

## Final recorded state

The [Terra effort results](llm-play/terra-effort-results.md) record 16 new completed
games and one timeout. The batch was closed at Alex's request; no game remains
running and no reserve retry was launched. Across the earlier pilot and this
panel, [curated action records](../records/llm/README.md) now retain 29 complete
games, one partial game, 907 engine actions and 43 unvalidated reports. They were
reconstructed and checked before export. Full journals remain local.

The final connector check is 27 passing tests. The engine has 136 passing library
tests and 52 passing integration tests. A CLI capability beginning with a dash
was reproduced and fixed; capture limits were raised for long responses.

Four completed scenarios are shared across all three Terra efforts: medium and
high each won 2/4; xhigh won 1/4. The larger raw totals have unequal case coverage.
These data do not establish an effort ranking. Continue to validate decisions
and improve public-rule context and input features rather than assuming longer
LLM reasoning creates expert labels. Database/training storage work stays
explicitly deferred.

The separate rules-engine test correction is committed in
`witcher3-gwent-ai-research` as `df4e68b` on
`codex/scoia-history-testfix`. It changes the stale fixture only. The connector,
versioned contexts, reports and curated records are retained in this mod repo.
Alex subsequently set commit-and-push as the default for completed repository
work. Keep the source, decisions and curated records committed and pushed at
each handoff. The mod work is on `codex/llm-games-and-handoff`; the engine
fixture correction is on `codex/scoia-history-testfix`.
