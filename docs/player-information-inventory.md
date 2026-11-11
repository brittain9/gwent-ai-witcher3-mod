# Witcher 3 Gwent player information inventory

This is the information a fair player can observe, remember, calculate, or
estimate. It is a menu for model design, not a requirement to feed every item
into a network. Preserve the underlying evidence first. Choose model features
afterward.

Prepared November 10, 2026. Scope: Witcher 3 Gwent, including Skellige and the
effect families implemented in the current research engine. This is not 2018
Gwent. Rules below must be tied to the intended native game version and seat
behavior. Engine support does not by itself prove native-game parity.

There is no finite list of every possible strategic deduction: a player can
combine observations into arbitrarily many plans. This inventory instead covers
the information sources, state changes, constraints, and major derived features
needed to make those deductions. New effects require a new coverage review.

## 1. Four kinds of information

| Kind | Meaning | Example |
|---|---|---|
| Observed fact | The rules or interface reveal it to this player | A card in our hand; a face-up unit on their board |
| Remembered fact | Previously observed, with a valid history of later changes | A unit publicly returned to their hand by Decoy |
| Exact deduction | True in every hidden state consistent with our evidence and the rules | At least one copy of that returned card remains in their hand if nothing could remove it |
| Belief or judgment | Plausible, but not logically certain | They may be saving a Scorch; this deck probably contains more spies |

Never turn a belief into a fact. Unknown is a separate state from zero or absent.
Track whether history is complete from setup; missing history weakens deductions.
Keep knowledge separately for each player. A private reveal to one player does
not reveal the same cards to the other.

## 2. Match setup and rule context

| ID | Information to retain | Boundary or use |
|---|---|---|
| S01 | Rules/catalog version and supported expansions | Defines effects, eligibility and round transitions |
| S02 | Our seat, current actor, current phase and pending choice | A target choice is not a new ordinary turn |
| S03 | Both factions and faction abilities | Public game/setup context; explicitly supply them because the research observation currently omits them |
| S04 | Both selected leaders, their passive effects, use state and cancellation | Distinguish used, disabled and temporarily unusable |
| S05 | Our submitted deck as a multiset of card types and counts | Known from our own deck construction; exclude shuffled order |
| S06 | Publicly displayed starting deck/hand sizes and later counts | Confirm visibility for the native UI; distinguish counts derived from history |
| S07 | Starting-player choice, who made it, who started each round | Include Scoia'tael choice when applicable |
| S08 | Initial deal, our extra draws, our mulligan choices and replacements | Own private facts; opposing replacements stay hidden |
| S09 | Deck-building constraints for this opponent class | NPC decks and generated cards need not obey player collection assumptions |
| S10 | Declared opponent context | Public faction/leader are facts; exact NPC deck lookup is a separate information protocol |

Our full collection is optional pre-match information for deck building. It is
not the same as the deck submitted for this match. Opponent collection, exact
registered deck and save-file progression are not ordinary fair-player inputs.
If prior encounters or public NPC identity are permitted as side information,
declare that separately and evaluate separately from an anonymous opponent.

## 3. Current visible position

| ID | Information to retain |
|---|---|
| V01 | Our entire current hand, including duplicate counts and legal uses |
| V02 | Both boards, each unit's row, card type, base strength and current strength |
| V03 | Hero status, spy status, row eligibility, effects and summon/transform relationships |
| V04 | Weather affecting each row and all active row modifiers |
| V05 | Sources of Horn, morale, Tight Bond, weather resistance and other passive effects |
| V06 | Both graveyards, including card counts, identities and eligible retrieval targets |
| V07 | Total and row scores, current score margin and tied strongest eligible units |
| V08 | Remaining lives, round number, previous round winners and scores |
| V09 | Both hand and deck counts, with zero and unknown distinguished |
| V10 | Explicit passes from action history, current round-done status and whether either player can act again |
| V11 | Leader availability, pending effect resolution and any turn-resume state visible through rules |
| V12 | Our legal plays, targets, rows, leader actions, passing and mulligan actions |
| V13 | Private choice candidates visible to us, and how many selections remain |
| V14 | Card owner, current controller and current zone where publicly knowable |
| V15 | Whether a card was publicly generated, transformed or transferred, when established by events |

