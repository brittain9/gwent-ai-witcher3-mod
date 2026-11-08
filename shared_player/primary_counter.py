"""Count accepted native primary chains, independent of turn polling and tracing."""

from __future__ import annotations

from .host import HostIntegrationError, _body, _once

FLOW = r"""      private var _gwaiPrimarySerial:Number = 0;
      private var _gwaiPrimaryTickets:Array = [null,null];
      private var _gwaiPrimaryCounterValid:Boolean = true;

      public function gwaiPrimaryReset() : void
      {
         this._gwaiDecisionId = 0;
         this._gwaiPrimarySerial = 0;
         this._gwaiPrimaryTickets = [null,null];
         this._gwaiPrimaryCounterValid = true;
      }

      public function gwaiPrimaryInvalidate() : void
      {
         this._gwaiPrimaryCounterValid = false;
      }

      public function gwaiPrimaryCounterValid() : Boolean
      {
         return this._gwaiPrimaryCounterValid;
      }

      public function gwaiPrimaryBegin(seat:int) : Number
      {
         if(!this._gwaiPrimaryCounterValid || (seat != 0 && seat != 1) || seat != this.currentPlayer) return -1;
         var previous:Object = this._gwaiPrimaryTickets[seat];
         if(previous != null && previous.committed && !previous.complete)
         {
            this.gwaiPrimaryInvalidate();
            return -1;
         }
         var manager:CardManager = CardManager.getInstance();
         var leader:CardLeaderInstance = manager.getCardLeader(seat);
         if(manager.getCardInstanceList(CardManager.CARD_LIST_LOC_HAND,seat).length == 0
            && (leader == null || !leader.canBeUsed))
         {
            this._gwaiPrimaryTickets[seat] = null;
            return -1;
         }
         if(this._gwaiPrimarySerial >= 9007199254740990 || this._gwaiDecisionId >= 2147483647)
         {
            this.gwaiPrimaryInvalidate();
            return -1;
         }
         var ticket:Object = {id:++this._gwaiPrimarySerial,seat:seat,
            began_counter:this._gwaiDecisionId,committed:false,complete:false,kind:""};
         this._gwaiPrimaryTickets[seat] = ticket;
         return ticket.id;
      }

      public function gwaiPrimaryTicket(seat:int) : Number
      {
         if(seat != 0 && seat != 1) return -1;
         var ticket:Object = this._gwaiPrimaryTickets[seat];
         return ticket == null || ticket.complete ? -1 : Number(ticket.id);
      }

      public function gwaiPrimaryObservationReady(seat:int) : Boolean
      {
         if(!this._gwaiPrimaryCounterValid || (seat != 0 && seat != 1)) return false;
         var ticket:Object = this._gwaiPrimaryTickets[seat];
         return ticket != null && !ticket.committed && !ticket.complete && seat == this.currentPlayer;
      }

      public function gwaiPrimaryCommitted(seat:int, ticketId:Number) : Boolean
      {
         if(!this._gwaiPrimaryCounterValid || (seat != 0 && seat != 1)) return false;
         var ticket:Object = this._gwaiPrimaryTickets[seat];
         if(ticket == null || ticket.id != ticketId)
         {
            this.gwaiPrimaryInvalidate();
            return false;
         }
         if(ticket.complete || ticket.committed) return false;
         ticket.committed = true;
         ticket.kind = "card";
         return true;
      }

      public function gwaiPrimaryComplete(seat:int, ticketId:Number) : Boolean
      {
         if(!this._gwaiPrimaryCounterValid || (seat != 0 && seat != 1)) return false;
         var ticket:Object = this._gwaiPrimaryTickets[seat];
         if(ticket == null || ticket.id != ticketId || !ticket.committed || ticket.complete) return false;
         if(this._gwaiDecisionId >= 2147483647)
         {
            this.gwaiPrimaryInvalidate();
            return false;
         }
         ticket.complete = true;
         this._gwaiDecisionId++;
         return true;
      }

      public function gwaiPrimaryPass(seat:int) : Boolean
      {
         if(!this.gwaiPrimaryObservationReady(seat)) return false;
         var ticket:Object = this._gwaiPrimaryTickets[seat];
         ticket.committed = true;
         ticket.kind = "pass";
         return this.gwaiPrimaryComplete(seat,Number(ticket.id));
      }

"""


def _start(source, method, addition):
    start, _ = _body(source, method)
    return source[:start] + "\n         " + addition + source[start:]


def _call(expression):
    return (
        "try { "
        + expression
        + "; } catch(gwaiCounterError:Error) "
        + "{ this.gameFlowControllerRef.gwaiPrimaryInvalidate(); }"
    )


