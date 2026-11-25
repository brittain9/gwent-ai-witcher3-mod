# Benchmarks & Evaluation

All benchmarks below measure **full best-of-three Gwent matches** in a rules-verified Rust simulator of The Witcher 3 Gwent engine, using **fixed, held-out, seat-swapped physical deals** (each deal is played twice with seats and decks swapped, canceling both first-player and draw variance). A draw counts as `0.5` wins.

None of the held-out evaluation decks or unseen procedural decks were used during training or distillation.

---

## 1. Deployed v1.0 Native Mod (`318k` Model, Zero Search)

Measurements for the exact **`Tiny-2L-96`** (`318,280` parameters, `1.27 MB` float32) weights shipped in `v1.0.0`, running **pure zero-search neural inference** *(before adding the in-game `GwaiTerminalTactics` round-closing solver)*:

### Balanced Held-Out 5-Faction Competitive Suite (`400` paired games per matchup)
| Matchup | Score | W / D / L | Elo Delta |
| :--- | :---: | :---: | :---: |
| **v1.0 Hard (`T=0.0`)** vs. **Vanilla Opponent** | **`87.12%`** *(`87.33%` on 300g)* | - (`400g`) | **`+332.2`** |
| **v1.0 Hard (`T=0.0`)** vs. **16.2M Teacher Ensemble (Pure)** | **`49.50%`** `[47.00, 52.00]` | `193 / 10 / 197` | **`-3.5`** *(~99% retention at 51x compression)* |

### 100% Unseen 20-Deck Procedural Suite (`400` paired games per matchup)
| Matchup | Score | W / D / L | Elo Delta |
| :--- | :---: | :---: | :---: |
| **v1.0 Hard (`T=0.0`)** vs. **Vanilla Opponent** | **`77.00%`** | - (`400g`) | **`+209.9`** |
| **v1.0 Hard (`T=0.0`)** vs. **16.2M Teacher Ensemble (Pure, Same-Deck Mirrors)** | **`48.25%`** `[45.75, 50.75]` | `183 / 20 / 197` | **`-12.2`** |

### All 50 Hard Campaign NPC Rosters (`196` matched deals, NPC seat)
Evaluated with each controller playing the exact campaign NPC seat across all 50 Hard Practice rosters against a fixed strong human-role baseline on identical physical deals:

| Controller in NPC Seat | Match Score (95% CI) | Delta vs. Vanilla |
| :--- | :---: | :---: |
| **Vanilla Opponent** | `25.00%` | baseline |
| **Earlier Prototype MLP (`1.98M` flat MLP)** | `29.34%` | `+4.34 pp` |
| **v1.0 Native Model (`318k`, Hard)** | **`38.27%`** `[31.89, 44.90]` | **`+13.27 pp`** |
| **Offline 16.2M Teacher Ensemble (Pure)** | `39.29%` | `+14.29 pp` |

---

## 2. Calibrated Difficulty Ladder (`300` paired games per tier, `held_out`)

| Witcher 3 Difficulty | Sampling Temp | vs. Vanilla Opponent | Elo vs. Vanilla |
| :--- | :---: | :---: | :---: |
| **Hard** | `T = 0.00` | **`87.33%`** | **`+335.4`** |
| **Normal** | `T = 2.60` *(interp. `2.3->2.8`)* | **`~81.2%`** | **`+255`** |
| **Easy** | `T = 4.20` | **`~70%`** | **`+150`** |

*(Exact measured sweep points on `Tiny-2L-96`: `T=0.00 -> 87.33%`, `T=1.80 -> 82.33%`, `T=2.30 -> 82.00%`, `T=2.80 -> 80.83%`, `T=3.50 -> 77.17%`.)*

---

## 3. Offline Teacher Context

For context on the larger offline self-play and search teacher that `v1.0` was distilled from:
- **Unseen 20-deck procedural (with search):** **`81.25%`** `[76.50, 85.75]` (`+254.7 Elo`) vs. vanilla opponent.
- **Competitive `held_out` (with search):** **`88.00%`** `[84.00, 92.00]` (`+346.1 Elo`) vs. vanilla opponent.
- **Dedicated best-response exploiter audit:** Against an adversarial RL exploiter trained specifically to beat it on `held_out`, the pure teacher held **`50.33%`** (`0%` measurable exploitability), and with search beat its own exploiter **`58.13%`** `[51.56, 64.38]` (`+57.0 Elo`).

---

## 4. Compiled ActionScript 3 Parity & Build Identity

- **Shipped weight tensor SHA-256:** `ca0ddfc042bedd88...` (`318,280` float32 params = `1,273,120` bytes).
- **Compiled `gwint_game.redswf`:** `13.59 MB` (`+1.31 MB` over vanilla); packed `blob0.bundle`: **`9.13 MB`**.
- **Compiled AS3 vs. PyTorch Parity:** **60 / 60 (`100.0%`) exact argmax agreement** directly on the `GwaiTransformer.as` source re-exported from the compiled `gwint_game.swf`, with **`0.0`** numerical difference between synchronous and 21-slice frame-sliced execution.

---

## 5. Honest Limits

- Simulator scores isolate decision quality under equal or standardized decks. In an actual Witcher 3 playthrough, your card collection vs. an NPC's deck still heavily shapes outcomes.
- The built-in ActionScript 3 model performs zero lookahead tree search during play (trading ~`60-80 Elo` of search depth for fast native moves and 1-click Steam Workshop installation).
- While much stronger and cleaner than the vanilla opponent, the model is still fallible and does not claim unbeatable or mathematically perfect play against skilled human players.
