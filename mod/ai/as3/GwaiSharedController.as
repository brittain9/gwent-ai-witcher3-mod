internal class GwaiSharedController
{
   private var _settings:Object;
   private var _difficulty:int;
   private var _observation:Object;
   private var _legal:Array;
   private var _ready:Boolean = false;
   private var _actionUniforms:Array = null;
   private var _terminalOnly:Boolean = false;
   public var done:Boolean = false;
   public var output:Object = null;
   public var value:Number = NaN;
   public var status:String = "idle";
   public var errorMessage:String = "";
   public var sourceKind:String = "unselected";
   public var modelCall:Boolean = false;

   public function GwaiSharedController(difficulty:int, actionUniforms:Array = null, terminalOnly:Boolean = false)
   {
      if(difficulty < 1 || difficulty > 3) throw new Error("difficulty outside 1..3");
      this._difficulty = difficulty;
      this._terminalOnly = terminalOnly;
      this._settings = GwaiSharedConfig.settingsFor(difficulty);
      this._actionUniforms = actionUniforms == null ? null : this.copyData(actionUniforms) as Array;
      if(GwaiSharedConfig.mode() != "policy-only" && !(terminalOnly && GwaiSharedConfig.mode() == "fair-continuation-v1")) throw new Error("unsupported shared controller mode");
      if(this._settings.planning_worlds != 0 || this._settings.rollout_primary_turns != 0 || this._settings.deadline_ms != 0)
         throw new Error("policy-only controller cannot simulate continuations");
      if(!isFinite(Number(this._settings.temperature)) || Number(this._settings.temperature) < 0)
         throw new Error("invalid shared controller temperature");
   }

   public function begin(observation:Object, canonicalActions:Array) : void
   {
      if(this._ready || this.done) throw new Error("a shared controller instance owns one primary decision");
      if(!GwaiSharedConfig.enabled())
      {
         this.done = true;
         this.status = "disabled";
         return;
      }
      var copied:Object = this.copyData(observation);
      this.validateObservation(copied);
      var legal:Object = this.copyData(canonicalActions);
      if(getQualifiedClassName(legal) != "Array" || legal.length == 0)
         throw new Error("shared controller needs canonical legal actions");
      var ids:Object = {};
      for(var index:int = 0; index < legal.length; index++)
      {
         var action:Object = legal[index];
         if(action == null || typeof action.id != "string" || action.id.length == 0 || ids.hasOwnProperty(action.id))
            throw new Error("canonical action identities must be unique nonempty strings");
         if(action.kind != "play" && action.kind != "leader" && action.kind != "pass")
            throw new Error("shared model controls primary actions only");
         ids[action.id] = true;
      }
      this._observation = copied;
      this._legal = legal as Array;
      this._ready = true;
      this.status = "ready";
   }

   public function advance() : Boolean
   {
      if(this.done) return true;
      if(!this._ready) throw new Error("shared controller has no prepared primary observation");
      try
      {
         return this.decide();
      }
      catch(error:Error)
      {
         this.errorMessage = error.toString();
         this.output = null;
         this.value = NaN;
         this.done = true;
         this._ready = false;
         this.status = "error";
         throw error;
      }
   }

   private function decide() : Boolean
   {
      var data:Object = GwaiSharedConfig.data();
      if(data == null || (data.DATA_KIND != "gameplay" && data.DATA_KIND != "inference-only")) throw new Error("shared player requires explicit model provenance");
      if(data == null || (data.OBSERVATION_PROFILE_ID != "full-faction-v1" && data.OBSERVATION_PROFILE_ID != "faction-unknown-v1"))
         throw new Error("shared model needs an explicit observation profile");
      if(this._settings.terminal_pass_v1 === true)
      {
         var terminalAction:Object = GwaiSharedController.terminalPass(this._observation,this._legal);
         if(terminalAction != null)
         {
            this.output = this.copyData(terminalAction);
            this.value = NaN;
            this.sourceKind = "terminal-pass-guard";
            this.modelCall = false;
            this.done = true; this._ready = false; this.status = "complete";
            return true;
         }
      }
      if(this._terminalOnly) throw new Error("terminal-only primary premise no longer holds");
      this.modelCall = true;
      var result:Object = GwaiNetPolicy.evaluate(
         this._observation,this._legal,data.CARD_IDS,data.CARD_EFFECTS,
         GwaiNetWeights.load(data),data.OFFSETS,data.WIDTH,data.BLOCKS,
         Number(this._settings.temperature),String(data.OBSERVATION_PROFILE_ID),this._actionUniforms);
      var chosen:Number = Number(result.chosen_index);
      if(!isFinite(chosen) || chosen != int(chosen) || chosen < 0 || chosen >= this._legal.length)
         throw new Error("shared model returned an invalid canonical index");
      if(!this.sameData(result.chosen_action,this._legal[int(chosen)],0))
         throw new Error("shared model returned a noncanonical primary action");
      if(!isFinite(Number(result.value))) throw new Error("shared model value is unavailable");
      this.output = this.copyData(this._legal[int(chosen)]);
      this.value = Number(result.value);
      this.sourceKind = "network";
      /*__SHARED_DIAGNOSTIC_RECORD__*/
      this.done = true;
      this._ready = false;
      this.status = "complete";
      return true;
   }

   // GwaiCapture intentionally omits to_move; the guarded caller owns that authority.
   public static function terminalPass(observation:Object, legal:Array) : Object
   {
      if(observation == null || observation.terminal !== false || observation.pending_choice !== null)
         return null;
      if(observation.seat !== 0 && observation.seat !== 1) return null;
      if(observation.hasOwnProperty("to_move") && observation.to_move !== observation.seat) return null;
      if(!observation.hasOwnProperty("scores") || !observation.hasOwnProperty("lives") || !observation.hasOwnProperty("players"))
         return null;
      if(getQualifiedClassName(observation.scores) != "Array" || observation.scores.length != 2 ||
         getQualifiedClassName(observation.lives) != "Array" || observation.lives.length != 2 ||
         getQualifiedClassName(observation.players) != "Array" || observation.players.length != 2)
         return null;
      var seat:int = int(observation.seat);
      var other:int = 1 - seat;
      for(var index:int = 0; index < 2; index++)
      {
         if(typeof observation.scores[index] != "number" || !isFinite(observation.scores[index]) || observation.scores[index] < 0)
            return null;
         if(typeof observation.lives[index] != "number" ||
            (observation.lives[index] !== 1 && observation.lives[index] !== 2))
            return null;
      }
      if(observation.lives[other] !== 1 || observation.scores[seat] <= observation.scores[other]) return null;
      var own:Object = null;
      var opponent:Object = null;
      for(index = 0; index < observation.players.length; index++)
      {
         var summary:Object = observation.players[index];
         if(summary == null) return null;
         if(summary.seat === seat && own == null) own = summary;
         else if(summary.seat === other && opponent == null) opponent = summary;
         else return null;
      }
      if(own == null || opponent == null || own.passed !== false || opponent.passed !== true) return null;
      var selected:Object = null;
      for(index = 0; index < legal.length; index++)
      {
         var action:Object = legal[index];
         if(action != null && action.id === "pass" && action.kind === "pass")
         {
            if(selected != null) return null;
            // The host's canonical pass has no target, row or native binding payload.
            for(var field:String in action)
               if(field != "id" && field != "kind") return null;
            selected = action;
         }
      }
      return selected;
   }

   public function cancel() : void
   {
      this._observation = null;
      this._legal = null;
      this._ready = false;
      this.output = null;
      this.value = NaN;
      this.done = true;
      this.status = "cancelled";
   }

   private function validateObservation(observation:Object) : void
   {
      if(observation == null || (observation.seat !== 0 && observation.seat !== 1))
         throw new Error("shared observation has no valid acting seat");
      if(!observation.hasOwnProperty("terminal") || (observation.terminal !== false && observation.terminal !== null))
         throw new Error("terminal observations cannot request a primary model choice");
      if(observation.pending_choice !== null) throw new Error("native pending abilities do not call the shared model");
      if(observation.hasOwnProperty("to_move") && observation.to_move !== observation.seat)
         throw new Error("shared observation belongs to a nonacting seat");
      var turn:Number = Number(observation.decision_id);
      if(typeof observation.decision_id != "number" || !isFinite(turn) || turn < 0 || turn != Math.floor(turn))
         throw new Error("shared observation needs a completed-primary-turn counter");
      var allowed:Array = ["seat","to_move","initiative","decision_id","round","rounds_played","lives","scores","round_results","hand","players","weather","pending_choice","terminal"];
      for(var field:String in observation)
         if(allowed.indexOf(field) < 0) throw new Error("unsupported observation data field: " + field);
      if(observation.players == null || observation.players.length != 2)
         throw new Error("shared observation needs two public player summaries");
      var summaries:Object = {};
      var publicFields:Array = ["seat","faction","hand_count","deck_count","leader","leader_used","graveyard","rows","modifiers","passed"];
      for(var index:int = 0; index < 2; index++)
      {
         var summary:Object = observation.players[index];
         if(summary == null || (summary.seat !== 0 && summary.seat !== 1) || summaries.hasOwnProperty(String(summary.seat)))
            throw new Error("invalid public player summary");
         summaries[String(summary.seat)] = true;
         for(field in summary)
            if(publicFields.indexOf(field) < 0) throw new Error("unsupported player-summary data field: " + field);
      }
   }

   private function sameData(left:Object, right:Object, depth:int) : Boolean
   {
      if(depth > 16) return false;
      if(left === null || right === null) return left === right;
      if(typeof left != typeof right) return false;
      if(typeof left != "object") return left === right;
      var kind:String = getQualifiedClassName(left);
      if(kind != getQualifiedClassName(right) || (kind != "Object" && kind != "Array")) return false;
      if(kind == "Array")
      {
         if(left.length != right.length || left.length > 64) return false;
         for(var index:int = 0; index < left.length; index++)
            if(!this.sameData(left[index],right[index],depth + 1)) return false;
         return true;
      }
      var count:int = 0;
      for(var key:String in left)
      {
         if(!left.hasOwnProperty(key)) continue;
         if(++count > 64 || !right.hasOwnProperty(key) || !this.sameData(left[key],right[key],depth + 1)) return false;
      }
      for(key in right)
         if(right.hasOwnProperty(key) && !left.hasOwnProperty(key)) return false;
      return true;
   }

   private function copyData(value:Object) : Object
   {
      if(value === null) return null;
      var kind:String = getQualifiedClassName(value);
      if(kind == "Number" || kind == "int" || kind == "uint")
      {
         if(!isFinite(Number(value))) throw new Error("non-finite data value");
         return value;
      }
      if(kind == "String" || kind == "Boolean") return value;
      var result:Object;
      if(kind == "Array")
      {
         result = [];
         for(var index:int = 0; index < value.length; index++) result.push(this.copyData(value[index]));
         return result;
      }
      if(kind != "Object") throw new Error("native objects are not simulator data");
      result = {};
      for(var key:String in value) result[key] = this.copyData(value[key]);
      return result;
   }
}
