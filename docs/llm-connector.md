# Headless LLM player connector

The connector lets an LLM or session subagent play one fixed seat against a
configured engine opponent. It runs the existing headless rules engine through
a trusted Python host. The player receives readable structured observations and
legal options; it cannot request another seat or arbitrary engine operations
through this interface. No provider SDK or paid API account is required.

The connector is development tooling. It does not install or change the mod.
It requires a separately built compatible headless engine and trusted private
game/model inputs. The public source does not contain the original game assets.

## Start a match

The trusted orchestrator prepares two private JSON files:

- `match.json`: the engine's `new` fields: `cards`, two `decks`, two `leaders`,
  `factions`, `seed`, and the intended `seat_kind` array. Optional engine setup
  fields must match that engine's protocol. Do not give this file to the player:
  it includes the other deck and the deal seed.
- `opponent.json`: one supported opponent configuration below. The host supplies
  the opposing seat and match identity; the player cannot change them.

From the repository root:

```sh
python3 scripts/llm_play.py serve \
  --engine /path/to/w3gwent-engine \
  --match-config /private/path/match.json \
  --opponent-config /private/path/opponent.json \
  --journal /private/path/game.json \
  --reports /private/path/reports.jsonl \
  --seat 0 --model MODEL_ID --prompt-version PROMPT_VERSION \
  --max-seconds 3600 --max-actions 512 --timeout 30
```

The server binds only to `127.0.0.1` and prints a URL and bearer token. Give
these to the player. Keep the process owned by the orchestrator. Ctrl-C or
SIGTERM closes the host and engine. The session deadline bounds idle lifetime;
an active engine call has its own timeout. Incomplete deadline expiry returns
a nonzero exit status. Completed games can remain available for reports until
the server is stopped or its deadline expires.

`--model` and `--prompt-version` are recorded claims from the orchestrator, not
proof of which remote model actually produced the moves. Retain the actual
agent/API transcript separately when dataset eligibility requires that proof.

## Configure the opponent

Original engine opponent:

```json
{"op":"stock_act"}
```

Direct trained model:

```json
{
  "op":"network_probe",
  "policy_metadata":"/private/path/metadata.json",
  "policy_weights":"/private/path/policy.f32.bin"
}
```

The direct model mode uses group-first deterministic selection with an immediate
winning-pass guard. It does not expose a temperature setting. Verify its model,
encoding and selector before describing it as any particular shipped opponent.

`network_search` and `network_tree` accept their engine configuration fields,
including model paths, search budgets, world seeds and deadline. Supply an
explicit fair belief (`faction_pool` or `recipe_public_v1`); a recipe prior also
needs the engine's public-history setup. Hidden-deck-list search is rejected.
Use configurations validated for the exact engine version. Supporting these
modes does not mean every search configuration or model has been qualified.

This means any compatible model through the supported controller modes, not
arbitrary external executables or every possible opponent implementation.

## Player commands

Read the current decision:

```sh
python3 scripts/llm_play.py state --url URL --token CAPABILITY
```

Choose one of the returned option IDs, using the decision ID and its separate
decision token:

```sh
python3 scripts/llm_play.py move --url URL --token CAPABILITY \
  --decision-id 12 --decision-token DECISION_TOKEN --action option-3 \
  --rationale "Brief evidence or intended move consequence."
```

The response is the next player decision or final state. Opponent turns run
inside the host. Handle every pending choice through the same interface.
Scoia’tael starting-player choices expose `starting_seat` as 0 or 1; these
values identify players, not card templates.

Option numbers are local to one decision. Reusing an old decision or token is
rejected. A transport failure after a submitted move requires reading `state`
before deciding whether anything needs resubmission.

Use the [mechanics reference](llm-player-rules.md) without adding untested
strategy advice. Card definitions, public setup, own initial deck, current
observation and visible history are provided by the host. The host preserves
permitted reveal information; it does not yet calculate every deduction in the
[information inventory](player-information-inventory.md).

Propose a label for a decision already delivered to the player:

```sh
python3 scripts/llm_play.py report --url URL --token CAPABILITY \
  --decision-id 12 --label possible-mistake \
  --report "Describe the observed move, a legal alternative and uncertainty."
```

Reports are separate append-only JSONL records marked `unvalidated`, associated
with a session and decision. The stable `session_id` appears in the journal,
player state and every report, so moving the files does not lose their link. They are not correctness labels or training
targets until reviewed. Notes should be concise evidence summaries; collecting
full internal reasoning is not required.

## Replay and data eligibility

After closing the host:

```sh
python3 scripts/llm_play.py replay \
  --engine /path/to/w3gwent-engine --journal /private/path/game.json
```

The journal uses `w3gwent-llm-session-v1`. It is not the browser client's journal
format. It retains privileged engine commands/responses and never goes to a live
player. Replay checks deterministic game transitions, action count, completion
and engine executable identity. It does not rerun time-dependent network search
selection, prove good decisions, or establish model playing strength. Model
artifact hashes are recorded separately from paths.

Keep completed, interrupted and failed attempts. Before training or evaluation,
verify game completion, replay, the information protocol, opponent identity and
resource validity. Link labels to source decisions and split data by game/deal,
not by individual rows from the same game.

## Session subagents

The orchestrator starts the server and gives a fresh `fork_turns="none"` player
only its URL/token, the mechanics reference, and the allowed state/move/report
commands. Start a new player context per game. During live play, do not give it
private setup, journals, engine access or the other player's notes.

The HTTP interface enforces its own seat boundary. It is not an OS sandbox for
a subagent that also has filesystem/shell tools. Prompt isolation alone does
not prove fair play. This first version has no automated transcript-admission
audit; review actual tool use before admitting session-agent games to a fair
dataset. An external runner restricted to this interface avoids granting raw
engine/file access in the first place.

## Development checks

```sh
python3 -m unittest discover -s tests -p 'test_llm_play.py'
W3GWENT_ENGINE=/path/to/w3gwent-engine \
  python3 -m unittest discover -s tests -p 'test_llm_host.py'
```

Without `W3GWENT_ENGINE`, native integration checks are skipped. A skip is not a
successful native qualification. Tests exercise the public connector, including
fixed-seat visibility, stale/illegal actions, full games, replay and cleanup.

## Versioned player context

Use the [versioned play pack](llm-play/README.md) for repeatable data collection.
Its player instructions, mechanics snapshot and review rubric have independent
file hashes in each private batch manifest. Keep post-game reports separate from
verified training targets.

## Session command for repeated collection

The v3 player pack uses a per-session command that supplies its connection
arguments. This avoids making an agent copy a long capability on every move.
The trusted orchestrator creates this command after starting the host:

```sh
#!/bin/sh
exec python3 /path/to/scripts/llm_play.py "$@" \
  --url=http://127.0.0.1:PORT --token=CAPABILITY
```

Keep this command local with owner-only permissions. Give the player its path
and allow only `state`, `move` and `report` invocations. The player does not need
to inspect the command file. Start a fresh player context for each game.

The [Terra effort panel plan](llm-play/terra-effort-plan.md) freezes 18 varied
games with this interface. [Curated action records](../records/llm/README.md)
carry the portable offline evidence; the existing replay CLI still expects the
full engine journal.
