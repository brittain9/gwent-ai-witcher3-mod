"""Strict offline native host patching for one shared policy/value model."""

from __future__ import annotations

import re

from .host_hooks import AI_MEMBERS, FLOW_METHODS, MENU_METHOD

PACKAGE = "red.game.witcher3.menus.gwint"
HOSTS = {"AIPlayerController", "GwintGameFlowController", "GwintGameMenu"}


class HostIntegrationError(ValueError):
    """Native anchors or the shared helper inventory differ from the contract."""


def _once(source: str, anchor: str, addition: str, label: str) -> str:
    if source.count(anchor) != 1:
        raise HostIntegrationError(f"{label}: expected exactly one native anchor")
    return source.replace(anchor, anchor + addition, 1)


def _body(source: str, method: str) -> tuple[int, int]:
    matches = list(re.finditer(r"\bfunction\s+" + re.escape(method) + r"\s*\(", source))
    if len(matches) != 1:
        raise HostIntegrationError(f"expected exactly one native method {method}")
    opening = source.find("{", matches[0].end())
    if opening < 0:
        raise HostIntegrationError(f"native method {method} lacks a body")
    depth, quote, comment, escaped = 1, "", "", False
    index = opening + 1
    while index < len(source):
        char = source[index]
        pair = source[index : index + 2]
        if comment:
            if comment == "//" and char == "\n":
                comment = ""
            elif comment == "/*" and pair == "*/":
                comment = ""
                index += 1
        elif quote:
            if escaped:
                escaped = False
            elif char == "\\":
                escaped = True
            elif char == quote:
                quote = ""
        elif pair in {"//", "/*"}:
            comment = pair
            index += 1
        elif char in {"'", '"'}:
            quote = char
        elif char == "{":
            depth += 1
        elif char == "}":
            depth -= 1
            if depth == 0:
                return opening + 1, index
        index += 1
    raise HostIntegrationError(f"native method {method} has an unclosed body")


def _method_once(source: str, method: str, anchor: str, addition: str) -> str:
    start, end = _body(source, method)
    return (
        source[:start]
        + _once(source[start:end], anchor, addition, method)
        + source[end:]
    )


def _helpers(helpers: dict[str, str]) -> None:
    expected = {
        "GwaiCapture",
        "GwaiNetPolicy",
        "GwaiNetWeights",
        "GwaiSharedController",
        "GwaiSharedConfig",
    }
    names = set(helpers)
    weights = {
        name for name in names if re.fullmatch(r"GwaiNetWeightsData_[0-9a-f]{16}", name)
    }
    if len(weights) != 1 or names - weights - {"GwaiSharedDiagnostic"} != expected:
        raise HostIntegrationError(
            "shared helper inventory requires exactly one model and supported classes"
        )
    for name, text in helpers.items():
        if (
            len(re.findall(r"\binternal\s+class\s+" + re.escape(name) + r"\b", text))
            != 1
        ):
            raise HostIntegrationError(f"shared helper declaration differs: {name}")
        if re.search(
            r"\bpackage\b|\bGwaiNetTierConfig\b|\bGwaiPolicyCore\b|\bGwaiRunner\b", text
        ):
            raise HostIntegrationError(
                "shared helper contains a package or obsolete model controller"
            )
    diagnostic = "GwaiSharedDiagnostic" in names
    if (
        "GwaiSharedDiagnostic.record(" in helpers["GwaiSharedController"]
    ) != diagnostic:
        raise HostIntegrationError("shared diagnostic helper and call must agree")


def patch_sources(originals: dict[str, str], helpers: dict[str, str]) -> dict[str, str]:
    """Patch exact native classes without starting compilation or inference."""
    if set(originals) != HOSTS:
        raise HostIntegrationError("native host class inventory differs")
    _helpers(helpers)
    patched = {name: text.replace("\r\n", "\n") for name, text in originals.items()}
    if any(
        re.search(r"\bgwai(?:Begin|ConfigureSettings|ConfigureModSettings)\b", text)
        for text in patched.values()
    ):
        raise HostIntegrationError("native host is already or partially patched")
    ai = patched["AIPlayerController"]
    start, end = _body(ai, "state_begin_ChoseMove")
    native_choice = ai[start:end]
    ai = _once(
        ai,
        f"package {PACKAGE}\n{{",
        "\n   import flash.utils.getTimer;\n   import flash.utils.getQualifiedClassName;",
        "AI package imports",
    )
    for method, statement in (
        ("state_begin_ChoseMove", "if(this.gwaiBegin()) return;"),
        ("state_update_ChooseMove", "if(this.gwaiPump()) return;"),
        ("state_begin_Idle", "this.gwaiCancel();"),
    ):
        start, _ = _body(ai, method)
        ai = ai[:start] + "\n         " + statement + ai[start:]
    ai = _once(
        ai,
        "protected var _currentRoundCritical:Boolean = false;",
        "\n" + AI_MEMBERS.replace("__GWAI_NATIVE_FALLBACK_BODY__", native_choice),
        "AI shared members",
    )
    closing = ai.rfind("}")
    if closing < 0 or ai[closing + 1 :].strip():
        raise HostIntegrationError("AI native package closing delimiter differs")
    ai = (
        ai[:closing]
        + "\n"
        + "\n".join(helpers[name] for name in sorted(helpers))
        + "\n"
        + ai[closing:]
    )
    flow = _once(
        patched["GwintGameFlowController"],
        "public var gameStarted:Boolean = false;",
        "\n" + FLOW_METHODS,
        "flow shared members",
    )
    for method, statement in (
        (
            "state_begin_reset",
            "this.gwaiCancelPending();\n         this._gwaiSignalShown = false;\n         this._gwaiInitiative = -1;\n         this._gwaiDecisionId = 0;\n         this._gwaiMatchEnded = true;",
        ),
        ("EndGame", "this.gwaiCancelPending();\n         this._gwaiMatchEnded = true;"),
    ):
        start, _ = _body(flow, method)
        flow = flow[:start] + "\n         " + statement + flow[start:]
    flow = _method_once(
        flow,
        "state_update_CoinToss",
        "         if(this.currentPlayer != CardManager.PLAYER_INVALID)\n         {",
        "\n            this._gwaiMatchEnded = false;\n            if(this._gwaiInitiative < 0)\n            {\n               this._gwaiInitiative = this.currentPlayer;\n               this.gwaiPracticeBanner();\n            }",
    )
    menu = _once(
        patched["GwintGameMenu"],
        'dispatchEvent(new GameEvent(GameEvent.REGISTER,"gwint.game.toggleAI",[this.setAIEnabled]));',
        '\n         dispatchEvent(new GameEvent(GameEvent.REGISTER,"gwai.mod.settings",[this.gwaiConfigureModSettings]));',
        "menu shared configuration",
    )
    menu = _once(
        menu,
        "public var gameFlowController:GwintGameFlowController;",
        "\n" + MENU_METHOD,
        "menu shared method",
    )
    return {
        "AIPlayerController": ai,
        "GwintGameFlowController": flow,
        "GwintGameMenu": menu,
    }
