# Witcher 3 Gwent AI

A trained opponent for the Gwent minigame in **The Witcher 3: Wild Hunt**.
It replaces decisions while keeping the game's cards, NPC decks, rules,
rewards and collection progression.

This repository is a **source preview**. The standalone source build and clean
source-only checks have passed. There is no downloadable mod package or trained
model yet; building a playable opponent requires a compatible trained checkpoint
supplied separately.

## Playing

- **Enable trained AI** is the switch for story and practice matches. Turn it
  off to use the stock opponent.
- Story matches follow the game's **Easy, Normal or Hard** Gwent difficulty.
  That game setting still selects the NPC's native deck level.
- Practice lets you choose an opponent and **Practice AI difficulty** separately
  from the native deck level, using your existing collection.
- Moves run when computation finishes, with no artificial thinking delay.
- Coaching, analysis and **Stronger story opponents** are optional. They are
  off by default.

The network sees its own hand and the public board, scores and card counts.
It does not read the cards in your hidden hand or either deck's order.
[How it works](docs/how-it-works.md) explains the policy and game integration.

## Status and limits

The AI can overcommit in early rounds and reach the third round without cards.
Match-level planning and card conservation are areas for further improvement.

The trained opponent and public-action recorder have worked in live games on
Linux through Steam/Proton with pinned Gwent resources. The current
simplified menu still needs a completed live acceptance check. A clean install
on another machine and Windows gameplay remain unverified. Classic 1.32 is not
supported; different game resources require a reviewed build.

Weather, card spending and passing can still look weak or wasteful. The project
does not claim optimal play or a measured human win rate. Original collection
gaps still affect how hard each opponent is. Mods that replace the Gwent screen
can conflict.

[Installation and controls](docs/install.md) · [Build from source](docs/building.md)
· [Changelog](CHANGELOG.md)

## Debugging

**[Debug] Show decision source** identifies network decisions and fallbacks.
**[Debug] Record play-by-play** records public actions, settled boards and
results locally. It excludes hidden hands, deck order and unrevealed choices.
Both debug controls are off by default. Recording and on-screen messages are
independent, and nothing is uploaded automatically. Include the build identity
and public trace when reporting a problem; see
[the install guide](docs/install.md#diagnose-a-problem).

## License

Original project code is licensed under [MIT](LICENSE). Game files and other
third-party material are outside that license. Trained models are separate
artifacts and are not included in this source preview. See [NOTICE](NOTICE.md)
for the boundary.
