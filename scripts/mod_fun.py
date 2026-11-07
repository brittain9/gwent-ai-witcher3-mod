"""Opt-in fun features of the Witcher 3 Gwent AI mod: toggles, story tiers and limits.

One table feeds the generated AS3 config class, the package manifest and the tests, so the
game, the manifest and the documentation cannot disagree about a feature or a tier.
"""

from __future__ import annotations

import json
import re
from typing import Any

SETTINGS_GROUP = "W3GwentAI"
STATS_SECTION = "W3GwentAIStats"
NOTES_SECTION = "W3GwentAIFun"

# Every feature is an Options > Mods toggle that is off by default. Player features stand alone;
# lab features are analysis tools, each on only while the Lab mode toggle is on too.
LAB_TOGGLE = {"toggle": "GwaiLab", "label": "Lab mode (analysis tools)"}
FEATURES: dict[str, dict[str, Any]] = {
    "coach": {
        "group": "player",
        "toggle": "GwaiCoach",
        "label": "Coach hint on your turn",
        "information": "your seat's view only: your hand, both boards and graveyards, the opponent's hand count",
        "network": "one policy and value pass of the reading network at each of your decision points",
        "shows": "a banner with the network's pick for your seat and your win chance",
    },
    "story": {
        "group": "player",
        "toggle": "GwaiStory",
        "label": "Stronger story opponents",
        "information": "the opponent deck's name, sent by the game's scripts; the AI keeps its fair seat view",
        "network": "none extra: it only picks the tier",
        "shows": "tournaments and quest opponents play Hard, other players at least Normal; never below your own setting",
    },
    "meter": {
        "group": "lab",
        "toggle": "GwaiMeter",
        "label": "Win-chance meter (the AI's reading)",
        "information": "the AI seat's view, which includes the AI's own hand: the number tells you how the AI rates its hand",
        "network": "none extra: the value head runs inside the AI's own decision pass",
        "shows": "the AI's win chance appended to its per-move banner",
    },
    "post_game": {
        "group": "lab",
        "toggle": "GwaiPostGame",
        "label": "Post-game coach card",
        "information": "your seat's view only",
        "network": "the coach pass at each of your decision points (it runs for this feature with the coach off)",
        "shows": "at the result screen, your move with the largest fall in win chance to your next turn, and the pick",
    },
    "blunder": {
        "group": "lab",
        "toggle": "GwaiBlunder",
        "label": "Blunder alert",
        "information": "your seat's view only",
        "network": "the post-game passes; none of its own",
        "shows": "at your next turn, a banner when your win chance fell a large step since a move that left the pick",
    },
    "stats": {
        "group": "lab",
        "toggle": "GwaiStats",
        "label": "Match stats",
        "information": "match results only",
        "network": "none",
        "shows": "your record on this tier and against this opponent at the result screen",
    },
}

# Feature id as the AS3 settings object spells it.
AS3_FLAGS = {
    "coach": "coach",
    "story": "story",
    "meter": "meter",
    "post_game": "postGame",
    "blunder": "blunder",
    "stats": "stats",
}

# The human seat's readings (coach, post-game card, blunder alert) use the Hard network on every
# tier: in calibration the Easy network's reading rose by 3.6 points a step on its own picks, so
# use a consistent reading network so changes remain comparable.
READING_TIER = 3

# The reading network is decoded from the coin toss on its own timer, one of its 221 base64 chunks
# (48,000 characters) per tick, so no tick holds the whole decode (about 410 ms in V8, more in
# Scaleform). The readings stay silent until it is done.
LOADER_TICK_MS = 20
LOADER_CHUNKS_PER_TICK = 1

STATS_BUCKETS = ("vanilla", "easy", "normal", "hard")
STATS_OUTCOMES = ("won", "lost", "drawn")

MAX_RECORDS = 64
CARD_MIN_DROP = 0.05
# scripts/calibrate_fun_drops.py, 4,000 games a pairing, the Hard network reading seat 0: a fall of
# 20 points to the next decision follows 2.5-2.7% of the network's own picks and 2.5-3.2% of
# second-best moves, so the alert is rare on sound play; it also fires on only 6-12% of forced
# mid-match passes, because much of a pass's cost shows later than the next decision.
BLUNDER_DROP = 0.20

