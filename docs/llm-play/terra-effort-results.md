# Terra effort results — November 10, 2026

The additional panel ended with **16 completed games and one timeout**. Alex
requested finishing existing games and starting no more. The remaining primary
high-effort slot and planned reserve retry were not run. All final game hosts
were closed. Together with the earlier 13 pilots, the repo now retains 29 complete
games and one partial game as portable action records.

| Terra effort | Completed wins–losses | Incomplete | Mean completed-game time |
|---|---|---|---|
| medium | 2–3 | 1 | 9.9 min |
| high | 3–2 | 0 | 6.7 min |
| xhigh | 2–4 | 0 | 5.5 min |

These are raw session wall times, including tools, opponent work, scheduling,
and a setup fix in one medium run. One medium run timed out at 15 minutes; another
resumed the same model turn after a long pause. No pure inference latency or
provider cost was measured.

The common completed subset has only four scenarios: medium 2/4 wins, high 2/4,
xhigh 1/4. The raw table has different case coverage. In particular, high did not
play the last difficult Nilfgaard/Skellige case, and medium's direct-network case
is incomplete. Do not rank strength from the raw percentages. Xhigh has not
shown a consistent advantage here.

## Scenario outcomes

| Player faction / opponent | Medium | High | Xhigh |
|---|---|---|---|
| monster-north-stock | win | win | loss |
| nilf-monster-network | incomplete | win | win |
| nilf-skellige-tree | loss | not run | loss |
| north-nilf-stock | win | loss | win |
| scoia-skellige-tree | loss | loss | loss |
| skellige-scoia-search | loss | win | loss |

The [frozen plan](terra-effort-plan.md) gives recipes, seats, models and budgets.
The [collection index](../../records/llm/2026-11-10-terra-effort-v3/index.json)
retains exact counts, interventions and record hashes.

## How the LLMs are playing

They can execute complete games, make legal choices, use medics and leaders, and
exploit Nilfgaard's tie advantage. They also make plausible-looking strategic
errors. Winning trajectories still contain questionable moves. Medium and high
both beat the stock controller in some positions where xhigh did not win.
The earlier matched pilot was Luna 2/3, Terra 3/3 and Sol 3/3; its different cases
must stay separate from this additional panel.

Recurring candidates for improvement:

1. **Match risk versus card preservation.** In multiple xhigh losses, the pass
   rationale was to force the opponent to spend more cards, even in a must-win
   round. One pass at 6–4 left five cards unused and ended 6–56. Another 50–4 lead
   became 50–94. The observed consequences are verified by replay. A winning
   alternative is not established in every case.
2. **Resources across rounds.** Agents sometimes spent their remaining hand to
   win round two, then entered round three without enough strength. Examples
   include high's Northern Realms loss and xhigh's Skellige loss.
3. **Removal and revival sequences.** Equal-strength or bonded groups repeatedly
   died together to Scorch. Agents also spent removal before a Medic restored a
   target. Future hidden draws alone do not prove those moves were wrong; compare
   alternatives under the player's information before assigning expert labels.
4. **Large faction continuations.** Muster, graveyard shuffling and third-round
   revivals produced swings the LLMs did not cover. The last Skellige opponents
   reached 94 and 99. Current context gives mechanics and known-card definitions,
   not a complete public faction-card reference. Test that information gap as
   well as model judgment.

A checked report from the direct-network game confirms Scorch reduced its own
side from 38 to 32 while the LLM remained 40. The comparison value remains unknown:
passing was also behind. This is a verified consequence, not a proven winning
alternative. The two earlier checked local decisions remain useful regression
positions: conserving a Blue Stripes card in an already-passed round, and a Horn
placement worth 24 immediate points instead of zero.

## Tooling and data

The new panel found a real capability edge case: a random leading dash was
parsed as an option. A failing CLI test reproduced it. The session command now
uses attached argument values, and new capabilities have an alphabetic prefix.
All 27 connector tests pass. Larger tool-capture budgets also prevent losing the
tail of long responses. No model weights were trained or changed.

All completed and partial action records passed reconstruction with the matching
card list and engine. Full journals remain local. The repo records omit bulk
card definitions, connection credentials and machine-specific paths, and retain
model/prompt/source identities and decision-linked reports. Exact executable
hashing still needs a portable-build policy for Linux. The database and training
storage migration remain deferred.

Use these records for evaluation cases, outcome evidence and targeted weakness
analysis. Proposed demonstrations still need fair-view reconstruction and
quality checks. The next session should verify high-value alternatives, test a
public card reference, and select the faction/history input changes before
scaling training.