def patch_primary_counter(patched):
    """Attach tickets to actual choice, commit, settled-chain and explicit-pass seams."""
    sources = dict(patched)
    required = {
        "AIPlayerController",
        "BasePlayerController",
        "HumanPlayerController",
        "GwintGameFlowController",
    }
    if not required <= sources.keys() or any(
        "function gwaiPrimaryBegin(" in source for source in sources.values()
    ):
        raise HostIntegrationError(
            "primary counter source inventory differs or patched"
        )
    flow = sources["GwintGameFlowController"]
    flow = _once(
        flow,
        "      private var _gwaiMatchEnded:Boolean = true;",
        "\n" + FLOW,
        "native primary ticket methods",
    )
    flow = _start(flow, "state_begin_SpawnLeaders", "this.gwaiPrimaryReset();")
    flow = _start(flow, "state_begin_reset", "this.gwaiPrimaryReset();")
    sources["GwintGameFlowController"] = flow

    ai = _start(
        sources["AIPlayerController"],
        "state_begin_ChoseMove",
        _call("this.gameFlowControllerRef.gwaiPrimaryBegin(this.playerID)"),
    )
    ai = _once(
        ai,
        '         var stage:String = "capture";',
        "\n         if(!this.gameFlowControllerRef.gwaiPrimaryObservationReady(this.playerID))\n"
        "         {\n"
        "            if(!this.gameFlowControllerRef.gwaiPrimaryCounterValid())\n"
        '               this.gameFlowControllerRef.gwaiNotifyDecision(profile,true,"native primary counter context refused");\n'
        "            return false;\n         }",
        "shared primary observation counter admission",
    )
    start, end = _body(ai, "state_begin_Idle")
    ai = (
        ai[:start]
        + _once(
            ai[start:end],
            "         if(this.attitude == TACTIC_PASS)\n         {\n"
            "            currentRoundStatus = BasePlayerController.ROUND_PLAYER_STATUS_DONE;",
            "\n            "
            + _call("this.gameFlowControllerRef.gwaiPrimaryPass(this.playerID)"),
            "explicit AI pass only",
        )
        + ai[end:]
    )
    sources["AIPlayerController"] = ai

    base = _once(
        sources["BasePlayerController"],
        "      protected var _turnOver:Boolean;",
        "\n      protected var _gwaiPrimaryCounterTicket:Number = -1;",
        "native primary ticket field",
    )
    base = _start(
        base,
        "state_begin_ApplyingCard",
        _call(
            "this._gwaiPrimaryCounterTicket = this.gameFlowControllerRef.gwaiPrimaryTicket(this.playerID)"
        ),
    )
    commit = "GwaiPlayTrace.actionCommitted();"
    if base.count(commit) != 4:
        raise HostIntegrationError("four native primary commit sites required")
    for anchor in (
        "            leaderCard.ApplyLeaderAbility(this.isAI);",
        "            CardManager.getInstance().addCardInstanceIDToList(this._transactionCard.instanceId,slotID,playerID);",
        "         CardManager.getInstance().replaceCardInstanceIDs(this._transactionCard.instanceId,cardInstance.instanceId);",
        "            CardManager.getInstance().sendToGraveyardID(this._transactionCard.instanceId);",
    ):
        base = _once(
            base,
            anchor,
            "\n            "
            + _call(
                "this.gameFlowControllerRef.gwaiPrimaryCommitted(this.playerID,this._gwaiPrimaryCounterTicket)"
            ),
            "accepted primary native mutation",
        )
    base = _once(
        base,
        "         if(!this.cardEffectApplying())\n         {",
        "\n            "
        + _call(
            "this.gameFlowControllerRef.gwaiPrimaryComplete(this.playerID,this._gwaiPrimaryCounterTicket)"
        ),
        "complete AI native chain before possible consecutive choice",
    )
    sources["BasePlayerController"] = base

    human = _start(
        sources["HumanPlayerController"],
        "state_begin_ChoosingCard",
        _call("this.gameFlowControllerRef.gwaiPrimaryBegin(this.playerID)"),
    )
    human = _once(
        human,
        '            _turnOver = true;\n            _stateMachine.ChangeState("Idle");',
        "\n            "
        + _call(
            "this.gameFlowControllerRef.gwaiPrimaryComplete(this.playerID,this._gwaiPrimaryCounterTicket)"
        ),
        "complete human native chain including visible pending choice",
    )
    start, end = _body(human, "skipTurn")
    human = (
        human[:start]
        + _once(
            human[start:end],
            "            currentRoundStatus = BasePlayerController.ROUND_PLAYER_STATUS_DONE;",
            "\n            "
            + _call("this.gameFlowControllerRef.gwaiPrimaryPass(this.playerID)"),
            "explicit human pass only",
        )
        + human[end:]
    )
    sources["HumanPlayerController"] = human
    return sources
