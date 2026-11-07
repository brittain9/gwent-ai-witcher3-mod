# Gwent: Trained Opponents

**A tougher opponent. The same Gwent.**

Trained Opponents replaces the original Gwent AI with a neural network trained
through **self-play reinforcement learning** for
**The Witcher 3: Wild Hunt — Remastered**.
The policy learned from simulated matches against itself, with match outcomes
providing the training signal. The mod preserves the original cards, rules,
NPC decks, rewards and collection progression.

The original Gwent AI uses handwritten rules and scoring to choose its moves.
These can produce wasteful decisions, such as repeating an ineffective weather
effect or spending cards for little benefit.

## How does it compare?

With identical decks, the Hard policy won **75% of its simulated matches
against the original AI**.

| Result | Games |
|---|---:|
| Trained Hard won | **1,026** |
| Original AI won | 282 |
| Draw | 60 |
| **Total** | **1,368** |

This simulator benchmark used the same deck for both sides and swapped seats
over 684 deals. It measures the Hard policy against a port
of the original AI, rather than predicting win rates against human players.
Your collection and the NPC's deck still affect each match.
[Evaluation details](docs/evaluation.md) describe the setup and limits.

## Designed for the game

The aim is a more capable, enjoyable opponent that fits the original minigame.
**Easy, Medium and Hard** provide different levels of challenge in story and
practice matches.

The mod does not aim to provide perfect or unbeatable play. Self-play is its
training method, not a guarantee of optimal strategy or convergence to a
**Nash equilibrium**—a pair of strategies where neither player benefits
from changing only their own strategy. The opponent can still make mistakes,
and winning every benchmark game is not the product's design goal.

During play, the trained network runs locally inside the game's Gwent screen,
using its own hand and the public board. It needs no server or AI subscription.
[How it works](docs/how-it-works.md) explains the policy and information it uses.

## Model stats

| Stat | Current release |
|---|---|
| Training method | Self-play reinforcement learning using PPO |
| Network size | Approximately 1.98 million parameters per model |
| Difficulty levels | Easy, Medium, Hard |
| Prebuilt player download | Approximately 31.6 MB, including all three models |
| In-game execution | Local inference; no server or subscription |

## Install

1. Download the **prebuilt native ZIP** and its checksum from the
   [latest release](https://github.com/brittain9/gwent-ai-witcher3-mod/releases/latest).
2. Close the game and REDlauncher.
3. For a ZIP download, use Vortex's **Install From File**, then enable and
   deploy the mod. For a Workshop download, subscribe and let Steam finish
   installing it. Enable local or Workshop mods in the game's Gameplay settings.
4. Start the game, load a save, and open **Settings > Mods > Gwent: Trained Opponents**.
   **Enable AI** is on by default. Choose the challenge through the game's
   **Gameplay > Gwent difficulty** setting.

The package includes the finished modified Gwent screen and all three policies.
Python, Java, Node.js and training tools are not needed to play. If you used
the earlier Python installer, uninstall that version using its original
download folder before installing this package. Keep one installation of
this mod active at a time.

[Installation, compatibility and removal](docs/install.md) ·
[Build from source](docs/building.md) · [Changelog](CHANGELOG.md)

## Playing

- **Enable AI** selects the trained opponent in both story and practice matches.
  Turn it off to use the stock opponent.
- **Gameplay > Gwent difficulty** selects the trained policy for both story
  and practice matches. Easy, Normal and Hard select the Easy, Medium and
  Hard policies respectively.
- Practice lets you choose an opponent, using your existing collection.
  Native NPC deck selection stays with the game.
- Moves run when computation finishes, with no artificial thinking delay.
- The mod menu contains the AI switch, practice controls and troubleshooting.

The network sees its own hand, public cards, scores and card counts. It does
not read your hidden hand or either deck's order. Difficulty selects different
trained policies, rather than granting hidden information or changing cards.

## Compatibility and limits

The prebuilt package targets compatible Remastered Gwent resources. Tested
game versions are listed in the release notes; executable patch labels alone
do not determine resource compatibility. Classic 1.32 is unsupported. Another
mod that replaces the Gwent screen can conflict.

The preceding preview was played on Windows and Linux through Steam/Proton.
This release's tested platforms and build identity are listed in its
[release notes](https://github.com/brittain9/gwent-ai-witcher3-mod/releases/latest).
The AI is fallible; there is no claim of optimal play or a calibrated human
win rate. Your collection and the NPC's native deck still affect each match.

## Diagnose a problem

**Show AI status (current / latest match)** separates current settings from the
latest recorded match and its decision sources. **Show decision source**
displays network and fallback messages.
**Record public play-by-play** saves public actions, settled boards and match results
locally, including the selected tier and model identity in new traces.

Both debug controls are off by default. Recording excludes hidden hands, deck
order and unrevealed choices, and nothing uploads automatically. See
[troubleshooting](docs/install.md#troubleshooting) for reports and rollback.

## License

Original code, documentation and the inference weights in the player download
use the [MIT license](LICENSE). The game and external tools retain their own
rights and licenses. See [NOTICE](NOTICE.md).
