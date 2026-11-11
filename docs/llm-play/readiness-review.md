# LLM connector readiness review

The connector is ready for bounded exploratory games and weakness collection.
This is not a certification of superhuman play, expert demonstrations, or every
native rule combination. Storage migration is deferred at Alex's request.

## Standards

Independent review found one material lifecycle defect: invalid engine responses
could leave the match active and later label it merely incomplete. The fixed
fatal-protocol path stops the engine, invalidates the lease, and records the
failed operation and safe reason. Illegal or stale player submissions remain
recoverable. Public-interface tests cover wrong-seat observations, missing acting
players, changed decisions, and illegal opponent selections.

A separate reproduced bookkeeping bug replaced action-limit status on close.
The regression test now verifies that the original reason survives shutdown.
No further material standards or maintainability findings were reported.

## Spec

Independent review found two interface gaps, both fixed:

- Scoia’tael start options now expose `starting_seat` instead of overloading a
  card-template field. Native tests exercise both choices.
- Journal, player state, and review records share one stable `session_id`.
  Earlier pilot files remain associated by their batch manifest and file layout.

Rules review confirmed that Tight Bond counts linked units on the same row;
linked hand/deck cards do not count or enter play. The `summons` field is also
used for bond relationships. Monsters keeps one eligible non-hero unit from its
controlled board when the match continues. Skellige revives up to two eligible
units at the third-round boundary. The v2 mechanics snapshot clarifies these
rules without prescribing strategy. V1 remains frozen for the comparison panel.

No concrete hidden-card leak was found in the reviewed connector paths. Snapshot
history supports ordinary Decoy and graveyard-to-hand deductions. This review
does not prove every transient chain is covered.

## Verification and interpretation

- Connector: 27 tests passed, including native games, replay and start choices.
- Ruff and the public-source check passed.
- Rules-engine integration suite: 52 tests passed.
- Broader engine review found one stale Scoia fixture before ledger decoding.
  The test expected a manual opponent-seat coin choice that current native flow
  does not offer. A test-only correction passed all 136 library tests in an
  isolated copy and again in the owning engine repository after applying it. It changes no
  game rule. The comparison's faction-pool search bypasses that decoder.

The stock controller is a native behavior reference. Its implementation reads
both internal hands while calculating potentials. Do not label it a fair-view
opponent. Direct network inference uses the opponent seat view; tested search
configurations use the explicit faction-pool belief. The LLM interface exposes
only its assigned player's information in each mode.

The three-model panel shares initial deals/decks/seats per scenario. Stock
controller random seeds were recorded per action but were not held equal across
models. Search is deadline-bounded and not guaranteed to make identical choices
across runs. Start-to-terminal wall time includes scheduling, tools and opponent
work; local checks also ran during the session. These are descriptive timings,
not isolated inference benchmarks.

Session agents have shell tools. Prompt restrictions are not an OS sandbox, and
no automated transcript-admission audit exists yet. Replay proves recorded game
transitions, not optimal decisions or proper tool access. Human/LLM reports stay
unvalidated until checked. Exact executable hashing also prevents the current
replay command from accepting a different Mac/Linux build without further
portable identity design.

## Next session

Validate useful decision alternatives under fair information. Prefer local,
checkable claims over hindsight judgments using later hidden draws. Keep model
comparison games separate from prompt-tuning games. Add faction and history
features through a versioned input contract before spending heavily on training.

Use v2 prompts for new collection. Measure output size and repeated context
before adding an incremental player protocol. Complete the deferred storage
benchmark and portable curated-record format described in the
[next-session plan](../next-session-plan.md).

The later Terra panel added a CLI capability regression: values starting with a
dash are now safe in generated capabilities and session commands. It passed
the failing-first subprocess test and all 27 connector checks. See the
[additional results](terra-effort-results.md) for the closed batch and its
operational interventions.