STORY_TIER_BY_CATEGORY = {"tournament": 3, "named": 3, "generic": 2}
TIER_NAMES = {1: "easy", 2: "normal", 3: "hard"}

# The 56 deck collections of the game's def_gwint_decks.xml. A collection with its own deck is a
# quest opponent (named); the faction decks at three difficulties are the random merchants and
# innkeepers (generic); the starter decks and the tutorial keep the game's own setting.
DECK_CATEGORIES: dict[str, str] = {
    **{
        name: "tournament"
        for name in (
            "NKTournament",
            "NilfTournament",
            "NMLTournament",
            "ScoiaTournament",
            "NKTournament2",
            "NilfTournament2",
            "NMLTournament2",
            "ScoiaTournament2",
            "SkelTournament2",
        )
    },
    **{
        name: "named"
        for name in (
            "Baron",
            "BoatBuilder",
            "CardProdigy",
            "CircusGwentAddict",
            "Crach",
            "CrossroadsInnkeeper",
            "Dijkstra",
            "Gambler",
            "Gremista",
            "Halflings",
            "Hermit",
            "Lambert",
            "LugosTheMad",
            "MarkizaSerenity",
            "Mousesack",
            "Olgierd",
            "Olivier",
            "Roche",
            "ScoiaTrader",
            "Shani",
            "Sjusta",
            "Stjepan",
            "Thaler",
            "VimmeVivaldi",
            "Zoltan",
        )
    },
    **{
        name: "generic"
        for name in (
            "NKEasy",
            "NKNormal",
            "NKHard",
            "NilfEasy",
            "NilfNormal",
            "NilfHard",
            "NMLEasy",
            "NMLNormal",
            "NMLHard",
            "ScoiaEasy",
            "ScoiaNormal",
            "ScoiaHard",
            "SkelEasy",
            "SkelNormal",
            "SkelHard",
            "NilfPrologue",
        )
    },
    **{
        name: "unchanged"
        for name in ("North", "Nilf", "scotial", "nml", "ske", "tutorialDeck")
    },
}

_FACTIONS = {
    "NK": "Northern Realms",
    "Nilf": "Nilfgaard",
    "NML": "Monsters",
    "Scoia": "Scoia'tael",
    "Skel": "Skellige",
}


def story_tier_for(deck: str) -> int:
    """The AI tier a deck gets with the story toggle on; 0 keeps the game's own setting."""
    return STORY_TIER_BY_CATEGORY.get(DECK_CATEGORIES.get(deck, "unchanged"), 0)


def deck_label(deck: str) -> str:
    """A readable opponent name for the stats banner."""
    match = re.fullmatch(
        r"(NK|Nilf|NML|Scoia|Skel)(Easy|Normal|Hard|Prologue|Tournament2?)", deck
    )
    if match:
        faction, kind = _FACTIONS[match.group(1)], match.group(2)
        if kind == "Tournament":
            return f"the {faction} tournament deck"
        if kind == "Tournament2":
            return f"the second {faction} tournament deck"
        return f"a {faction} player ({kind.lower()})"
    return re.sub(r"(?<=[a-z])(?=[A-Z])", " ", deck)


def _as3_string(value: str) -> str:
    return json.dumps(value, ensure_ascii=True)


def fun_config_source() -> str:
    """The generated AS3 class with the story table, labels and limits."""
    tiers = ",".join(
        f"{_as3_string(name)}:{story_tier_for(name)}"
        for name in sorted(DECK_CATEGORIES)
        if story_tier_for(name) > 0
    )
    labels = ",".join(
        f"{_as3_string(name)}:{_as3_string(deck_label(name))}"
        for name in sorted(DECK_CATEGORIES)
    )
    return "\n".join(
        [
            "internal class GwaiFunConfig",
            "{",
            f"   public static const MAX_RECORDS:int = {MAX_RECORDS};",
            f"   public static const READING_TIER:int = {READING_TIER};",
            f"   public static const LOADER_TICK_MS:int = {LOADER_TICK_MS};",
            f"   public static const LOADER_CHUNKS_PER_TICK:int = {LOADER_CHUNKS_PER_TICK};",
            f"   public static const CARD_MIN_DROP:Number = {CARD_MIN_DROP!r};",
            f"   public static const BLUNDER_DROP:Number = {BLUNDER_DROP!r};",
            f"   private static const STORY_TIERS:Object = {{{tiers}}};",
            f"   private static const LABELS:Object = {{{labels}}};",
            "   public static function storyTierFor(deck:String) : int",
            "   {",
            "      if(deck == null || !STORY_TIERS.hasOwnProperty(deck)) return 0;",
            "      return int(STORY_TIERS[deck]);",
            "   }",
            "   public static function labelFor(deck:String) : String",
            "   {",
            '      if(deck == null || deck == "") return "";',
            "      if(!LABELS.hasOwnProperty(deck)) return deck;",
            "      return String(LABELS[deck]);",
            "   }",
            "}",
            "",
        ]
    )


