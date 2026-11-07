#!/usr/bin/env python3
"""Build the Witcher 3 Gwent AI integration mod."""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import os
import re
import shutil
import subprocess
import tempfile
from pathlib import Path
from typing import Any

try:
    from scripts import build_probe, compiled_policy, mod_fun, mod_practice, mod_trace
except ModuleNotFoundError:
    import build_probe
    import compiled_policy
    import mod_fun
    import mod_practice
    import mod_trace


ROOT = Path(__file__).resolve().parents[1]
BUILD_ID = "0.1.0"
AS3_DIR = ROOT / "mod/ai/as3"
OUTPUT_DIR = ROOT / ".runtime/mod-build"
RECORDER_OUTPUT_DIR = ROOT / ".runtime/mod-build-recorder"
PROBE_OUTPUT_DIR = ROOT / ".runtime/mod-build-probe"
POLICY_EXPORT_DIR = ROOT / ".runtime/policy-as3"
POLICY_FIXTURE_DIR = ROOT / ".runtime/policy-as3-fixtures"
DEFAULT_CHECKPOINT = ROOT / ".runtime/models/hard.pt"
TIER_NAMES = ("easy", "normal", "hard")
PACKAGE = build_probe.PACKAGE
PACKAGE_PATH = build_probe.PACKAGE_PATH
AI_BEGIN_ANCHOR = "protected function state_begin_ChoseMove() : void\n      {"
AI_PUMP_ANCHOR = "protected function state_update_ChooseMove() : void\n      {"
AI_IDLE_ANCHOR = "protected function state_begin_Idle() : void\n      {"
MENU_CONFIG_ANCHOR = 'dispatchEvent(new GameEvent(GameEvent.REGISTER,"gwint.game.toggleAI",[this.setAIEnabled]));'
FLOW_FIELD_ANCHOR = "public var gameStarted:Boolean = false;"
FLOW_RESET_ANCHOR = "protected function state_begin_reset() : void\n      {"
FLOW_END_ANCHOR = "public function EndGame(result:int) : void\n      {"
FLOW_FINAL_RESULT_ANCHOR = (
    "         this.mcEndGameDialog.show(gameWinner,this.OnEndGameResult);"
)
RECORDER_RANDOM_COUNTS = {
    "AIPlayerController": 15,
    "CardInstance": 10,
    "CardLeaderInstance": 4,
    "CardManager": 3,
    "GwintDeck": 2,
    "GwintGameFlowController": 1,
}
RECORDER_CLASSES = tuple(RECORDER_RANDOM_COUNTS) + (
    "BasePlayerController",
    "HumanPlayerController",
    "GwintGameMenu",
)


class ModBuildError(RuntimeError):
    """Raised when the offline inputs or a source hook are ambiguous."""


# A capture holding a card outside the network's vocabulary hands the turn to the vanilla AI
# before the runner exists, with its own banner (gwaiBegin, gwaiNotifyDecision).
UNKNOWN_CARD_GUARD = (
    "GwaiNetPolicy.unknownCards(\n"
    "               this._gwaiView.observation,GwaiNetTierConfig.dataFor(profile).CARD_IDS)"
)
UNKNOWN_CARD_BANNER = (
    'PushMessage("W3 Gwent AI: " + detail + ", vanilla AI plays this turn")'
)

# Frame labels of the message queue's image sprite (SWF symbol 918). W3MessageQueue calls
# mcImages.gotoAndStop(image) before it dequeues a message; a missing label can leave the
# queue showing forever, which stops the match flow. An omitted image means "none".
VANILLA_MESSAGE_IMAGES = frozenset(
    {
        "battle_won",
        "battle_lost",
        "battle_draw",
        "Opponents_turn",
        "your_turn",
        "north_ability",
        "sco_ability",
        "monster_ability",
        "nilf_ability",
        "coin_flip_win",
        "passed",
        "coin_flip_loss",
        "round_start",
        "skel_ability",
        "none",
    }
)


def _call_arguments(source: str, open_paren: int) -> list[str]:
    """Split a call's top-level arguments, ignoring commas inside strings and brackets."""
    arguments: list[str] = []
    depth = 0
    quote = ""
    start = open_paren + 1
    for index in range(open_paren + 1, len(source)):
        char = source[index]
        if quote:
            if char == quote and source[index - 1] != "\\":
                quote = ""
            continue
        if char in "\"'":
            quote = char
        elif char in "([{":
            depth += 1
        elif char in ")]}":
            if depth == 0:
                arguments.append(source[start:index].strip())
                return [argument for argument in arguments if argument]
            depth -= 1
        elif char == "," and depth == 0:
            arguments.append(source[start:index].strip())
            start = index + 1
    raise ModBuildError("unterminated PushMessage call")


def verify_message_images(source: str, label: str) -> None:
    """Refuse a banner whose image is not a frame label of the vanilla message queue."""
    for match in re.finditer(r"\bPushMessage\s*\(", source):
        arguments = _call_arguments(source, match.end() - 1)
        if len(arguments) < 2:
            continue
        image = arguments[1]
        literal = re.fullmatch(r"\"([^\"]*)\"", image)
        if literal is None or (
            literal.group(1) and literal.group(1) not in VANILLA_MESSAGE_IMAGES
        ):
            raise ModBuildError(
                f"{label}: banner image {image} is not a vanilla message frame label"
            )


AI_MEMBERS = r"""      private var _gwaiRunner:Object = null;
      private var _gwaiView:Object = null;
      private var _gwaiMoves:Array = null;
      private var _gwaiProfile:int = -1;
      private var _gwaiStartedAt:int = -1;

      public function gwaiCancel() : void
      {
         if(this._gwaiRunner != null) this._gwaiRunner.cancel();
         this._gwaiRunner = null;
         this._gwaiView = null;
         this._gwaiMoves = null;
         this._gwaiProfile = -1;
         this._gwaiStartedAt = -1;
      }

      public function gwaiBegin() : Boolean
      {
         this.gwaiCancel();
         var profile:int = this.gameFlowControllerRef.gwaiProfileForPlayer(this.playerID);
         if(profile < 0)
         {
            this.gameFlowControllerRef.gwaiNotifyStockTurn();
            return false;
         }
         var stage:String = "capture";
         try
         {
            this._gwaiStartedAt = getTimer();
            var manager:CardManager = CardManager.getInstance();
            this._gwaiView = GwaiCapture.capture(manager,this.gameFlowControllerRef,this.playerID);
            stage = "vocabulary";
            var unknownCards:Array = GwaiNetPolicy.unknownCards(
               this._gwaiView.observation,GwaiNetTierConfig.dataFor(profile).CARD_IDS);
            if(unknownCards.length > 0)
            {
               this.gwaiReportDecision(profile,true,"unknown card vocabulary");
               this.gwaiCancel();
               return false;
            }
            stage = "legal moves";
            this._gwaiMoves = GwaiCapture.legalMoves(manager,this.playerID);
            if(this._gwaiMoves.length == 0)
            {
               this.gwaiReportDecision(profile,true,"no legal moves");
               this.gwaiCancel();
               return false;
            }
            this._gwaiProfile = profile;
            stage = "runner setup";
            this._gwaiRunner = new GwaiRunner(this._gwaiView,this._gwaiMoves,profile);
            this._decidedCardTransaction = null;
            this.attitude = TACTIC_NONE;
            return true;
         }
         catch(error:Error)
         {
            this.gwaiReportDecision(profile,true,stage + " failed: " + error.toString());
            this.gwaiCancel();
            return false;
         }
      }

      public function gwaiPump() : Boolean
      {
         if(this._gwaiRunner == null) return false;
         var profile:int = this._gwaiProfile;
         var stage:String = "inference";
         try
         {
            if(this.gameFlowControllerRef.gwaiMatchEnded())
            {
               this.gwaiCancel();
               return true;
            }
            if(this.gameFlowControllerRef.gwaiProfileForPlayer(this.playerID) != this._gwaiProfile)
            {
               this.gwaiCancel();
               this.gwaiReportDecision(profile,true,"settings changed while thinking");
               this.gwaiNativeFallback();
               return false;
            }
            if(!this._gwaiRunner.advance()) return true;
            stage = "transaction";
            var move:Object = this._gwaiRunner.output;
            var networkMode:Boolean = this._gwaiView != null && this._gwaiView.policyMode == "network";
            var greedyFallback:Boolean = this._gwaiView != null && this._gwaiView.policyFallback === true;
            var startedAt:int = this._gwaiStartedAt;
            var meter:String = "";
            if(networkMode && !greedyFallback)
            {
               try
               {
                  meter = this.gameFlowControllerRef.gwaiMeterSuffix(Number(this._gwaiRunner.winChance));
               }
               catch(meterError:Error)
               {
                  meter = "";
               }
            }
            this._gwaiRunner = null;
            this._gwaiView = null;
            this._gwaiMoves = null;
            this._gwaiProfile = -1;
            this._gwaiStartedAt = -1;
            if(move == null) throw new Error("policy returned no action");
            if(move.kind == "pass")
            {
               this._decidedCardTransaction = null;
               this.attitude = TACTIC_PASS;
               var decisionMs:int = startedAt < 0 ? -1 : getTimer() - startedAt;
               this.gwaiReportDecision(profile,false,"pass" + (networkMode ? (greedyFallback ? " greedy-fallback network " : " network ") + decisionMs + "ms" + meter : ""));
               return false;
            }
            var manager:CardManager = CardManager.getInstance();
            if(move.kind == "leader")
            {
               var leader:CardLeaderInstance = manager.getCardLeader(this.playerID);
               if(leader == null || !leader.canBeUsed || leader.instanceId != int(move.instanceId))
                  throw new Error("selected leader action is no longer legal for this seat");
               leader.recalculatePowerPotential(manager);
               var leaderTransaction:CardTransaction = leader.getOptimalTransaction();
               if(leaderTransaction == null || leaderTransaction.strategicValue == -1)
                  throw new Error("selected leader action has no legal transaction");
               this._decidedCardTransaction = leaderTransaction;
               this.attitude = TACTIC_MAXIMIZE_WIN;
               decisionMs = startedAt < 0 ? -1 : getTimer() - startedAt;
               this.gwaiReportDecision(profile,false,"leader" + (networkMode ? (greedyFallback ? " greedy-fallback network " : " network ") + decisionMs + "ms" + meter : ""));
               return false;
            }
            if(move.kind != "play") throw new Error("policy returned an unknown action");
            var card:CardInstance = manager.getCardInstance(int(move.instanceId));
            var targetSlot:int = move.slotId == undefined ? int(move.row) : int(move.slotId);
            var targetInstanceId:Number = move.targetInstanceId == undefined ? -1 : Number(move.targetInstanceId);
            var targetCard:CardInstance = targetInstanceId < 0 ? null : manager.getCardInstance(int(targetInstanceId));
            var isGlobalScorch:Boolean = card != null && targetInstanceId < 0 && card.templateRef.isType(CardTemplate.CardType_Global_Effect) && card.templateRef.hasEffect(CardTemplate.CardEffect_Scorch);
            var legalTarget:Boolean = targetInstanceId >= 0 ? targetCard != null && card != null && card.canBeCastOn(targetCard) : isGlobalScorch || card != null && card.canBePlacedInSlot(targetSlot,int(move.targetPlayer));
            if(card == null || card.listsPlayer != this.playerID || card.inList != CardManager.CARD_LIST_LOC_HAND || !legalTarget)
               throw new Error("selected move is no longer legal for this seat");
            var transaction:CardTransaction = new CardTransaction();
            transaction.sourceCardInstanceRef = card;
            transaction.targetSlotID = targetInstanceId >= 0 || isGlobalScorch ? CardManager.CARD_LIST_LOC_INVALID : targetSlot;
            transaction.targetPlayerID = targetInstanceId >= 0 ? this.playerID : (isGlobalScorch ? CardManager.PLAYER_INVALID : int(move.targetPlayer));
            transaction.targetCardInstanceRef = targetInstanceId >= 0 ? targetCard : null;
            transaction.powerChangeResult = Number(move.scoreGain);
            transaction.strategicValue = Number(move.scoreGain);
            this._decidedCardTransaction = transaction;
            this.attitude = TACTIC_MAXIMIZE_WIN;
            decisionMs = startedAt < 0 ? -1 : getTimer() - startedAt;
            this.gwaiReportDecision(profile,false,"play " + card.templateId + (networkMode ? (greedyFallback ? " greedy-fallback network " : " network ") + decisionMs + "ms" + meter : ""));
            return false;
         }
         catch(error:Error)
         {
            this.gwaiReportDecision(profile,true,stage + " failed: " + error.toString());
            this.gwaiCancel();
            this.gwaiNativeFallback();
            return false;
         }
      }

      private function gwaiReportDecision(profile:int, fallback:Boolean, detail:String) : void
      {
         var difficulty:String = profile == 1 ? "Easy" : (profile == 2 ? "Medium" : "Hard");
         if(fallback)
         {
            CardManager.log("#W3AI# fallback: " + detail);
            this.gameFlowControllerRef.gwaiNotifyDecision(profile,true,detail);
         }
         else
         {
            CardManager.log("#W3AI# decision: " + difficulty + " " + detail + " " + GwaiNetPolicy.provenance(profile));
            this.gameFlowControllerRef.gwaiNotifyDecision(profile,false,detail);
         }
      }

      private function gwaiNativeFallback() : void
      {
__GWAI_NATIVE_FALLBACK_BODY__
      }
"""

