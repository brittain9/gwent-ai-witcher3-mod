"""Public play-by-play hooks shared by the trained and timing-probe editions.

This observes the native transaction path. It neither selects moves nor reads
hidden card identities. Pass the already AI-patched flow to ``patch_sources``.
"""

from __future__ import annotations

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SCHEMA = "w3gwent-playtrace-v1"
SETTINGS_SECTION = "W3GwentPlayTrace"
TOGGLE = "GwaiTrace"
EXTRA_CLASSES = (
    "BasePlayerController",
    "HumanPlayerController",
    "CardInstance",
    "CardLeaderInstance",
)
HELPER_PATH = ROOT / "mod/trace/as3/GwaiPlayTrace.as"
SINK_PATH = ROOT / "mod/trace/settings/scripts/local/gwai_playtrace.ws"


class TraceBuildError(ValueError):
    """A native hook moved or a trace was already injected."""


def helper_source(build_id: str) -> str:
    if not re.fullmatch(r"[A-Za-z0-9_.:-]{1,128}", build_id):
        raise TraceBuildError("trace build ID must be a stable non-empty identifier")
    return HELPER_PATH.read_text().replace("__GWAI_TRACE_BUILD__", build_id)


def _once(source: str, anchor: str, replacement: str, label: str) -> str:
    if source.count(anchor) != 1:
        raise TraceBuildError(
            f"{label}: expected one native hook, found {source.count(anchor)}"
        )
    return source.replace(anchor, replacement, 1)


def _call(expression: str) -> str:
    site = expression.partition("(")[0]
    return (
        f"try {{ {expression}; }} catch(gwaiTraceError:Error) "
        f'{{ try {{ GwaiPlayTrace.failed(gwaiTraceError,"{site}"); }} catch(gwaiTraceStopError:Error) {{}} }}'
    )


def _method_start(source: str, name: str, expression: str) -> str:
    match = re.search(r"function\s+" + re.escape(name) + r"\s*\([^)]*\)[^{]*\{", source)
    if match is None:
        raise TraceBuildError(f"missing native trace method {name}")
    return (
        source[: match.end()]
        + "\n         "
        + _call(expression)
        + source[match.end() :]
    )


