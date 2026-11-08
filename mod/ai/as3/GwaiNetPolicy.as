internal class GwaiNetPolicy
{
   private static const ROWS:Array = ["melee","ranged","siege"];
   private static const ACTION_KINDS:Array = ["pass","play","leader","mulligan","decline_mulligan","choice"];
   private static const ZONE_NAMES:Array = [
      "own_hand","own_melee","own_ranged","own_siege",
      "opponent_melee","opponent_ranged","opponent_siege",
      "own_graveyard","opponent_graveyard","weather",
      "own_leader","opponent_leader",
      "own_melee_modifier","own_ranged_modifier","own_siege_modifier",
      "opponent_melee_modifier","opponent_ranged_modifier","opponent_siege_modifier"
   ];
   private static const PENDING_KINDS:Array = [null,"mulligan","nurse","leader_resurrect","leader_weather","leader_discard","leader_pick","coin_toss"];
   private static const EFFECT_COUNT:int = 45;
   private static const GLOBAL_SIZE:int = 64;
   private static const SPARSE_INPUT_V1:Boolean = true;
   private static var FLOAT32_BUFFER:flash.utils.ByteArray = new flash.utils.ByteArray();
   private static var _lastWinChance:Number = NaN;

   public static function decide(observation:Object, legalActions:Array, difficulty:int = 2) : Object
   {
      _lastWinChance = NaN;
      var data:Object = GwaiNetTierConfig.dataFor(difficulty);
      var result:Object = evaluate(
         observation,
         legalActions,
         data.CARD_IDS,
         data.CARD_EFFECTS,
         GwaiNetWeights.load(data),
         data.OFFSETS,
         data.WIDTH,
         data.BLOCKS,
         GwaiNetTierConfig.temperatureFor(difficulty),
         String(data.OBSERVATION_PROFILE_ID),
         GwaiNetTierConfig.temperatureFor(difficulty) > 0 ? legacyActionUniforms() : null
      );
      _lastWinChance = winChanceOf(result);
      return result.chosen_action;
   }

   private static function legacyActionUniforms() : Array
   {
      return [legacyUniform(),legacyUniform()];
   }

   private static function legacyUniform() : Number
   {
      return Math.random();
   }

   public static function lastWinChance() : Number
   {
      return _lastWinChance;
   }

   public static function insight(observation:Object, legalActions:Array, difficulty:int) : Object
   {
      var data:Object = GwaiNetTierConfig.dataFor(difficulty);
      var weights:Vector.<Number> = GwaiNetWeights.load(data);
      return evaluate(observation,legalActions,data.CARD_IDS,data.CARD_EFFECTS,weights,data.OFFSETS,data.WIDTH,data.BLOCKS,0,String(data.OBSERVATION_PROFILE_ID));
   }

   public static function winChanceOf(result:Object) : Number
   {
      if(result == null || result.outcome_probabilities == null) return NaN;
      return Number(result.outcome_probabilities[2]);
   }

   public static function provenance(difficulty:int) : String
   {
      var data:Object = GwaiNetTierConfig.dataFor(difficulty);
      return "checkpoint_sha256=" + data.CHECKPOINT_SHA256
         + " updates=" + data.UPDATE_COUNT + " export_sha256=" + data.EXPORT_SHA256;
   }

   public static function evaluate(observation:Object, legalActions:Array, cardIds:Array, cardEffects:Array,
                                   weights:Vector.<Number>, offsets:Object, width:int, blocks:int,
                                   temperature:Number = 0.0,
                                   observationProfile:String = "full-faction-v1", actionUniforms:Array = null) : Object
   {
      if(observation == null || legalActions == null || legalActions.length == 0)
         throw new Error("network inference requires a seat observation and legal actions");
      if(!isFinite(temperature) || temperature < 0) throw new Error("policy temperature must be finite and non-negative");
      if(temperature > 0)
      {
         if(actionUniforms == null || actionUniforms.length != 2) throw new Error("temperature selection requires two owned action uniforms");
         for(var drawIndex:int = 0; drawIndex < 2; drawIndex++)
            if(typeof actionUniforms[drawIndex] != "number" || !isFinite(actionUniforms[drawIndex]) || actionUniforms[drawIndex] < 0 || actionUniforms[drawIndex] >= 1)
               throw new Error("owned action uniform outside [0,1)");
      }
      var encoded:Vector.<Number> = encodeObservation(observation,cardIds,cardEffects,observationProfile);
      var observationSize:int = 24 * cardIds.length + 18 * EFFECT_COUNT + GLOBAL_SIZE;
      var trunk:Vector.<Number> = trunkOf(encoded,observationSize,offsets,width,blocks,weights);
      var reading:Object = null;
      try
      {
         reading = valueHead(trunk,width,offsets,weights);
      }
      catch(valueError:Error)
      {
         reading = null;
      }

      var visible:Array = visibleCards(observation);
      var actionFeatures:Array = [];
      var groupKeys:Array = [];
      var groupFeatures:Array = [];
      var actionGroupIds:Array = [];
      for(var actionIndex:int = 0; actionIndex < legalActions.length; actionIndex++)
      {
         var action:Object = legalActions[actionIndex];
         var feature:Array = actionFeature(action,visible,cardIds);
         actionFeatures.push(feature);
         var key:String = groupKey(action);
         var groupIndex:int = groupKeys.indexOf(key);
         if(groupIndex < 0)
         {
            groupIndex = groupKeys.length;
            groupKeys.push(key);
            groupFeatures.push([feature[0],feature[1]]);
         }
         actionGroupIds.push(groupIndex);
      }

      var groupLogits:Array = [];
      for(var groupIndex:int = 0; groupIndex < groupFeatures.length; groupIndex++)
      {
         var groupFeature:Array = groupFeatures[groupIndex];
         var groupCode:Vector.<Number> = concat(
            embedding(int(groupFeature[0]),24,int(offsets["kind_embedding.weight"]),weights),
            embedding(int(groupFeature[1]),48,int(offsets["card_embedding.weight"]),weights));
         groupCode = silu(linear(groupCode,72,width,int(offsets["group_projection.weight"]),
            int(offsets["group_projection.bias"]),weights));
         var groupState:Vector.<Number> = concat(trunk,groupCode);
         var groupHidden:Vector.<Number> = linear(groupState,width * 2,128,
            int(offsets["group_actor.0.weight"]),int(offsets["group_actor.0.bias"]),weights);
         groupHidden = tanhVector(groupHidden);
         groupLogits.push(linear(groupHidden,128,1,int(offsets["group_actor.2.weight"]),
            int(offsets["group_actor.2.bias"]),weights)[0]);
      }

      var localLogits:Array = [];
      for(actionIndex = 0; actionIndex < actionFeatures.length; actionIndex++)
      {
         feature = actionFeatures[actionIndex];
         var actionCode:Vector.<Number> = concat(
            embedding(int(feature[0]),24,int(offsets["kind_embedding.weight"]),weights),
            embedding(int(feature[1]),48,int(offsets["card_embedding.weight"]),weights),
            embedding(int(feature[2]),48,int(offsets["card_embedding.weight"]),weights),
            embedding(int(feature[3]),8,int(offsets["row_embedding.weight"]),weights),
            embedding(int(feature[4]),48,int(offsets["card_embedding.weight"]),weights));
         actionCode = silu(linear(actionCode,176,width,int(offsets["action_projection.weight"]),
            int(offsets["action_projection.bias"]),weights));
         var actionState:Vector.<Number> = concat(trunk,actionCode);
         var localHidden:Vector.<Number> = linear(actionState,width * 2,128,
            int(offsets["local_actor.0.weight"]),int(offsets["local_actor.0.bias"]),weights);
         localHidden = tanhVector(localHidden);
         localLogits.push(linear(localHidden,128,1,int(offsets["local_actor.2.weight"]),
            int(offsets["local_actor.2.bias"]),weights)[0]);
      }

      var groupCandidates:Array = [];
      for(groupIndex = 0; groupIndex < groupLogits.length; groupIndex++) groupCandidates.push(groupIndex);
      var bestGroup:int = temperature == 0 ? argmax(groupLogits) : sampleLogit(groupLogits,groupCandidates,temperature,Number(actionUniforms[0]));
      var bestAction:int = -1;
      var actionCandidates:Array = [];
      for(actionIndex = 0; actionIndex < actionGroupIds.length; actionIndex++)
      {
         if(int(actionGroupIds[actionIndex]) != bestGroup) continue;
         actionCandidates.push(actionIndex);
         if(bestAction < 0 || Number(localLogits[actionIndex]) > Number(localLogits[bestAction])) bestAction = actionIndex;
      }
      if(temperature > 0) bestAction = sampleLogit(localLogits,actionCandidates,temperature,Number(actionUniforms[1]));
      if(bestAction < 0) throw new Error("network selected a group without a legal action");
      return {
         observation_profile_id:observationProfile,
         group_logits:groupLogits,
         local_logits:localLogits,
         action_group_ids:actionGroupIds,
         action_features:actionFeatures,
         group_features:groupFeatures,
         observation:encoded,
         chosen_index:bestAction,
         chosen_action:legalActions[bestAction],
         chosen_action_id:actionId(legalActions[bestAction]),
         value:reading == null ? NaN : Number(reading.value),
         outcome_probabilities:reading == null ? null : [reading.loss,reading.draw,reading.win]
      };
   }

   public static function trunkOf(encoded:Vector.<Number>, observationSize:int, offsets:Object, width:int,
                                  blocks:int, weights:Vector.<Number>) : Vector.<Number>
   {
      var trunk:Vector.<Number> = SPARSE_INPUT_V1 ?
         sparseInputLinear(encoded,observationSize,width,int(offsets["input.weight"]),int(offsets["input.bias"]),weights) :
         linear(encoded,observationSize,width,int(offsets["input.weight"]),int(offsets["input.bias"]),weights);
      trunk = silu(layerNorm(trunk,width,int(offsets["input_norm.weight"]),int(offsets["input_norm.bias"]),weights));
      for(var blockIndex:int = 0; blockIndex < blocks; blockIndex++)
      {
         var blockPrefix:String = "blocks." + blockIndex + ".";
         var normalized:Vector.<Number> = layerNorm(
            trunk,width,int(offsets[blockPrefix + "norm.weight"]),int(offsets[blockPrefix + "norm.bias"]),weights);
         var hidden:Vector.<Number> = linear(
            normalized,width,width,int(offsets[blockPrefix + "first.weight"]),int(offsets[blockPrefix + "first.bias"]),weights);
         hidden = silu(hidden);
         hidden = linear(
            hidden,width,width,int(offsets[blockPrefix + "second.weight"]),int(offsets[blockPrefix + "second.bias"]),weights);
         for(var trunkIndex:int = 0; trunkIndex < width; trunkIndex++) hidden[trunkIndex] += trunk[trunkIndex];
         trunk = silu(hidden);
      }
      return trunk;
   }

   public static function valueHead(trunk:Vector.<Number>, width:int, offsets:Object, weights:Vector.<Number>) : Object
   {
      if(offsets["value_head.0.weight"] == undefined || offsets["value_head.2.weight"] == undefined)
         throw new Error("exported policy has no value head");
      var hidden:Vector.<Number> = tanhVector(linear(trunk,width,128,
         int(offsets["value_head.0.weight"]),int(offsets["value_head.0.bias"]),weights));
      var logits:Vector.<Number> = linear(hidden,128,3,
         int(offsets["value_head.2.weight"]),int(offsets["value_head.2.bias"]),weights);
      var maximum:Number = Math.max(logits[0],Math.max(logits[1],logits[2]));
      var loss:Number = Math.exp(logits[0] - maximum);
      var draw:Number = Math.exp(logits[1] - maximum);
      var win:Number = Math.exp(logits[2] - maximum);
      var total:Number = loss + draw + win;
      return {loss:loss / total,draw:draw / total,win:win / total,value:(draw * 0.5 + win) / total};
   }

   public static function encodeObservation(observation:Object, cardIds:Array, cardEffects:Array,
                                            observationProfile:String = "full-faction-v1") : Vector.<Number>
   {
      if(observationProfile != "full-faction-v1" && observationProfile != "faction-unknown-v1")
         throw new Error("unsupported model observation profile");
      var vocabSize:int = cardIds.length;
      var powerStart:int = ZONE_NAMES.length * vocabSize;
      var effectStart:int = powerStart + 6 * vocabSize;
      var globalStart:int = effectStart + ZONE_NAMES.length * EFFECT_COUNT;
      var encoded:Vector.<Number> = zero(globalStart + GLOBAL_SIZE);
      var seat:int = int(observation.seat);
      if(seat < 0 || seat > 1 || observation.players == null || observation.players.length != 2)
         throw new Error("seat observation does not match schema v1");
      var own:Object = null;
      var opponent:Object = null;
      for(var playerIndex:int = 0; playerIndex < observation.players.length; playerIndex++)
      {
         var player:Object = observation.players[playerIndex];
         if(int(player.seat) == seat) own = player;
         else opponent = player;
      }
      if(own == null || opponent == null) throw new Error("seat observation has invalid player summaries");
      addZone(encoded,0,observation.hand,cardIds,cardEffects,vocabSize,effectStart);
      var relativePlayers:Array = [own,opponent];
      for(var relativeSeat:int = 0; relativeSeat < 2; relativeSeat++)
      {
         player = relativePlayers[relativeSeat];
         for(var rowIndex:int = 0; rowIndex < ROWS.length; rowIndex++)
         {
            var rowName:String = String(ROWS[rowIndex]);
            var zone:int = relativeSeat == 0 ? 1 + rowIndex : 4 + rowIndex;
            var rowView:Object = player.rows[rowName];
            var cards:Array = rowView == null ? [] : rowView.cards;
            addZone(encoded,zone,cards,cardIds,cardEffects,vocabSize,effectStart);
            var powerPlane:int = powerStart + (relativeSeat * 3 + rowIndex) * vocabSize;
            for(var cardIndex:int = 0; cardIndex < cards.length; cardIndex++)
            {
               var templateIndex:int = cardIndexOf(cardIds,fieldNumber(cards[cardIndex],"template_id","templateId"));
               if(templateIndex < 0) throw new Error("visible card is outside the frozen catalog vocabulary");
               var currentPower:Number = cards[cardIndex].score == null ? Number(cards[cardIndex].power) : Number(cards[cardIndex].score);
               encoded[powerPlane + templateIndex] = float32(encoded[powerPlane + templateIndex] + float32(currentPower / 100.0));
            }
         }
         var graveZone:int = relativeSeat == 0 ? 7 : 8;
         addZone(encoded,graveZone,player.graveyard,cardIds,cardEffects,vocabSize,effectStart);
         var leaderZone:int = relativeSeat == 0 ? 10 : 11;
         if(player.leader != null) addZone(encoded,leaderZone,[player.leader],cardIds,cardEffects,vocabSize,effectStart);
         var modifierBase:int = relativeSeat == 0 ? 12 : 15;
         for(rowIndex = 0; rowIndex < ROWS.length; rowIndex++)
         {
            var modifier:Object = player.modifiers == null ? null : player.modifiers[String(ROWS[rowIndex])];
            if(modifier != null) addZone(encoded,modifierBase + rowIndex,[modifier],cardIds,cardEffects,vocabSize,effectStart);
         }
      }
      addZone(encoded,9,observation.weather,cardIds,cardEffects,vocabSize,effectStart);
      for(var valueIndex:int = 0; valueIndex < powerStart; valueIndex++) encoded[valueIndex] /= 10.0;
      for(valueIndex = effectStart; valueIndex < globalStart; valueIndex++) encoded[valueIndex] /= 10.0;

      var globals:Vector.<Number> = zero(GLOBAL_SIZE);
      globals[0] = int(observation.initiative) == seat ? 1 : 0;
      var roundNumber:int = int(observation.round == null ? 1 : observation.round);
      if(roundNumber >= 1 && roundNumber <= 3) globals[roundNumber] = 1;
      globals[5] = Math.min(1,Number(observation.rounds_played || 0) / 3.0);
      globals[6] = clamp(Number(observation.scores[seat]) / 500.0,-1,1);
      globals[7] = clamp(Number(observation.scores[1 - seat]) / 500.0,-1,1);
      globals[8] = Number(observation.lives[seat]) / 2.0;
      globals[9] = Number(observation.lives[1 - seat]) / 2.0;
      var roundResults:Array = observation.round_results == null ? [] : observation.round_results;
      var firstResult:int = Math.max(0,roundResults.length - 3);
      for(var resultIndex:int = firstResult; resultIndex < roundResults.length; resultIndex++)
      {
         var result:Object = roundResults[resultIndex];
         var outputResultIndex:int = resultIndex - firstResult;
         var resultOffset:int = 10 + outputResultIndex * 6;
         globals[resultOffset] = clamp(Number(result.scores[seat]) / 500.0,-1,1);
         globals[resultOffset + 1] = clamp(Number(result.scores[1 - seat]) / 500.0,-1,1);
         if(result.winner == null) globals[resultOffset + 3] = 1;
         else if(int(result.winner) == seat) globals[resultOffset + 2] = 1;
         else globals[resultOffset + 4] = 1;
         globals[resultOffset + 5] = 1;
      }
      relativePlayers = [own,opponent];
      for(relativeSeat = 0; relativeSeat < 2; relativeSeat++)
      {
         player = relativePlayers[relativeSeat];
         var playerOffset:int = 28 + relativeSeat * 11;
         // The model input profile preserves the faction contract used in training.
         var faction:int = observationProfile == "faction-unknown-v1" ? 0 : int(player.faction == null ? 0 : player.faction);
         if(faction >= 0 && faction < 6) globals[playerOffset + faction] = 1;
         globals[playerOffset + 6] = Math.min(1,Number(player.hand_count || 0) / 20.0);
         globals[playerOffset + 7] = Math.min(1,Number(player.deck_count || 0) / 100.0);
         globals[playerOffset + 8] = Boolean(player.passed) ? 1 : 0;
         globals[playerOffset + 9] = Boolean(player.leader_used) ? 1 : 0;
         globals[playerOffset + 10] = player.leader == null ? 0 : 1;
      }
      var pending:Object = observation.pending_choice;
      var pendingIndex:int = pending == null ? 0 : PENDING_KINDS.indexOf(pending.kind);
      if(pendingIndex < 0) pendingIndex = 0;
      globals[50 + pendingIndex] = 1;
      if(pending != null)
      {
         globals[58] = relativeSeatValue(pending.seat,seat);
         globals[59] = relativeSeatValue(pending.graveyard_seat,seat);
         globals[60] = Math.min(1,Number(pending.remaining || 0) / 2.0);
         globals[61] = Math.min(1,(pending.templates == null ? 0 : pending.templates.length) / 10.0);
      }
      globals[62] = Boolean(observation.terminal) ? 1 : 0;
      globals[63] = Math.min(1,Number(observation.decision_id || 0) / 10000.0);
      for(valueIndex = 0; valueIndex < GLOBAL_SIZE; valueIndex++) encoded[globalStart + valueIndex] = globals[valueIndex];
      if(encoded.length != 24 * vocabSize + 18 * EFFECT_COUNT + GLOBAL_SIZE)
         throw new Error("observation width does not match schema v1");
      return encoded;
   }

   public static function addZone(encoded:Vector.<Number>, zone:int, cards:Array, cardIds:Array,
                                  cardEffects:Array, vocabSize:int, effectStart:int) : void
   {
      if(cards == null) return;
      for(var cardIndex:int = 0; cardIndex < cards.length; cardIndex++)
      {
         var templateIndex:int = cardIndexOf(cardIds,fieldNumber(cards[cardIndex],"template_id","templateId"));
         if(templateIndex < 0) throw new Error("visible card is outside the frozen catalog vocabulary");
         encoded[zone * vocabSize + templateIndex] += 1;
         var effects:Array = cardEffects[templateIndex];
         for(var effectIndex:int = 0; effectIndex < effects.length; effectIndex++)
         {
            var effect:int = int(effects[effectIndex]);
            if(effect < 0 || effect >= EFFECT_COUNT) throw new Error("card effect is outside schema v1");
            encoded[effectStart + zone * EFFECT_COUNT + effect] += 1;
         }
      }
   }

   private static function float32(value:Number) : Number
   {
      FLOAT32_BUFFER.position = 0;
      FLOAT32_BUFFER.writeFloat(value);
      FLOAT32_BUFFER.position = 0;
      return FLOAT32_BUFFER.readFloat();
   }

   public static function actionFeature(action:Object, visible:Array, cardIds:Array) : Array
   {
      var kind:int = ACTION_KINDS.indexOf(action.kind);
      if(kind < 0) throw new Error("unknown legal action kind " + action.kind);
      var instanceId:Number = fieldNumber(action,"instance_id","instanceId");
      var templateValue:Object = action.template_id == null ? action.templateId : action.template_id;
      var cardTemplate:Number = templateValue == null ? visibleTemplate(visible,instanceId) : Number(templateValue);
      var targetId:Number = fieldNumber(action,"target_instance_id","targetInstanceId");
      var targetTemplate:Number = visibleTemplate(visible,targetId);
      var cardFeature:int = cardFeatureOf(cardIds,cardTemplate);
      var targetFeature:int = cardFeatureOf(cardIds,targetTemplate);
      var templateFeature:int = templateValue == null ? 0 : cardFeatureOf(cardIds,Number(templateValue));
      var row:Object = action.row;
      var rowFeature:int = ROWS.indexOf(row) + 1;
      if(rowFeature < 0) rowFeature = 0;
      return [kind,cardFeature,targetFeature,rowFeature,templateFeature];
   }

   // An action's card embedding row: 0 for no card, else the vocabulary index + 1. A card outside
   // the vocabulary is refused, never encoded as "no card".
   public static function cardFeatureOf(cardIds:Array, templateId:Number) : int
   {
      if(templateId < 0) return 0;
      var index:int = cardIndexOf(cardIds,templateId);
      if(index < 0) throw new Error("action card is outside the frozen catalog vocabulary");
      return index + 1;
   }

   // The template ids of visible cards the network has no embedding for, each once, in capture
   // order. These are the zones encodeObservation reads; a non-empty result hands the turn to the
   // vanilla AI before the network runs (AIPlayerController.gwaiBegin).
   public static function unknownCards(observation:Object, cardIds:Array) : Array
   {
      var unknown:Array = [];
      var cards:Array = visibleCards(observation);
      for(var index:int = 0; index < cards.length; index++)
      {
         var templateId:Number = fieldNumber(cards[index],"template_id","templateId");
         if(cardIndexOf(cardIds,templateId) < 0 && unknown.indexOf(templateId) < 0) unknown.push(templateId);
      }
      return unknown;
   }

   public static function groupKey(action:Object) : String
   {
      var kind:String = String(action.kind);
      if(kind == "choice") return kind + ":";
      if(kind == "play" || kind == "leader" || kind == "mulligan")
         return kind + ":" + String(fieldNumber(action,"instance_id","instanceId"));
      if(kind == "pass" || kind == "decline_mulligan") return kind + ":";
      var template:Object = action.template_id == null ? action.templateId : action.template_id;
      return kind + ":" + String(template == null ? fieldNumber(action,"instance_id","instanceId") : template);
   }

   public static function actionId(action:Object) : String
   {
      if(action.id != null) return String(action.id);
      return String(action.kind) + ":" + String(fieldNumber(action,"instance_id","instanceId"));
   }

   public static function visibleCards(observation:Object) : Array
   {
      var result:Array = [];
      appendCards(result,observation.hand);
      appendCards(result,observation.weather);
      for(var playerIndex:int = 0; playerIndex < observation.players.length; playerIndex++)
      {
         var player:Object = observation.players[playerIndex];
         appendCards(result,player.graveyard);
         if(player.leader != null) result.push(player.leader);
         for(var rowIndex:int = 0; rowIndex < ROWS.length; rowIndex++)
         {
            var rowView:Object = player.rows[String(ROWS[rowIndex])];
            if(rowView != null) appendCards(result,rowView.cards);
            var modifier:Object = player.modifiers == null ? null : player.modifiers[String(ROWS[rowIndex])];
            if(modifier != null) result.push(modifier);
         }
      }
      return result;
   }

   public static function appendCards(target:Array, cards:Array) : void
   {
      if(cards == null) return;
      for(var index:int = 0; index < cards.length; index++) target.push(cards[index]);
   }

   public static function visibleTemplate(cards:Array, instanceId:Number) : Number
   {
      if(instanceId < 0) return -1;
      for(var index:int = 0; index < cards.length; index++)
      {
         if(fieldNumber(cards[index],"instance_id","instanceId") == instanceId)
            return fieldNumber(cards[index],"template_id","templateId");
      }
      return -1;
   }

   public static function fieldNumber(value:Object, snake:String, camel:String) : Number
   {
      var field:Object = value[snake] == null ? value[camel] : value[snake];
      return field == null ? -1 : Number(field);
   }

   public static function cardIndexOf(cardIds:Array, templateId:Number) : int
   {
      for(var index:int = 0; index < cardIds.length; index++)
         if(Number(cardIds[index]) == templateId) return index;
      return -1;
   }

   public static function relativeSeatValue(value:Object, seat:int) : Number
   {
      if(value == null) return 0;
      return int(value) == seat ? 1 : -1;
   }

   public static function clamp(value:Number, minimum:Number, maximum:Number) : Number
   {
      return Math.max(minimum,Math.min(maximum,value));
   }

   public static function argmax(values:Array) : int
   {
      var best:int = 0;
      for(var index:int = 1; index < values.length; index++)
         if(Number(values[index]) > Number(values[best])) best = index;
      return best;
   }

   public static function sampleLogit(values:Array, candidates:Array, temperature:Number, uniform:Number) : int
   {
      if(!isFinite(uniform) || uniform < 0 || uniform >= 1) throw new Error("explicit owned uniform in [0,1) required");
      var law:Object = temperatureWeights(values,candidates,temperature);
      var draw:Number = uniform * Number(law.total);
      var lastSupported:int = -1;
      for(var index:int = 0; index < candidates.length; index++)
      {
         if(Number(law.weights[index]) <= 0) continue;
         lastSupported = int(candidates[index]);
         draw -= Number(law.weights[index]);
         if(draw < 0) return lastSupported;
      }
      if(lastSupported < 0) throw new Error("empty sample probability support");
      return lastSupported;
   }

   public static function ownedActionUniforms(seed:Object) : Array
   {
      if(typeof seed != "number" || !isFinite(Number(seed)) || Number(seed) != Math.floor(Number(seed)) || Number(seed) < 0 || Number(seed) > 4294967295)
         throw new Error("owned action seed must be uint32");
      var state:Number = Number(seed) == 0 ? 0x6d2b79f5 : Number(seed);
      var uniforms:Array = [];
      for(var draw:int = 0; draw < 2; draw++)
      {
         state ^= state << 13;
         state ^= state >>> 17;
         state ^= state << 5;
         uniforms.push(Number(state >>> 0) / 4294967296);
      }
      return uniforms;
   }

   private static function temperatureWeights(values:Array, candidates:Array, temperature:Number) : Object
   {
      if(candidates == null || candidates.length == 0 || values == null || !isFinite(temperature) || temperature <= 0)
         throw new Error("nonempty candidates and explicit positive temperature required");
      var seen:Object = {};
      for(var index:int = 0; index < candidates.length; index++)
      {
         var candidate:Number = Number(candidates[index]);
         if(typeof candidates[index] != "number" || !isFinite(candidate) || candidate != int(candidate) || candidate < 0 || candidate >= values.length || seen.hasOwnProperty(String(candidate)))
            throw new Error("invalid or duplicate sample candidate");
         if(typeof values[int(candidate)] != "number" || !isFinite(values[int(candidate)])) throw new Error("nonfinite sample logit");
         seen[String(candidate)] = true;
      }
      var maximum:Number = Number(values[int(candidates[0])]);
      for(index = 1; index < candidates.length; index++) maximum = Math.max(maximum,Number(values[int(candidates[index])]));
      var total:Number = 0;
      var weights:Array = [];
      for(index = 0; index < candidates.length; index++)
      {
         var weight:Number = Math.exp((Number(values[int(candidates[index])]) - maximum) / temperature);
         weights.push(weight); total += weight;
      }
      var probabilities:Array = [];
      for(index = 0; index < weights.length; index++) probabilities.push(Number(weights[index]) / total);
      return {weights:weights,total:total,probabilities:probabilities};
   }

   public static function zero(length:int) : Vector.<Number>
   {
      return new Vector.<Number>(length,true);
   }

   public static function embedding(index:int, dimension:int, offset:int, weights:Vector.<Number>) : Vector.<Number>
   {
      var result:Vector.<Number> = zero(dimension);
      var start:int = offset + index * dimension;
      for(var component:int = 0; component < dimension; component++) result[component] = weights[start + component];
      return result;
   }

   public static function concat(first:Vector.<Number>, second:Vector.<Number>, third:Vector.<Number> = null,
                                 fourth:Vector.<Number> = null, fifth:Vector.<Number> = null) : Vector.<Number>
   {
      var length:int = first.length + second.length;
      if(third != null) length += third.length;
      if(fourth != null) length += fourth.length;
      if(fifth != null) length += fifth.length;
      var result:Vector.<Number> = zero(length);
      var cursor:int = 0;
      for(var index:int = 0; index < first.length; index++) result[cursor++] = first[index];
      for(index = 0; index < second.length; index++) result[cursor++] = second[index];
      if(third != null) for(index = 0; index < third.length; index++) result[cursor++] = third[index];
      if(fourth != null) for(index = 0; index < fourth.length; index++) result[cursor++] = fourth[index];
      if(fifth != null) for(index = 0; index < fifth.length; index++) result[cursor++] = fifth[index];
      return result;
   }

   // Only the first observation projection calls this flagged specialization.
   // Weights are the already hash-verified finite exported float32 payload.
   public static function sparseInputLinear(input:Vector.<Number>, inputSize:int, outputSize:int,
                                            weightOffset:int, biasOffset:int, weights:Vector.<Number>) : Vector.<Number>
   {
      if(input == null || weights == null || inputSize < 0 || outputSize < 0 || input.length != inputSize ||
         weightOffset < 0 || biasOffset < 0 || weightOffset + inputSize * outputSize > weights.length ||
         biasOffset + outputSize > weights.length)
         throw new Error("invalid sparse input projection geometry");
      var columns:Array = [];
      var values:Array = [];
      for(var column:int = 0; column < inputSize; column++)
      {
         var value:Number = input[column];
         if(!isFinite(value)) throw new Error("nonfinite encoded observation feature");
         if(value != 0)
         {
            columns.push(column);
            values.push(value);
         }
      }
      var output:Vector.<Number> = zero(outputSize);
      for(var row:int = 0; row < outputSize; row++)
      {
         var sum:Number = weights[biasOffset + row];
         var weightRow:int = weightOffset + row * inputSize;
         for(var item:int = 0; item < columns.length; item++)
            sum += weights[weightRow + int(columns[item])] * Number(values[item]);
         // Removing +/-0 additions can affect only a zero result's sign. Reuse
         // the original complete row exactly in that case, including underflow.
         if(sum == 0) sum = linear(input,inputSize,1,weightRow,biasOffset + row,weights)[0];
         output[row] = sum;
      }
      return output;
   }

   public static function linear(input:Vector.<Number>, inputSize:int, outputSize:int,
                                 weightOffset:int, biasOffset:int, weights:Vector.<Number>) : Vector.<Number>
   {
      var output:Vector.<Number> = zero(outputSize);
      for(var row:int = 0; row < outputSize; row++)
      {
         var sum:Number = weights[biasOffset + row];
         var weightRow:int = weightOffset + row * inputSize;
         for(var column:int = 0; column < inputSize; column++) sum += weights[weightRow + column] * input[column];
         output[row] = sum;
      }
      return output;
   }

   public static function layerNorm(input:Vector.<Number>, width:int, scaleOffset:int,
                                   biasOffset:int, weights:Vector.<Number>) : Vector.<Number>
   {
      var mean:Number = 0;
      for(var index:int = 0; index < width; index++) mean += input[index];
      mean /= width;
      var variance:Number = 0;
      for(index = 0; index < width; index++)
      {
         var difference:Number = input[index] - mean;
         variance += difference * difference;
      }
      variance /= width;
      var inverse:Number = 1.0 / Math.sqrt(variance + 0.00001);
      var output:Vector.<Number> = zero(width);
      for(index = 0; index < width; index++)
         output[index] = (input[index] - mean) * inverse * weights[scaleOffset + index] + weights[biasOffset + index];
      return output;
   }

   public static function silu(input:Vector.<Number>) : Vector.<Number>
   {
      var output:Vector.<Number> = zero(input.length);
      for(var index:int = 0; index < input.length; index++) output[index] = input[index] / (1.0 + Math.exp(-input[index]));
      return output;
   }

   public static function tanhVector(input:Vector.<Number>) : Vector.<Number>
   {
      var output:Vector.<Number> = zero(input.length);
      for(var index:int = 0; index < input.length; index++)
      {
         var value:Number = Math.max(-20.0,Math.min(20.0,input[index]));
         output[index] = 2.0 / (1.0 + Math.exp(-2.0 * value)) - 1.0;
      }
      return output;
   }
}