FLOW_METHODS = r"""      private var _gwaiSettings:Object = null;
      private var _gwaiFun:Object = null;
      private var _gwaiSignalShown:Boolean = false;
      private var _gwaiInitiative:int = -1;
      private var _gwaiDecisionId:int = 0;
      private var _gwaiMatchEnded:Boolean = true;

      public function gwaiConfigureSettings(data:Object) : void
      {
         if(data == null || int(data.version) != 1)
         {
            this._gwaiSettings = null;
            return;
         }
         var vanillaDifficulty:int = int(data.vanillaDifficulty);
         if(vanillaDifficulty < 0 || vanillaDifficulty > 3) vanillaDifficulty = 2;
         var signal:Boolean = data.signal == undefined ? false : Boolean(data.signal);
         var fun:Object = null;
         try
         {
            fun = GwaiFun.parseSettings(data);
         }
         catch(funError:Error)
         {
            fun = null;
         }
         this._gwaiSettings = {
            enabled:Boolean(data.enabled),vanillaDifficulty:vanillaDifficulty,
            signal:signal,
            trace:Boolean(data.trace),
            fun:fun,storyTier:fun == null ? 0 : int(fun.storyTier),
            practice:Boolean(data.practice),practiceOpponent:String(data.practiceOpponent),
            practiceDeck:String(data.practiceDeck),practiceDeckDifficulty:int(data.practiceDeckDifficulty)
         };
      }

      public function gwaiTier() : int
      {
         if(this._gwaiSettings == null) return -1;
         return GwaiFunLogic.tierFor(int(this._gwaiSettings.vanillaDifficulty),int(this._gwaiSettings.storyTier));
      }

      public function gwaiProfileForPlayer(side:int) : int
      {
         if(side != CardManager.PLAYER_2 || this._gwaiSettings == null
            || !this._gwaiSettings.enabled || this._gwaiMatchEnded) return -1;
         return this.gwaiTier();
      }

      public function gwaiFunBegin() : void
      {
         try
         {
            this._gwaiFun = this._gwaiSettings == null ? null : GwaiFun.create(this._gwaiSettings.fun);
            if(this._gwaiFun != null) this._gwaiFun.prepare();
         }
         catch(error:Error)
         {
            this._gwaiFun = null;
         }
      }

      public function gwaiFunFrame() : void
      {
         if(this._gwaiFun == null) return;
         try
         {
            if(this.gwaiFunInTutorial()) return;
            if(this.currentPlayer != CardManager.PLAYER_1 || !this.sawStartMessage) return;
            var human:BasePlayerController = this.playerControllers[CardManager.PLAYER_1];
            if(human == null || human.turnOver
               || human.currentRoundStatus != BasePlayerController.ROUND_PLAYER_STATUS_ACTIVE) return;
            this._gwaiFun.playerTurn(this,CardManager.getInstance(),this._gwaiDecisionId);
         }
         catch(error:Error)
         {
            this._gwaiFun = null;
         }
      }

      public function gwaiFunTurnEnded() : void
      {
         if(this._gwaiFun == null || this.currentPlayer != CardManager.PLAYER_1) return;
         try
         {
            this._gwaiFun.playerMoved(this,CardManager.getInstance());
         }
         catch(error:Error)
         {
            this._gwaiFun = null;
         }
      }

      public function gwaiFunMatchEnd(winner:int) : void
      {
         if(this._gwaiFun == null) return;
         try
         {
            this._gwaiFun.matchEnded(this,winner,this._gwaiSettings != null && Boolean(this._gwaiSettings.enabled),
               this.gwaiTier(),this.gwaiFunInTutorial());
         }
         catch(error:Error)
         {
         }
         this._gwaiFun = null;
      }

      // The tutorial match: the menu's tutorial flag, or its tutorial panel on screen.
      private function gwaiFunInTutorial() : Boolean
      {
         return (GwintGameMenu.mSingleton != null && GwintGameMenu.mSingleton.tutorialsOn)
            || (this.mcTutorials != null && this.mcTutorials.visible);
      }

      public function gwaiMeterSuffix(winChance:Number) : String
      {
         if(this._gwaiSettings == null || this._gwaiSettings.fun == null || !this._gwaiSettings.fun.meter) return "";
         try
         {
            return GwaiFunLogic.meterSuffix(winChance);
         }
         catch(error:Error)
         {
         }
         return "";
      }

      public function gwaiMatchEnded() : Boolean
      {
         return this._gwaiMatchEnded;
      }

      public function gwaiPolicyInitiative() : int
      {
         return this._gwaiInitiative < 0 ? this.currentPlayer : this._gwaiInitiative;
      }

      public function gwaiPolicyDecisionId() : int
      {
         return this._gwaiDecisionId;
      }

      public function gwaiPolicyRound() : int
      {
         return this.currentRound + 1;
      }

      public function gwaiTraceSelection() : Object
      {
         if(this._gwaiSettings == null) return null;
         var enabled:Boolean = Boolean(this._gwaiSettings.enabled);
         var tier:int = enabled ? this.gwaiTier() : 0;
         var data:Object = enabled ? GwaiNetTierConfig.dataFor(tier) : null;
         return {enabled:enabled,requested_tier:Math.max(1,int(this._gwaiSettings.vanillaDifficulty)),
            effective_tier:tier,mode:this._gwaiSettings.practice ? "practice" : "story",
            opponent:String(this._gwaiSettings.practiceDeck),
            npc_difficulty:Math.max(1,Math.min(3,int(this._gwaiSettings.practiceDeckDifficulty))),
            checkpoint_sha256:data == null ? "" : String(data.CHECKPOINT_SHA256),
            weights_sha256:data == null ? "" : String(data.EXPORT_SHA256)};
      }

      public function gwaiNotifyDecision(profile:int, fallback:Boolean, detail:String = "") : void
      {
         this.gwaiRecordDecision(fallback ? "vanilla" : (detail.indexOf("greedy-fallback") >= 0 ? "greedy" : "network"),detail);
         if(this._gwaiSettings == null || !this._gwaiSettings.signal || this.mcMessageQueue == null) return;
         if(fallback)
         {
            if(detail.indexOf("unknown card ") == 0)
               this.mcMessageQueue.PushMessage("W3 Gwent AI: " + detail + ", vanilla AI plays this turn");
            else
               this.mcMessageQueue.PushMessage("W3 Gwent AI: fallback (vanilla AI plays this turn)");
            return;
         }
         var difficulty:String = profile == 1 ? "Easy" : (profile == 2 ? "Medium" : "Hard");
         if(detail.indexOf("network ") >= 0 && this.mcMessageQueue != null)
         {
            this._gwaiSignalShown = true;
            this.mcMessageQueue.PushMessage("W3 Gwent AI: " + difficulty + " " + detail);
            return;
         }
         if(this._gwaiSignalShown) return;
         this._gwaiSignalShown = true;
         this.mcMessageQueue.PushMessage("W3 Gwent AI: " + difficulty);
      }

      public function gwaiRecordDecision(kind:String, detail:String) : void
      {
         // This bridge uses plain strings: diagnostics must not depend on JSON
         // support or on CardManager.debugVerbose being enabled in Scaleform.
         try
         {
            if(GwintGameMenu.mSingleton != null)
               GwintGameMenu.mSingleton.dispatchEvent(new red.core.events.GameEvent(red.core.events.GameEvent.CALL,
                  "OnW3GwentAIDiagnostic",[kind,detail]));
         }
         catch(diagnosticError:Error) {}
      }

      public function gwaiNotifyStockTurn() : void
      {
         if(this._gwaiSettings == null || this._gwaiSettings.enabled) return;
         var mode:String = this._gwaiSettings.practice ? "practice" : "story";
         this.gwaiRecordDecision("stock","selected for " + mode);
         try
         {
            if(this._gwaiSettings.signal && this.mcMessageQueue != null)
               this.mcMessageQueue.PushMessage("W3 Gwent AI: stock AI (selected for " + mode + ")");
         }
         catch(stockSignalError:Error) {}
      }

      public function gwaiPracticeBanner() : void
      {
         if(this._gwaiSettings == null || !this._gwaiSettings.practice || !this._gwaiSettings.signal || this.mcMessageQueue == null) return;
         try
         {
            var tier:int = this.gwaiTier();
            var policy:String = this._gwaiSettings.enabled ? "Trained " + ["Easy","Medium","Hard"][tier - 1] : "Stock game AI";
            var level:int = int(this._gwaiSettings.practiceDeckDifficulty);
            var deckLevel:String = level == 1 ? "Easy" : (level == 3 ? "Hard" : "Medium");
            var manager:CardManager = CardManager.getInstance();
            var faction:String = manager.playerDeckDefinitions[CardManager.PLAYER_2].getFactionNameString();
            this.mcMessageQueue.PushMessage("Practice: " + this._gwaiSettings.practiceOpponent + " | " + faction + " | " + policy + " | NPC deck: " + deckLevel);
         }
         catch(practiceError:Error) {}
      }

      public function gwaiCancelPending() : void
      {
         if(this.playerControllers == null) return;
         for each(var baseController:BasePlayerController in this.playerControllers)
         {
            var controller:AIPlayerController = baseController as AIPlayerController;
            if(controller != null) controller.gwaiCancel();
         }
      }
"""


def _once(
    source: str, anchor: str, replacement: str, label: str, *, before: bool = False
) -> str:
    count = source.count(anchor)
    if count != 1:
        raise ModBuildError(f"{label}: expected one exact hook anchor, found {count}")
    value = replacement + anchor if before else anchor + replacement
    return source.replace(anchor, value, 1)


def _method_body(source: str, name: str) -> str:
    compact = source.replace("\r\n", "\n")
    start = compact.find("function " + name)
    if start < 0:
        raise ModBuildError(f"source is missing method {name}")
    open_brace = compact.find("{", start)
    if open_brace < 0:
        raise ModBuildError(f"source method {name} has no body")
    depth = 1
    for index in range(open_brace + 1, len(compact)):
        if compact[index] == "{":
            depth += 1
        elif compact[index] == "}":
            depth -= 1
            if depth == 0:
                return compact[open_brace + 1 : index]
    raise ModBuildError(f"source method {name} has an unterminated body")


def verify_unknown_card_hand_back(ai: str) -> None:
    """Refuse a gwaiBegin that lets a card outside the vocabulary reach the runner or the network."""
    begin = "".join(_method_body(ai, "gwaiBegin").split())
    guard = "".join(UNKNOWN_CARD_GUARD.split())
    hand_back = 'if(unknownCards.length>0){this.gwaiReportDecision(profile,true,"unknowncardvocabulary");this.gwaiCancel();returnfalse;}'
    if guard not in begin or hand_back not in begin:
        raise ModBuildError(
            "gwaiBegin does not hand a turn with an unknown card to the vanilla AI"
        )
    first_use = min(
        begin.find(name) for name in ("GwaiCapture.legalMoves(", "newGwaiRunner(")
    )
    if not begin.find(guard) < begin.find(hand_back) < first_use:
        raise ModBuildError(
            "gwaiBegin checks for unknown cards after the runner or the legal moves"
        )


def verify_opponent_hand_length_only(capture: str) -> None:
    compact = "".join(capture.split())
    opponent_hand_reads = (
        "getCardInstanceList(CardManager.CARD_LIST_LOC_HAND,opponentID)"
    )
    assignment = re.search(
        r"varopponentHand:[^=]*=" + re.escape("manager." + opponent_hand_reads) + r";",
        compact,
    )
    if (
        compact.count(opponent_hand_reads) != 1
        or compact.count("opponentHand.length") != 1
        or assignment is None
    ):
        raise ModBuildError(
            "capture must read the opponent hand only to pass its length"
        )
    remainder = compact[: assignment.start()] + compact[assignment.end() :]
    remainder = remainder.replace("opponentHand.length", "", 1)
    if re.search(r"\bopponentHand\b", remainder):
        raise ModBuildError("capture uses opponent hand data beyond its length")


def verify_compiled_timer_bindings(
    pcode: dict[str, str], expected_calls: dict[str, int]
) -> None:
    """FFDec can compile package function syntax as an undefined root variable."""
    for name, expected in expected_calls.items():
        text = pcode.get(name, "")
        if re.search(r'\bgetlex\s+Multiname\("flash",', text):
            raise ModBuildError(
                f"{name}: timer bytecode resolves an undefined variable named flash"
            )
        calls = re.findall(
            r'\bcallproperty\s+QName\(PackageNamespace\("flash\.utils"\),"getTimer"\),\s*0\b',
            text,
        )
        if len(calls) != expected:
            raise ModBuildError(
                f"{name}: expected {expected} bound flash.utils::getTimer calls, got {len(calls)}"
            )


def verify_compiled_initialization(compiled: Path, log: list[str]) -> int:
    """Check actual script/cinit bytecode; JS replay cannot model class creation."""
    checker = ROOT / "scripts/CheckAs3Initialization.java"
    command = [
        "java",
        "-Djava.awt.headless=true",
        "-cp",
        str(build_probe.FFDEC_JAR.parent / "lib/*"),
        str(checker),
        str(compiled),
    ]
    try:
        result = subprocess.run(
            command, capture_output=True, text=True, timeout=60, check=False
        )
    except (OSError, subprocess.TimeoutExpired) as error:
        raise ModBuildError(
            f"compiled class initialization check could not run: {error}"
        ) from error
    output = result.stdout + result.stderr
    log.append("$ " + " ".join(command) + "\n" + output)
    matched = re.search(r"PASS: (\d+) helper class initializers", result.stdout)
    if result.returncode or matched is None:
        raise ModBuildError(
            "compiled class initialization check failed: " + output[-4000:]
        )
    return int(matched.group(1))


def _append_internal_classes(source: str, class_sources: dict[str, str]) -> str:
    base = (
        "GwaiCapture",
        "GwaiPolicyCore",
        "GwaiRunner",
    )
    network = (
        "GwaiNetPolicy",
        "GwaiNetWeights",
        "GwaiNetTierConfig",
        "GwaiFunConfig",
        "GwaiFunLogic",
        "GwaiFun",
    )
    data_classes = tuple(
        sorted(name for name in class_sources if name.startswith("GwaiNetWeightsData_"))
    )
    probe = ("GwaiTimingProbe",)
    trace = ("GwaiPlayTrace",) if "GwaiPlayTrace" in class_sources else ()
    plain_sources = set(class_sources) - set(trace)
    if plain_sources == set(base):
        names = base
    elif data_classes and plain_sources == set(base + network + data_classes):
        names = base + network + data_classes
    elif data_classes and plain_sources == set(base + network + data_classes + probe):
        names = base + network + data_classes + probe
    else:
        raise ModBuildError("AI helper source set is incomplete or ambiguous")
    names += trace
    for name, text in class_sources.items():
        if not text.lstrip().startswith(f"internal class {name}"):
            raise ModBuildError(f"{name}: expected package-scoped internal AS3 class")
    package_closes = [
        index for index in range(len(source)) if source.startswith("\n}", index)
    ]
    if len(package_closes) != 1 or not source.rstrip().endswith("}"):
        raise ModBuildError("AIPlayerController: package close is missing or ambiguous")
    close_index = package_closes[0]
    helpers = "\n\n".join(class_sources[name].strip() for name in names)
    return source[:close_index] + "\n\n" + helpers + source[close_index:]


def _append_internal_class(source: str, name: str, helper: str) -> str:
    if not helper.lstrip().startswith(f"internal class {name}"):
        raise ModBuildError(f"{name}: expected package-scoped internal AS3 class")
    package_closes = [
        index for index in range(len(source)) if source.startswith("\n}", index)
    ]
    if len(package_closes) != 1 or not source.rstrip().endswith("}"):
        raise ModBuildError(f"{name}: package close is missing or ambiguous")
    close_index = package_closes[0]
    return source[:close_index] + "\n\n" + helper.strip() + source[close_index:]


def tag_random_calls(source: str, class_name: str) -> tuple[str, int]:
    """Replace every original Math.random call with a stable class/ordinal tag."""
    expected = RECORDER_RANDOM_COUNTS.get(class_name)
    if expected is None:
        raise ModBuildError(f"{class_name}: no registered random-site inventory")
    count = source.count("Math.random()")
    if count != expected:
        raise ModBuildError(
            f"{class_name}: expected {expected} Math.random() call sites, found {count}; "
            "review and update the recorder inventory before building"
        )
    index = 0

    def replace(_: re.Match[str]) -> str:
        nonlocal index
        index += 1
        return f'CardManager.gwaiRandom("{class_name}#{index:02d}")'

    patched = re.sub(r"Math\.random\(\)", replace, source)
    if index != expected:
        raise ModBuildError(f"{class_name}: random-site count changed during patching")
    return patched, count


def _insert_method_start(
    source: str, method_name: str, statement: str, label: str
) -> str:
    match = re.search(
        r"function\s+" + re.escape(method_name) + r"\s*\([^)]*\)[^{]*\{", source
    )
    if match is None:
        raise ModBuildError(f"{label}: source is missing method {method_name}")
    opening = source.rfind("{", match.start(), match.end())
    return source[: opening + 1] + "\n" + statement + source[opening + 1 :]