Do not expose the opponent's legal-action list: it can reveal their hidden hand.
Do not treat internal ownership/provenance fields as public merely because the
engine stores them. Derive persistent identity from observations and events.
Board position within a row is only useful if it affects a rule or visible identity;
do not import adjacency mechanics from 2018 Gwent.
The research observation's field named `passed` actually reports round-done,
which also includes having no cards and no usable leader. That field alone does
not establish an explicit pass.

## 4. History a human can remember

| ID | Event or memory | What remains known |
|---|---|---|
| H01 | Every public play, target and placement in order | Cards spent, effect chains, public choices and resulting zones |
| H02 | Decoy returns | Identity and destination hand of the returned unit; consume this knowledge when it leaves |
| H03 | A spy changes sides, is decoyed, revived or replayed | Track the same visible card rather than counting each play as a new deck copy |
| H04 | Leader reveals cards in the opponent's hand | Only the addressed viewer learns the identities; remember them afterward |
| H05 | A public graveyard card moves to a hand | Identity follows the observed retrieval, including retrieval from the other graveyard |
| H06 | A card leaves a known hand through public play or discard | Remove the matching known copy; preserve uncertainty among indistinguishable copies |
| H07 | Our own draws, discards, mulligans and deck choices | Update our known hand and remaining deck; do not assume a returned card cannot be redrawn without a rule guarantee |
| H08 | Opponent draws or exchanges cards | Count changes are evidence; hidden identities are not |
| H09 | Muster pulls cards from hand and deck | Record revealed cards and hand/deck count changes; do not invent a source for each copy if ambiguous |
| H10 | Public summons or transformations | Link source and result; distinguish generated cards from original deck evidence |
| H11 | Destruction, round cleanup and weather removal | Record each card's actual destination and any triggered follow-up effect |
| H12 | Graveyard cards shuffled back into a deck | Know the returned multiset at that time, not the shuffled order or later draw identity |
| H13 | Random carryover or resurrection | Eligible set before the event and the selected result after it becomes visible |
| H14 | Passed rounds and cards/resources committed to each round | Useful for both exact resource accounting and uncertain style estimates |
| H15 | Our own revealed information | Track what the opponent could have learned about us |
| H16 | Choice opportunities and choices declined | A behavior observation, not proof the opponent lacked another card |

### Decoy example

1. The opponent plays a unit. Its identity becomes public.
2. The opponent uses Decoy to return that unit to their hand.
3. We know their hand contains at least one copy of that unit.
4. An unrelated hidden draw does not erase that fact.
5. When they play that unit, reduce the known-hand count. Do not count it as a
   second original deck copy just because it appeared twice.
6. If an unobserved exchange or removal could have taken it, retain a possible
   location or count range instead of asserting it is still in the hand.

A visible card returned to a shuffled deck is different: its identity is known,
but a subsequent hidden draw can move it into the hand. Track that uncertainty.

## 5. Exact resource and hidden-state deductions

These require complete applicable history and correct rules. If either is
missing, keep bounds or mark the result unknown.

