# Curated LLM game records

These JSON files retain completed and partial exploratory games for the next
session. Check `completed` and `status` before using an outcome. An incomplete
game is not a loss and does not have a terminal training target.
They contain offline full-match action sequences and must not be passed directly
to a live player. Reconstruct that player's observations before making training
examples. Post-game reports and move notes are subjective and unvalidated.

`w3gwent-action-record-v1` stores:

- Game, scenario/deal-family and session IDs, model and reasoning effort.
  Legacy pilot journals have a null session ID; their paired reports and source
  hash retain the association.
- Initial setup and ordered engine commands, without the card catalog.
- Engine, card-table, policy, prompt and source-journal hashes.
- Expected response hashes, terminal result and replay verification.
- Decision summaries and post-game reports with their evidence status.

It omits connection credentials, player decision tokens, machine-specific paths,
repeated observations, and bulk card definitions. Full engine journals remain
local. Related scenarios at different effort levels must share a dataset split.

## Replay contract

These are compact action records, not the full journal accepted directly by
`llm_play.py replay`. Replay needs the matching local card-definition list and
engine executable. Hash structured values as SHA-256 of UTF-8 JSON using sorted
keys, separators `(',', ':')`, and `ensure_ascii=True`.

1. Check the engine file hash and the normalized card-list hash.
2. Send `new` with `schema_version=1`, the recorded setup, and that card list.
   Verify the complete result against `opening_response_sha256`.
3. Send each recorded command in order and verify the complete result against
   `response_sha256`. Stock selection calls are retained because they are part
   of the original deterministic protocol sequence. Network/search selection
   calls are omitted; their selected moves are preserved as step commands.
4. Observe the recorded player seat and compare its terminal result.

The collection process runs this reconstruction before accepting each exported
record. A later Linux build will need a portable engine identity policy; current
exact executable hashing identifies the verified Mac build. Do not silently
ignore a hash mismatch or declare different builds equivalent.

This small curated collection is Git-friendly. Bulk generated data, database
files, compression choices and numeric training batches remain part of the
[deferred storage work](../../docs/next-session-plan.md).