def _insert_method_end(
    source: str, method_name: str, statement: str, label: str
) -> str:
    match = re.search(r"function\s+" + re.escape(method_name) + r"\s*\(", source)
    if match is None:
        raise ModBuildError(f"{label}: source is missing method {method_name}")
    start = match.start()
    opening = source.find("{", start)
    if opening < 0:
        raise ModBuildError(f"{label}: method {method_name} has no body")
    depth = 1
    for index in range(opening + 1, len(source)):
        if source[index] == "{":
            depth += 1
        elif source[index] == "}":
            depth -= 1
            if depth == 0:
                return source[:index] + statement + source[index:]
    raise ModBuildError(f"{label}: method {method_name} has an unterminated body")


def _replace_in_method(
    source: str, method_name: str, anchor: str, replacement: str, label: str
) -> str:
    match = re.search(r"function\s+" + re.escape(method_name) + r"\s*\(", source)
    if match is None:
        raise ModBuildError(f"{label}: source is missing method {method_name}")
    start = match.start()
    opening = source.find("{", start)
    depth = 1
    closing = -1
    for index in range(opening + 1, len(source)):
        if source[index] == "{":
            depth += 1
        elif source[index] == "}":
            depth -= 1
            if depth == 0:
                closing = index
                break
    if opening < 0 or closing < 0:
        raise ModBuildError(f"{label}: method {method_name} has an invalid body")
    body = source[opening + 1 : closing]
    if body.count(anchor) != 1:
        raise ModBuildError(
            f"{label}: expected one method-local anchor in {method_name}, found {body.count(anchor)}"
        )
    body = body.replace(anchor, replacement, 1)
    return source[: opening + 1] + body + source[closing:]


def _recorder_choice(source: str, method_name: str, expression: str, label: str) -> str:
    return _insert_method_start(
        source,
        method_name,
        f'         GwaiRecorder.choice(owningPlayer,"{method_name}",{expression});\n',
        label,
    )


def patch_recorder_sources(
    originals: dict[str, str], recorder_helper: str
) -> tuple[dict[str, str], dict[str, int]]:
    if set(originals) != set(RECORDER_CLASSES):
        raise ModBuildError("recorder export is missing or contains unexpected classes")
    patched = {name: source.replace("\r\n", "\n") for name, source in originals.items()}
    counts: dict[str, int] = {}
    for name, expected in RECORDER_RANDOM_COUNTS.items():
        patched[name], counts[name] = tag_random_calls(patched[name], name)
        if counts[name] != expected:
            raise ModBuildError(
                f"{name}: patched random-site count does not match inventory"
            )

    card_manager = patched["CardManager"]
    card_manager = build_probe.require_exactly_once(
        card_manager,
        "   import red.core.utils.Debug;",
        "\n   import red.core.events.GameEvent;",
        "CardManager imports",
    )
    card_manager = build_probe.require_exactly_once(
        card_manager,
        "      public static function getInstance() : CardManager",
        "      public static function gwaiRandom(site:String) : Number\n"
        "      {\n         return GwaiRecorder.random(site);\n      }\n\n",
        "CardManager random recorder bridge",
        before=True,
    )
    card_manager = _replace_in_method(
        card_manager,
        "drawCard",
        "            this.addCardInstanceToList(newCardInstance,CARD_LIST_LOC_HAND,playerID);",
        "            this.addCardInstanceToList(newCardInstance,CARD_LIST_LOC_HAND,playerID);\n"
        "            GwaiRecorder.draw(playerID,newCardInstance);",
        "CardManager draw recorder",
    )
    card_manager = build_probe.require_exactly_once(
        card_manager,
        "               return elligibleCardList[keepIndex];",
        '               GwaiRecorder.chance("monsters_keep_unit",playerIndex,elligibleCardList,elligibleCardList[keepIndex]);\n',
        "CardManager Monsters keep recorder",
        before=True,
    )
    card_manager = build_probe.require_exactly_once(
        card_manager,
        "            stolenCardInstance = enemyHandCardList[stolenCardIndex];",
        '\n            GwaiRecorder.chance("random_steal",stolenPlayerId,enemyHandCardList,stolenCardInstance);',
        "CardManager random steal recorder",
    )
    card_manager = build_probe.require_exactly_once(
        card_manager,
        "            ressurectInstance = elligibleCardListFromOpponentsGrave[resIndex];",
        '\n            GwaiRecorder.chance("graveyard_random_revive",playerID,elligibleCardListFromOpponentsGrave,ressurectInstance);',
        "CardManager random graveyard choice recorder",
    )
    card_manager = _replace_in_method(
        card_manager,
        "tryDrawAndPlaySpecificCard_Weather",
        "            this.addCardInstanceToList(newCardInstance,CARD_LIST_LOC_WEATHERSLOT,CardManager.PLAYER_INVALID);",
        "            this.addCardInstanceToList(newCardInstance,CARD_LIST_LOC_WEATHERSLOT,CardManager.PLAYER_INVALID);\n"
        "            GwaiRecorder.draw(playerID,newCardInstance);",
        "CardManager weather draw recorder",
    )
    card_manager = _replace_in_method(
        card_manager,
        "tryDrawSpecificCard",
        "            this.addCardInstanceToList(newCardInstance,CARD_LIST_LOC_HAND,playerID);",
        "            this.addCardInstanceToList(newCardInstance,CARD_LIST_LOC_HAND,playerID);\n"
        "            GwaiRecorder.draw(playerID,newCardInstance);",
        "CardManager specific draw recorder",
    )
    patched["CardManager"] = _append_internal_class(
        card_manager, "GwaiRecorder", recorder_helper
    )

    deck = _replace_in_method(
        patched["GwintDeck"],
        "shuffleDeck",
        "         if(this._deckRenderer)\n         {\n            this._deckRenderer.cardCount = this.cardIndicesInDeck.length;",
        "         GwaiRecorder.deckShuffled(this);\n"
        "         if(this._deckRenderer)\n         {\n            this._deckRenderer.cardCount = this.cardIndicesInDeck.length;",
        "GwintDeck shuffle recorder",
    )
    patched["GwintDeck"] = deck
    patched["GwintDeck"] = _replace_in_method(
        patched["GwintDeck"],
        "readdCard",
        "            this.cardIndicesInDeck.splice(newIndex,0,templateID);",
        "            this.cardIndicesInDeck.splice(newIndex,0,templateID);\n"
        "            GwaiRecorder.deckReadded(this,templateID);",
        "GwintDeck random reinsert recorder",
    )

    ai = build_probe.require_exactly_once(
        patched["AIPlayerController"],
        '         CardManager.log("#AI# the ai decided on the following transaction: " + _decidedCardTransaction);',
        "         GwaiRecorder.decision(this.playerID,this.attitude,this.attitudeToString(this.attitude),_decidedCardTransaction);\n",
        "AIPlayerController stock decision recorder",
        before=True,
    )
    patched["AIPlayerController"] = ai

    base = _insert_method_start(
        patched["BasePlayerController"],
        "state_begin_ApplyingCard",
        "         GwaiRecorder.action(this.playerID,this._decidedCardTransaction);\n",
        "BasePlayerController applied action recorder",
    )
    base = build_probe.require_exactly_once(
        base,
        "         if(!this.cardEffectApplying())\n         {",
        "\n            this.gameFlowControllerRef.gwaiRecorderStable(this.playerID);",
        "BasePlayerController stable-state recorder",
    )
    patched["BasePlayerController"] = base

    human = _replace_in_method(
        patched["HumanPlayerController"],
        "skipTurn",
        '         if(_stateMachine.currentState == "ChoosingCard" && this._currentZoomedHolder == null)\n         {',
        '         if(_stateMachine.currentState == "ChoosingCard" && this._currentZoomedHolder == null)\n         {\n'
        "            GwaiRecorder.pass(this.playerID);",
        "HumanPlayerController pass recorder",
    )
    patched["HumanPlayerController"] = human

    flow = build_probe.require_exactly_once(
        patched["GwintGameFlowController"],
        "         this.cardManager.spawnLeaders();",
        "\n         GwaiRecorder.begin(this.cardManager,this.playerControllers);",
        "GwintGameFlowController recorder header",
    )
    flow = _insert_method_end(
        flow,
        "state_begin_CoinToss",
        "\n         GwaiRecorder.firstPlayer(this.currentPlayer);\n      ",
        "GwintGameFlowController first player recorder",
    )
    flow = build_probe.require_exactly_once(
        flow,
        "      protected function state_update_CoinToss() : void",
        "      public function gwaiRecorderStable(seat:int) : void\n"
        "      {\n         GwaiRecorder.stable(this.cardManager,this,seat,this.currentRound);\n      }\n\n",
        "GwintGameFlowController stable-state bridge",
        before=True,
    )
    patched["GwintGameFlowController"] = flow

    flow = _insert_method_start(
        flow,
        "handleAcceptMulligan",
        '         GwaiRecorder.choice(CardManager.PLAYER_1,"mulligan",instanceId);\n',
        "GwintGameFlowController mulligan recorder",
    )
    flow = _insert_method_start(
        flow,
        "handleDeclineMulligan",
        '         GwaiRecorder.choice(CardManager.PLAYER_1,"mulligan_decline",null);\n',
        "GwintGameFlowController mulligan recorder",
    )
    flow = _insert_method_end(
        flow,
        "state_begin_ShowingFinalResult",
        '\n         GwaiRecorder.end("final");\n      ',
        "GwintGameFlowController final recorder flush",
    )
    patched["GwintGameFlowController"] = flow

    leader = patched["CardLeaderInstance"]
    for method, expression in (
        ("handleHandShowClose", "instanceId"),
        ("handleResurrectChoice", "instanceId"),
        ("handleCardDrawChoice_Weather", "templateID"),
        ("handleBinChoice", "instanceId"),
        ("handlePickChoice", "templateID"),
    ):
        leader = _recorder_choice(
            leader, method, expression, "CardLeaderInstance choice recorder"
        )
    leader = build_probe.require_exactly_once(
        leader,
        "            cardsToShow.push(cardsToChoose[randomIndex]);",
        "            cardsToShow.push(cardsToChoose[randomIndex]);\n"
        '            GwaiRecorder.chance("enemy_hand_reveal_pick",notOwningPlayer,cardsToChoose,cardsToChoose[randomIndex]);',
        "CardLeaderInstance hand reveal random recorder",
    )
    leader = build_probe.require_exactly_once(
        leader,
        "               cardManagerRef.addCardInstanceToList(cardsInGraveyard[randomIndex],CardManager.CARD_LIST_LOC_HAND,owningPlayer);",
        '               GwaiRecorder.chance("random_resurrect",targetPlayer,cardsInGraveyard,cardsInGraveyard[randomIndex]);\n'
        "               cardManagerRef.addCardInstanceToList(cardsInGraveyard[randomIndex],CardManager.CARD_LIST_LOC_HAND,owningPlayer);",
        "CardLeaderInstance random resurrection recorder",
    )
    patched["CardLeaderInstance"] = leader

    card_instance = _recorder_choice(
        patched["CardInstance"],
        "handleNurseChoice",
        "instanceId",
        "CardInstance nurse choice recorder",
    )
    nurse_anchor = '                  this.handleNurseChoice(copyList[Math.min(Math.floor(CardManager.gwaiRandom("CardInstance#10") * copyList.length),copyList.length - 1)].instanceId);'
    nurse_replacement = (
        '                  var gwaiNurseRandomIndex:int = Math.min(Math.floor(CardManager.gwaiRandom("CardInstance#10") * copyList.length),copyList.length - 1);\n'
        '                  GwaiRecorder.chance("random_nurse_resurrection",this.listsPlayer,copyList,copyList[gwaiNurseRandomIndex]);\n'
        "                  this.handleNurseChoice(copyList[gwaiNurseRandomIndex].instanceId);"
    )
    if card_instance.count(nurse_anchor) != 1:
        raise ModBuildError("CardInstance: expected one random nurse choice anchor")
    card_instance = card_instance.replace(nurse_anchor, nurse_replacement, 1)
    for site in ("CardInstance#01", "CardInstance#02"):
        expression = f'CardManager.gwaiRandom("{site}") < 0.5'
        if card_instance.count(expression) != 1:
            raise ModBuildError(f"CardInstance: expected one agile tie call at {site}")
        card_instance = card_instance.replace(
            expression,
            f'GwaiRecorder.tiePick("agile_tie_pick",this,CardManager.gwaiRandom("{site}"))',
        )
    patched["CardInstance"] = card_instance

    for name, source in patched.items():
        if name in RECORDER_RANDOM_COUNTS:
            helper_random_calls = 1 if name == "CardManager" else 0
            if source.count("Math.random()") != helper_random_calls:
                raise ModBuildError(
                    f"{name}: original random call was not routed through the recorder"
                )
            observed = source.count(f'CardManager.gwaiRandom("{name}#')
            if observed != RECORDER_RANDOM_COUNTS[name]:
                raise ModBuildError(
                    f"{name}: compiled random tag inventory is incomplete ({observed}/{RECORDER_RANDOM_COUNTS[name]})"
                )
    if (
        "gwaiBegin" in patched["AIPlayerController"]
        or "GwaiRunner" in patched["AIPlayerController"]
    ):
        raise ModBuildError(
            "recorder edition must leave the stock AI controller path untouched"
        )
    return patched, counts


def verify_recorder_compiled(
    compiled: dict[str, str], counts: dict[str, int], helper: str
) -> None:
    for name, count in counts.items():
        source = compiled.get(name, "")
        observed = source.count(f'CardManager.gwaiRandom("{name}#')
        if observed != count:
            raise ModBuildError(
                f"FFDec export has {observed} tagged {name} calls; expected {count}"
            )
    required = {
        "CardManager": (
            "gwaiRandom(site:String)",
            "GwaiRecorder.random",
            "monsters_keep_unit",
            "random_steal",
            "graveyard_random_revive",
            "GwaiRecorder.draw",
        ),
        "AIPlayerController": ("GwaiRecorder.decision",),
        "BasePlayerController": ("GwaiRecorder.action", "gwaiRecorderStable"),
        "GwintGameFlowController": (
            "GwaiRecorder.begin",
            "GwaiRecorder.firstPlayer",
            "GwaiRecorder.stable",
            "mulligan_decline",
            "GwaiRecorder.end",
        ),
        "HumanPlayerController": ("GwaiRecorder.pass",),
        "GwintDeck": ("GwaiRecorder.deckShuffled", "GwaiRecorder.deckReadded"),
        "CardLeaderInstance": ("GwaiRecorder.choice", "random_resurrect"),
        "CardInstance": (
            "GwaiRecorder.choice",
            "random_nurse_resurrection",
            "agile_tie_pick",
        ),
    }
    for name, fragments in required.items():
        source = compiled.get(name, "")
        for fragment in fragments:
            if fragment not in source:
                raise ModBuildError(
                    f"FFDec export is missing recorder hook {name}.{fragment}"
                )
    if "gwaiBegin" in compiled.get(
        "AIPlayerController", ""
    ) or "GwaiRunner" in compiled.get("AIPlayerController", ""):
        raise ModBuildError(
            "FFDec output unexpectedly contains mod AI hooks in recorder edition"
        )
    helper_markers = (
        "internal class GwaiRecorder",
        'event:"match_start"',
        'event:"random"',
        'event:"stock_decision"',
        "attitude_name:attitudeName",
        'event:"action"',
        'event:"choice"',
        'event:"chance"',
        'event:"shuffle"',
        'event:"draw"',
        'event:"state"',
        'event:"match_end"',
        "ai_hand",
        "randomCallIndex",
    )
    for marker in helper_markers:
        if marker not in helper:
            raise ModBuildError(f"GwaiRecorder helper is missing {marker}")


