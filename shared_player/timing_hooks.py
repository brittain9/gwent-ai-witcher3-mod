"""Private timing hooks on actual native boundaries; no change to release play."""

from __future__ import annotations

import re

from .host import HostIntegrationError, _body


def patch_timing_runtime(patched):
    """Return patched source plus EXACT injected snippets for compiled QName proof."""
    sources = dict(patched)
    authored = {}

    def insert(name, method, addition, *, end=False, anchor=None, before=False):
        source = sources[name]
        start, stop = _body(source, method)
        body = source[start:stop]
        if anchor is None:
            body = body + "\n" + addition if end else "\n" + addition + body
        else:
            if body.count(anchor) != 1:
                raise HostIntegrationError(
                    "private timing native seam differs: " + name + "." + method
                )
            body = body.replace(
                anchor,
                addition + "\n" + anchor if before else anchor + "\n" + addition,
                1,
            )
        sources[name] = source[:start] + body + source[stop:]
        authored[name] = authored.get(name, "") + "\n" + addition

    def call(stage, *, seat="this.playerID"):
        return (
            "         try { GwaiSharedDiagnostic.timingMark("
            + seat
            + ',"'
            + stage
            + '",getTimer()); } catch(gwaiTimingError:Error) {}'
        )

    ai = "AIPlayerController"
    base = "BasePlayerController"
    flow = "GwintGameFlowController"
    insert(
        ai, "state_begin_ChoseMove", "         var gwaiTimingOnset:Number = getTimer();"
    )
    insert(
        ai,
        "state_begin_ChoseMove",
        "         try { GwaiSharedDiagnostic.timingBegin(this.playerID,this.gameFlowControllerRef.gwaiPolicyDecisionId(),this.gameFlowControllerRef.gwaiPrimaryTicket(this.playerID),gwaiTimingOnset); } catch(gwaiTimingError:Error) {}",
        anchor="         if(this.gwaiBegin()) return;",
        before=True,
    )
    insert(
        ai,
        "gwaiReportDecision",
        "         if(!fallback) {\n" + call("transaction_ready") + "\n         }",
    )
    insert(
        ai,
        "state_begin_Idle",
        call("pass_committed"),
        anchor="         if(this.attitude == TACTIC_PASS)\n         {\n            currentRoundStatus = BasePlayerController.ROUND_PLAYER_STATUS_DONE;",
    )
    insert(
        ai,
        "state_begin_Idle",
        '         try { GwaiSharedDiagnostic.timingClose("native-idle-without-commit",getTimer()); } catch(gwaiTimingError:Error) {}',
        end=True,
    )
    insert(base, "state_begin_ApplyingCard", call("action_started"))
    for method, anchor in (
        (
            "state_begin_ApplyingCard",
            "            leaderCard.ApplyLeaderAbility(this.isAI);",
        ),
        (
            "transferTransactionCardToDestination",
            "            CardManager.getInstance().addCardInstanceIDToList(this._transactionCard.instanceId,slotID,playerID);",
        ),
        (
            "applyTransactionCardToCardInstance",
            "         CardManager.getInstance().replaceCardInstanceIDs(this._transactionCard.instanceId,cardInstance.instanceId);",
        ),
        (
            "applyGlobalEffectTransactionCard",
            "            CardManager.getInstance().sendToGraveyardID(this._transactionCard.instanceId);",
        ),
    ):
        insert(base, method, call("primary_committed"), anchor=anchor)
    stable_anchor = "try { this.gameFlowControllerRef.gwaiTraceStable(this.playerID); }"
    for name in (base, "HumanPlayerController"):
        insert(
            name,
            "state_update_ApplyingCard",
            call("public_stable"),
            anchor=stable_anchor,
            before=True,
        )
    insert(
        flow, "gwaiTraceStable", call("trace_bridge_returned", seat="seat"), end=True
    )
    insert(
        flow,
        "gwaiRecordDecision",
        "         try { GwaiSharedDiagnostic.timingSource(kind); } catch(gwaiTimingError:Error) {}",
    )
    for method in ("EndGame", "state_begin_reset"):
        insert(
            flow,
            method,
            '         try { GwaiSharedDiagnostic.timingClose("'
            + method
            + '",getTimer()); } catch(gwaiTimingError:Error) {}',
        )
    # gwaiCancel is also a mechanical reset inside gwaiBegin: only actual stale
    # and settings cancellations close evidence. A policy failure keeps onset
    # while the original native fallback runs.
    for anchor, reason in (
        (
            '               this.gwaiReportDecision(profile,true,"stale primary context cancelled");',
            "stale-primary-context",
        ),
        (
            '               this.gwaiReportDecision(profile,true,"settings changed while thinking");',
            "settings-changed",
        ),
    ):
        insert(
            ai,
            "gwaiPump",
            '               try { GwaiSharedDiagnostic.timingClose("'
            + reason
            + '",getTimer()); } catch(gwaiTimingError:Error) {}',
            anchor=anchor,
            before=True,
        )
    for name in authored:
        source = sources[name]
        if "import flash.utils.getTimer;" not in source:
            package = re.search(r"\bpackage\s+[\w.]+\s*\{", source)
            if package is None:
                raise HostIntegrationError(
                    "private timing host package declaration missing"
                )
            sources[name] = (
                source[: package.end()]
                + "\n   import flash.utils.getTimer;"
                + source[package.end() :]
            )
    return sources, authored


def verify_compiled_timing(native_sources, authored):
    """Require all injected timing calls in the actual re-exported classes."""
    expected = {}
    for name, snippet in authored.items():
        methods = set(re.findall(r"GwaiSharedDiagnostic\.(timing\w+)\(", snippet))
        expected[name] = {}
        for method in methods:
            count = len(re.findall(r"\." + method + r"\s*\(", snippet))
            actual = len(re.findall(r"\." + method + r"\s*\(", native_sources[name]))
            if actual != count:
                raise HostIntegrationError(
                    "compiled private timing hook differs: " + name + "." + method
                )
            expected[name][method] = count
    return {
        "clock": "flash-getTimer-ms-v1",
        "compiled_hook_sites": expected,
        "native_timing_verified": False,
    }
