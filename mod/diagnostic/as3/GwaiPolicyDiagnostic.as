internal class GwaiPolicyDiagnostic
{
   private static var active:Boolean = false;
   private static var sequence:int = 0;
   private static var decisions:int = 0;
   private static var randomDraws:Array = [];

   public static function begin() : void
   {
      active = true;
      sequence = 0;
      decisions = 0;
      dispatch("OnW3GwentPolicyDiagnosticStart",["__GWAI_DIAGNOSTIC_ID__"]);
      emit({event:"diagnostic_start",schema:"w3gwent-policy-diagnostic-v1",schema_version:1,
         build:"__GWAI_DIAGNOSTIC_BUILD__",build_identity:"__GWAI_DIAGNOSTIC_ID__",
         information:"acting seat observation and own hand; no opponent hidden cards"});
   }

   public static function beginDecision() : void
   {
      randomDraws = [];
   }

   public static function random() : Number
   {
      // Preserve the original single draw, including when capture has stopped.
      var draw:Number = Math.random();
      if(active) randomDraws.push(draw);
      return draw;
   }

   public static function record(observation:Object, legalActions:Array, result:Object, difficulty:int, data:Object, temperature:Number) : void
   {
      if(!active) return;
      try
      {
         if(decisions >= 512)
         {
            finish("truncated");
            return;
         }
         validateObservation(observation);
         var encoded:Array = [];
         for(var index:int = 0; index < result.observation.length; index++) encoded.push(Number(result.observation[index]));
         var record:Object = {event:"policy_input",decision_index:decisions++,seat:int(observation.seat),
            native_decision_id:int(observation.decision_id),difficulty:difficulty,temperature:temperature,random_draws:randomDraws.concat(),
            observation_profile_id:String(result.observation_profile_id),
            checkpoint_sha256:String(data.CHECKPOINT_SHA256),weights_sha256:String(data.EXPORT_SHA256),
            observation:observation,legal_actions:legalActions,encoded_observation:encoded,
            selected_action:result.chosen_action,selected_action_id:String(result.chosen_action_id),selected_index:int(result.chosen_index),
            group_logits:result.group_logits,local_logits:result.local_logits,
            action_group_ids:result.action_group_ids,action_features:result.action_features,group_features:result.group_features,
            value:isFinite(Number(result.value)) ? Number(result.value) : null,
            value_nonfinite:!isFinite(Number(result.value)),outcome_probabilities:result.outcome_probabilities};
         emit(record);
      }
      catch(error:Error)
      {
         failed("policy capture failed: " + error.name);
      }
   }

   public static function failedAttempt(observation:Object, legalActions:Array, difficulty:int,
                                        category:String, data:Object, temperature:Number) : void
   {
      if(!active) return;
      try
      {
         validateObservation(observation);
         emit({event:"policy_failure",decision_index:decisions++,seat:int(observation.seat),
            native_decision_id:int(observation.decision_id),difficulty:difficulty,temperature:temperature,
            random_draws:randomDraws.concat(),observation_profile_id:String(data.OBSERVATION_PROFILE_ID),
            checkpoint_sha256:String(data.CHECKPOINT_SHA256),weights_sha256:String(data.EXPORT_SHA256),
            observation:observation,legal_actions:legalActions,error_category:category});
      }
      catch(error:Error) {}
      failed("network policy failed: " + category);
   }

   private static function validateObservation(observation:Object) : void
   {
      // Refuse an expanded capture contract rather than persisting new hidden fields.
      onlyKeys(observation,["seat","initiative","decision_id","round","rounds_played","lives","scores",
            "round_results","hand","players","weather","pending_choice","terminal"]);
      for(var playerIndex:int = 0; playerIndex < observation.players.length; playerIndex++)
         onlyKeys(observation.players[playerIndex],["seat","faction","hand_count","deck_count","leader",
               "leader_used","graveyard","rows","modifiers","passed"]);
   }

   public static function finish(reason:String) : void
   {
      if(!active) return;
      emit({event:"diagnostic_end",reason:reason,decisions:decisions});
      dispatch("OnW3GwentPolicyDiagnosticEnd",[reason]);
      active = false;
   }

   private static function onlyKeys(value:Object, allowed:Array) : void
   {
      for(var key:String in value)
         if(value.hasOwnProperty(key) && allowed.indexOf(key) < 0) throw new Error("diagnostic capture contract changed");
   }

   private static function emit(record:Object) : void
   {
      if(!active) return;
      try
      {
         record.seq = sequence;
         var encoded:String = GwaiPlayTrace.encodeDiagnostic(record);
         if(encoded.length > 1048576) throw new Error("diagnostic record too large");
         dispatch("OnW3GwentPolicyDiagnosticRecord",[encoded]);
         sequence++;
      }
      catch(error:Error)
      {
         failed("diagnostic encoding failed: " + error.name);
      }
   }

   private static function failed(reason:String) : void
   {
      active = false;
      try { dispatch("OnW3GwentPolicyDiagnosticError",[reason]); } catch(error:Error) {}
   }

   private static function dispatch(name:String, args:Array) : void
   {
      if(GwintGameMenu.mSingleton == null) throw new Error("diagnostic sink unavailable");
      GwintGameMenu.mSingleton.dispatchEvent(new red.core.events.GameEvent(red.core.events.GameEvent.CALL,name,args));
   }
}