def patch_sources(
    ai: str,
    flow: str,
    menu: str,
    helpers: dict[str, str],
    *,
    probe: bool = False,
) -> dict[str, str]:
    ai = ai.replace("\r\n", "\n")
    # FFDec treats fully qualified package functions as dynamic property chains.
    # Helpers share this compilation unit and use this import too.
    ai = build_probe.require_exactly_once(
        ai,
        f"package {PACKAGE}\n{{",
        "\n   import flash.utils.getTimer;",
        "AIPlayerController timer import",
    )
    flow = flow.replace("\r\n", "\n")
    menu = menu.replace("\r\n", "\n")
    build_probe.require_exactly_once(ai, AI_BEGIN_ANCHOR, "", "AIPlayerController")
    build_probe.require_exactly_once(ai, AI_PUMP_ANCHOR, "", "AIPlayerController")
    build_probe.require_exactly_once(ai, AI_IDLE_ANCHOR, "", "AIPlayerController")
    native_fallback_body = _method_body(ai, "state_begin_ChoseMove")
    ai = build_probe.require_exactly_once(
        ai,
        AI_BEGIN_ANCHOR,
        "\n         if(this.gwaiBegin()) return;",
        "AIPlayerController",
    )
    ai = build_probe.require_exactly_once(
        ai,
        AI_PUMP_ANCHOR,
        "\n         if(this.gwaiPump()) return;",
        "AIPlayerController",
    )
    ai = build_probe.require_exactly_once(
        ai, AI_IDLE_ANCHOR, "\n         this.gwaiCancel();", "AIPlayerController"
    )
    ai_members = AI_MEMBERS.replace(
        "__GWAI_NATIVE_FALLBACK_BODY__", native_fallback_body
    )
    ai = _once(
        ai,
        "protected var _currentRoundCritical:Boolean = false;",
        "\n" + ai_members,
        "AIPlayerController",
    )
    ai = _append_internal_classes(ai, helpers)
    flow_methods = FLOW_METHODS
    flow = _once(
        flow, FLOW_FIELD_ANCHOR, "\n" + flow_methods, "GwintGameFlowController"
    )
    reset_injection = "\n         this.gwaiCancelPending();\n         this._gwaiFun = null;\n         this._gwaiSignalShown = false;\n         this._gwaiInitiative = -1;\n         this._gwaiDecisionId = 0;"
    if probe:
        reset_injection += "\n         GwaiTimingProbe.beginMatch();"
    flow = _once(flow, FLOW_RESET_ANCHOR, reset_injection, "GwintGameFlowController")
    flow = _replace_in_method(
        flow,
        "state_begin_reset",
        "         this._gwaiDecisionId = 0;",
        "         this._gwaiDecisionId = 0;\n         this._gwaiMatchEnded = true;",
        "GwintGameFlowController match reset",
    )
    end_injection = (
        "\n         this.gwaiCancelPending();\n         this._gwaiMatchEnded = true;"
    )
    if probe:
        end_injection += "\n         GwaiTimingProbe.finish();"
    flow = _once(flow, FLOW_END_ANCHOR, end_injection, "GwintGameFlowController")
    flow = _replace_in_method(
        flow,
        "state_update_CoinToss",
        "         if(this.currentPlayer != CardManager.PLAYER_INVALID)\n         {",
        "         if(this.currentPlayer != CardManager.PLAYER_INVALID)\n         {\n            this._gwaiMatchEnded = false;\n            if(this._gwaiInitiative < 0)\n            {\n               this._gwaiInitiative = this.currentPlayer;\n               this.gwaiPracticeBanner();\n               this.gwaiFunBegin();\n            }",
        "GwintGameFlowController policy initiative",
    )
    flow = _replace_in_method(
        flow,
        "state_update_PlayerTurn",
        "         if(currentPlayerController.turnOver)\n         {",
        "         if(currentPlayerController.turnOver)\n         {\n            this._gwaiDecisionId++;\n            this.gwaiFunTurnEnded();",
        "GwintGameFlowController policy decision id",
    )
    flow = _insert_method_start(
        flow,
        "state_update_PlayerTurn",
        "         this.gwaiFunFrame();",
        "GwintGameFlowController fun frame",
    )
    flow = _replace_in_method(
        flow,
        "state_begin_ShowingFinalResult",
        FLOW_FINAL_RESULT_ANCHOR,
        "         this.gwaiFunMatchEnd(gameWinner);\n" + FLOW_FINAL_RESULT_ANCHOR,
        "GwintGameFlowController fun match end",
    )
    menu = _once(
        menu,
        MENU_CONFIG_ANCHOR,
        '\n         dispatchEvent(new GameEvent(GameEvent.REGISTER,"gwai.mod.settings",[this.gwaiConfigureModSettings]));',
        "GwintGameMenu",
    )
    menu = _once(
        menu,
        "public var gameFlowController:GwintGameFlowController;",
        "\n" + MENU_METHOD,
        "GwintGameMenu",
    )
    return {
        "AIPlayerController": ai,
        "GwintGameFlowController": flow,
        "GwintGameMenu": menu,
    }


MENU_METHOD = """
      public function gwaiConfigureModSettings(data:Object) : void
      {
         this.gameFlowController.gwaiConfigureSettings(data);
      }
"""


def verify_sources(
    ai: str,
    flow: str,
    menu: str,
    capture: str,
    core: str,
    runner: str,
) -> None:
    checks = (
        (ai, "if(this.gwaiBegin()) return;", "AI begin hook"),
        (ai, "if(this.gwaiPump()) return;", "per-frame AI pump hook"),
        (ai, "this.gwaiCancel();", "Idle cancellation hook"),
        (
            ai,
            "GwaiCapture.capture(manager,this.gameFlowControllerRef,this.playerID)",
            "fair view capture",
        ),
        (ai, "this.gwaiNativeFallback();", "native fallback"),
        (ai, "#W3AI# decision:", "decision log"),
        (ai, "#W3AI# fallback:", "fallback log"),
        (flow, "gwaiProfileForPlayer(side:int)", "settings-derived profile"),
        (capture, 'policyMode:"network"', "network default policy mode"),
        (flow, "gwaiPolicyInitiative()", "seat-view initiative capture"),
        (flow, "gwaiPolicyDecisionId()", "seat-view decision id capture"),
        (flow, "gwaiMatchEnded()", "match-end cancellation"),
        (
            flow,
            "this._gwaiSettings.vanillaDifficulty",
            "vanilla difficulty tier mapping",
        ),
        (
            flow,
            'gwaiNotifyDecision(profile:int, fallback:Boolean, detail:String = "")',
            "visible decision signal",
        ),
        (
            flow,
            'PushMessage("W3 Gwent AI: " + difficulty + " " + detail)',
            "per-turn network timing display",
        ),
        (
            flow,
            'PushMessage("W3 Gwent AI: fallback (vanilla AI plays this turn)")',
            "visible fallback signal",
        ),
        (flow, UNKNOWN_CARD_BANNER, "visible unknown-card signal"),
        (ai, UNKNOWN_CARD_GUARD, "unknown-card hand-back to the vanilla AI"),
        (
            flow,
            'PushMessage("W3 Gwent AI: " + difficulty)',
            "visible first-decision signal",
        ),
        (flow, "this.gwaiCancelPending();", "match-end and reset cancellation"),
        (menu, '"gwai.mod.settings"', "WitcherScript settings event"),
        (
            core,
            "decide(view:Object, legalMoves:Array, difficulty:int = 2)",
            "policy interface",
        ),
        (
            core,
            "GwaiNetPolicy.decide(view.observation,legalMoves,difficulty)",
            "network policy selection hook",
        ),
        (core, "view.policyFallback = true", "greedy network-error fallback"),
        (core, "decideGreedy(view,legalMoves)", "greedy fallback policy"),
        (runner, "MOVES_PER_FRAME", "bounded per-frame policy work"),
        (runner, 'policyMode == "network"', "full network action evaluation"),
        (
            capture,
            "var opponentHandCount:int = opponentHand.length;",
            "opponent hand count",
        ),
        (capture, "observation:observation", "schema v1 observation capture"),
        (
            capture,
            "CardManager.CARD_LIST_LOC_GRAVEYARD,CardManager.PLAYER_2",
            "opponent public graveyard",
        ),
        (capture, "CardManager.CARD_LIST_LOC_HAND,playerID", "own hand only"),
    )
    for source, expected, label in checks:
        if expected not in source:
            raise ModBuildError(f"source verification is missing {label}: {expected}")
    verify_fun_sources(ai, flow)
    compact_ai = "".join(ai.split())
    for method_name, expression, label in (
        (
            "state_begin_ChoseMove",
            "if(this.gwaiBegin())return;",
            "begin hook placement",
        ),
        (
            "state_update_ChooseMove",
            "if(this.gwaiPump())return;",
            "pump hook placement",
        ),
        ("state_begin_Idle", "this.gwaiCancel();", "Idle cancellation placement"),
    ):
        if not _method_contains(compact_ai, method_name, expression):
            raise ModBuildError(f"AIPlayerController has an invalid {label}")
    verify_unknown_card_hand_back(ai)
    verify_opponent_hand_length_only(capture)
    verify_message_images(flow, "GwintGameFlowController")
    verify_message_images(ai, "AIPlayerController")
    if (
        "CARD_LIST_LOC_DECK" in capture
        or "opponentHand[" in capture
        or "cardList(opponentHand)" in capture
    ):
        raise ModBuildError(
            "capture attempts to expose opponent hand identities or deck order"
        )
    signal_body = "".join(_method_body(flow, "gwaiNotifyDecision").split())
    for expected in (
        "if(this._gwaiSignalShown)return;",
        "this._gwaiSignalShown=true;",
        "this._gwaiSettings.signal",
    ):
        if expected not in signal_body:
            raise ModBuildError(
                f"visible decision signal is missing its one-time or setting guard: {expected}"
            )
    fallback_body = _method_body(ai, "gwaiNativeFallback")
    for expected in (
        "CalculateCardPowerPotentials",
        "ChooseAttitude",
        "decideWhichCardToPlay",
        "CardEffect_Morph",
        "_currentRoundCritical",
    ):
        if expected not in fallback_body:
            raise ModBuildError(
                f"native fallback is missing vanilla decision logic: {expected}"
            )
    if "Math.random" in core or "opponentHand" in core or "deckOrder" in core:
        raise ModBuildError("policy core uses randomness or hidden information")


FUN_FLOW_HOOKS = (
    ("state_update_PlayerTurn", "this.gwaiFunFrame();", "per-frame fun hook"),
    (
        "state_update_PlayerTurn",
        "this._gwaiDecisionId++;this.gwaiFunTurnEnded();",
        "turn-end fun hook",
    ),
    (
        "state_begin_ShowingFinalResult",
        "this.gwaiFunMatchEnd(gameWinner);this.mcEndGameDialog.show(",
        "match-end fun hook",
    ),
    ("state_begin_reset", "this._gwaiFun=null;", "per-match fun reset"),
    ("state_update_CoinToss", "this.gwaiFunBegin();", "match-start fun setup"),
)
# FFDec re-exports statements in its own forms (for example ++x for x++), so the compiled export is
# checked for the calls; the patched source above is checked for their exact placement.
FUN_FLOW_COMPILED_HOOKS = (
    ("state_update_PlayerTurn", "gwaiFunFrame();", "per-frame fun hook"),
    ("state_update_PlayerTurn", "gwaiFunTurnEnded();", "turn-end fun hook"),
    ("state_begin_ShowingFinalResult", "gwaiFunMatchEnd(", "match-end fun hook"),
    ("state_begin_reset", "_gwaiFun=null;", "per-match fun reset"),
    ("state_update_CoinToss", "gwaiFunBegin();", "match-start fun setup"),
)


# Each flow entry point is one try/catch; outside it only these guards may run (they cannot throw).
FUN_ENTRY_GUARDS = {
    "gwaiFunBegin": "",
    "gwaiFunFrame": "if(this._gwaiFun==null)return;",
    "gwaiFunTurnEnded": "if(this._gwaiFun==null||this.currentPlayer!=CardManager.PLAYER_1)return;",
    "gwaiFunMatchEnd": "if(this._gwaiFun==null)return;this._gwaiFun=null;",
    "gwaiMeterSuffix": 'if(this._gwaiSettings==null||this._gwaiSettings.fun==null||!this._gwaiSettings.fun.meter)return"";return"";',
}
FUN_FLOW_METHODS = tuple(FUN_ENTRY_GUARDS) + ("gwaiFunInTutorial",)
# Calls that take a seat, and the position of the seat argument. Each must name the human seat.
FUN_SEAT_ARGUMENTS = {
    "getCardInstanceList": 1,
    "getCardLeader": 0,
    "GwaiCapture.capture": 2,
    "GwaiCapture.legalMoves": 1,
}
FUN_HUMAN_SEAT = "CardManager.PLAYER_1"
# The two places a feature may name the AI seat: the match result and a spy's target side.
FUN_AI_SEAT_USES = (
    "winner==CardManager.PLAYER_2",
    "int(move.targetPlayer)==CardManager.PLAYER_2",
)
# Sampling, the AI's decision path, its runner, and dynamic class lookup.
FUN_FORBIDDEN_NAMES = (
    "random",
    "decide",
    "evaluate",
    "GwaiRunner",
    "GwaiPolicyCore",
    "_gwaiRunner",
    "lastWinChance",
    "positionValue",
    "getDefinitionByName",
    "getQualifiedClassName",
)
# An allowlist: the only members the feature code may use on the card manager and the flow it is
# handed, and on each class it names. Any other member, or any other class, is refused.
FUN_MANAGER_MEMBERS = ("getCardInstance", "getCardInstanceList", "getCardLeader")
FUN_FLOW_ARGUMENT_MEMBERS = ("mcMessageQueue",)
FUN_CLASS_MEMBERS: dict[str, tuple[str, ...] | None] = {
    "CardManager": (
        "PLAYER_1",
        "PLAYER_2",
        "PLAYER_INVALID",
        "CARD_LIST_LOC_HAND",
        "CARD_LIST_LOC_INVALID",
    ),
    "GwaiCapture": ("capture", "legalMoves"),
    "GwaiNetPolicy": ("insight", "winChanceOf"),
    "GwaiNetWeights": ("loadStep", "isLoaded"),
    "GwaiNetTierConfig": ("dataFor",),
    "GwintGameMenu": ("mSingleton",),
    "BasePlayerController": ("ROUND_PLAYER_STATUS_ACTIVE",),
    "GwaiFun": ("create", "parseSettings"),
    "GwaiFunConfig": None,
    "GwaiFunLogic": None,
    "Math": None,
}
# The flow's feature methods: their own members, and the card manager they hand to GwaiFun.
FUN_FLOW_SELF_MEMBERS = (
    "_gwaiFun",
    "_gwaiSettings",
    "_gwaiDecisionId",
    "currentPlayer",
    "sawStartMessage",
    "playerControllers",
    "mcTutorials",
    "gwaiTier",
    "gwaiFunInTutorial",
)
FUN_FLOW_EXTRA_CLASS_MEMBERS = {"CardManager": ("getInstance",)}
# Callees that may receive the card manager or the flow itself.
FUN_HANDLE_CALLEES = ("GwaiCapture.capture", "GwaiCapture.legalMoves")
FUN_CONSTRUCTIBLE = ("GwaiFun", "Error")
# The pure logic class reaches no game object.
FUN_LOGIC_FORBIDDEN = (
    "CardManager",
    "GwintGameFlowController",
    "GwintGameMenu",
    "GwaiCapture",
    "GwaiNetPolicy",
    "GwaiFunConfig",
    "getInstance",
    "mSingleton",
    "dispatchEvent",
    "PushMessage",
)