| ID | Deduction | Conditions |
|---|---|---|
| D01 | Our remaining deck multiset | Start with our submitted deck; apply draws, mulligans, summons, transfers, transformations and returns |
| D02 | Known opponent hand and unknown hand slots | Hand count minus distinct known copies; never double-count a replayed instance |
| D03 | Minimum distinct original copies shown by the opponent | Exclude generated, transferred and transformed copies unless origin is established |
| D04 | Cards certainly outside the opponent's hand now | Current public zones and tracked removals, with possible later retrieval accounted for |
| D05 | Remaining-copy upper bounds | Only when original copy limits/deck list are actually known; a sampler cap is not a game rule |
| D06 | Hidden hand/deck possibilities consistent with counts | Enforce total counts, known copies, transfers and public actions jointly |
| D07 | Muster-family depletion immediately after complete resolution | If the effect exhausts matching hand/deck cards, none remain there until later additions; retain all effect-chain exceptions |
| D08 | Number pulled from hand versus deck | Derive from before/after counts only when other simultaneous draws and moves are accounted for |
| D09 | Exhausted draw pile and number of draws still possible | Distinguish a draw attempt from an actual card gained |
| D10 | Legal resurrection/retrieval candidates | Compute eligibility from visible graveyard and active effects |
| D11 | Final-round resource horizon | Resources with no future usable opportunity may have no remaining value, subject to current effects |
| D12 | Guaranteed immediate round or match result from passing | Resolve ties, faction rules, lives and pending effects; score lead alone is insufficient |
| D13 | No further action this round | Distinguish explicit irrevocable pass from temporary lack of playable cards/leader actions |
| D14 | Opponent's knowledge of our returned/revealed cards | Mirror the public history; private reveals may leave uncertainty about exactly what they saw |
| D15 | Hidden-state impossibility | Reject a proposed world only when it violates evidence or a real rule, not because it is strategically unusual |
| D16 | Known deck-order constraints | Retain a top/bottom placement or ordering restriction only when an observed action and a verified rule establish it; erase that certainty after a shuffle |

An absent card is not automatically impossible. Faction restrictions usually
constrain the original deck, not every card that can reach a player's hand after
cross-player retrieval or transfer. Exact initial copy counts and copy limits
must not be inferred from the research sampler's default caps.

## 6. Effect coverage checklist

This groups the current engine's effect families. Static card definitions and
effect rules are shared knowledge; random outcomes and private candidates are not.

| Effect family | Information and calculations to preserve |
|---|---|
| Ordinary units and heroes | Identity, eligible rows, base/current power, immunity and target eligibility |
| Agile and leader repositioning | Alternative legal rows, score consequences and publicly completed moves |
| Spy/draw effects | Recipient side, points granted, actual draws, hand/deck deltas and later reuse |
| Decoy | Legal non-hero targets, returned identity, receiving hand and replaced board slot |
| Medic | Graveyard candidates, selected card, entry effects, nested choices and replayed spies/medics |
| Own/enemy graveyard leader retrieval | Source graveyard, destination hand and known acquired identity; do not confuse with direct board revival |
| Random resurrection modifier | Active modifier, eligible set, randomness before selection and visible identity afterward |
| Muster | Matching templates, all recruited copies, source-count constraints and depletion after the full chain |
| Tight Bond | Matching units and nonlinear changes after adding/removing a copy |
| Morale | Buff sources, eligible recipients and recalculation when a source leaves |
| Horn and leader Horn | Row scope, hero exclusions, overlapping-source rules and score change if a source is removed |
| Frost, Fog, Rain and multi-row weather | Affected rows, susceptible units, weather-resistant effects and replacement/stacking rules |
| Clear Weather and leader clearing | All affected weather and resulting score changes on both sides |
| Global Scorch | All tied strongest eligible units on both sides and effects triggered by their removal |
| Row Scorch and leader variants | Row scope, activation threshold, hero exclusions, strongest ties and resulting scores |
| Death-triggered summons | Triggering unit, generated result, actual timing, new row and future-round consequences |
| Mardroeme/Berserker transformation | Trigger, original/result identity, changed strength/effects and later return/revival semantics |
| View-opponent-hand leader | Which viewer sees which sampled identities; observation persists only through valid memory updates |
| Discard-two/pick-one leader | Private discards/candidates as exposed by the rules, public resulting graveyard facts, selected own deck card |
| Weather-from-deck leaders | Own candidate weather cards, chosen card, deck depletion and public played result |
| Passive leaders | Extra starting card, opponent-leader cancellation, spy modification, weather modification and random resurrection |
| Graveyard-to-deck leader | Eligible returned cards by destination deck, count changes, unknown shuffled order and subsequent location uncertainty |

