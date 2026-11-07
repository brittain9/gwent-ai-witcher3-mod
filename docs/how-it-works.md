# How the AI works

The opponent learns a policy: a neural network that rates legal Gwent actions
from the information available to one player. A value head estimates match
outcomes from that same view. The objective is winning the match, which can
require giving up a round or saving a useful card.

## Decisions inside The Witcher 3

The mod captures the AI seat's own hand, public zones and scores, and the
opponent's hand count. It lists legal actions and runs the exported policy
inside the game's Gwent screen. Policy evaluation runs locally in the game.
Different compatible checkpoints can provide the three difficulty levels.

At temperature zero it selects the highest-rated legal action through the
network's action grouping. The chosen move goes through the game's native
transaction and effect path. There is no artificial pause after inference;
loading, computation and the game's own presentation still take time.

Original cards, deck collections, rule execution, rewards and progression stay
with the game. The native Gwent difficulty still selects the NPC deck level.
The mod's practice controls can choose the policy separately from that level.

## When something fails

Unknown card vocabulary or an invalid integration step hands the turn back to
the stock opponent. An inference error can use a simple greedy rule instead.
These are recorded as separate decision sources, so a fallback game cannot be
mistaken for evidence about the trained policy.

Optional public recording observes the native action start and the settled
result. It excludes hidden hand identities and deck order. That makes it useful
for explaining a visible sequence and diagnosing execution failures, while
leaving hidden-information reconstruction out of the public trace.

## Verification

Portable policy checks compare Python and ActionScript on synthetic schema
fixtures. Compilation checks repeat those comparisons against the exported
screen and verify timer bindings and class initialization. These checks cover
encoding and integration; they do not establish playing strength or full game
rules parity. Weather, passing and card conservation remain areas to improve.

See [building](building.md) for required inputs and verification status, and
[NOTICE](../NOTICE.md) for licensing of source and generated artifacts.
