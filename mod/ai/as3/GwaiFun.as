internal class GwaiFun
{
   private var _flags:Object;
   private var _seen:int = -1;
   private var _open:Object = null;
   private var _pending:Object = null;
   private var _records:Array = [];
   private var _coachShown:int = 0;
   private var _blunders:int = 0;
   private var _passCount:int = 0;
   private var _passMsTotal:int = 0;
   private var _passMsMax:int = 0;
   private var _failure:String = "";
   private var _loader:flash.utils.Timer = null;
   private var _loaderTicks:int = 0;
   private var _loaderStartedAt:int = -1;
   private var _loaderMs:int = -1;
   private var _loaderTickMsMax:int = 0;

   public function GwaiFun(flags:Object)
   {
      this._flags = flags;
   }

   public static function parseSettings(data:Object) : Object
   {
      var flags:Object = GwaiFunLogic.featureFlags(data);
      if(flags.story) flags.storyTier = GwaiFunConfig.storyTierFor(String(flags.deck));
      return flags;
   }

   public static function create(flags:Object) : Object
   {
      if(flags == null || !(flags.coach || flags.postGame || flags.blunder || flags.stats)) return null;
      return new GwaiFun(flags);
   }

   // From the coin toss: decode the reading network a few chunks per timer tick, so no flow tick
   // holds the whole decode. Only the coach, the post-game card and the blunder alert read it.
   public function prepare() : void
   {
      if(!this.readsNetwork() || this._loader != null) return;
      if(GwaiNetWeights.isLoaded(GwaiNetTierConfig.dataFor(GwaiFunConfig.READING_TIER))) return;
      this._loaderStartedAt = getTimer();
      this._loader = new flash.utils.Timer(GwaiFunConfig.LOADER_TICK_MS,0);
      this._loader.addEventListener(flash.events.TimerEvent.TIMER,this.loaderTick);
      this._loader.start();
   }

   private function loaderTick(event:Object) : void
   {
      try
      {
         var startedAt:int = getTimer();
         var done:Boolean = GwaiNetWeights.loadStep(
            GwaiNetTierConfig.dataFor(GwaiFunConfig.READING_TIER),GwaiFunConfig.LOADER_CHUNKS_PER_TICK);
         var now:int = getTimer();
         this._loaderTicks++;
         if(now - startedAt > this._loaderTickMsMax) this._loaderTickMsMax = now - startedAt;
         if(done)
         {
            this._loaderMs = now - this._loaderStartedAt;
            this.stopLoader();
         }
      }
      catch(error:Error)
      {
         this.stopLoader();
         this.fail("reading network: " + error.toString());
      }
   }

   private function stopLoader() : void
   {
      if(this._loader == null) return;
      this._loader.stop();
      this._loader.removeEventListener(flash.events.TimerEvent.TIMER,this.loaderTick);
      this._loader = null;
   }

   private function readsNetwork() : Boolean
   {
      return Boolean(this._flags.coach) || this.tracksMoves();
   }

   // One reading per human decision point. It closes the previous move, whose cost is the fall
   // from that decision's win chance to this one's: both are states where the human seat is about
   // to act, the states the value head is trained on.
   public function playerTurn(flow:GwintGameFlowController, manager:CardManager, decisionId:int) : void
   {
      if(this._failure != "" || decisionId == this._seen || !this.readsNetwork()) return;
      // Silent until the reading network is decoded; the decision is read once it is, if still open.
      if(!GwaiNetWeights.isLoaded(GwaiNetTierConfig.dataFor(GwaiFunConfig.READING_TIER))) return;
      this._seen = decisionId;
      try
      {
         var view:Object = GwaiCapture.capture(manager,flow,CardManager.PLAYER_1);
         var moves:Array = GwaiCapture.legalMoves(manager,CardManager.PLAYER_1);
         if(moves.length == 0) return;
         var startedAt:int = getTimer();
         var reading:Object = GwaiNetPolicy.insight(view.observation,moves,GwaiFunConfig.READING_TIER);
         this.countPass(getTimer() - startedAt);
         var pick:Object = reading.chosen_action;
         var pickLabel:String = this.labelOf(manager,pick);
         var winChance:Number = GwaiNetPolicy.winChanceOf(reading);
         if(this.tracksMoves())
         {
            if(this._pending != null) this.closeMove(flow,winChance,false);
            this._open = {
               round:int(view.observation.round),before:winChance,after:NaN,atResult:false,
               pick:pick,pickLabel:pickLabel,hand:this.handIds(manager),leaderUsed:this.leaderUsed(manager),
               actualLabel:"",agreed:false
            };
         }
         if(this._flags.coach && flow.mcMessageQueue != null)
         {
            flow.mcMessageQueue.PushMessage(GwaiFunLogic.coachLine(pickLabel,winChance));
            this._coachShown++;
         }
      }
      catch(error:Error)
      {
         this.fail("player turn: " + error.toString());
      }
   }

   // At the human's turn end: name the move by what changed; its cost is read at the next decision.
   public function playerMoved(flow:GwintGameFlowController, manager:CardManager) : void
   {
      if(this._failure != "") return;
      // A decision with no reading leaves the last move without its next reading: drop it.
      if(this._open == null)
      {
         this._pending = null;
         return;
      }
      var record:Object = this._open;
      this._open = null;
      try
      {
         this.describeMove(manager,record);
         this._pending = record;
      }
      catch(error:Error)
      {
         this.fail("player moved: " + error.toString());
      }
   }

   public function matchEnded(flow:GwintGameFlowController, winner:int, enabled:Boolean, tier:int, tutorial:Boolean) : void
   {
      var outcome:String = GwaiFunLogic.outcomeName(winner == CardManager.PLAYER_1,winner == CardManager.PLAYER_2);
      var bucket:String = GwaiFunLogic.tierBucket(enabled,tier);
      var deck:String = GwaiFunLogic.statsDeckKey(String(this._flags.deck));
      var lines:Array = [];
      try
      {
         if(this._pending != null && this._failure == "")
            this.closeMove(flow,winner == CardManager.PLAYER_1 ? 1 : 0,true);
         this._open = null;
         if(this._flags.postGame && this._failure == "")
         {
            lines = GwaiFunLogic.postGameLines(this._records,GwaiFunConfig.CARD_MIN_DROP);
            this.send("OnW3GwentFunCard",GwaiFunLogic.settingsText(lines.join(" ")));
         }
         if(this._flags.stats && !tutorial)
         {
            this.send("OnW3GwentFunStat",bucket + "_" + outcome);
            if(deck != "") this.send("OnW3GwentFunStat","vs_" + deck + "_" + outcome);
            var bucketCounts:Array = this.countMatch(bucket,outcome);
            var deckCounts:Array = this.countMatch("deck",outcome);
            lines.push(GwaiFunLogic.statsLine(
               GwaiFunLogic.bucketLabel(bucket),bucketCounts,deck == "" ? "" : GwaiFunConfig.labelFor(deck),deckCounts));
         }
      }
      catch(error:Error)
      {
         this.fail("match end: " + error.toString());
      }
      this.send("OnW3GwentFunNote",GwaiFunLogic.settingsText(this.note(bucket,outcome,deck,tier,tutorial)));
      this.send("OnW3GwentFunSave",null);
      if(flow.mcMessageQueue == null) return;
      for(var index:int = 0; index < lines.length; index++) flow.mcMessageQueue.PushMessage(String(lines[index]));
   }

   private function tracksMoves() : Boolean
   {
      return Boolean(this._flags.postGame) || Boolean(this._flags.blunder);
   }

   private function closeMove(flow:GwintGameFlowController, after:Number, atResult:Boolean) : void
   {
      var record:Object = this._pending;
      this._pending = null;
      record.after = after;
      record.atResult = atResult;
      if(this._records.length < GwaiFunConfig.MAX_RECORDS) this._records.push(record);
      if(atResult || !this._flags.blunder || !GwaiFunLogic.isBlunder(record,GwaiFunConfig.BLUNDER_DROP)) return;
      if(flow.mcMessageQueue == null) return;
      flow.mcMessageQueue.PushMessage(GwaiFunLogic.blunderLine(
         Number(record.before) - Number(record.after),String(record.actualLabel),String(record.pickLabel)));
      this._blunders++;
   }

   // A draw restarts the match in the same menu with the same flags, so the counts move with it.
   private function countMatch(prefix:String, outcome:String) : Array
   {
      var counts:Array = GwaiFunLogic.countsAfter(this._flags.counts,prefix,outcome);
      this._flags.counts[prefix + "_won"] = counts[0];
      this._flags.counts[prefix + "_lost"] = counts[1];
      this._flags.counts[prefix + "_drawn"] = counts[2];
      return counts;
   }

   private function countPass(elapsed:int) : void
   {
      this._passCount++;
      this._passMsTotal += elapsed;
      if(elapsed > this._passMsMax) this._passMsMax = elapsed;
   }

   private function fail(reason:String) : void
   {
      if(this._failure == "") this._failure = reason;
      this._open = null;
      this._pending = null;
   }

   private function note(bucket:String, outcome:String, deck:String, tier:int, tutorial:Boolean) : String
   {
      var agreed:Array = GwaiFunLogic.agreement(this._records);
      return "result: " + outcome + "; bucket: " + bucket + "; tier: " + tier
         + "; deck: " + (deck == "" ? "unknown" : deck) + "; story tier: " + int(this._flags.storyTier)
         + "; lab: " + (this._flags.lab ? "on" : "off") + "; tutorial: " + (tutorial ? "yes" : "no")
         + "; coach banners: " + this._coachShown + "; moves read: " + this._records.length
         + "; matched: " + agreed[0] + " of " + agreed[1] + "; blunder alerts: " + this._blunders
         + "; reading network: " + (GwaiNetWeights.isLoaded(GwaiNetTierConfig.dataFor(GwaiFunConfig.READING_TIER))
            ? "decoded" : "not decoded") + ", loader ticks " + this._loaderTicks + ", tick ms max " + this._loaderTickMsMax
         + ", decode ms " + this._loaderMs
         + "; coach passes: " + this._passCount + "; pass ms mean "
         + (this._passCount == 0 ? 0 : Math.round(this._passMsTotal / this._passCount)) + " max " + this._passMsMax
         + "; failure: " + (this._failure == "" ? "none" : this._failure);
   }

   private function send(eventName:String, argument:String) : void
   {
      try
      {
         if(GwintGameMenu.mSingleton == null) return;
         var eventArguments:Array = argument == null ? [] : [argument];
         GwintGameMenu.mSingleton.dispatchEvent(
            new red.core.events.GameEvent(red.core.events.GameEvent.CALL,eventName,eventArguments));
      }
      catch(error:Error) {}
   }

   private function labelOf(manager:CardManager, move:Object) : String
   {
      if(move == null) return "a move";
      var kind:String = String(move.kind);
      if(kind != "play") return GwaiFunLogic.moveLabel(kind,"",null,null,false);
      var card:CardInstance = manager.getCardInstance(int(move.instanceId));
      var target:CardInstance = move.targetInstanceId == undefined ? null : manager.getCardInstance(int(move.targetInstanceId));
      return GwaiFunLogic.moveLabel(
         kind,
         card == null ? "" : card.templateRef.title,
         move.row == null ? null : String(move.row),
         target == null ? null : target.templateRef.title,
         move.targetPlayer != undefined && int(move.targetPlayer) == CardManager.PLAYER_2);
   }

   // A turn is one action. The leader goes first: a leader that discards (Eredin's "discard 2,
   // draw 1") also takes cards out of the hand.
   private function describeMove(manager:CardManager, record:Object) : void
   {
      var now:Array = this.handIds(manager);
      var pick:Object = record.pick;
      if(this.leaderUsed(manager) && record.leaderUsed !== true)
      {
         record.actualLabel = "your leader ability";
         record.agreed = pick != null && pick.kind == "leader";
         return;
      }
      var before:Array = record.hand;
      var played:int = -1;
      for(var index:int = 0; index < before.length; index++)
      {
         if(now.indexOf(before[index]) < 0)
         {
            played = int(before[index]);
            break;
         }
      }
      if(played < 0)
      {
         record.actualLabel = "pass";
         record.agreed = pick != null && pick.kind == "pass";
         return;
      }
      var card:CardInstance = manager.getCardInstance(played);
      record.actualLabel = card == null ? "a card" : card.templateRef.title;
      record.agreed = pick != null && pick.kind == "play" && int(pick.instanceId) == played
         && card != null && GwaiFunLogic.samePlacement(
            pick,card.inList,card.listsPlayer,now,CardManager.CARD_LIST_LOC_INVALID,CardManager.PLAYER_INVALID);
   }

   private function handIds(manager:CardManager) : Array
   {
      var result:Array = [];
      var hand:Vector.<CardInstance> = manager.getCardInstanceList(CardManager.CARD_LIST_LOC_HAND,CardManager.PLAYER_1);
      for(var index:int = 0; index < hand.length; index++) result.push(hand[index].instanceId);
      return result;
   }

   private function leaderUsed(manager:CardManager) : Boolean
   {
      var leader:CardLeaderInstance = manager.getCardLeader(CardManager.PLAYER_1);
      return leader != null && leader.hasBeenUsed;
   }
}