Seat behavior matters. The current simulator reproduces native AI paths where
the view-hand and discard-two/pick-one leaders spend their ability without the
human-seat effect. Record the intended seat rules explicitly; do not silently
train a human-seat ability and deploy it on a different AI-seat path.
Effect support also needs an explicit boundary: the research catalog validator
rejects single-draw effect 28; unit entry implements two-card-draw effect 34.
Spy type and draw effect are separate fields. Do not infer a draw solely from
spy classification or import an unsupported effect from the 2018 engine.

## 7. Factions and round transitions

| Faction or event | Information to retain and derive |
|---|---|
| Northern Realms | Whether a qualifying round win causes an actual draw; resulting counts and our drawn identity if we are the recipient |
| Nilfgaard | Tie resolution, including mirrors; resulting lives and next-round state |
| Scoia'tael | Who has the starting-player choice and the publicly selected starter |
| Monsters | Eligible carryover units before cleanup; uncertainty before selection; actual retained unit afterward |
| Skellige | Third-round resurrection timing, eligible graveyard cards, sampled outcomes and any triggered pending choices |
| Round cleanup | Cards retained, moved to graveyard, transformed or spawned; removed modifiers and weather |
| Next round | Starting player, remaining hand resources, restored turn/pass state and continuing passive effects |
| Terminal match | Winner/draw and no assumption of another playable round |

Evaluate a prospective pass through the whole rule transition. Do not value it
from the current score alone. A card's graveyard use or death-triggered summon
can matter even when its points no longer affect the current round.

## 8. Calculable tactical features

These are derived from the facts above. They may help a network or search, but
they are not additional observations and need not all be stored.

- Immediate score change on both sides for every legal move, after full effects.
- Resulting hand/deck changes, cards spent, cards drawn and retained leader use.
- Minimum known card expenditure to overtake a passed opponent under current rules.
- Exact current Scorch exposure, strongest ties, threshold crossings and removal
  of Horn/morale sources.
- Weather harm/help to both sides and recovery from clearing weather.
- Value of alternative Agile placements under visible modifiers.
- Known Decoy/medic/spy chains, including targets opened or consumed by a move.
- Muster payoff and cost of holding redundant matching cards.
- Transformation and carryover possibilities, including random outcome ranges.
- Graveyard resources available now and after a round transition.
- Match consequences of winning, losing or drawing this round.
- Bounds against known opponent cards; conditional threats from unknown cards.
- Choices that reveal information or give the opponent information about our hand.
- Whether two legal actions have identical full consequences under a proved scope.

Separate immediate, rule-exact calculations from multi-turn estimates. Adding all
cards' printed strengths is not a valid general bound when interactions, draws,
revivals, weather or opponent replies can change the result.

## 9. Probabilistic information and learned judgment

| ID | Estimate | Required qualification |
|---|---|---|
| B01 | Probability of a card in the opponent's hand | Condition on evidence and a declared deck/draw model |
| B02 | Likely deck family from faction, leader and revealed cards | A prior over possible lists, not access to the registered list |
| B03 | Chance of drawing a needed own card | Exact composition may be known; probability assumes a valid model of shuffled order |
| B04 | Chance of Scorch, weather, clearing, spies, Decoy or medic replies | Correlated with deck composition and play history; avoid independent guesses that violate counts |
| B05 | Opponent's card conservation, bluffing and passing tendencies | Learned behavior with uncertainty, not a rule |
| B06 | Information from an opportunity the opponent declined | They may lack a card, save it, misplay or bluff; no hard exclusion |
| B07 | Expected carryover or random resurrection value | Use the rule's eligible set and sampling mechanism |
| B08 | Expected future-round strength and value of card advantage | Depends on combinations, remaining rounds and opposing resources |
| B09 | Likely response to a candidate move | Depends on what the opponent knows, not our private hand |
| B10 | Match win/draw/loss estimates and move preferences | Learned predictions; measure accuracy and playing strength separately |
| B11 | Confidence and disagreement between plausible hidden worlds | Retain alternative explanations rather than only the most likely hand |
| B12 | Opponent adaptation across matches | Optional persistent model; declare access, reset rules and evaluation protocol |

