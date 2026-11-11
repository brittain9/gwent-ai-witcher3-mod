# Witcher 3 Gwent mechanics reference

This reference explains the headless game's mechanics. The current legal actions
and observed state are authoritative for a decision. It contains no prescribed
strategy. This is Witcher 3 Gwent, not the later standalone Gwent card game.

## Match and turn

A match is decided by round losses. Each player starts with two lives. A round
loss removes one life; a tied round normally removes a life from both players.
Nilfgaard has a faction-specific tie advantage outside a faction mirror. When
lives reach zero, the match ends, possibly in a draw.

Players normally alternate actions. Passing ends that player's participation
in the round. The other player may continue. The round ends when neither player
can continue. A displayed round-done flag can also mean an empty hand and no
usable leader; it is not always evidence of an explicit pass.

The opening deal normally gives ten cards, with up to two mulligan exchanges.
Faction and leader effects can change the setup. Hands carry across rounds;
there is no universal full hand refill between rounds. Resolve pending target,
weather, discard, resurrection and starting-player choices when they appear.
The active chooser can differ from the player whose ordinary turn began.

## Cards, rows and scores

Units occupy melee, ranged or siege rows according to their legal placement.
Agile units can have more than one legal row. A spy is placed on the opposing
board. Its draw effect is separate from its spy type. The usual two-card-draw
effect draws only as many cards as remain available.

Hero units retain their base strength under the engine's ordinary weather and
buff calculation and are excluded from several targeting effects. Use the legal
target list rather than assuming a hero can be retrieved with Decoy or revived.

Current unit strength can differ from printed strength. The engine applies
weather, applicable spy modification, Tight Bond, morale and Horn in that order.
Weather normally reduces susceptible base strength to at most one; a leader can
modify weather resistance. Tight Bond links matching eligible units. Morale buffs
other eligible units. Horn doubles eligible resulting strength; overlapping Horn
sources do not simply multiply repeatedly. Heroes bypass this calculation.

## Main effects

- **Decoy:** replaces an eligible unit on your board and returns that unit to
  your hand. The returned card is known to an observer who saw the exchange.
- **Medic:** selects an eligible graveyard unit to play. The revived unit can
  trigger another effect or pending choice.
- **Muster:** brings matching units from hand and deck into play. It can spend
  cards already in hand as well as pull cards from the deck.
- **Weather:** affects specified rows on both boards. Some cards affect multiple
  rows. Repeating weather need not create an additional strength penalty.
- **Clear Weather:** removes weather and recalculates both players' strength.
- **Scorch:** removes tied strongest eligible units under its scope. Global
  Scorch can affect either side. Row-specific versions have a threshold and
  target scope. Do not assume only one card is destroyed.
- **Death-triggered summon:** removal or cleanup can create a new unit. Its
  timing and resulting board matter for the next decision or round.
- **Mardroeme/transformation:** a relevant effect transforms eligible units;
  the resulting identity, strength and effects can differ from the original.

Leaders can clear/play weather, supply a row Horn, Scorch a row, retrieve a
graveyard card into a hand, reveal enemy cards, discard and pick from a deck,
reposition units, or return eligible graveyard cards to decks. Passive leaders
can modify draws, spies, weather or resurrection, or cancel another leader.
The opponent's private candidates are not visible just because a leader acts.

The current engine preserves native seat-specific behavior: the view-hand and
discard-two/pick-one leaders have no such effect on an AI-kind seat. A human-kind
seat can use the corresponding choices. The session setup declares the seat
kind; do not infer human-seat behavior from the controller being an LLM.

## Factions

- **Northern Realms:** a qualifying round win draws an additional card when one
  is available and the match continues.
- **Nilfgaard:** has the round tie advantage outside the faction mirror.
- **Scoia'tael:** can choose the initial starting player.
- **Monsters:** can retain an eligible randomly selected unit across a round.
- **Skellige:** can revive eligible graveyard units at the third-round boundary.

Round cleanup removes or moves cards and modifiers, clears weather, and can
trigger carryover, summons, draws and resurrection. Inspect the settled result
and pending choices before treating the next round as ready.

## Knowledge

You know your hand, your submitted deck without shuffled order, and public
cards/events. Remember publicly returned cards and private reveals addressed
to you. Unknown opponent cards remain unknown. A known card returned to a
shuffled deck may later be in either deck or hand after a hidden draw.

An opponent declining to play a card does not prove that they lack it. A played
card can be the same copy seen earlier after Decoy or resurrection. A generated
or transferred card does not prove it was in the original deck.

The structured state and its legal options provide the card names, values,
effect identifiers and targets needed for the current decision. Full details
and proposed information checks are in the
[player information inventory](player-information-inventory.md).
