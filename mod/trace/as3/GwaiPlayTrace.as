internal class GwaiPlayTrace
{
   private static var active:Boolean = false;
   private static var sequence:int = 0;
   private static var actionIndex:int = 0;
   private static var pending:Object = null;

   public static function begin(enabled:Boolean, manager:CardManager, flow:GwintGameFlowController, round:int, selection:Object = null) : void
   {
      active = false;
      sequence = 0;
      actionIndex = 0;
      pending = null;
      if(!enabled) return;
      active = true;
      dispatch("OnW3GwentPlayTraceStart",["__GWAI_TRACE_BUILD__"]);
      var record:Object = {event:"match_start",schema:"w3gwent-playtrace-v1",schema_version:1,
         build:"__GWAI_TRACE_BUILD__",information:"public",state:stateRecord(manager,flow,round)};
      if(selection != null) record.selection = selectionRecord(selection);
      emit(record);
   }

   // Retain identities only until native effects finish; do not expose a selected
   // card from the AI's hand before it has actually been played.
   public static function actionStarted(seat:int, transaction:CardTransaction) : void
   {
      if(!active) return;
      pending = {seat:seat,action_id:++actionIndex,committed:false,transaction:transactionRecord(transaction)};
      emit({event:"action_started",seat:seat,action_id:actionIndex});
   }

   public static function actionCommitted() : void
   {
      if(active && pending != null) pending.committed = true;
   }

   public static function stable(manager:CardManager, flow:GwintGameFlowController, seat:int, round:int) : void
   {
      if(!active || pending == null) return;
      if(pending.committed)
         emit({event:"action",seat:pending.seat,action_id:pending.action_id,
            transaction:pending.transaction,state:stateRecord(manager,flow,round)});
      else
         emit({event:"action_cancelled",seat:pending.seat,action_id:pending.action_id,
            state:stateRecord(manager,flow,round)});
      pending = null;
   }

   public static function failed(error:Error = null, site:String = "hook") : void
   {
      if(!active) return;
      active = false;
      pending = null;
      // This route must work even if the record encoder itself failed. Do not
      // try to serialize another event from an encoder-error handler.
      var detail:String = site + ": public trace failed";
      try
      {
         if(error != null)
         {
            var text:String = error.toString();
            var runtimeError:Array = /(?:ReferenceError|TypeError|VerifyError|RangeError|ArgumentError|Error)(?:\s*:\s*Error)?\s*#\d+/.exec(text);
            if(runtimeError != null) detail = site + ": " + String(runtimeError[0]);
            else if(text.indexOf("Error: public trace ") == 0) detail = site + ": " + text;
            else detail = site + ": " + error.name;
         }
      }
      catch(detailError:Error) {}
      dispatch("OnW3GwentPlayTraceError",[detail.substr(0,160)]);
   }

   public static function roundComplete(seat:int) : void
   {
      if(!active) return;
      var manager:CardManager = CardManager.getInstance();
      var exhausted:Boolean = manager.getCardInstanceList(CardManager.CARD_LIST_LOC_HAND,seat).length == 0
         && !manager.getCardLeader(seat).canBeUsed;
      if(exhausted) emit({event:"round_complete",seat:seat,reason:"no_cards"});
      else emit({event:"pass",seat:seat});
   }

   public static function choice(seat:int, kind:String) : void
   {
      // Choices may concern hidden discards or cards drawn from a deck. The
      // public board at the next stable state records only revealed outcomes.
      emit({event:"choice",seat:seat,choice:kind});
   }

   public static function decision(source:String, detail:String, selection:Object = null) : void
   {
      if(!active) return;
      var record:Object = {event:"decision_source",source:source};
      if(selection != null) record.selection = selectionRecord(selection);
      // The detail may name a selected card which is still private. Preserve
      // only a boundary-stage label and the standard runtime error identifier.
      if(source == "vanilla" || source == "greedy")
      {
         var errorMatch:Array = /(?:ReferenceError|TypeError|VerifyError|Error)(?:\s*:\s*Error)?\s*#\d+/.exec(detail);
         record.error = errorMatch == null ? "handoff" : String(errorMatch[0]);
         var boundary:int = detail.indexOf(" failed:");
         record.stage = boundary < 0 ? "policy" : detail.substr(0,Math.min(boundary,80));
      }
      emit(record);
   }

   private static function selectionRecord(selection:Object) : Object
   {
      return {enabled:Boolean(selection.enabled),requested_tier:int(selection.requested_tier),
         effective_tier:int(selection.effective_tier),mode:String(selection.mode),
         opponent:String(selection.opponent),npc_difficulty:int(selection.npc_difficulty),
         checkpoint_sha256:String(selection.checkpoint_sha256),weights_sha256:String(selection.weights_sha256)};
   }

   public static function stateEvent(kind:String, manager:CardManager, flow:GwintGameFlowController, round:int) : void
   {
      if(!active) return;
      emit({event:kind,state:stateRecord(manager,flow,round)});
   }

   public static function finish(reason:String, winner:int, manager:CardManager, flow:GwintGameFlowController, round:int) : void
   {
      if(!active) return;
      emit({event:"match_end",reason:reason,winner:winner,pending_action:pending != null,
         state:stateRecord(manager,flow,round)});
      dispatch("OnW3GwentPlayTraceEnd",[reason]);
      active = false;
      pending = null;
   }

   private static function transactionRecord(transaction:CardTransaction) : Object
   {
      if(transaction == null) return null;
      var source:CardInstance = transaction.sourceCardInstanceRef;
      var target:CardInstance = transaction.targetCardInstanceRef;
      return {source:source == null ? null : cardRecord(source),target:target == null ? null : cardRecord(target),
         target_slot:transaction.targetSlotID,target_seat:transaction.targetPlayerID};
   }

   private static function cardRecord(card:CardInstance) : Object
   {
      return {instance_id:card.instanceId,template_id:card.templateId,name:card.templateRef.title,
         power:card.getTotalPower(),owner:card.owningPlayer,seat:card.listsPlayer};
   }

   private static function cardList(manager:CardManager, list:int, seat:int) : Array
   {
      var result:Array = [];
      var cards:Vector.<CardInstance> = manager.getCardInstanceList(list,seat);
      for(var index:int = 0; index < cards.length; index++) result.push(cardRecord(cards[index]));
      return result;
   }

   private static function stateRecord(manager:CardManager, flow:GwintGameFlowController, round:int) : Object
   {
      var players:Array = [];
      var results:Array = [];
      for(var resultIndex:int = 0; resultIndex < manager.roundResults.length; resultIndex++)
      {
         var result:GwintRoundResult = manager.roundResults[resultIndex];
         if(result.played) results.push({winner:result.getRoundWinner(),
            scores:[result.getPlayerScore(CardManager.PLAYER_1),result.getPlayerScore(CardManager.PLAYER_2)]});
      }
      for(var seat:int = 0; seat < 2; seat++)
      {
         players.push({seat:seat,score:manager.currentPlayerScores[seat],
            hand_count:manager.getCardInstanceList(CardManager.CARD_LIST_LOC_HAND,seat).length,
            deck_count:manager.playerDeckDefinitions[seat].cardIndicesInDeck.length,
            passed:flow.playerControllers[seat].currentRoundStatus == BasePlayerController.ROUND_PLAYER_STATUS_DONE,
            rows:{melee:cardList(manager,CardManager.CARD_LIST_LOC_MELEE,seat),
               ranged:cardList(manager,CardManager.CARD_LIST_LOC_RANGED,seat),
               siege:cardList(manager,CardManager.CARD_LIST_LOC_SEIGE,seat)},
            graveyard:cardList(manager,CardManager.CARD_LIST_LOC_GRAVEYARD,seat),
            leader:cardList(manager,CardManager.CARD_LIST_LOC_LEADER,seat),
            modifiers:{melee:cardList(manager,CardManager.CARD_LIST_LOC_MELEEMODIFIERS,seat),
               ranged:cardList(manager,CardManager.CARD_LIST_LOC_RANGEDMODIFIERS,seat),
               siege:cardList(manager,CardManager.CARD_LIST_LOC_SEIGEMODIFIERS,seat)}});
      }
      return {round:round,actor:flow.currentPlayer,players:players,round_results:results,
         weather:cardList(manager,CardManager.CARD_LIST_LOC_WEATHERSLOT,CardManager.PLAYER_INVALID)};
   }

   private static function emit(record:Object) : void
   {
      if(!active) return;
      try
      {
         record.seq = sequence;
         var encoded:String = encodeValue(record,0);
         sequence++;
         dispatch("OnW3GwentPlayTraceRecord",[encoded]);
      }
      catch(encodingError:Error)
      {
         failed(encodingError,"encode");
      }
   }

   // Native Flash JSON availability is unverified. This writer accepts only the
   // null/Boolean/finite-number/string/array/plain-object payloads created above.
   // Non-ASCII UTF-16 units are escaped so native settings chunks stay ASCII.
   private static function encodeValue(value:*, depth:int) : String
   {
      if(depth > 32) throw new Error("public trace nesting limit");
      if(value === null) return "null";
      var kind:String = typeof value;
      if(kind == "string") return encodeString(String(value));
      if(kind == "boolean") return value ? "true" : "false";
      if(kind == "number")
      {
         if(!isFinite(Number(value))) throw new Error("public trace non-finite number");
         return String(value);
      }
      var parts:Array = [];
      if(value is Array)
      {
         for(var index:int = 0; index < value.length; index++)
            parts.push(encodeValue(value[index],depth + 1));
         return "[" + parts.join(",") + "]";
      }
      if(kind != "object" || value.constructor !== Object)
         throw new Error("public trace unsupported value");
      for(var key:String in value)
      {
         if(value.hasOwnProperty(key))
            parts.push(encodeString(key) + ":" + encodeValue(value[key],depth + 1));
      }
      return "{" + parts.join(",") + "}";
   }

   private static function encodeString(value:String) : String
   {
      var output:String = "\"";
      for(var index:int = 0; index < value.length; index++)
      {
         var code:int = value.charCodeAt(index);
         if(code == 34) output += "\\\"";
         else if(code == 92) output += "\\\\";
         else if(code < 32 || code > 126)
         {
            var hex:String = code.toString(16);
            while(hex.length < 4) hex = "0" + hex;
            output += "\\u" + hex;
         }
         else output += value.charAt(index);
      }
      return output + "\"";
   }

   private static function dispatch(name:String, args:Array) : void
   {
      try
      {
         if(GwintGameMenu.mSingleton == null) throw new Error("trace menu unavailable");
         GwintGameMenu.mSingleton.dispatchEvent(new red.core.events.GameEvent(red.core.events.GameEvent.CALL,name,args));
      }
      catch(traceError:Error)
      {
         // failed() deactivates before its plain callback. A failed error
         // callback therefore stops here without a retry or recursion loop.
         failed(traceError,"dispatch");
      }
   }
}