Random draw probability can be computed from counts when the draw is uniform
over a known multiset. A known top/bottom placement or an exchange rule can make
that assumption false. Inspect the actual mulligan/return rule before computing
draw odds; the engine currently inserts a mulligan return at a specific end of
its deck representation. Do not assume that implementation detail is native
player knowledge without confirming the rule and its effect on later draws.
Strategic opponent choices are not uniform random draws.
Observing a pass may change beliefs, but it does not prove a particular hidden hand.

## 10. Information that must stay outside the fair player

- Opponent's unrevealed hand, exact registered deck, collection or private choices.
- Unobserved shuffled deck order, engine random seed/state, future random outcomes.
  Ordering facts legitimately established by visible rules are allowed.
- Raw full-state snapshots or chance-event records containing hidden candidates.
- Raw protocol `new`/`step` responses, unfiltered transitions and `rng_trace`:
  these can include shuffled orders, exact draws and private reveal candidates.
  Construct player inputs from viewer-filtered observations, that viewer's legal
  actions, permitted setup context and filtered events instead.
- Opponent-only reveals or their private memory ledger.
- Internal instance numbering, creation order or provenance flags that identify
  hidden cards without a legitimate observation.
- Opponent legal moves or model diagnostics that expose hidden resources.
- Exact NPC configuration files under an ordinary anonymous-deck protocol.
- Future moves, final match outcomes or search using privileged state fed into
  the acting policy's inputs. Training labels and optional privileged critics
  require explicit separation from deployable inputs.

In search, simulated opponents must act on their own simulated information.
Knowing our real hand at the root does not entitle the simulated opponent to
use it. A sampled hidden world is a hypothesis, not new knowledge for either seat.

## 11. Suggested evidence record and model boundary

Keep one authoritative event stream and a derived information state per viewer.
Each remembered fact should retain:

- Card type and a safe observed-instance reference, or a count/range when identity
  is ambiguous. Do not expose arbitrary engine IDs as numeric model features.
- Current known zone or possible zones, known owner/controller where established.
- Source event, viewer, timing, certainty and reason for later invalidation.
- Original/generated/transformed provenance only to the degree observable.
- Complete/incomplete history marker and information-schema/rules version.

The network input can then choose among:

1. Current public board and own private hand.
2. Own remaining-deck multiset and faction/leader rules.
3. Known opponent-hand counts, unknown slots and public resource history.
4. Compact exact constraints and optional belief summaries.
5. Optional ordered events or learned memory when summaries lose useful history.

Avoid adding an enormous raw event sequence before checking whether a smaller
summary preserves the needed facts. Storage format and learning architecture are
separate decisions. Binary shards and compressed replay records can retain the
same evidence as JSON; changing format does not create missing observations.

## 12. Current implementation audit

The production-mod source examined here is
`2d82b2527050ce4326cf38aa2c8938ab01466ca7`. Its encoder/model provide the current
snapshot described in [learning direction](learning-direction.md). They do not
explicitly encode own-deck composition or a remembered opponent hand. Its legacy
faction profile uses a constant faction input. Action targets include card types
and rows, but the observation aggregates cards by template/zone rather than
preserving a complete event history.

The separate research source inspected at
`385f89eec16c7b433702052e9f886d2d1982ee75` already has search-side handling for
publicly decoyed/revealed cards, a per-viewer evidence ledger, and public-history
conditioning. Its schema-v1 network seat view still explicitly uses faction zero.
Search knowing a fact does not establish that the network receives it. Earlier
discussion of missing memory applied to the mod network, not every research path.