def _outside_try(compact_body: str) -> str:
    """The method body with every try{...}catch(...){...} removed."""
    remainder = []
    index = 0
    while index < len(compact_body):
        if compact_body.startswith("try{", index) and (
            index == 0
            or not (compact_body[index - 1].isalnum() or compact_body[index - 1] == "_")
        ):
            depth = 0
            cursor = index + 3
            while True:
                char = compact_body[cursor]
                depth += char == "{"
                depth -= char == "}"
                cursor += 1
                if depth == 0:
                    break
            match = re.match(r"catch\([^)]*\)\{", compact_body[cursor:])
            if match is None:
                raise ModBuildError("a try block has no catch")
            depth = 1
            cursor += match.end()
            while depth:
                depth += compact_body[cursor] == "{"
                depth -= compact_body[cursor] == "}"
                cursor += 1
            index = cursor
            continue
        remainder.append(compact_body[index])
        index += 1
    return "".join(remainder)


def verify_fun_sources(ai: str, flow: str) -> None:
    """The opt-in features hook only where planned, and every entry point contains its errors."""
    compact_flow = "".join(flow.split())
    for method_name, expression, label in FUN_FLOW_HOOKS:
        if not _method_contains(compact_flow, method_name, expression):
            raise ModBuildError(
                f"GwintGameFlowController is missing the {label}: {expression}"
            )
    for method_name, guards in FUN_ENTRY_GUARDS.items():
        body = "".join(_method_body(flow, method_name).split())
        if "try{" not in body or _outside_try(body) != guards:
            raise ModBuildError(
                f"GwintGameFlowController.{method_name} must contain its own errors"
            )
    if "if(this._gwaiFun!=null)this._gwaiFun.prepare();" not in "".join(
        _method_body(flow, "gwaiFunBegin").split()
    ):
        raise ModBuildError(
            "GwintGameFlowController.gwaiFunBegin must start the reading network's loader"
        )
    configure = "".join(_method_body(flow, "gwaiConfigureSettings").split())
    if (
        "try{fun=GwaiFun.parseSettings(data);}catch(funError:Error){fun=null;}"
        not in configure
    ):
        raise ModBuildError(
            "GwintGameFlowController.gwaiConfigureSettings must contain the feature settings' errors"
        )
    profile = "".join(_method_body(flow, "gwaiProfileForPlayer").split())
    if (
        "returnthis.gwaiTier();" not in profile
        or "!this._gwaiSettings.enabled" not in profile
    ):
        raise ModBuildError(
            "the AI profile must keep the enabled switch and use the shared tier rule"
        )
    if "returnGwaiFunLogic.tierFor(" not in "".join(
        _method_body(flow, "gwaiTier").split()
    ):
        raise ModBuildError("the AI tier must come from the shared, tested tier rule")
    meter = "try{meter=this.gameFlowControllerRef.gwaiMeterSuffix(Number(this._gwaiRunner.winChance));}"
    if meter + 'catch(meterError:Error){meter="";}' not in "".join(ai.split()):
        raise ModBuildError(
            "AIPlayerController must build the win-chance meter suffix inside its own try"
        )


def _seat_violations(compact: str) -> list[str]:
    found = []
    for call, position in FUN_SEAT_ARGUMENTS.items():
        for match in re.finditer(r"(?<!\w)" + re.escape(call) + r"\(", compact):
            arguments = _call_arguments(compact, match.end() - 1)
            seat = arguments[position] if len(arguments) > position else ""
            if seat != FUN_HUMAN_SEAT:
                found.append(f"{call}(...{seat})")
    rest = compact.replace("this.playerControllers[CardManager.PLAYER_1]", "")
    if re.search(r"\bplayerControllers\b", rest):
        found.append(
            "playerControllers other than this.playerControllers[CardManager.PLAYER_1]"
        )
    return found


def _without_comments(source: str) -> str:
    """AS3 source with // and /* */ comments removed; string literals are kept as they are."""
    result = []
    index = 0
    quote = ""
    while index < len(source):
        char = source[index]
        if quote:
            result.append(char)
            if char == "\\" and index + 1 < len(source):
                result.append(source[index + 1])
                index += 2
                continue
            if char == quote:
                quote = ""
            index += 1
        elif char in "\"'":
            quote = char
            result.append(char)
            index += 1
        elif source.startswith("//", index):
            end = source.find("\n", index)
            index = len(source) if end < 0 else end
        elif source.startswith("/*", index):
            end = source.find("*/", index + 2)
            index = len(source) if end < 0 else end + 2
        else:
            result.append(char)
            index += 1
    return "".join(result)


def _enclosing_callee(source: str, position: int) -> str:
    """The callee of the innermost call whose argument list holds `position`, or ""."""
    depth = 0
    for index in range(position - 1, -1, -1):
        char = source[index]
        if char in ")]}":
            depth += 1
        elif char in "([{":
            if depth == 0:
                if char != "(":
                    return ""
                callee = re.search(r"([\w$.]+)\s*$", source[:index])
                return callee.group(1) if callee else ""
            depth -= 1
    return ""


def _member_violations(name: str, spaced: str) -> list[str]:
    """Members outside the allowlist, and the game handles passed anywhere but to an allowed callee."""
    flow_method = name.startswith("GwintGameFlowController.")
    found = []
    for member in re.findall(r"\bmanager\s*\.\s*(\w+)", spaced):
        if member not in FUN_MANAGER_MEMBERS:
            found.append(f"manager.{member}")
    for member in re.findall(r"\bflow\s*\.\s*(\w+)", spaced):
        if member not in FUN_FLOW_ARGUMENT_MEMBERS:
            found.append(f"flow.{member}")
    for match in re.finditer(r"(?<![\w$.])([A-Z][\w$]*)\s*\.\s*(\w+)", spaced):
        owner, member = match.groups()
        if re.fullmatch(r"[A-Z][A-Z0-9_]*", owner):
            continue  # a class's own constant, such as STAT_PREFIXES.length
        if owner not in FUN_CLASS_MEMBERS:
            found.append(f"{owner}.{member}")
            continue
        allowed = FUN_CLASS_MEMBERS[owner]
        if flow_method:
            allowed = (
                None
                if allowed is None
                else allowed + FUN_FLOW_EXTRA_CLASS_MEMBERS.get(owner, ())
            )
        if allowed is not None and member not in allowed:
            found.append(f"{owner}.{member}")
    for owner in re.findall(r"\bnew\s+([A-Z][\w$]*)", spaced):
        if owner not in FUN_CONSTRUCTIBLE:
            found.append(f"new {owner}")
    for match in re.finditer(
        r"\b(manager|flow)\b(?!\s*\.)(?!\s*:\s*(?:CardManager|GwintGameFlowController)\b)",
        spaced,
    ):
        callee = _enclosing_callee(spaced, match.start())
        if callee not in FUN_HANDLE_CALLEES and not callee.startswith("this."):
            found.append(f"{match.group(1)} handed to {callee or 'a variable'}")
    if flow_method:
        for member in re.findall(r"\bthis\s*\.\s*(\w+)", spaced):
            if member not in FUN_FLOW_SELF_MEMBERS:
                found.append(f"this.{member}")
        for match in re.finditer(r"\bthis\b(?!\s*\.)", spaced):
            if not _enclosing_callee(spaced, match.start()).startswith(
                "this._gwaiFun."
            ):
                found.append("the flow handed to a variable")
    elif re.search(r"\bthis\b(?!\s*\.)", spaced):
        found.append("this handed to a variable")
    return found


_ARRAY_LITERAL_WORDS = frozenset({"return", "in", "case", "typeof", "delete", "throw"})


def _hidden_lookup(source: str) -> str:
    """The first bracket lookup the lint cannot read: a string key, or one on a class or game object."""
    for match in re.finditer(r"\[", source):
        before = source[: match.start()].rstrip()
        word = re.search(r"([A-Za-z_$][\w$]*)$", before)
        if before.endswith((")", "]")):
            target = before[-1]
        elif word is not None and word.group(1) not in _ARRAY_LITERAL_WORDS:
            target = word.group(1)
        else:
            continue
        key = source[match.end() :].lstrip()[:1]
        if (
            key in "\"'"
            or re.fullmatch(r"[A-Z][a-z]\w*", target)
            or target in ("flow", "manager", "this")
        ):
            return (
                target
                + "["
                + source[match.end() : match.end() + 20].split("]")[0]
                + "]"
            )
    return ""


def verify_fun_helpers(helpers: dict[str, str], flow: str) -> None:
    """A lint, not a proof: the feature code reads the human seat, never samples, and stays off
    the AI's decision path. It scans GwaiFun, GwaiFunLogic and the flow's feature methods for the
    forms below; a card looked up by instance id and values passed through locals are review's."""
    fun = helpers.get("GwaiFun", "")
    logic = helpers.get("GwaiFunLogic", "")
    for expected in (
        "function parseSettings(",
        "function create(",
        "function playerTurn(",
        "function playerMoved(",
        "function matchEnded(",
        "function prepare(",
        "GwaiNetPolicy.insight(",
        "GwaiNetWeights.loadStep(",
        "GwaiNetWeights.isLoaded(",
        "OnW3GwentFunStat",
        "OnW3GwentFunCard",
        "OnW3GwentFunNote",
        "OnW3GwentFunSave",
    ):
        if expected not in fun:
            raise ModBuildError(f"GwaiFun is missing {expected}")
    for expected in (
        "function featureFlags(",
        "function tierFor(",
        "function postGameLines(",
    ):
        if expected not in logic:
            raise ModBuildError(f"GwaiFunLogic is missing {expected}")
    scanned = {"GwaiFun": fun, "GwaiFunLogic": logic}
    for method_name in FUN_FLOW_METHODS:
        scanned[f"GwintGameFlowController.{method_name}"] = _method_body(
            flow, method_name
        )
    for name, source in scanned.items():
        source = _without_comments(source)
        compact = "".join(source.split())
        violations = _seat_violations(compact)
        if violations:
            raise ModBuildError(
                f"{name} reads a seat other than the human's: {violations[0]}"
            )
        rest = compact
        for allowed in FUN_AI_SEAT_USES:
            rest = rest.replace(allowed, "")
        if re.search(r"\bPLAYER_2\b", rest):
            raise ModBuildError(
                f"{name} refers to the AI seat beyond the result and a spy's target side"
            )
        spaced = " ".join(source.split())
        for forbidden in FUN_FORBIDDEN_NAMES:
            if re.search(
                r"\b" + re.escape(forbidden) + r"\b",
                spaced,
                re.IGNORECASE if forbidden == "random" else 0,
            ):
                raise ModBuildError(
                    f"{name} must not sample or reach the AI's decision path: {forbidden}"
                )
        members = _member_violations(name, spaced)
        if members:
            raise ModBuildError(
                f"{name} uses a member outside the feature allowlist: {members[0]}"
            )
        lookup = _hidden_lookup(source)
        if lookup:
            raise ModBuildError(
                f"{name} uses a member lookup the check cannot read: {lookup}"
            )
    pure = _without_comments(logic)
    for forbidden in FUN_LOGIC_FORBIDDEN:
        if re.search(r"\b" + re.escape(forbidden) + r"\b", pure):
            raise ModBuildError(
                f"GwaiFunLogic must stay pure: it refers to {forbidden}"
            )
    for name in ("GwaiFun", "GwaiFunLogic"):
        verify_message_images(helpers.get(name, ""), name)


def verify_timing_probe_sources(
    patched: dict[str, str], helpers: dict[str, str]
) -> None:
    probe = helpers.get("GwaiTimingProbe", "")
    network = helpers.get("GwaiNetPolicy", "")
    required_probe = (
        "function beginMatch(",
        "function measure(",
        "function finish(",
        "function modelFor(",
        "function warmupObservation(",
        "function emitBegin(",
        "function emitFinish(",
        "STUDENT_WIDTHS",
        "MAX_DECISION_RECORDS",
        'event:"decision"',
        'event:"summary"',
        "student_model_build_ms:",
        "student_model_warm_ms:",
        "discarded_width_samples:",
        "mean:sum / sorted.length",
        "legal_action_count:legalActions.length",
        "GwaiNetPolicy.encodeObservation",
        "GwaiNetPolicy.evaluate",
        "getTimer()",
        "new Vector.<Number>",
    )
    if not probe or any(marker not in probe for marker in required_probe):
        raise ModBuildError("timing probe helper is incomplete")
    if (
        "GwaiTimingProbe.measure(observation,legalActions,data,weights,evaluationElapsed)"
        not in network
    ):
        raise ModBuildError(
            "network timing hook is missing or changed its selected-action path"
        )
    if "GwaiTimingProbe.beginMatch();" not in patched["GwintGameFlowController"]:
        raise ModBuildError("timing probe does not reset at match start")
    if "GwaiTimingProbe.finish();" not in patched["GwintGameFlowController"]:
        raise ModBuildError("timing probe summary is not emitted at match end")
    if "return result.chosen_action;" not in network:
        raise ModBuildError("timing probe changed the network-selected action")


def verify_compiled_timing_probe(
    patched: dict[str, str], helpers: dict[str, str]
) -> None:
    network = helpers.get("GwaiNetPolicy", "")
    probe = helpers.get("GwaiTimingProbe", "")
    flow = patched.get("GwintGameFlowController", "")
    if "GwaiTimingProbe.measure" not in network:
        raise ModBuildError("compiled network is missing the timing call")
    if "GwaiTimingProbe.beginMatch" not in flow or "GwaiTimingProbe.finish" not in flow:
        raise ModBuildError(
            "compiled game flow is missing timing reset or summary hooks"
        )
    if "measure" not in probe or "finish" not in probe or "modelFor" not in probe:
        raise ModBuildError(
            "compiled timing probe is missing its measurement implementation"
        )


def verify_compiled_data_classes(
    helpers: dict[str, str], expected_checkpoints: dict[str, str]
) -> None:
    data_names = {name for name in helpers if name.startswith("GwaiNetWeightsData_")}
    expected_names = set(expected_checkpoints)
    if data_names != expected_names:
        extra = sorted(data_names - expected_names)
        missing = sorted(expected_names - data_names)
        raise ModBuildError(
            f"FFDec checkpoint data classes differ from requested checkpoints: extra={extra}, missing={missing}"
        )
    for name, expected_hash in expected_checkpoints.items():
        source = helpers[name]
        match = re.search(
            r"\bCHECKPOINT_SHA256\s*:\s*String\s*=\s*['\"]([0-9a-fA-F]{64})['\"]",
            source,
        )
        if match is None or match.group(1).lower() != expected_hash.lower():
            actual = match.group(1) if match is not None else "missing"
            raise ModBuildError(
                f"FFDec checkpoint data class {name} has checkpoint {actual}; expected {expected_hash}"
            )


