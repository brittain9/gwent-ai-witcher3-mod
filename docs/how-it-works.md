# How the AI works

This page describes the current source on `main`. The older downloadable
release uses three separate policies; the shared-model controller awaits a new
player package.

The opponent uses **self-play reinforcement learning**: a policy learns from
simulated games against itself, with match outcomes providing the learning
signal. A neural network rates legal actions from one player's information.
Its value head estimates loss, draw and win probabilities from that same view.
Winning the match can require giving up a round or saving a useful card.

## Training and play

The trained model is a policy/value network with a shared representation of
the visible position and separate action-group, placement and outcome heads.
Proximal policy optimization updates the policy from completed-game outcomes,
with damped self-play updates that limit abrupt changes. One frozen model supplies all
three difficulty levels; the controller varies move selection.

Training happens before the mod is packaged. During a match, the mod runs
**policy inference**: it evaluates the current legal actions with fixed
learned weights. It does not train, perform a tree search or call an AI service
while you play. Self-play describes how the policy learned, not a guarantee of
optimal play or a measured advantage against strong humans.

## Information and decisions

The mod captures the AI seat's own hand, public zones, scores and card counts.
It does not read the other seat's hidden cards or either deck's order.
Original cards, rule execution, rewards and collection progression stay with
the game.

At temperature zero, the network selects the highest-rated action group,
then the highest-rated legal action within that group. The chosen action goes
through the game's native transaction and effect path. There is no artificial
pause after inference; loading, computation and presentation still take time.

The game's existing Gwent difficulty setting selects the NPC's deck level and
move selection for both story and practice. All three use the same weights.
Easy samples at temperature 1, Normal at 0.25, and Hard at zero. Higher
temperatures vary choices more; these settings do not establish a measured
strength gap between levels. After loading a save, the
same control is available in the mod menu. A change in either menu updates
the shared game setting.

The controller passes when the visible position guarantees an immediate match
win against a passed opponent. This guard does not solve general round
commitment or weather conservation.

## When a decision fails

Unknown card vocabulary or a failed integration step hands the turn back to
the stock opponent. An inference error can use a simple greedy rule instead.
These are distinct diagnostic sources. Verification and optional public traces
make a selected stock turn, a fallback and a network decision distinguishable.

The recorder publishes a card's identity only after its native action commits.
It records public results and selection/model identity, while excluding hidden
hands, deck order and unrevealed choices.

## Packaging and verification

The prebuilt player package contains the modified Gwent screen, original
integration scripts and one shared trained model. During the release build,
the tools derive the card vocabulary and effect tables from a compatible
game, generate the model headers and compile the modified screen. Players
receive the finished files. The source build tools can also perform this
preparation locally.

During the release build, verification compares Python reference outputs with
ActionScript on deterministic fictional positions. Compilation checks repeat inference
checks against re-exported code and verify embedded tensor bytes, encoding
tables, timer bindings and class initialization. These test encoding and
integration; they do not measure playing strength or certify every game path.

See [installation](install.md), [building](building.md) and [NOTICE](../NOTICE.md).
