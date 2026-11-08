"""Inject independent action uniforms at the admitted native primary boundary."""

import re

from .host import PACKAGE, HostIntegrationError, _body, _once

FLOW = r"""      private var _gwaiOwnedActionRng:Object = null;
      private var _gwaiOwnedActionTicket:Number = -1;
      private var _gwaiOwnedActionDraws:Array = null;

      public function gwaiOwnedActionReset(seed:Number) : void
      {
         if(!isFinite(seed) || seed != Math.floor(seed)) throw new Error("finite integer action seed required");
         this._gwaiOwnedActionRng = {state:uint(uint(seed) ^ 2738958700),cursor:0};
         this._gwaiOwnedActionTicket = -1;
         this._gwaiOwnedActionDraws = null;
      }

      public function gwaiOwnedActionUniforms(profile:int, seat:int, ticket:Number) : Array
      {
         if(seat != 1 || !this.gwaiPrimaryCounterValid() || !this.gwaiPrimaryObservationReady(seat)
            || ticket < 1 || ticket != this.gwaiPrimaryTicket(seat))
            throw new Error("owned action uniforms require current uncommitted primary authority");
         var settings:Object = GwaiSharedConfig.settingsFor(profile);
         var temperature:Number = Number(settings.temperature);
         if(!isFinite(temperature) || temperature < 0) throw new Error("invalid action temperature");
         if(temperature == 0) return null;
         if(this._gwaiOwnedActionRng == null) throw new Error("owned action stream did not start with the match");
         if(this._gwaiOwnedActionTicket != ticket)
         {
            this._gwaiOwnedActionDraws = GwaiNetPolicy.ownedActionUniforms(Number(this._gwaiOwnedActionRng.state));
            // Each uniform is an exact uint32 divided by 2^32. The second
            // draw therefore recovers the exact next stream state, without
            // another random call or a duplicate xorshift implementation.
            this._gwaiOwnedActionRng.state = uint(Number(this._gwaiOwnedActionDraws[1]) * 4294967296);
            this._gwaiOwnedActionRng.cursor += 2;
            this._gwaiOwnedActionTicket = ticket;
         }
         return this._gwaiOwnedActionDraws.concat();
      }

      public function gwaiOwnedActionState() : Object
      {
         if(this._gwaiOwnedActionRng == null) return null;
         return {contract:"owned-action-xorshift32-v1",state:this._gwaiOwnedActionRng.state,
            cursor:this._gwaiOwnedActionRng.cursor,ticket:this._gwaiOwnedActionTicket};
      }
"""


class OwnedActionError(ValueError):
    """The native host cannot prove its current primary or source boundary."""


def patch_owned_actions(sources):
    """Copy/persist match-local RNG; never touch live game chance or model data."""
    if not isinstance(sources, dict) or not {
        "AIPlayerController",
        "GwintGameFlowController",
    }.issubset(sources):
        raise OwnedActionError("explicit AI and flow native source required")
    patched = dict(sources)
    try:
        flow = patched["GwintGameFlowController"]
        if "_gwaiOwnedActionRng" in flow:
            raise OwnedActionError("native owned-action stream is already patched")
        flow = _once(
            flow,
            "public var gameStarted:Boolean = false;",
            "\n" + FLOW,
            "owned action flow state",
        )
        if "import flash.utils.getTimer;" not in flow:
            flow = _once(
                flow,
                f"package {PACKAGE}\n{{",
                "\n   import flash.utils.getTimer;",
                "owned action clock import",
            )
        start, _ = _body(flow, "state_begin_SpawnLeaders")
        flow = (
            flow[:start]
            + "\n         this.gwaiOwnedActionReset(getTimer());"
            + flow[start:]
        )
        ai = patched["AIPlayerController"]
        anchor = "new GwaiSharedController(profile)"
        if ai.count(anchor) != 1:
            raise OwnedActionError("one native shared-controller construction required")
        ai = ai.replace(
            anchor,
            "new GwaiSharedController(profile,this.gameFlowControllerRef.gwaiOwnedActionUniforms(profile,this.playerID,this.gameFlowControllerRef.gwaiPrimaryTicket(this.playerID)))",
            1,
        )
        patched["AIPlayerController"] = ai
        patched["GwintGameFlowController"] = flow
    except HostIntegrationError as error:
        raise OwnedActionError(str(error)) from error
    return patched


def verify_owned_actions(sources):
    """Require the real compiled host wiring; this is not an execution proof."""
    try:
        ai = sources["AIPlayerController"]
        flow = sources["GwintGameFlowController"]
        for text, method in (
            (ai, "gwaiOwnedActionUniforms"),
            (flow, "gwaiOwnedActionReset"),
            (flow, "ownedActionUniforms"),
        ):
            if len(re.findall(r"\." + method + r"\s*\(", text)) != 1:
                raise OwnedActionError(
                    "compiled owned action callback differs: " + method
                )
        for method in (
            "gwaiOwnedActionReset",
            "gwaiOwnedActionUniforms",
            "gwaiOwnedActionState",
        ):
            _body(flow, method)
        start, end = _body(flow, "gwaiOwnedActionUniforms")
        body = flow[start:end]
        if (
            not re.search(r"if\s*\([^()]*==\s*0\s*\)\s*(?:\{\s*)?return\s+null", body)
            or "gwaiPrimaryObservationReady" not in body
        ):
            raise OwnedActionError(
                "compiled no-RNG Hard/primary authority boundary differs"
            )
        if "Math.random" in body:
            raise OwnedActionError("compiled selector consumes live chance RNG")
    except (KeyError, HostIntegrationError) as error:
        raise OwnedActionError(
            "complete compiled owned-action host required"
        ) from error
    return {
        "contract": "owned-action-xorshift32-v1",
        "per_primary_ticket_draws": 2,
        "hard_zero_draws": 0,
        "compiled_bindings_checked": True,
        "runtime_execution_verified": False,
    }