def verify_compiled_sources(
    ai: str,
    flow: str,
    menu: str,
    helpers: dict[str, str],
    expected_checkpoints: dict[str, str],
) -> None:
    combined = "\n".join((ai, flow, menu))
    compact = "".join(combined.split())
    checks = (
        (compact, "gwaiBegin()", "compiled begin hook"),
        (compact, "gwaiPump()", "compiled per-frame pump hook"),
        (compact, "gwaiCancel()", "compiled Idle cancellation"),
        (compact, "gwaiProfileForPlayer", "compiled settings profile"),
        (compact, "gwai.mod.settings", "compiled WitcherScript settings event"),
    )
    for source, expected, label in checks:
        if expected not in source:
            raise ModBuildError(f"FFDec export is missing {label}: {expected}")
    ai_compact = "".join(ai.split())
    for method_name, expression, label in (
        (
            "state_begin_ChoseMove",
            "if(this.gwaiBegin())return;",
            "begin hook placement",
        ),
        (
            "state_update_ChooseMove",
            "if(this.gwaiPump())return;",
            "pump hook placement",
        ),
        ("state_begin_Idle", "this.gwaiCancel();", "Idle cancellation placement"),
    ):
        if not _method_contains(ai_compact, method_name, expression):
            index = ai_compact.find(method_name)
            excerpt = (
                ai_compact[max(0, index) : max(0, index) + 180]
                if index >= 0
                else "method not found"
            )
            function_index = ai_compact.find("function" + method_name)
            function_excerpt = (
                ai_compact[max(0, function_index) : max(0, function_index) + 600]
                if function_index >= 0
                else "function not exported"
            )
            hook_index = ai_compact.find(expression)
            hook_excerpt = (
                ai_compact[max(0, hook_index - 100) : hook_index + 120]
                if hook_index >= 0
                else "hook not found"
            )
            raise ModBuildError(
                f"FFDec export has an invalid {label}: method={excerpt}; function={function_excerpt}; hook={hook_excerpt}"
            )
    fallback_body = _method_body(ai, "gwaiNativeFallback")
    for expected in (
        "CalculateCardPowerPotentials",
        "ChooseAttitude",
        "decideWhichCardToPlay",
        "CardEffect_Morph",
        "_currentRoundCritical",
    ):
        if expected not in fallback_body:
            raise ModBuildError(
                f"FFDec export has incomplete native fallback logic: {expected}"
            )
    if "GwaiNetPolicy.unknownCards(" not in "".join(
        _method_body(ai, "gwaiBegin").split()
    ):
        raise ModBuildError(
            "FFDec export is missing the unknown-card hand-back in gwaiBegin"
        )
    for method_name, expression, label in (
        ("state_begin_reset", "gwaiCancelPending();", "reset cancellation"),
        ("state_begin_reset", "_gwaiSignalShown=false;", "per-match signal reset"),
        ("EndGame", "gwaiCancelPending();", "match-end cancellation"),
        ("EndGame", "_gwaiMatchEnded=true;", "match-end cancellation"),
        ("state_update_CoinToss", "_gwaiMatchEnded=false;", "match-start state reset"),
    ):
        if not _method_contains("".join(flow.split()), method_name, expression):
            raise ModBuildError(f"FFDec export is missing {label}")
    signal_body = _method_body(flow, "gwaiNotifyDecision")
    for expected, label in (
        (
            'PushMessage("W3 Gwent AI: fallback (vanilla AI plays this turn)")',
            "visible fallback signal",
        ),
        (UNKNOWN_CARD_BANNER, "visible unknown-card signal"),
        ('PushMessage("W3 Gwent AI: " + difficulty)', "visible first-decision signal"),
        (
            'PushMessage("W3 Gwent AI: " + difficulty + " " + detail)',
            "visible per-turn network timing",
        ),
    ):
        if expected not in signal_body:
            raise ModBuildError(f"FFDec export is missing {label}")
    signal_compact = "".join(signal_body.split())
    for expected in (
        "if(this._gwaiSignalShown)",
        "this._gwaiSignalShown=true;",
        "this._gwaiSettings.signal",
    ):
        if expected not in signal_compact:
            raise ModBuildError(f"FFDec export is missing the signal guard: {expected}")
    if "#W3AI#decision:" not in compact or "#W3AI#fallback:" not in compact:
        raise ModBuildError("FFDec export is missing a W3 AI decision log")
    for name, required in {
        "GwaiCapture": ("function capture(", "function legalMoves(", "pending_choice"),
        "GwaiPolicyCore": ("function scoreMove(", "function decide("),
        "GwaiRunner": ("function advance(", "function cancel(", "MAX_EVALUATIONS"),
    }.items():
        source = helpers.get(name, "")
        if not source or f"class {name}" not in source:
            raise ModBuildError(f"FFDec export is missing compiled helper class {name}")
        for expected in required:
            if expected not in source:
                raise ModBuildError(f"FFDec export is missing {name} member {expected}")
    verify_opponent_hand_length_only(helpers["GwaiCapture"])
    for method_name, expression, label in FUN_FLOW_COMPILED_HOOKS:
        if not _method_contains("".join(flow.split()), method_name, expression):
            raise ModBuildError(f"FFDec export is missing the {label}")
    for name, required in {
        "GwaiFun": (
            "function playerTurn(",
            "function playerMoved(",
            "function matchEnded(",
            "function prepare(",
            "function loaderTick(",
            "OnW3GwentFunNote",
        ),
        "GwaiFunLogic": (
            "function postGameLines(",
            "function costliest(",
            "function meterSuffix(",
            "function tierFor(",
            "function featureFlags(",
        ),
        "GwaiFunConfig": (
            "function storyTierFor(",
            "STORY_TIERS",
            "MAX_RECORDS",
            "READING_TIER",
        ),
    }.items():
        source = helpers.get(name, "")
        if not source or f"class {name}" not in source:
            raise ModBuildError(f"FFDec export is missing compiled helper class {name}")
        for expected in required:
            if expected not in source:
                raise ModBuildError(f"FFDec export is missing {name} member {expected}")
    for name, required in {
        "GwaiNetPolicy": (
            "function evaluate(",
            "function encodeObservation(",
            "function decide(",
            "function float32(",
            "function valueHead(",
            "function insight(",
            "function unknownCards(",
        ),
        "GwaiNetWeights": (
            "function load(",
            "decodeBase64",
            "function loadStep(",
            "function isLoaded(",
        ),
        "GwaiNetTierConfig": (
            "function dataFor(",
            "function temperatureFor(",
            "TEMPERATURES",
        ),
    }.items():
        source = helpers.get(name, "")
        if not source or f"class {name}" not in source:
            raise ModBuildError(f"FFDec export is missing compiled helper class {name}")
        for expected in required:
            if expected not in source:
                raise ModBuildError(f"FFDec export is missing {name} member {expected}")
    data_names = [name for name in helpers if name.startswith("GwaiNetWeightsData_")]
    if not data_names:
        raise ModBuildError("FFDec export is missing embedded checkpoint weight data")
    for name in data_names:
        source = helpers[name]
        if f"class {name}" not in source:
            raise ModBuildError(f"FFDec export is missing checkpoint data class {name}")
        for expected in (
            "EMBEDDED",
            "BASE64",
            "CARD_EFFECTS",
            "CHECKPOINT_SHA256",
            "UPDATE_COUNT",
            "EXPORT_SHA256",
        ):
            if expected not in source:
                raise ModBuildError(f"FFDec export is missing {name} member {expected}")
    verify_compiled_data_classes(helpers, expected_checkpoints)
    if any(
        value in helpers["GwaiCapture"]
        for value in ("CARD_LIST_LOC_DECK", "opponentHand[", "cardList(opponentHand)")
    ):
        raise ModBuildError(
            "compiled capture exposes opponent hand identities or deck order"
        )
    if "GwaiTimingProbe" in helpers:
        probe = helpers["GwaiTimingProbe"]
        compact_probe = "".join(probe.split())
        for marker in (
            "measure(",
            "finish(",
            "modelFor(",
            '"event":"decision"',
            '"event":"summary"',
        ):
            if marker not in compact_probe:
                raise ModBuildError(f"compiled timing probe helper is missing {marker}")
        if "GwaiTimingProbe.measure" not in helpers["GwaiNetPolicy"]:
            raise ModBuildError(
                "compiled network is missing the timing instrumentation hook"
            )


def _class_file(directory: Path, name: str) -> Path:
    path = directory / "scripts" / PACKAGE_PATH / f"{name}.as"
    if not path.is_file():
        raise ModBuildError(f"FFDec did not export expected class {PACKAGE}.{name}")
    return path