The research records also contain a search-controller comparison with unchanged
weights and a narrow measured gain. Therefore “the whole project has no search”
would be incorrect. No new benchmark, training run or native parity check was
performed for this inventory.

### Source map

Mod sources:

- [Input encoding](../learning/encoding.py)
- [Network](../learning/model.py)
- [Model input profiles](../scripts/policy_format.py)
- [Native capture](../mod/ai/as3/GwaiCapture.as)
- [Controller](../mod/ai/as3/GwaiSharedController.as)

Private research references below are relative to
`witcher3-gwent-ai-research` at the revision above. They are
source pointers, not copied game assets or proof of complete native parity.

| Source | Covers |
|---|---|
| `engine/src/model.rs` | Card types and effect-family vocabulary |
| `engine/src/engine.rs`, `observe`, `pending_summary` | Viewer observations and pending-choice visibility |
| `engine/src/engine/seat_view.rs` | Network seat view and constant faction input |
| `engine/src/engine.rs`, `apply_leader_action`, `settle_round`, `clear_board`, `summon_matching_cards`, `transform_morph` | Leader, faction, round and effect transitions |
| `engine/src/engine.rs`, `faction_pool_remembers_an_opponents_publicly_decoyed_unit`, `faction_pool_remembers_cards_revealed_by_a_leader` | Existing search-memory regression cases |
| `engine/src/public_prior_evidence.rs` | Per-viewer event ledger and private reveal boundaries |
| `engine/src/public_prior_history.rs` | Public graveyard returns and later location uncertainty |
| `engine/src/engine/public_prior_world.rs` | Hidden-world reconstruction and privacy checks |
| Focused policy confirmation result (November 10, 2026) | Narrow search-controller result with unchanged weights |

## 13. Acceptance checklist before model changes

This is a proposed test inventory, not a claim that these checks all pass.

1. Same player-visible history, different unobserved hands/orders/IDs: identical
   player inputs and identical seeded decisions or decision distributions.
2. Different legitimate histories ending on the same visible board: different
   memory when one history establishes a returned or revealed hand card.
3. Decoy, spy reuse, medic, cross-graveyard retrieval and duplicate copies:
   no double counting; known cards move to the correct knowledge state.
4. Private reveals/candidates: only the intended viewer receives them.
5. Draws, mulligans, Muster and graveyard shuffles: conservation holds; uncertain
   locations remain uncertain; incomplete history does not become certainty.
6. Transformations and generated cards: original-deck constraints remain valid.
7. Faction mirrors, ties, carryover, third-round revival and pending chains:
   passing and next-round features match the rule transition.
8. Native human/AI seat differences: observation and action semantics match the
   intended deployed seat, including leaders with different native behavior.
9. Save/resume: restore only knowledge actually available to that viewer; refuse
   to fabricate missing history from a full-state checkpoint.
10. Python, Rust and deployed encoders: same meaning, same schema and numerical
    agreement for corresponding inputs, including new faction/memory features.
11. Beliefs: every sampled world respects hard evidence; uncertain deck recipes
    and research copy caps remain soft assumptions unless independently justified.
12. Storage replay: reconstructed observations, legal actions and outcomes match
    the original record before any representation replaces retained evidence.

## 14. What this means for more training

More fresh, useful games may improve an undertrained model substantially. We
have not established that the current model has reached its capacity. A single
day of work is not a measure of training scale: count completed games, decisions,
updates, opponent/deck coverage and compute time.

More training is not guaranteed to improve independent play. It can plateau,
overfit a narrow opponent pool, forget earlier skills, or optimize a faulty
training signal. It cannot distinguish two situations that require different
moves if the encoder makes them identical. This inventory addresses that last
problem; it does not prove it is the dominant cause of weak play.

After correcting the selected information contract, measure strength across
successive training checkpoints. Use those learning curves and measured pipeline
throughput to decide whether more local or rented compute is justified.
