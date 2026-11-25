# How Gwent: Trained Opponents Works

**Gwent: Trained Opponents** is a compact neural network running 100% locally inside The Witcher 3's Scaleform ActionScript 3 UI (`gwint_game.redswf`) that replaces the vanilla Gwent opponent across every Story and Practice match. It runs offline with no external app, no Python runtime, and no internet connection.

---

## 1. Fair Play & Information Boundary

The opponent plays by the same visibility rules as a human player. It reads **only** information legitimately available to its own seat:
- **Own hand** (cards currently in its hand)
- **Own remaining deck** (which cards remain in its deck, with draw order erased)
- **Public board state** (all six rows, active row modifiers/horns, active weather, graveyards, leaders and leader-used flags, factions, round scores, lives, pass states, and card counts)
- **Publicly revealed player cards** (only cards explicitly returned to your hand in public view, such as via Decoy)
- **Public turn history** (recent strictly public plays, passes, leader activations, and round results)

It **never** reads your hidden hand, your hidden mulligans, or either player's draw order.

---

## 2. Two-Stage Training: Self-Play Teacher -> Native Student

### Stage A: Training the Offline Self-Play Teacher
Discovering multi-round Gwent strategy from scratch (card advantage, Spy/Decoy trades, Round 2 passes, weather timing, and Scorch discipline) takes high model capacity and millions of simulated matches. We trained a large multi-model self-play and endgame-search teacher from scratch across **4.5+ million simulated games** covering balanced 5-faction decks, hundreds of generated early/mid/late decks, and all 50 Hard campaign NPC rosters.

### Stage B: Knowledge Distillation for Scaleform ActionScript 3
A large research model plus live search cannot run inside Witcher 3's single-threaded Flash VM, so we distill the teacher's move decisions into a compact neural network designed specifically for ActionScript 3:
- **Compact size (`318k` parameters, `1.27 MB` weights):** Embedded directly inside `gwint_game.redswf`, keeping the entire packed mod bundle to **`9.13 MB`**.
- **High retention:** Keeps **93-94% top-1 move agreement** and lands within **3.5 Elo** (`49.5%` head-to-head over 400 held-out games) of the ~50x larger pure teacher.
- **Frame-sliced execution:** Both initial weight decoding and per-turn inference yield back to Witcher 3's render loop every `<= 10 ms`, keeping board animations, weather effects, and cursor movement smooth (~1.5 to 3 seconds per turn, zero screen freezing).

---

## 3. Exact Round-Closing Solver

Before calling the neural network, the controller checks `GwaiTerminalTactics.as`:
- If passing mathematically guarantees winning the match (accounting for faction tie rules), it passes immediately (`~3 ms`).
- If you have already passed and a catalog-verified unit mathematically clinches the match, it plays the proven winning move.

---

## 4. Difficulty Settings

All three Witcher 3 Gwent difficulty levels share the same embedded weights, using calibrated move selection:
- **Hard:** Deterministic best-move play (`87.3%` vs. vanilla opponent).
- **Normal:** Softened move selection (`~81.0%` vs. vanilla opponent).
- **Easy:** Relaxed selection (`~70%` vs. vanilla opponent), avoiding goofy vanilla blunders while staying forgiving. You can also toggle the mod off in 1 click anytime to play the vanilla opponent.

---

## 5. Mod-Pack Safety & Fallback Behavior

Every turn, the controller verifies visible cards against the 228-card Witcher 3 base+DLC catalog. If another mod adds custom cards outside that catalog, or if an unexpected runtime error occurs, that single turn hands cleanly back to the vanilla opponent so your match never freezes or crashes.

---

## 6. Build & Parity Verification

Every build (`scripts/build_transformer_mod.py`) verifies:
1. Exact embedded float32 weight SHA-256 round-trip (`1,273,120` bytes) after compilation into `gwint_game.redswf`.
2. Literal-only AVM2 static initializers across all 9 compiled `Gwai*` classes.
3. **Direct PyTorch vs. Compiled-AS3 parity** on real multi-faction matches (**60/60 exact argmax agreement**, **`0.0`** frame-slice vs. synchronous numerical drift).
4. Packed `Mods/modW3GwentAI/content/blob0.bundle` and `metadata.store` byte verification.