def manifest_section() -> dict[str, Any]:
    """The package manifest's record of the feature set."""
    categories: dict[str, dict[str, Any]] = {}
    for name in sorted(DECK_CATEGORIES):
        category = DECK_CATEGORIES[name]
        entry = categories.setdefault(
            category,
            {
                "tier": TIER_NAMES.get(
                    STORY_TIER_BY_CATEGORY.get(category, 0), "the game's setting"
                ),
                "decks": [],
            },
        )
        entry["decks"].append(name)
    return {
        "schema": "w3-gwent-fun-features-v2",
        "default_enabled": False,
        "lab_mode": {
            "toggle": f"{SETTINGS_GROUP}.{LAB_TOGGLE['toggle']}",
            "label": LAB_TOGGLE["label"],
            "default_enabled": False,
            "gates": [
                feature for feature, spec in FEATURES.items() if spec["group"] == "lab"
            ],
        },
        "features": {
            feature: {
                "group": spec["group"],
                "toggle": f"{SETTINGS_GROUP}.{spec['toggle']}",
                **(
                    {"requires": f"{SETTINGS_GROUP}.{LAB_TOGGLE['toggle']}"}
                    if spec["group"] == "lab"
                    else {}
                ),
                "label": spec["label"],
                "default_enabled": False,
                "information": spec["information"],
                "network": spec["network"],
                "shows": spec["shows"],
            }
            for feature, spec in FEATURES.items()
        },
        "network_used": (
            f"the human seat's readings use the {TIER_NAMES[READING_TIER]} network at temperature zero on every "
            "tier and also with the vanilla AI selected; only the coach, the post-game card and the blunder alert "
            "read it, and only they decode it: from the coin toss, one chunk per timer tick, the readings silent "
            "until it is done; beside another tier's network that is two decoded networks, about 31.8 MB of AS3 Numbers"
        ),
        "win_chance": "the value head's P(win) for the named seat, from that seat's view",
        "move_cost": (
            "the fall in the human seat's win chance from one of its decision points to the next; after its "
            "last move, to the result (100% for a win, 0% otherwise); readings are only at decision points, "
            "the states the value head is trained on"
        ),
        "frame_budget": "at most one network pass per flow tick, only on the first tick of a human decision point",
        "failure": "the first error stops the features for the rest of the match and is written to the match note",
        "story_tiers": {
            "applies_when": "the story toggle is on",
            "rule": "the higher of the game's own mapping and the deck's story tier: the story never lowers a tier",
            "deck_source": "NameToString(gwintManager.GetCurrentAIDeck().deckName) in the settings packet",
            "unknown_deck": "the game's own setting",
            "categories": categories,
        },
        "persistence": {
            STATS_SECTION: "one counter per tier bucket and per opponent deck: <bucket>_<outcome>, vs_<deck>_<outcome>",
            NOTES_SECTION: "last_card (post-game card) and last_match (feature diagnostics) for the last match",
            "buckets": list(STATS_BUCKETS),
            "outcomes": list(STATS_OUTCOMES),
        },
        "limits": {
            "post_game_records_per_match": MAX_RECORDS,
            "post_game_minimum_drop": CARD_MIN_DROP,
            "reading_loader_tick_ms": LOADER_TICK_MS,
            "reading_loader_chunks_per_tick": LOADER_CHUNKS_PER_TICK,
            "blunder_drop": BLUNDER_DROP,
        },
    }
