# Comparison with the original AI

The shared controller retains these Hard weights. The benchmark below measured
the earlier policy directly; it is not a new measurement of controller changes
or of the difficulty gaps.

## Equal-deck benchmark

On October 31, 2026, the Hard policy played a simulator port of the original
Gwent AI over 684 deals, each played in both seats: 1,368 games total.
Both players received the same deck list within each deal. The policy selected
moves directly at temperature zero, with the same action-group selection rule
used by the mod; it performed no tree search. Its inputs were its own hand
and public information, excluding the opponent's hidden hand and deck order.
The simulator included corrections from an audit of the original AI's rules.

| Result | Games | Share of all games |
|---|---:|---:|
| Trained Hard won | 1,026 | 75.0% |
| Original AI won | 282 | 20.6% |
| Draw | 60 | 4.4% |

Among games with a winner, Hard won 78.4%; the recorded 95% Wilson interval
was 76.1–80.6%. This interval treats decisive games as individual observations;
seat-swapped games share deals, so it is not a deal-clustered interval.

## Model identity

The evaluated checkpoint SHA-256 was
`f9af1f6965506e2ec9695a2d02883c4a5d75a879b297f85adea77f6a15f9bec8`.
The player download identifies its Hard source checkpoint as
`ee1efc640bda386b3b0fdee4e49188efa176154bd9f63715a058950639233a8d`.
Its catalog metadata was normalized after evaluation; the relocation record
verified unchanged policy tensors. The distributed inference-weight SHA-256 is
`c0df847ad4e571f89116c5c14dc25901ba45d7ef9d91dc1328bc8f59abc87d38`.
The rename does not change those weights.

## Interpretation and limits

The comparison isolates decision-making with equal decks. It does not estimate
human win rates or reproduce every native-game interaction. Story matches use
NPC collections that can differ substantially from the player's collection.
The result applies to Hard; it is not a separate measurement of Easy or Medium.

The benchmark used 19 deck lists: ten player-obtainable lists and nine
additional lists containing normally unobtainable cards. On the
player-obtainable subset, the recorded decisive-game win rate was 79.6%
(95% Wilson interval 76.4–82.5%). These are simulator test decks, rather than
a claim that every list can be assembled in ordinary play.

Avoiding wasteful moves is the intended experience. This result does not
certify that the trained opponent never repeats weather or misuses a card.
The design goal is a capable opponent with selectable difficulty, rather than
perfect play or proven convergence to a Nash equilibrium.
