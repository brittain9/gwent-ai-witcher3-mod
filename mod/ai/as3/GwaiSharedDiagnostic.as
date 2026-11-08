internal class GwaiSharedDiagnostic
{
   private static var _last:String = "";
   private static var _rejected:int = 0;
   private static var _active:Boolean = false;
   private static var _sequence:int = 0;
   private static var _decisions:int = 0;

   private static var _timingMode:Boolean = false;
   private static var _timingCurrent:Object = null;
   private static var _timingCount:int = 0;
   private static var _timingOmitted:int = 0;
   private static var _timingFirstModel:Boolean = true;
   private static var _timingFirstWeights:Boolean = true;
   private static var _timingMatch:int = 0;

   public static function begin(mode:String = "policy", buildId:String = "") : void
   {
      _last = "";
      _timingMode = mode == "timing-only";
      _timingCurrent = null;
      _timingCount = 0;
      _timingOmitted = 0;
      _timingFirstModel = true;
      _timingFirstWeights = true;
      _timingMatch++;
      _sequence = 0;
      _decisions = 0;
      _active = true;
      try
      {
         if(mode != "policy" && mode != "timing-only") throw new Error("unsupported private diagnostic mode");
         var data:Object = GwaiSharedConfig.data();
         dispatch("OnW3GwentPolicyDiagnosticStart",[String(data.CHECKPOINT_SHA256)]);
         emit({event:"diagnostic_start",schema:"w3gwent-shared-policy-diagnostic-v1",schema_version:1,mode:mode,build_id:buildId,match_sequence:_timingMatch,
            clock:_timingMode ? "flash-getTimer-ms-v1" : "unmeasured",
            checkpoint_sha256:String(data.CHECKPOINT_SHA256),weights_sha256:String(data.EXPORT_SHA256),
            information:"acting seat observation and own hand; no opponent hidden cards"});
      }
      catch(error:Error) { failed("diagnostic start failed"); }
   }

   public static function record(observation:Object, legal:Array, chosen:Object, value:Number, result:Object = null, difficulty:int = 0) : void
   {
      if(!_active || _timingMode) return;
      try
      {
         if(_decisions >= 512) { finish("truncated"); return; }
         onlyKeys(observation,["seat","to_move","initiative","decision_id","round","rounds_played","lives","scores",
            "round_results","hand","players","weather","pending_choice","terminal"]);
         for(var index:int = 0; index < observation.players.length; index++)
            onlyKeys(observation.players[index],["seat","faction","hand_count","deck_count","leader","leader_used",
               "graveyard","rows","modifiers","passed"]);
         if(!isFinite(value)) throw new Error("nonfinite diagnostic value");
         var data:Object = GwaiSharedConfig.data();
         var record:Object = {event:"policy_input",decision_index:_decisions++,seat:int(observation.seat),
            native_decision_id:int(observation.decision_id),difficulty:difficulty,
            observation_profile_id:String(data.OBSERVATION_PROFILE_ID),
            checkpoint_sha256:String(data.CHECKPOINT_SHA256),weights_sha256:String(data.EXPORT_SHA256),
            observation:observation,legal_actions:legal,selected_action:chosen,value:value};
         if(result != null)
         {
            record.encoded_observation = result.observation;
            record.group_logits = result.group_logits;
            record.local_logits = result.local_logits;
            record.action_group_ids = result.action_group_ids;
            record.action_features = result.action_features;
            record.group_features = result.group_features;
            record.selected_index = result.chosen_index;
            record.outcome_probabilities = result.outcome_probabilities;
         }
         emit(record);
      }
      catch(error:Error) { failed("policy capture failed"); }
   }

   public static function finish(reason:String, now:Number = -1) : void
   {
      if(!_active) return;
      if(_timingMode && _timingCurrent != null) timingClose("match-end:" + reason,now);
      emit({event:"diagnostic_end",reason:reason,decisions:_decisions,
         timing_onsets:_timingCount,timing_omitted:_timingOmitted});
      try { dispatch("OnW3GwentPolicyDiagnosticEnd",[reason]); }
      catch(error:Error) { failed("diagnostic finish failed"); }
      _active = false;
   }

   public static function timingBegin(seat:int, completedPrimaryTurn:Number, ticket:Number, now:Number) : void
   {
      if(!_active || !_timingMode) return;
      if(_timingCurrent != null) timingClose("superseded",now);
      if(!_active) return;
      if(_timingCount >= 512) { _timingOmitted++; finish("truncated",now); return; }
      if((seat != 0 && seat != 1) || !isFinite(completedPrimaryTurn) || completedPrimaryTurn < 0 || completedPrimaryTurn != Math.floor(completedPrimaryTurn)
         || !isFinite(ticket) || ticket < -1 || ticket != Math.floor(ticket)
         || !isFinite(now) || now < 0 || now != Math.floor(now))
      { failed("invalid timing onset context"); return; }
      _timingCurrent = {event:"decision_timing",clock:"flash-getTimer-ms-v1",match_sequence:_timingMatch,
         capture_sequence:_timingCount++,seat:seat,primary_turn:completedPrimaryTurn,
         counter_contract:"completed-primary-turns-v1",primary_ticket:ticket,
         marks:{decision_onset:now},last:now,source_kind:"unreported",first_model_attempt:false,first_weights_attempt:false,
         model_attempts:0,duplicate_commit_count:0};
      if(ticket < 0) timingFlush("no-primary","no accepted primary ticket");
   }

   public static function timingSource(kind:String) : void
   {
      if(!_active || !_timingMode || _timingCurrent == null) return;
      var allowed:Array = ["network","vanilla","stock","greedy","rule","search","terminal-pass-guard","fair-search-v1"];
      _timingCurrent.source_kind = allowed.indexOf(kind) < 0 ? "unsupported-source" : kind;
   }

   public static function timingMark(seat:int, stage:String, now:Number) : void
   {
      if(!_active || !_timingMode || _timingCurrent == null || seat != _timingCurrent.seat) return;
      var record:Object = _timingCurrent;
      if(!isFinite(now) || now < record.last || now != Math.floor(now))
      { timingFlush("invalid-clock","negative, fractional or backward native clock"); return; }
      var allowed:Array = ["weights_begin","weights_returned","model_begin","model_returned","transaction_ready","action_started",
         "primary_committed","public_stable","trace_bridge_returned","pass_committed"];
      if(allowed.indexOf(stage) < 0) { timingFlush("invalid-stage","unknown timing stage"); return; }
      record.last = now;
      if(record.marks.hasOwnProperty(stage))
      {
         if(stage == "primary_committed") record.duplicate_commit_count++;
         else timingFlush("invalid-stage","duplicate timing stage");
         return;
      }
      if(stage == "weights_begin")
      {
         record.first_weights_attempt = _timingFirstWeights;
         _timingFirstWeights = false;
      }
      if(stage == "weights_returned" && !record.marks.hasOwnProperty("weights_begin"))
      { timingFlush("invalid-stage","weights return without attempt"); return; }
      if(stage == "model_begin")
      {
         record.first_model_attempt = _timingFirstModel;
         _timingFirstModel = false;
         record.model_attempts++;
      }
      if(stage == "model_returned" && !record.marks.hasOwnProperty("model_begin"))
      { timingFlush("invalid-stage","model return without attempt"); return; }
      record.marks[stage] = now;
      if(stage == "pass_committed") timingFlush("complete","");
      else if(stage == "trace_bridge_returned")
      {
         if(!record.marks.hasOwnProperty("public_stable") || !record.marks.hasOwnProperty("primary_committed"))
            timingFlush("invalid-stage","public trace return without stable committed primary");
         else timingFlush(record.duplicate_commit_count == 0 ? "complete" : "duplicate-commit","");
      }
   }

   public static function timingClose(reason:String, now:Number) : void
   {
      if(!_active || !_timingMode || _timingCurrent == null) return;
      if(!isFinite(now) || now < _timingCurrent.last || now != Math.floor(now))
      { timingFlush("invalid-clock","cancel clock invalid"); return; }
      _timingCurrent.marks.closed = now;
      timingFlush("cancelled",reason);
   }

   private static function timingFlush(status:String, reason:String) : void
   {
      if(_timingCurrent == null) return;
      var record:Object = _timingCurrent;
      _timingCurrent = null;
      var marks:Object = record.marks;
      var onset:Number = Number(marks.decision_onset);
      var commit:Object = marks.hasOwnProperty("pass_committed") ? marks.pass_committed :
         (marks.hasOwnProperty("primary_committed") ? marks.primary_committed : null);
      record.status = status;
      record.reason = reason;
      record.onset_to_ready_ms = marks.hasOwnProperty("transaction_ready") ? Number(marks.transaction_ready) - onset : null;
      record.onset_to_commit_ms = commit == null ? null : Number(commit) - onset;
      record.onset_to_stable_ms = marks.hasOwnProperty("public_stable") ? Number(marks.public_stable) - onset : null;
      record.stable_to_trace_bridge_return_ms = marks.hasOwnProperty("trace_bridge_returned") && marks.hasOwnProperty("public_stable") ?
         Number(marks.trace_bridge_returned) - Number(marks.public_stable) : null;
      record.weights_load_ms = marks.hasOwnProperty("weights_returned") && marks.hasOwnProperty("weights_begin") ?
         Number(marks.weights_returned) - Number(marks.weights_begin) : null;
      record.model_ms = marks.hasOwnProperty("model_returned") && marks.hasOwnProperty("model_begin") ?
         Number(marks.model_returned) - Number(marks.model_begin) : null;
      delete record.last;
      emit(record);
   }

   private static function onlyKeys(value:Object, allowed:Array) : void
   {
      if(value == null) throw new Error("missing diagnostic data");
      for(var key:String in value)
         if(value.hasOwnProperty(key) && allowed.indexOf(key) < 0) throw new Error("diagnostic capture contract changed");
   }

   private static function emit(record:Object) : void
   {
      if(!_active) return;
      try
      {
         record.seq = _sequence;
         var text:String = GwaiPlayTrace.encodeDiagnostic(record);
         if(text.length > 262144) throw new Error("diagnostic record too large");
         dispatch("OnW3GwentPolicyDiagnosticRecord",[text]);
         _last = text;
         _sequence++;
      }
      catch(error:Error) { failed("diagnostic encoding failed"); }
   }

   private static function failed(reason:String) : void
   {
      _last = "";
      _rejected++;
      _active = false;
      try { dispatch("OnW3GwentPolicyDiagnosticError",[reason]); }
      catch(error:Error) {}
   }

   private static function dispatch(name:String, args:Array) : void
   {
      if(GwintGameMenu.mSingleton == null) throw new Error("diagnostic sink unavailable");
      GwintGameMenu.mSingleton.dispatchEvent(new red.core.events.GameEvent(red.core.events.GameEvent.CALL,name,args));
   }

   public static function lastRecord() : String
   {
      return _last;
   }

   public static function rejectedRecords() : int
   {
      return _rejected;
   }
}