def patch_sources(originals: dict[str, str]) -> dict[str, str]:
    """Patch native hooks, preserving the existing trained AI hooks and RNG calls.

    The parent build appends ``helper_source(build_id)`` to its AI compilation
    unit. It also adds ``trace:Boolean(data.trace)`` to its settings packet.
    """
    required = {"GwintGameFlowController", *EXTRA_CLASSES}
    if not required.issubset(originals):
        raise TraceBuildError(
            f"trace exports are missing {sorted(required - originals.keys())}"
        )
    if any("GwaiPlayTrace." in source for source in originals.values()):
        raise TraceBuildError("play trace hooks already installed")
    patched = {name: source.replace("\r\n", "\n") for name, source in originals.items()}
    base = _method_start(
        patched["BasePlayerController"],
        "state_begin_ApplyingCard",
        "GwaiPlayTrace.actionStarted(this.playerID,this._decidedCardTransaction)",
    )
    base = _once(
        base,
        "         if(!this.cardEffectApplying())\n         {",
        "         if(!this.cardEffectApplying())\n         {\n            "
        + _call("this.gameFlowControllerRef.gwaiTraceStable(this.playerID)"),
        "AI stable action",
    )
    for anchor in (
        "            leaderCard.ApplyLeaderAbility(this.isAI);",
        "            CardManager.getInstance().addCardInstanceIDToList(this._transactionCard.instanceId,slotID,playerID);",
        "         CardManager.getInstance().replaceCardInstanceIDs(this._transactionCard.instanceId,cardInstance.instanceId);",
        "            CardManager.getInstance().sendToGraveyardID(this._transactionCard.instanceId);",
    ):
        base = _once(
            base,
            anchor,
            anchor + "\n            " + _call("GwaiPlayTrace.actionCommitted()"),
            "native transaction commit",
        )
    base = _once(
        base,
        "         this._currentRoundStatus = value;",
        "         if(value == ROUND_PLAYER_STATUS_DONE && this._currentRoundStatus != value)\n"
        "         {\n            "
        + _call("GwaiPlayTrace.roundComplete(this.playerID)")
        + "\n         }\n"
        "         this._currentRoundStatus = value;",
        "native pass transition",
    )
    patched["BasePlayerController"] = base
    human = _once(
        patched["HumanPlayerController"],
        '            _turnOver = true;\n            _stateMachine.ChangeState("Idle");',
        "            "
        + _call("this.gameFlowControllerRef.gwaiTraceStable(this.playerID)")
        + '\n            _turnOver = true;\n            _stateMachine.ChangeState("Idle");',
        "human stable action",
    )
    patched["HumanPlayerController"] = human
    flow = patched["GwintGameFlowController"]
    flow = _once(
        flow,
        "         this.cardManager.spawnLeaders();",
        "         this.cardManager.spawnLeaders();\n         "
        + _call(
            "GwaiPlayTrace.begin(this._gwaiSettings != null && this._gwaiSettings.trace === true,"
            "this.cardManager,this,this.currentRound + 1,this.gwaiTraceSelection())"
        ),
        "match start",
    )
    bridge = """      public function gwaiTraceStable(seat:int) : void
      {
         try { GwaiPlayTrace.stable(this.cardManager,this,seat,this.currentRound + 1); }
         catch(gwaiTraceError:Error) { try { GwaiPlayTrace.failed(gwaiTraceError,"GwaiPlayTrace.stable"); } catch(gwaiTraceStopError:Error) {} }
      }

"""
    flow = _once(
        flow,
        "      protected function state_update_CoinToss() : void",
        bridge + "      protected function state_update_CoinToss() : void",
        "stable bridge",
    )
    round_ready = (
        "               this.playerControllers[CardManager.PLAYER_2].resetCurrentRoundStatus();\n"
        "               if(this.playerControllers[this.currentPlayer].currentRoundStatus == BasePlayerController.ROUND_PLAYER_STATUS_DONE)"
    )
    flow = _once(
        flow,
        round_ready,
        "               this.playerControllers[CardManager.PLAYER_2].resetCurrentRoundStatus();\n               "
        + _call(
            'GwaiPlayTrace.stateEvent("round_start",this.cardManager,this,this.currentRound + 1)'
        )
        + "\n               if(this.playerControllers[this.currentPlayer].currentRoundStatus == BasePlayerController.ROUND_PLAYER_STATUS_DONE)",
        "round ready after faction effects and pass reset",
    )
    flow = _once(
        flow,
        "         this.cardManager.updatePlayerLives();",
        "         this.cardManager.updatePlayerLives();\n         "
        + _call(
            'GwaiPlayTrace.stateEvent("round_end",this.cardManager,this,this.currentRound + 1)'
        ),
        "round result",
    )
    final_anchor = "         this.gwaiFunMatchEnd(gameWinner);"
    flow = _once(
        flow,
        final_anchor,
        "         "
        + _call(
            'GwaiPlayTrace.finish("final",gameWinner,this.cardManager,this,this.currentRound + 1)'
        )
        + "\n"
        + final_anchor,
        "final result",
    )
    for method in ("OnEndGameResult", "state_begin_reset"):
        flow = _method_start(
            flow,
            method,
            'GwaiPlayTrace.finish("interrupted",-1,this.cardManager,this,this.currentRound + 1)',
        )
    for method, seat, label in (
        ("handleAcceptMulligan", "CardManager.PLAYER_1", "mulligan"),
        ("handleDeclineMulligan", "CardManager.PLAYER_1", "mulligan_decline"),
    ):
        flow = _method_start(flow, method, f'GwaiPlayTrace.choice({seat},"{label}")')
    # Source/error reporting is independent of the optional on-screen banners.
    flow = _method_start(
        flow,
        "gwaiRecordDecision",
        "GwaiPlayTrace.decision(kind,detail,this.gwaiTraceSelection())",
    )
    patched["GwintGameFlowController"] = flow
    for name, methods in {
        "CardInstance": ("handleNurseChoice",),
        "CardLeaderInstance": (
            "handleHandShowClose",
            "handleResurrectChoice",
            "handleCardDrawChoice_Weather",
            "handleBinChoice",
            "handlePickChoice",
        ),
    }.items():
        for method in methods:
            seat = "this.listsPlayer" if name == "CardInstance" else "owningPlayer"
            patched[name] = _method_start(
                patched[name], method, f'GwaiPlayTrace.choice({seat},"{method}")'
            )
    return patched


def verify_compiled(compiled: dict[str, str], helper: str) -> None:
    required = {
        "BasePlayerController": (
            "GwaiPlayTrace.actionStarted",
            "GwaiPlayTrace.actionCommitted",
            "gwaiTraceStable",
            "GwaiPlayTrace.roundComplete",
        ),
        "HumanPlayerController": ("gwaiTraceStable",),
        "GwintGameFlowController": (
            "GwaiPlayTrace.begin",
            "GwaiPlayTrace.stable",
            "GwaiPlayTrace.stateEvent",
            "GwaiPlayTrace.finish",
            "GwaiPlayTrace.decision",
        ),
        "CardInstance": ("GwaiPlayTrace.choice",),
        "CardLeaderInstance": ("GwaiPlayTrace.choice",),
    }
    for name, fragments in required.items():
        for fragment in fragments:
            if fragment not in compiled.get(name, ""):
                raise TraceBuildError(f"compiled trace is missing {name}: {fragment}")
    if (
        "OnW3GwentPlayTraceRecord" not in helper
        or "OnW3GwentPlayTraceError" not in helper
        or SCHEMA not in helper
    ):
        raise TraceBuildError("compiled public trace helper is missing its protocol")
    if re.search(r"\bJSON\s*\.", helper):
        raise TraceBuildError("public trace must use its own constrained encoder")
