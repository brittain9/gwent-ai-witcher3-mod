"""Practice opponents shared by the settings script, menus, and build manifest."""

from __future__ import annotations

import json
import re
import xml.etree.ElementTree as ET
from pathlib import Path

try:
    from scripts.mod_fun import deck_label
except ModuleNotFoundError:
    from mod_fun import deck_label


ROOT = Path(__file__).resolve().parents[1]
# Saved values are zero-based positions: preserve this order and append entries.
DECKS = (
    "NKHard",
    "NilfHard",
    "ScoiaHard",
    "NMLHard",
    "SkelHard",
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
    "NKTournament",
    "NilfTournament",
    "ScoiaTournament",
    "NMLTournament",
    "ScoiaTournament2",
    "NMLTournament2",
    "NKTournament2",
    "NilfTournament2",
    "SkelTournament2",
)
LABELS = {
    "NKHard": "Generic: Northern Realms",
    "NilfHard": "Generic: Nilfgaard",
    "ScoiaHard": "Generic: Scoia'tael",
    "NMLHard": "Generic: Monsters",
    "SkelHard": "Generic: Skellige",
    "Baron": "Bloody Baron",
    "CardProdigy": "White Orchard scholar",
    "MarkizaSerenity": "Marquise Serenity",
    "Mousesack": "Ermion",
    "LugosTheMad": "Madman Lugos",
    "ScoiaTrader": "Scoia'tael trader",
}
BEGIN = "// BEGIN GENERATED PRACTICE OPPONENTS"
END = "// END GENERATED PRACTICE OPPONENTS"


def label(deck: str) -> str:
    return LABELS.get(deck, deck_label(deck))


def script_source() -> str:
    lines = [BEGIN, "function GwaiPracticeOpponent(id : int) : name", "{"]
    for index, deck in enumerate(DECKS, 1):
        lines.append(f"    if (id == {index}) {{ return '{deck}'; }}")
    lines += [
        "    return 'NKHard';",
        "}",
        "",
        "function GwaiPracticeOpponentLabel(id : int) : string",
        "{",
    ]
    for index, deck in enumerate(DECKS, 1):
        lines.append(f"    if (id == {index}) {{ return {json.dumps(label(deck))}; }}")
    lines += [f"    return {json.dumps(label(DECKS[0]))};", "}", END]
    return "\n".join(lines)


def verify() -> None:
    source = (ROOT / "mod/ai/settings/scripts/local/gwai_settings.ws").read_text()
    if script_source() not in source:
        raise ValueError("practice script is stale; run python scripts/mod_practice.py")
    expected = [(str(i), label(deck), str(i)) for i, deck in enumerate(DECKS)]
    for name in (
        "mod/ai/settings/menu/modW3GwentAI.xml",
        "mod/probe-timing/settings/menu/modW3GwentAIProbe.xml",
    ):
        root = ET.parse(ROOT / name).getroot()
        choices = root.findall(".//Var[@id='GwaiDevOpponent']/OptionsArray/Option")
        actual = [
            (o.get("id"), o.get("displayName"), o.find("Entry").get("value"))
            for o in choices
        ]
        if actual != expected:
            raise ValueError(
                f"{name}: practice opponents are stale; run python scripts/mod_practice.py"
            )


def generate() -> None:
    path = ROOT / "mod/ai/settings/scripts/local/gwai_settings.ws"
    source = path.read_text()
    if BEGIN in source:
        source = re.sub(
            re.escape(BEGIN) + r".*?" + re.escape(END),
            lambda _: script_source(),
            source,
            flags=re.S,
        )
    else:
        source += "\n" + script_source() + "\n"
    path.write_text(source)
    for name in (
        "mod/ai/settings/menu/modW3GwentAI.xml",
        "mod/probe-timing/settings/menu/modW3GwentAIProbe.xml",
    ):
        path = ROOT / name
        tree = ET.parse(path)
        choices = tree.getroot().find(".//Var[@id='GwaiDevOpponent']/OptionsArray")
        choices.clear()
        for i, deck in enumerate(DECKS):
            option = ET.SubElement(
                choices, "Option", id=str(i), displayName=label(deck)
            )
            ET.SubElement(option, "Entry", varId="GwaiDevOpponent", value=str(i))
        ET.indent(tree, space="    ")
        tree.write(path, encoding="UTF-8", xml_declaration=True)
        with path.open("a") as handle:
            handle.write("\n")


if __name__ == "__main__":
    generate()
    verify()