def _hash(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _tier_config_source(
    tier_classes: dict[str, str], temperatures: dict[str, float]
) -> str:
    temperature_values = [repr(float(temperatures[tier])) for tier in TIER_NAMES]
    return "\n".join(
        [
            "internal class GwaiNetTierConfig",
            "{",
            "   private static const TEMPERATURES:Array = ["
            + ",".join(temperature_values)
            + "];",
            "   public static function dataFor(difficulty:int) : Object",
            "   {",
            # FFDec creates same-script classes in declaration order. Resolve sibling
            # classes at call time, after every script class slot has been installed.
            *[
                f"      if(difficulty == {index}) return {tier_classes[tier]};"
                for index, tier in enumerate(TIER_NAMES, 1)
            ],
            '      throw new Error("difficulty outside 1..3");',
            "   }",
            "   public static function temperatureFor(difficulty:int) : Number",
            "   {",
            '      if(difficulty < 1 || difficulty > 3) throw new Error("difficulty outside 1..3");',
            "      return Number(TEMPERATURES[difficulty - 1]);",
            "   }",
            "}",
            "",
        ]
    )


def _difficulty_ladder(tier_policies: dict[str, dict[str, Any]]) -> dict[str, Any]:
    labels = {"easy": "Easy", "normal": "Medium", "hard": "Hard"}
    tiers = {
        tier: {
            "game_difficulty": {"label": labels[tier], "fact_value": fact_value},
            "selected_by": "the game's Gwent difficulty setting or explicit practice tier",
            "checkpoint_sha256": tier_policies[tier]["checkpoint_sha256"],
            "temperature": tier_policies[tier]["temperature"],
        }
        for fact_value, tier in enumerate(TIER_NAMES, start=1)
    }
    return {
        "selection": "Easy, Medium and Hard follow the game's Gwent difficulty; practice can select the policy separately.",
        "vanilla_difficulty_fact": "gwent_difficulty",
        "vanilla_fact_values": {"easy": 1, "normal": 2, "hard": 3},
        "unset_fact_value": 0,
        "unset_fact_policy_tier": "easy",
        "preserves": [
            "NPC deck selection",
            "cards",
            "rules",
            "rewards",
            "collection progression",
        ],
        "tiers": tiers,
    }


def value_head_layout(metadata: dict[str, Any]) -> dict[str, dict[str, Any]]:
    """Return the exported value-head tensors, refusing an export without the full head."""
    width = int(metadata["width"])
    expected = {
        "value_head.0.weight": [128, width],
        "value_head.0.bias": [128],
        "value_head.2.weight": [3, 128],
        "value_head.2.bias": [3],
    }
    offsets = metadata.get("offsets", {})
    shapes = metadata.get("shapes", {})
    layout: dict[str, dict[str, Any]] = {}
    for name, shape in expected.items():
        if name not in offsets or shapes.get(name) != shape:
            raise ModBuildError(
                f"policy export lacks value-head tensor {name} with shape {shape}"
            )
        layout[name] = {"offset": int(offsets[name]), "shape": shape}
    return layout


def node_gate_failures(summary: dict[str, Any]) -> list[str]:
    """Name every Node replay check that did not pass, including the value-head gate."""
    expected_weight_loads = (
        summary.get("decisionTotal", 0)
        + summary.get("easyDecisionTotal", 0)
        + summary.get("insightTotal", 0)
    )
    checks = {
        "at least 200 positions": summary.get("total", 0) >= 200,
        "evaluate agreement": summary.get("agreement") == summary.get("total"),
        "decide agreement": summary.get("decisionAgreement")
        == summary.get("decisionTotal"),
        "no decide fallbacks": summary.get("decisionFallbacks") == 0,
        "weight loads": summary.get("weightLoadCalls") == expected_weight_loads,
        "no Easy fallbacks": summary.get("easyDecisionFallbacks") == 0,
        "no invalid Easy actions": summary.get("easyDecisionInvalids") == 0,
        "logits": summary.get("logitsPass") is True,
        "encoder": summary.get("encoderPass") is True,
        "value head and insight": (
            summary.get("valuePass") is True
            and summary.get("valueTotal") == summary.get("total")
            and summary.get("insightTotal") == summary.get("total")
        ),
    }
    return [name for name, passed in checks.items() if not passed]


def _prepare_policy_exports(
    tier_checkpoints: dict[str, Path],
    temperatures: dict[str, float],
    *,
    action_sequence_hash: bool = False,
    policy_bundle_path: Path | None = None,
) -> tuple[dict[str, str], dict[str, Any], dict[str, dict[str, Any]]]:
    if policy_bundle_path is None:
        try:
            from scripts import create_policy_fixtures, export_policy_as3
        except ModuleNotFoundError:
            import create_policy_fixtures
            import export_policy_as3
    else:
        try:
            from scripts import policy_bundle, policy_probes
        except ModuleNotFoundError:
            import policy_bundle
            import policy_probes

    catalog_path = ROOT / ".runtime/catalog.json"
    prepared = None
    probes_path = None
    if policy_bundle_path is not None:
        bundle = policy_bundle.load_bundle(policy_bundle_path)
        if bundle.probes_path is None:
            raise ModBuildError(
                "policy bundle is missing its reference inference checks"
            )
        probes_path = bundle.probes_path
        policy_probes.validate_bundle_probes(probes_path, bundle.metadata)
        POLICY_EXPORT_DIR.mkdir(parents=True, exist_ok=True)
        prepared_root = Path(
            tempfile.mkdtemp(prefix="prepared-", dir=POLICY_EXPORT_DIR)
        )
        prepared = policy_bundle.materialize(
            policy_bundle_path, catalog_path, prepared_root / "models"
        )
    else:
        for tier, checkpoint in tier_checkpoints.items():
            if not checkpoint.expanduser().is_file():
                raise ModBuildError(f"{tier} checkpoint does not exist: {checkpoint}")
    tier_hashes = {
        tier: prepared[tier].metadata["checkpoint_sha256"]
        if prepared is not None
        else _hash(tier_checkpoints[tier].expanduser().resolve())
        for tier in TIER_NAMES
    }
    node = shutil.which("node")
    if node is None:
        raise ModBuildError("Node.js is required to gate the exported policy")
    replay = ROOT / "mod/ai/node/replay.js"
    network_source = AS3_DIR / "GwaiNetPolicy.as"
    network_digest = _hash(network_source)
    metadata_by_hash: dict[str, dict[str, Any]] = {}
    export_dir_by_hash: dict[str, Path] = {}
    class_by_hash: dict[str, str] = {}
    fixture_by_hash: dict[str, dict[str, Any]] = {}
    value_layout_by_hash: dict[str, dict[str, dict[str, Any]]] = {}

    for tier in TIER_NAMES:
        checkpoint_hash = tier_hashes[tier]
        if checkpoint_hash in metadata_by_hash:
            continue
        class_name = f"GwaiNetWeightsData_{checkpoint_hash[:16]}"
        if prepared is not None:
            model = prepared[tier]
            output_dir = model.export_dir
            metadata = model.metadata
            fixture_summary = policy_probes.prepare_checks(
                probes_path, metadata, output_dir
            )
            fixture_path = Path(fixture_summary["path"])
            replay_metadata_path = Path(fixture_summary["metadata_path"])
        else:
            checkpoint = tier_checkpoints[tier].expanduser().resolve()
            output_dir = POLICY_EXPORT_DIR / checkpoint_hash
            metadata = export_policy_as3.export(
                checkpoint, catalog_path, output_dir, class_name=class_name
            )
            fixture_path = POLICY_FIXTURE_DIR / f"{checkpoint_hash}.jsonl"
            fixture_summary = create_policy_fixtures.create_fixtures(
                checkpoint_path=checkpoint,
                catalog_path=catalog_path,
                output_path=fixture_path,
                count=200,
                seed=20261026,
                env_count=10,
            )
            replay_metadata_path = output_dir / "metadata.json"
        command = [
            node,
            str(replay),
            "--network",
            str(network_source),
            "--core",
            str(AS3_DIR / "GwaiPolicyCore.as"),
            "--easy-temperature",
            str(temperatures[tier]),
            "--record",
            str(fixture_path),
            "--metadata",
            str(replay_metadata_path),
            "--weights",
            str(output_dir / "policy.f32.bin"),
            "--expected-network-sha256",
            network_digest,
        ]
        if action_sequence_hash:
            command.append("--action-sequence-hash")
        replay_result = subprocess.run(
            command,
            capture_output=True,
            text=True,
            check=False,
        )
        if replay_result.returncode != 0:
            raise ModBuildError(
                f"Node policy gate failed for {tier} checkpoint:\n{replay_result.stdout}\n{replay_result.stderr}"
            )
        summary = json.loads(replay_result.stdout)
        value_layout = value_head_layout(metadata)
        if (
            node_gate_failures(summary)
            or summary.get("networkSourceSha256") != network_digest
            or summary.get("fixtureHeader", {}).get("checkpoint_sha256")
            != checkpoint_hash
            or fixture_summary.get("decision_source")
            != (
                policy_probes.SOURCE
                if prepared is not None
                else "synthetic schema coverage (not gameplay or rules parity)"
            )
            or fixture_summary.get("dev_game_count") != 0
            or not 1 <= fixture_summary.get("synthetic_batch_size", 0) <= 10
            or fixture_summary.get("count") != 200
        ):
            raise ModBuildError(
                f"Node policy gate did not pass for {tier}: failed={node_gate_failures(summary)} {summary}"
            )
        metadata_by_hash[checkpoint_hash] = metadata
        export_dir_by_hash[checkpoint_hash] = output_dir
        class_by_hash[checkpoint_hash] = class_name
        value_layout_by_hash[checkpoint_hash] = value_layout
        fixture_by_hash[checkpoint_hash] = {
            "path": str(fixture_path),
            "metadata_path": str(replay_metadata_path),
            "decision_source": fixture_summary["decision_source"],
            "count": fixture_summary["count"],
            "dev_game_count": 0,
            "synthetic_batch_size": fixture_summary["synthetic_batch_size"],
            "node": summary,
        }

    tier_class_names = {tier: class_by_hash[tier_hashes[tier]] for tier in TIER_NAMES}
    sources = {
        "GwaiCapture": (AS3_DIR / "GwaiCapture.as").read_text(encoding="utf-8"),
        "GwaiPolicyCore": (AS3_DIR / "GwaiPolicyCore.as").read_text(encoding="utf-8"),
        "GwaiRunner": (AS3_DIR / "GwaiRunner.as").read_text(encoding="utf-8"),
        "GwaiNetPolicy": network_source.read_text(encoding="utf-8"),
        "GwaiNetWeights": (AS3_DIR / "GwaiNetWeights.as").read_text(encoding="utf-8"),
        "GwaiNetTierConfig": _tier_config_source(tier_class_names, temperatures),
        "GwaiFunConfig": mod_fun.fun_config_source(),
        "GwaiFunLogic": (AS3_DIR / "GwaiFunLogic.as").read_text(encoding="utf-8"),
        "GwaiFun": (AS3_DIR / "GwaiFun.as").read_text(encoding="utf-8"),
    }
    for checkpoint_hash, class_name in class_by_hash.items():
        sources[class_name] = (
            export_dir_by_hash[checkpoint_hash] / f"{class_name}.as"
        ).read_text(encoding="utf-8")

    policy_exports = [
        {
            "checkpoint_sha256": checkpoint_hash,
            "export_directory": str(export_dir_by_hash[checkpoint_hash]),
            "update_count": metadata["update_count"],
            "export_sha256": metadata["export_sha256"],
            "catalog_sha256": metadata["catalog_sha256"],
            "width": metadata["width"],
            "blocks": metadata["blocks"],
            "parameter_count": metadata["parameter_count"],
            "weight_bytes": metadata["weight_bytes"],
            "weights_class_name": metadata["weights_class_name"],
            "weights_blob_shared_by": [
                tier for tier in TIER_NAMES if tier_hashes[tier] == checkpoint_hash
            ],
            "export_gate": fixture_by_hash[checkpoint_hash],
            "value_head": {
                "exported": True,
                "tensors": value_layout_by_hash[checkpoint_hash],
                "outputs": "softmax over loss, draw, win for the evaluating seat; value = P(draw) / 2 + P(win)",
                "node_gate": {
                    key: fixture_by_hash[checkpoint_hash]["node"].get(key)
                    for key in (
                        "valueTotal",
                        "valueMaxAbsError",
                        "insightTotal",
                        "insightAgreement",
                        "valuePass",
                    )
                },
            },
        }
        for checkpoint_hash, metadata in metadata_by_hash.items()
    ]
    tier_policies = {
        tier: {
            "form": "checkpoint",
            "checkpoint_sha256": metadata_by_hash[tier_hashes[tier]][
                "checkpoint_sha256"
            ],
            "update_count": metadata_by_hash[tier_hashes[tier]]["update_count"],
            "export_sha256": metadata_by_hash[tier_hashes[tier]]["export_sha256"],
            "weights_blob_sha256": metadata_by_hash[tier_hashes[tier]]["export_sha256"],
            "temperature": temperatures[tier],
            "fallback": "greedy",
        }
        for tier in TIER_NAMES
    }
    primary_hash = tier_hashes["normal"]
    return (
        sources,
        {
            "checkpoint_sha256": primary_hash,
            "update_count": metadata_by_hash[primary_hash]["update_count"],
            "export_sha256": metadata_by_hash[primary_hash]["export_sha256"],
            "default_policy": "network",
            "vanilla_switch": "GwaiEnabled=false",
            "tier_policies": tier_policies,
            "difficulty_ladder": _difficulty_ladder(tier_policies),
            "policy_exports": policy_exports,
            "fun_features": _fun_features_manifest(tier_policies),
        },
        tier_policies,
    )


def _fun_features_manifest(tier_policies: dict[str, Any]) -> dict[str, Any]:
    """The feature set, with the exact network the human seat's readings use."""
    section = mod_fun.manifest_section()
    tier = mod_fun.TIER_NAMES[mod_fun.READING_TIER]
    section["reading_network"] = {
        "tier": tier,
        "checkpoint_sha256": tier_policies[tier]["checkpoint_sha256"],
        "export_sha256": tier_policies[tier]["export_sha256"],
    }
    return section


def _add_timing_probe_sources(sources: dict[str, str]) -> dict[str, str]:
    result = dict(sources)
    network = result["GwaiNetPolicy"]
    network = build_probe.require_exactly_once(
        network,
        "      var result:Object = evaluate(\n",
        "      var weights:Vector.<Number> = GwaiNetWeights.load(data);\n"
        "      var evaluationStarted:Number = Number(getTimer());\n",
        "GwaiNetPolicy timing start",
        before=True,
    )
    load_expression = "         GwaiNetWeights.load(data),\n"
    if network.count(load_expression) != 1:
        raise ModBuildError("GwaiNetPolicy: expected one real-weight load in decide")
    network = network.replace(load_expression, "         weights,\n", 1)
    network = build_probe.require_exactly_once(
        network,
        "      return result.chosen_action;\n",
        "      var evaluationElapsed:Number = Number(getTimer()) - evaluationStarted;\n"
        "      GwaiTimingProbe.measure(observation,legalActions,data,weights,evaluationElapsed);\n",
        "GwaiNetPolicy timing report",
        before=True,
    )
    result["GwaiNetPolicy"] = network
    result["GwaiTimingProbe"] = (
        ROOT / "mod/probe-timing/as3/GwaiTimingProbe.as"
    ).read_text(encoding="utf-8")
    return result


def _write_json(path: Path, value: dict[str, Any]) -> None:
    path.write_text(
        json.dumps(value, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )


def _method_contains(compact_source: str, name: str, expression: str) -> bool:
    compact_source = compact_source.replace("this.", "")
    expression = expression.replace("this.", "")
    start = compact_source.find("function" + name)
    if start < 0:
        return False
    open_brace = compact_source.find("{", start)
    if open_brace < 0:
        return False
    depth = 1
    body_end = -1
    for index in range(open_brace + 1, len(compact_source)):
        if compact_source[index] == "{":
            depth += 1
        elif compact_source[index] == "}":
            depth -= 1
            if depth == 0:
                body_end = index
                break
    if body_end < 0:
        return False
    body = compact_source[open_brace + 1 : body_end]
    variants = (expression, expression.replace(")return;", "){return;}"))
    return any(candidate in body for candidate in variants)


def build(
    *,
    recorder: bool = False,
    probe: bool = False,
    checkpoint_path: Path = DEFAULT_CHECKPOINT,
    tier_checkpoint_overrides: dict[str, Path] | None = None,
    easy_temperature: float = 0.0,
    policy_bundle_path: Path | None = None,
    output_dir: Path | None = None,
) -> dict[str, Any]:
    if recorder and probe:
        raise ModBuildError("recorder and timing-probe editions are mutually exclusive")
    if policy_bundle_path is not None and (recorder or tier_checkpoint_overrides):
        raise ModBuildError(
            "a policy bundle cannot be combined with recorder or checkpoint overrides"
        )
    if output_dir is None:
        output_dir = (
            RECORDER_OUTPUT_DIR
            if recorder
            else PROBE_OUTPUT_DIR
            if probe
            else OUTPUT_DIR
        )
    output_dir = output_dir.expanduser().resolve()
    output_dir.mkdir(parents=True, exist_ok=True)
    variant = "recorder" if recorder else "probe" if probe else "ai"
    report: dict[str, Any] = {
        "format": "witcher3-gwent-mod-build-v4",
        "variant": variant,
        "status": "building",
        "installed": False,
        "runtime_verified": False,
        "witcher_script_engine_compiled": False,
        "node_policy_replay_verified": False,
        "information_contract": (
            "full local diagnostic trace; stock AI; own AI hand; all random call sites; "
            "deck, choice and state events"
            if recorder
            else "own hand; both boards and graveyards; scores, lives, results; opponent hand count and pass status; leader states; deck factions"
        ),
    }
    if probe:
        report["timing_probe"] = {
            "schema": "w3-gwent-timing-probe-v1",
            "student_widths": [192, 128, 96, 64],
            "student_architecture": "policy/value architecture with the checkpoint block count",
            "student_weights": "deterministic random initialization; never used to select actions",
            "timer": "flash.utils.getTimer; millisecond resolution",
        }
    log: list[str] = []
    try:
        source_redswf, source_swf, ffdec_bytes, _, vanilla_manifest_hash = (
            build_probe.verify_source_pins()
        )
        if recorder:
            helper_path = ROOT / "mod/recorder/as3/GwaiRecorder.as"
            helper = helper_path.read_text(encoding="utf-8")
            script_path = ROOT / "mod/recorder/settings/scripts/local/gwai_recorder.ws"
            settings = script_path.read_text(encoding="utf-8")
            extra_sources = {"GwaiRecorder.as": helper, "gwai_recorder.ws": settings}
            report["inputs_sha256"] = {
                "gwint_game.redswf": hashlib.sha256(source_redswf).hexdigest(),
                "gwint_game.swf": hashlib.sha256(source_swf).hexdigest(),
                "ffdec-cli.jar": hashlib.sha256(ffdec_bytes).hexdigest(),
                "vanilla-manifest.json": vanilla_manifest_hash,
                **{
                    name: hashlib.sha256(text.encode("utf-8")).hexdigest()
                    for name, text in extra_sources.items()
                },
            }
            report["random_call_sites"] = dict(RECORDER_RANDOM_COUNTS)
            report["random_call_site_total"] = sum(RECORDER_RANDOM_COUNTS.values())
        else:
            mod_practice.verify()
            report["practice"] = {
                "opponents": [
                    {
                        "selection_index": i,
                        "deck": deck,
                        "label": mod_practice.label(deck),
                    }
                    for i, deck in enumerate(mod_practice.DECKS)
                ],
                "policies": ["stock", "trained"],
                "trained_tier_default": "hard",
                "npc_difficulty": "the game's unchanged Gwent difficulty fact",
                "deals": "new random deals; not a matched-deal comparison",
            }
            tier_checkpoints = {tier: checkpoint_path for tier in TIER_NAMES}
            for tier, path in (tier_checkpoint_overrides or {}).items():
                if tier not in TIER_NAMES:
                    raise ModBuildError(f"unknown difficulty tier: {tier}")
                tier_checkpoints[tier] = path
            temperatures = {tier: 0.0 for tier in TIER_NAMES}
            temperatures["easy"] = easy_temperature
            if not math.isfinite(easy_temperature) or easy_temperature < 0:
                raise ModBuildError("Easy temperature must be finite and non-negative")
            sources, policy_manifest, _ = _prepare_policy_exports(
                tier_checkpoints,
                temperatures,
                action_sequence_hash=probe,
                policy_bundle_path=policy_bundle_path,
            )
            if probe:
                sources = _add_timing_probe_sources(sources)
            sources["GwaiPlayTrace"] = mod_trace.helper_source(BUILD_ID)
            report["play_trace"] = {
                "schema": mod_trace.SCHEMA,
                "build": BUILD_ID,
                "toggle": "W3GwentAI.GwaiTrace",
                "default_enabled": False,
                "information": "public actions and board state; hand/deck counts only",
            }
            report.update(policy_manifest)
            report["policy_export"] = {
                "observation_schema_id": "witcher3-gwent-seat-view-v1",
                "action_schema_id": "witcher3-gwent-factorized-action-v1",
                "embedded": True,
                "default_enabled": True,
            }
            report["inputs_sha256"] = {
                "gwint_game.redswf": hashlib.sha256(source_redswf).hexdigest(),
                "gwint_game.swf": hashlib.sha256(source_swf).hexdigest(),
                "ffdec-cli.jar": hashlib.sha256(ffdec_bytes).hexdigest(),
                "vanilla-manifest.json": vanilla_manifest_hash,
                **{
                    name + ".as": hashlib.sha256(text.encode("utf-8")).hexdigest()
                    for name, text in sources.items()
                },
                "gwai_settings.ws": _hash(
                    ROOT / "mod/ai/settings/scripts/local/gwai_settings.ws"
                ),
                "modW3GwentAI.xml": _hash(
                    ROOT / "mod/ai/settings/menu/modW3GwentAI.xml"
                ),
                "gwai_playtrace.ws": _hash(mod_trace.SINK_PATH),
            }
            if probe:
                report["inputs_sha256"].update(
                    {
                        "gwai_timing_probe.ws": _hash(
                            ROOT
                            / "mod/probe-timing/settings/scripts/local/gwai_timing_probe.ws"
                        ),
                        "modW3GwentAIProbe.xml": _hash(
                            ROOT
                            / "mod/probe-timing/settings/menu/modW3GwentAIProbe.xml"
                        ),
                    }
                )
            for policy_export in report["policy_exports"]:
                checkpoint_hash = policy_export["checkpoint_sha256"]
                export_dir = Path(policy_export["export_directory"])
                report["inputs_sha256"][f"policy-{checkpoint_hash}.f32.bin"] = _hash(
                    export_dir / "policy.f32.bin"
                )
                report["inputs_sha256"][f"policy-{checkpoint_hash}.metadata.json"] = (
                    _hash(export_dir / "metadata.json")
                )

        class_names = (
            RECORDER_CLASSES
            if recorder
            else (
                "AIPlayerController",
                "GwintGameFlowController",
                "GwintGameMenu",
                *mod_trace.EXTRA_CLASSES,
            )
        )
        class_list = ",".join(f"{PACKAGE}.{name}" for name in class_names)
        with tempfile.TemporaryDirectory(
            prefix=f"witcher3-gwint-{variant}-"
        ) as temp_value:
            temp = Path(temp_value)
            input_swf = temp / "gwint_game.swf"
            input_swf.write_bytes(source_swf)
            export = temp / "export"
            build_probe._run_ffdec(
                build_probe.FFDEC_JAR,
                log,
                "-selectclass",
                class_list,
                "-export",
                "script",
                export,
                input_swf,
                cwd=temp,
            )
            originals = {
                name: _class_file(export, name).read_text(encoding="utf-8")
                for name in class_names
            }
            if recorder:
                patched, counts = patch_recorder_sources(originals, helper)
            else:
                patched = patch_sources(
                    originals["AIPlayerController"],
                    originals["GwintGameFlowController"],
                    originals["GwintGameMenu"],
                    sources,
                    probe=probe,
                )
                verify_sources(
                    patched["AIPlayerController"],
                    patched["GwintGameFlowController"],
                    patched["GwintGameMenu"],
                    sources["GwaiCapture"],
                    sources["GwaiPolicyCore"],
                    sources["GwaiRunner"],
                )
                verify_fun_helpers(sources, patched["GwintGameFlowController"])
                if probe:
                    verify_timing_probe_sources(patched, sources)
                patched.update(
                    {name: originals[name] for name in mod_trace.EXTRA_CLASSES}
                )
                patched = mod_trace.patch_sources(patched)
            import_root = temp / "import"
            import_package = import_root / PACKAGE_PATH
            import_package.mkdir(parents=True)
            for name, source in patched.items():
                (import_package / f"{name}.as").write_text(source, encoding="utf-8")
            compiled_path = temp / "compiled.swf"
            build_probe._run_ffdec(
                build_probe.FFDEC_JAR,
                log,
                "-importScript",
                input_swf,
                compiled_path,
                import_root,
                cwd=temp,
            )
            compiled_swf = build_probe.normalize_swf(
                compiled_path.read_bytes(), "FFDec output"
            )
            verify = temp / "verify"
            build_probe._run_ffdec(
                build_probe.FFDEC_JAR,
                log,
                "-export",
                "script",
                verify,
                compiled_path,
                cwd=temp,
            )
            compiled = {
                name: _class_file(verify, name).read_text(encoding="utf-8")
                for name in patched
            }
            if recorder:
                verify_recorder_compiled(compiled, counts, helper)
                report["random_call_sites"] = counts
                report["random_call_site_total"] = sum(counts.values())
                report["stock_ai_path"] = (
                    "unmodified vanilla controller with trace hook only"
                )
                report["recorder_script_exported"] = (
                    "GwaiRecorder embedded in CardManager"
                )
            else:
                helper_exports = {
                    name: _class_file(verify, name).read_text(encoding="utf-8")
                    for name in sources
                }
                timer_calls = {"AIPlayerController": AI_MEMBERS.count("getTimer()")}
                timer_calls.update(
                    {
                        name: text.count("getTimer()")
                        for name, text in sources.items()
                        if "getTimer()" in text
                    }
                )
                pcode_dir = temp / "timer-bytecode"
                build_probe._run_ffdec(
                    build_probe.FFDEC_JAR,
                    log,
                    "-selectclass",
                    ",".join(f"{PACKAGE}.{name}" for name in timer_calls),
                    "-format",
                    "script:pcode",
                    "-export",
                    "script",
                    pcode_dir,
                    compiled_path,
                    cwd=temp,
                )
                timer_pcode = {
                    name: (
                        pcode_dir / "scripts" / PACKAGE_PATH / f"{name}.pcode"
                    ).read_text(encoding="utf-8")
                    for name in timer_calls
                }
                verify_compiled_timer_bindings(timer_pcode, timer_calls)
                report["timer_bytecode_bindings"] = timer_calls
                report["class_initialization"] = {
                    "direct_eager_reads_verified": verify_compiled_initialization(
                        compiled_path, log
                    ),
                    "scope": "direct same-script class reads in Gwai static initializers; not VM execution",
                }
                report["inputs_sha256"]["CheckAs3Initialization.java"] = _hash(
                    ROOT / "scripts/CheckAs3Initialization.java"
                )
                mod_trace.verify_compiled(compiled, helper_exports["GwaiPlayTrace"])
                verify_compiled_sources(
                    compiled["AIPlayerController"],
                    compiled["GwintGameFlowController"],
                    compiled["GwintGameMenu"],
                    helper_exports,
                    {
                        item["weights_class_name"]: item["checkpoint_sha256"]
                        for item in report["policy_exports"]
                    },
                )
                report["compiled_policy_data"] = compiled_policy.verify(
                    helper_exports,
                    {
                        item["weights_class_name"]: json.loads(
                            (
                                Path(item["export_directory"]) / "metadata.json"
                            ).read_text(encoding="utf-8")
                        )
                        for item in report["policy_exports"]
                    },
                )
                if probe:
                    verify_compiled_timing_probe(compiled, helper_exports)
                node = shutil.which("node")
                if node is None:
                    raise ModBuildError(
                        "Node.js is required to verify the exported policy core"
                    )
                core_path = _class_file(verify, "GwaiPolicyCore")
                core_digest = _hash(core_path)
                network_path = _class_file(verify, "GwaiNetPolicy")
                node_network_bytes = network_path.read_bytes()
                network_digest = hashlib.sha256(node_network_bytes).hexdigest()
                replay = ROOT / "mod/ai/node/replay.js"
                replay_summaries = {}
                probe_action_gates = {}
                for policy_export in report["policy_exports"]:
                    checkpoint_hash = policy_export["checkpoint_sha256"]
                    export_dir = Path(policy_export["export_directory"])
                    fixture_path = policy_export["export_gate"]["path"]
                    easy_temperature = (
                        temperatures["easy"]
                        if "easy" in policy_export["weights_blob_shared_by"]
                        else 0.0
                    )
                    replay_command = [
                        node,
                        str(replay),
                        "--network",
                        "-",
                        "--record",
                        fixture_path,
                        "--core",
                        str(core_path),
                        "--easy-temperature",
                        str(easy_temperature),
                        "--metadata",
                        policy_export["export_gate"]["metadata_path"],
                        "--weights",
                        str(export_dir / "policy.f32.bin"),
                        "--expected-network-sha256",
                        network_digest,
                    ]
                    if probe:
                        replay_command.extend(
                            ["--probe-timing", "--action-sequence-hash"]
                        )
                    replay_result = subprocess.run(
                        replay_command,
                        input=node_network_bytes,
                        capture_output=True,
                        check=False,
                    )
                    if replay_result.returncode != 0:
                        raise ModBuildError(
                            f"Node replay of checkpoint {checkpoint_hash} failed: "
                            f"stderr={replay_result.stderr.decode(errors='replace')} "
                            f"stdout={replay_result.stdout.decode(errors='replace')}"
                        )
                    replay_summary = json.loads(replay_result.stdout.decode("utf-8"))
                    if probe:
                        baseline_sequence = policy_export["export_gate"]["node"].get(
                            "actionSequenceSha256"
                        )
                        probe_sequence = replay_summary.get("actionSequenceSha256")
                        timing = replay_summary.get("probeTiming")
                        if (
                            not baseline_sequence
                            or probe_sequence != baseline_sequence
                            or timing is None
                            or timing.get("total") != 200
                            or set(timing.get("widths", {}))
                            != {"full", "192", "128", "96", "64"}
                        ):
                            raise ModBuildError(
                                "probe Node timing/action gate failed: "
                                f"normal={baseline_sequence}, probe={probe_sequence}, timing={timing}"
                            )
                        probe_action_gates[checkpoint_hash] = {
                            "positions": replay_summary["total"],
                            "normal_action_sequence_sha256": baseline_sequence,
                            "probe_action_sequence_sha256": probe_sequence,
                            "matches": True,
                        }
                    if (
                        node_gate_failures(replay_summary)
                        or replay_summary.get("networkSourceSha256") != network_digest
                    ):
                        raise ModBuildError(
                            f"compiled network disagrees with checkpoint {checkpoint_hash}: "
                            f"failed={node_gate_failures(replay_summary)} {replay_summary}"
                        )
                    replay_summaries[checkpoint_hash] = replay_summary
                report["node_policy_replay_verified"] = True
                report["policy_core_export_sha256"] = core_digest
                report["policy_network_export_sha256"] = network_digest
                report["node_policy_replay"] = replay_summaries
                if probe:
                    report["node_probe_action_gate"] = probe_action_gates
                    report["node_timing_probe"] = {
                        checkpoint_hash: {
                            "total": summary["total"],
                            "action_sequence_sha256": summary["actionSequenceSha256"],
                            "encoder_ms": summary["probeTiming"]["encoder"],
                            "widths": summary["probeTiming"]["widths"],
                        }
                        for checkpoint_hash, summary in replay_summaries.items()
                    }

        compiled_redswf = build_probe.patch_redswf(
            source_redswf,
            compiled_swf,
            build_probe.unpack_redswf(source_redswf)[1],
        )
        output_swf = output_dir / "gwint_game.swf"
        output_redswf = output_dir / "gwint_game.redswf"
        output_swf.write_bytes(compiled_swf)
        output_redswf.write_bytes(compiled_redswf)
        report.update(
            status="offline_compiled_and_export_verified",
            outputs_sha256={
                output_swf.name: _hash(output_swf),
                output_redswf.name: _hash(output_redswf),
            },
            output_directory=str(output_dir),
        )
    except Exception as exc:
        report.update(status="build_failed", error=f"{type(exc).__name__}: {exc}")
        log.append(f"BUILD FAILED: {type(exc).__name__}: {exc}")
    if not recorder and report.get("status") == "offline_compiled_and_export_verified":
        node = shutil.which("node")
        if node is None:
            report.update(
                status="build_failed", error="Node.js is required for mod runtime tests"
            )
            log.append("BUILD FAILED: Node.js is required for mod runtime tests")
        else:
            manifest_path = output_dir / "manifest.json"
            _write_json(manifest_path, report)
            env = dict(os.environ)
            env["W3_MOD_MANIFEST"] = str(manifest_path)
            test_result = subprocess.run(
                [node, str(ROOT / "mod/ai/node/mod.test.js")],
                capture_output=True,
                text=True,
                check=False,
                env=env,
            )
            log.append(test_result.stdout.rstrip())
            if test_result.returncode != 0:
                report.update(
                    status="build_failed",
                    error=f"Node mod tests failed:\n{test_result.stderr or test_result.stdout}",
                )
                log.append(f"BUILD FAILED: {report['error']}")
            else:
                tests_match = [
                    re.search(rf"(?:#|ℹ)\s*{label}\s+(\d+)", test_result.stdout)
                    for label in ("tests", "pass", "fail")
                ]
                if any(match is None for match in tests_match):
                    report.update(
                        status="build_failed",
                        error="Node mod tests did not report a TAP summary",
                    )
                    log.append(f"BUILD FAILED: {report['error']}")
                else:
                    report["node_mod_tests"] = {
                        "total": int(tests_match[0].group(1)),
                        "passed": int(tests_match[1].group(1)),
                        "failed": int(tests_match[2].group(1)),
                    }
    _write_json(output_dir / "manifest.json", report)
    manifest_hash = _hash(output_dir / "manifest.json")
    (output_dir / "manifest.sha256").write_text(
        f"{manifest_hash}  manifest.json\n", encoding="ascii"
    )
    (output_dir / "build.log").write_text("\n\n".join(log) + "\n", encoding="utf-8")
    return report


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--recorder",
        action="store_true",
        help="build the diagnostic recorder edition with vanilla stock AI",
    )
    parser.add_argument(
        "--probe",
        action="store_true",
        help="build the opt-in AI edition with in-game network timing capture",
    )
    parser.add_argument("--checkpoint", type=Path, default=DEFAULT_CHECKPOINT)
    parser.add_argument(
        "--policy-bundle", type=Path, help="use a verified inference-only model bundle"
    )
    parser.add_argument(
        "--tier-checkpoint",
        action="append",
        default=[],
        metavar="TIER=PATH",
        help="override a tier checkpoint (easy, normal, hard); repeatable",
    )
    parser.add_argument("--easy-temperature", type=float, default=0.0)
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=None,
        help="write the build here instead of the edition's .runtime/mod-build* directory",
    )
    args = parser.parse_args()
    if args.recorder and args.probe:
        parser.error("--recorder and --probe select separate editions")
    overrides: dict[str, Path] = {}
    for assignment in args.tier_checkpoint:
        tier, separator, value = assignment.partition("=")
        tier = tier.lower()
        if not separator or tier not in TIER_NAMES or not value:
            parser.error(
                "--tier-checkpoint must be TIER=PATH for an Easy, Medium, or Hard tier"
            )
        overrides[tier] = Path(value)
    report = build(
        recorder=args.recorder,
        probe=args.probe,
        checkpoint_path=args.checkpoint,
        policy_bundle_path=args.policy_bundle,
        tier_checkpoint_overrides=overrides,
        easy_temperature=args.easy_temperature,
        output_dir=args.output_dir,
    )
    print(json.dumps(report, indent=2, sort_keys=True))
    return 0 if report.get("status") == "offline_compiled_and_export_verified" else 1


if __name__ == "__main__":
    raise SystemExit(main())
