internal class GwaiTransformer
{
      private static const ROWS:Array = ["melee","ranged","siege"];
      private static const ROW_MASKS:Array = [1,2,4];
      private static const MAX_ENTITIES:int = 128;
      private static const MAX_BOARD_ENTITIES:int = 100;
      private static const MAX_HIST_ENTITIES:int = 28;
      private static const ENTITY_FEAT_DIM:int = 24;
      private static const GLOBAL_FEAT_DIM:int = 64;
      private static const MAX_ACTIONS:int = 64;
      private static const ACTION_FEAT_DIM:int = 24;

      private static const ZONE_OWN_HAND:int = 1;
      private static const ZONE_OWN_MELEE:int = 2;
      private static const ZONE_OWN_RANGED:int = 3;
      private static const ZONE_OWN_SIEGE:int = 4;
      private static const ZONE_OPP_MELEE:int = 5;
      private static const ZONE_OPP_RANGED:int = 6;
      private static const ZONE_OPP_SIEGE:int = 7;
      private static const ZONE_OWN_GRAVE:int = 8;
      private static const ZONE_OPP_GRAVE:int = 9;
      private static const ZONE_WEATHER:int = 10;
      private static const ZONE_OWN_LEADER:int = 11;
      private static const ZONE_OPP_LEADER:int = 12;
      private static const ZONE_OWN_MOD_MELEE:int = 13;
      private static const ZONE_OWN_MOD_RANGED:int = 14;
      private static const ZONE_OWN_MOD_SIEGE:int = 15;
      private static const ZONE_OPP_MOD_MELEE:int = 16;
      private static const ZONE_OPP_MOD_RANGED:int = 17;
      private static const ZONE_OPP_MOD_SIEGE:int = 18;
      private static const ZONE_OWN_REM_DECK:int = 19;
      private static const ZONE_OPP_KNOWN_HAND:int = 20;
      private static const ZONE_HIST_OWN_PLAY:int = 21;
      private static const ZONE_HIST_OPP_PLAY:int = 22;
      private static const ZONE_HIST_EVENT:int = 23;

      private static var _vocabCache:Object = null;
      private static var _vocabKey:String = "";

      public function GwaiTransformer()
      {
         super();
      }

      public static function ownedActionUniforms(seed:Object) : Array
      {
         var state:uint = uint(seed) >>> 0;
         if(state == 0) state = 2463534242;
         var uniforms:Array = [];
         for(var i:int = 0; i < 2; i++)
         {
            state ^= state << 13;
            state ^= state >>> 17;
            state ^= state << 5;
            uniforms.push(Number(state >>> 0) / 4294967296.0);
         }
         return uniforms;
      }

      private static function buildVocab(cardIds:Array) : Object
      {
         var key:String = String(cardIds.length) + ":" + String(cardIds[0]) + ":" + String(cardIds[cardIds.length - 1]);
         if(_vocabCache != null && _vocabKey == key) return _vocabCache;
         var map:Object = {};
         for(var i:int = 0; i < cardIds.length; i++)
         {
            map[String(cardIds[i])] = i + 1;
         }
         _vocabCache = map;
         _vocabKey = key;
         return map;
      }

      public static function unknownCards(observation:Object, cardIds:Array) : Array
      {
         var vocab:Object = buildVocab(cardIds);
         var seen:Object = {};
         var unknown:Array = [];
         if(observation == null) return unknown;
         collectUnknown(observation.hand,vocab,seen,unknown);
         collectUnknown(observation.weather,vocab,seen,unknown);
         if(observation.players is Array)
         {
            for(var i:int = 0; i < observation.players.length; i++)
            {
               var p:Object = observation.players[i];
               if(p == null) continue;
               if(p.leader != null) checkCardUnknown(p.leader,vocab,seen,unknown);
               collectUnknown(p.graveyard,vocab,seen,unknown);
               if(p.rows != null)
               {
                  for(var r:int = 0; r < 3; r++)
                  {
                     var rowObj:Object = p.rows[ROWS[r]];
                     if(rowObj != null) collectUnknown(rowObj.cards,vocab,seen,unknown);
                  }
               }
               if(p.modifiers != null)
               {
                  for(r = 0; r < 3; r++)
                  {
                     if(p.modifiers[ROWS[r]] != null) checkCardUnknown(p.modifiers[ROWS[r]],vocab,seen,unknown);
                  }
               }
            }
         }
         return unknown;
      }

      private static function collectUnknown(list:Object, vocab:Object, seen:Object, out:Array) : void
      {
         if(!(list is Array)) return;
         for(var i:int = 0; i < list.length; i++) checkCardUnknown(list[i],vocab,seen,out);
      }

      private static function checkCardUnknown(card:Object, vocab:Object, seen:Object, out:Array) : void
      {
         if(card == null) return;
         var tid:String = String(card.template_id);
         if(!vocab.hasOwnProperty(tid) && !seen.hasOwnProperty(tid))
         {
            seen[tid] = true;
            out.push(int(card.template_id));
         }
      }

      private static function hasEffect(effects:Array, eff:int) : Boolean
      {
         if(effects == null) return false;
         for(var i:int = 0; i < effects.length; i++)
         {
            if(int(effects[i]) == eff) return true;
         }
         return false;
      }

      private static function hasHornDef(effects:Array) : Boolean
      {
         return hasEffect(effects,27) || hasEffect(effects,18) || hasEffect(effects,19) || hasEffect(effects,20);
      }

      private static function isScorchDef(types:int, effects:Array) : Boolean
      {
         return (types == 2048 && hasEffect(effects,29)) ||
            hasEffect(effects,29) || hasEffect(effects,7) || hasEffect(effects,39) || hasEffect(effects,21);
      }

      private static function cardDefByVocab(vid:int, data:Object) : Object
      {
         if(vid <= 0) return null;
         var idx:int = vid - 1;
         return {
            id:int(data.CARD_IDS[idx]),
            power:int(data.CARD_POWER[idx]),
            types:int(data.CARD_TYPES[idx]),
            effects:data.CARD_EFFECTS[idx] as Array,
            summons:data.CARD_SUMMONS[idx] as Array
         };
      }

      private static function fillCardFeats(
         feats:Vector.<Number>,
         baseOff:int,
         power:int,
         types:int,
         effects:Array,
         liveScore:Number,
         copyCount:Number,
         isScorchTarget:Boolean,
         isRowWeathered:Boolean,
         isRowHorned:Boolean
      ) : void
      {
         var base:Number = Number(power);
         var isClear:Boolean = hasEffect(effects,30);
         var isScorch:Boolean = isScorchDef(types,effects);
         var isOtherSpec:Boolean = hasEffect(effects,42) || hasEffect(effects,41) || hasEffect(effects,40);
         feats[baseOff + 0] = base / 15.0;
         feats[baseOff + 1] = liveScore / 30.0;
         feats[baseOff + 2] = (liveScore - base) / 15.0;
         feats[baseOff + 3] = copyCount / 4.0;
         feats[baseOff + 4] = (types & 8) != 0 ? 1.0 : 0.0;
         feats[baseOff + 5] = (types & 128) != 0 ? 1.0 : 0.0;
         feats[baseOff + 6] = (types & 256) != 0 ? 1.0 : 0.0;
         feats[baseOff + 7] = hasEffect(effects,33) ? 1.0 : 0.0;
         feats[baseOff + 8] = (types == (32 | 512) && hasEffect(effects,26)) ? 1.0 : 0.0;
         feats[baseOff + 9] = isScorch ? 1.0 : 0.0;
         feats[baseOff + 10] = hasEffect(effects,35) ? 1.0 : 0.0;
         feats[baseOff + 11] = hasEffect(effects,31) ? 1.0 : 0.0;
         feats[baseOff + 12] = hasEffect(effects,32) ? 1.0 : 0.0;
         feats[baseOff + 13] = hasHornDef(effects) ? 1.0 : 0.0;
         feats[baseOff + 14] = hasEffect(effects,36) ? 1.0 : 0.0;
         feats[baseOff + 15] = ((types & 16) != 0 && !isClear) ? 1.0 : 0.0;
         feats[baseOff + 16] = isClear ? 1.0 : 0.0;
         feats[baseOff + 17] = isOtherSpec ? 1.0 : 0.0;
         feats[baseOff + 18] = (types & 1) != 0 ? 1.0 : 0.0;
         feats[baseOff + 19] = (types & 2) != 0 ? 1.0 : 0.0;
         feats[baseOff + 20] = (types & 4) != 0 ? 1.0 : 0.0;
         feats[baseOff + 21] = isScorchTarget ? 1.0 : 0.0;
         feats[baseOff + 22] = isRowWeathered ? 1.0 : 0.0;
         feats[baseOff + 23] = isRowHorned ? 1.0 : 0.0;
      }

      private static function sortNumericKeys(map:Object) : Array
      {
         var keys:Array = [];
         for(var k:String in map) keys.push(int(k));
         keys.sort(Array.NUMERIC);
         return keys;
      }

      private static function clamp(x:Number, lo:Number, hi:Number) : Number
      {
         if(x < lo) return lo;
         if(x > hi) return hi;
         return x;
      }

      private static function actionKindId(kind:String) : int
      {
         if(kind == "pass") return 0;
         if(kind == "play") return 1;
         if(kind == "leader") return 2;
         if(kind == "mulligan") return 3;
         if(kind == "decline_mulligan") return 4;
         if(kind == "choice") return 5;
         return 0;
      }

      private static function rowIdFromName(row:Object) : int
      {
         if(row == "melee") return 1;
         if(row == "ranged") return 2;
         if(row == "siege") return 3;
         return 0;
      }

      private static function pendingIndex(pending:Object) : int
      {
         if(pending == null) return 0;
         var k:String = String(pending.kind != null ? pending.kind : pending.type);
         if(k == "mulligan") return 1;
         if(k == "nurse") return 2;
         if(k == "leader_resurrect") return 3;
         if(k == "leader_weather") return 4;
         if(k == "leader_discard" || k == "leader_bin") return 5;
         if(k == "leader_pick") return 6;
         if(k == "coin_toss") return 7;
         return 0;
      }

      private static function leaderEffectOf(playerObj:Object, vocab:Object, data:Object) : int
      {
         if(playerObj == null || playerObj.leader == null) return -1;
         var vid:int = int(vocab[String(playerObj.leader.template_id)]);
         if(vid <= 0) return -1;
         var effs:Array = data.CARD_EFFECTS[vid - 1] as Array;
         if(effs == null || effs.length == 0) return -1;
         return int(effs[0]);
      }

      private static function leadersDisabled(ownP:Object, oppP:Object, vocab:Object, data:Object) : Boolean
      {
         return leaderEffectOf(ownP,vocab,data) == 22 || leaderEffectOf(oppP,vocab,data) == 22;
      }

      private static function halfWeatherEnabled(pObj:Object, disabled:Boolean, vocab:Object, data:Object) : Boolean
      {
         return !disabled && leaderEffectOf(pObj,vocab,data) == 43;
      }

      private static function doubleSpyEnabled(ownP:Object, oppP:Object, disabled:Boolean, vocab:Object, data:Object) : Boolean
      {
         return !disabled && (leaderEffectOf(ownP,vocab,data) == 38 || leaderEffectOf(oppP,vocab,data) == 38);
      }

      private static function simUnitPower(
         rowCards:Array,
         idx:int,
         weathered:Boolean,
         modHorn:Boolean,
         halfW:Boolean,
         dblSpy:Boolean,
         vocab:Object,
         data:Object
      ) : int
      {
         var tid:int = int(rowCards[idx]);
         var vid:int = int(vocab[String(tid)]);
         if(vid <= 0) return 0;
         var types:int = int(data.CARD_TYPES[vid - 1]);
         if((types & 8) == 0) return 0;
         var base:int = int(data.CARD_POWER[vid - 1]);
         if((types & 128) != 0) return base;

         var wp:int = base;
         if(weathered)
         {
            wp = halfW ? int(Math.floor(base / 2)) : (base < 1 ? base : 1);
         }
         var sp:int = ((types & 256) != 0 && dblSpy) ? wp * 2 : wp;

         var morale:int = 0;
         var bonds:int = 0;
         var unitHorn:Boolean = false;
         var selfEffs:Array = data.CARD_EFFECTS[vid - 1] as Array;
         var selfSummons:Array = data.CARD_SUMMONS[vid - 1] as Array;
         var selfHasBond:Boolean = hasEffect(selfEffs,35);

         for(var j:int = 0; j < rowCards.length; j++)
         {
            if(j == idx) continue;
            var otid:int = int(rowCards[j]);
            var ovid:int = int(vocab[String(otid)]);
            if(ovid <= 0) continue;
            var oeffs:Array = data.CARD_EFFECTS[ovid - 1] as Array;
            if(hasEffect(oeffs,32)) morale++;
            if(hasHornDef(oeffs)) unitHorn = true;
            if(j < idx)
            {
               if(selfHasBond && selfSummons != null && selfSummons.indexOf(otid) >= 0) bonds++;
            }
            else
            {
               var osum:Array = data.CARD_SUMMONS[ovid - 1] as Array;
               if(hasEffect(oeffs,35) && osum != null && osum.indexOf(tid) >= 0) bonds++;
            }
         }
         var beforeHorn:int = sp + sp * bonds + morale;
         return (modHorn || unitHorn) ? beforeHorn * 2 : beforeHorn;
      }

      private static function simRowScore(
         rowCards:Array,
         weathered:Boolean,
         modHorn:Boolean,
         halfW:Boolean,
         dblSpy:Boolean,
         vocab:Object,
         data:Object
      ) : int
      {
         var total:int = 0;
         for(var i:int = 0; i < rowCards.length; i++)
         {
            total += simUnitPower(rowCards,i,weathered,modHorn,halfW,dblSpy,vocab,data);
         }
         return total;
      }

      private static function simRowScorch(
         rowCards:Array,
         weathered:Boolean,
         modHorn:Boolean,
         halfW:Boolean,
         dblSpy:Boolean,
         vocab:Object,
         data:Object
      ) : void
      {
         if(simRowScore(rowCards,weathered,modHorn,halfW,dblSpy,vocab,data) < 10) return;
         var powers:Array = [];
         var maxP:int = 0;
         for(var i:int = 0; i < rowCards.length; i++)
         {
            var vid:int = int(vocab[String(rowCards[i])]);
            var types:int = vid > 0 ? int(data.CARD_TYPES[vid - 1]) : 0;
            if((types & 8) != 0 && (types & 128) == 0)
            {
               var p:int = simUnitPower(rowCards,i,weathered,modHorn,halfW,dblSpy,vocab,data);
               powers.push(p);
               if(p > maxP) maxP = p;
            }
            else
            {
               powers.push(-1);
            }
         }
         if(maxP <= 0) return;
         for(i = rowCards.length - 1; i >= 0; i--)
         {
            if(int(powers[i]) == maxP) rowCards.splice(i,1);
         }
      }

      private static function simGlobalScorch(
         ownRows:Array,
         oppRows:Array,
         wFlags:Array,
         ownModH:Array,
         oppModH:Array,
         ownHalfW:Boolean,
         oppHalfW:Boolean,
         dblSpy:Boolean,
         vocab:Object,
         data:Object
      ) : void
      {
         var maxP:int = 0;
         var ownPow:Array = [[],[],[]];
         var oppPow:Array = [[],[],[]];
         for(var r:int = 0; r < 3; r++)
         {
            var rc:Array = ownRows[r] as Array;
            for(var i:int = 0; i < rc.length; i++)
            {
               var vid:int = int(vocab[String(rc[i])]);
               var types:int = vid > 0 ? int(data.CARD_TYPES[vid - 1]) : 0;
               if((types & 8) != 0 && (types & 128) == 0)
               {
                  var p:int = simUnitPower(rc,i,Boolean(wFlags[r]),Boolean(ownModH[r]),ownHalfW,dblSpy,vocab,data);
                  (ownPow[r] as Array).push(p);
                  if(p > maxP) maxP = p;
               }
               else (ownPow[r] as Array).push(-1);
            }
            rc = oppRows[r] as Array;
            for(i = 0; i < rc.length; i++)
            {
               vid = int(vocab[String(rc[i])]);
               types = vid > 0 ? int(data.CARD_TYPES[vid - 1]) : 0;
               if((types & 8) != 0 && (types & 128) == 0)
               {
                  p = simUnitPower(rc,i,Boolean(wFlags[r]),Boolean(oppModH[r]),oppHalfW,dblSpy,vocab,data);
                  (oppPow[r] as Array).push(p);
                  if(p > maxP) maxP = p;
               }
               else (oppPow[r] as Array).push(-1);
            }
         }
         if(maxP <= 0) return;
         for(r = 0; r < 3; r++)
         {
            rc = ownRows[r] as Array;
            var pa:Array = ownPow[r] as Array;
            for(i = rc.length - 1; i >= 0; i--) if(int(pa[i]) == maxP) rc.splice(i,1);
            rc = oppRows[r] as Array;
            pa = oppPow[r] as Array;
            for(i = rc.length - 1; i >= 0; i--) if(int(pa[i]) == maxP) rc.splice(i,1);
         }
      }

      private static function supportedDeltaPlay(types:int, effects:Array) : Boolean
      {
         if((types & 16) != 0) return true;
         if(types == 2048 && hasEffect(effects,29)) return true;
         if((types & 64) != 0) return hasHornDef(effects) && !hasEffect(effects,41);
         if((types & 8) != 0 && (types & 256) == 0)
         {
            for(var i:int = 0; i < effects.length; i++)
            {
               var e:int = int(effects[i]);
               if(e != 0 && e != 27 && e != 32 && e != 35 && e != 29 && e != 7 && e != 39 && e != 21 && e != 40)
                  return false;
            }
            return true;
         }
         return false;
      }

      private static function projectActionDelta(
         action:Object,
         cardDef:Object,
         ownRowsBase:Array,
         oppRowsBase:Array,
         wFlagsBase:Array,
         ownModHBase:Array,
         oppModHBase:Array,
         ownHalfW:Boolean,
         oppHalfW:Boolean,
         dblSpy:Boolean,
         beforeOwn:int,
         beforeOpp:int,
         pendingNull:Boolean,
         vocab:Object,
         data:Object
      ) : Array
      {
         if(!pendingNull) return null;
         if(action.kind == "pass") return [0,0];
         if(action.kind != "play" || action.target_instance_id != null || cardDef == null) return null;
         var types:int = int(cardDef.types);
         var effs:Array = cardDef.effects as Array;
         if(!supportedDeltaPlay(types,effs)) return null;

         var ownRows:Array = [(ownRowsBase[0] as Array).concat(),(ownRowsBase[1] as Array).concat(),(ownRowsBase[2] as Array).concat()];
         var oppRows:Array = [(oppRowsBase[0] as Array).concat(),(oppRowsBase[1] as Array).concat(),(oppRowsBase[2] as Array).concat()];
         var wFlags:Array = [Boolean(wFlagsBase[0]),Boolean(wFlagsBase[1]),Boolean(wFlagsBase[2])];
         var ownModH:Array = [Boolean(ownModHBase[0]),Boolean(ownModHBase[1]),Boolean(ownModHBase[2])];
         var oppModH:Array = [Boolean(oppModHBase[0]),Boolean(oppModHBase[1]),Boolean(oppModHBase[2])];

         var rid:int = rowIdFromName(action.row);
         if((types & 16) != 0)
         {
            if(hasEffect(effs,30))
            {
               wFlags[0] = false; wFlags[1] = false; wFlags[2] = false;
            }
            else
            {
               if(hasEffect(effs,23)) wFlags[0] = true;
               if(hasEffect(effs,24)) wFlags[1] = true;
               if(hasEffect(effs,25)) wFlags[2] = true;
            }
         }
         else if(types == 2048 && hasEffect(effs,29))
         {
            simGlobalScorch(ownRows,oppRows,wFlags,ownModH,oppModH,ownHalfW,oppHalfW,dblSpy,vocab,data);
         }
         else if((types & 64) != 0)
         {
            if(rid < 1 || rid > 3 || ownModH[rid - 1]) return null;
            ownModH[rid - 1] = true;
         }
         else if((types & 8) != 0)
         {
            if(rid < 1 || rid > 3) return null;
            (ownRows[rid - 1] as Array).push(int(cardDef.id));
            if(hasEffect(effs,29))
            {
               simGlobalScorch(ownRows,oppRows,wFlags,ownModH,oppModH,ownHalfW,oppHalfW,dblSpy,vocab,data);
            }
            if(hasEffect(effs,7)) simRowScorch(oppRows[0] as Array,Boolean(wFlags[0]),Boolean(oppModH[0]),oppHalfW,dblSpy,vocab,data);
            if(hasEffect(effs,39)) simRowScorch(oppRows[1] as Array,Boolean(wFlags[1]),Boolean(oppModH[1]),oppHalfW,dblSpy,vocab,data);
            if(hasEffect(effs,21)) simRowScorch(oppRows[2] as Array,Boolean(wFlags[2]),Boolean(oppModH[2]),oppHalfW,dblSpy,vocab,data);
         }
         else return null;

         var afterOwn:int = 0;
         var afterOpp:int = 0;
         for(var r:int = 0; r < 3; r++)
         {
            afterOwn += simRowScore(ownRows[r] as Array,Boolean(wFlags[r]),Boolean(ownModH[r]),ownHalfW,dblSpy,vocab,data);
            afterOpp += simRowScore(oppRows[r] as Array,Boolean(wFlags[r]),Boolean(oppModH[r]),oppHalfW,dblSpy,vocab,data);
         }
         return [afterOwn - beforeOwn, afterOpp - beforeOpp];
      }

      private static function gelu(x:Number) : Number
      {
         var ax:Number = x * 0.7071067811865475;
         var sign:Number = ax < 0.0 ? -1.0 : 1.0;
         var z:Number = ax < 0.0 ? -ax : ax;
         var t:Number = 1.0 / (1.0 + 0.3275911 * z);
         var y:Number = 1.0 - (((((1.061405429 * t - 1.453152027) * t) + 1.421413741) * t - 0.284496736) * t + 0.254829592) * t * Math.exp(-z * z);
         return 0.5 * x * (1.0 + sign * y);
      }

      private static function layerNormVec(
         src:Vector.<Number>, srcOff:int,
         dst:Vector.<Number>, dstOff:int,
         weights:Vector.<Number>, wOff:int, bOff:int,
         dim:int
      ) : void
      {
         var mean:Number = 0.0;
         var i:int;
         for(i = 0; i < dim; i++) mean += src[srcOff + i];
         mean /= Number(dim);
         var vsum:Number = 0.0;
         for(i = 0; i < dim; i++)
         {
            var diff:Number = src[srcOff + i] - mean;
            vsum += diff * diff;
         }
         var invStd:Number = 1.0 / Math.sqrt(vsum / Number(dim) + 1e-5);
         for(i = 0; i < dim; i++)
         {
            dst[dstOff + i] = (src[srcOff + i] - mean) * invStd * weights[wOff + i] + weights[bOff + i];
         }
      }

      private static function linearVec(
         src:Vector.<Number>, srcOff:int,
         dst:Vector.<Number>, dstOff:int,
         weights:Vector.<Number>, wOff:int, bOff:int,
         inDim:int, outDim:int,
         applyGelu:Boolean
      ) : void
      {
         var w:Vector.<Number> = weights;
         var s:Vector.<Number> = src;
         var kEnd8:int = inDim & ~7;
         for(var o:int = 0; o < outDim; o++)
         {
            var sum:Number = w[bOff + o];
            var sIdx:int = srcOff;
            var wIdx:int = wOff + o * inDim;
            var k:int = 0;
            while(k < kEnd8)
            {
               sum += s[sIdx] * w[wIdx]
                    + s[sIdx + 1] * w[wIdx + 1]
                    + s[sIdx + 2] * w[wIdx + 2]
                    + s[sIdx + 3] * w[wIdx + 3]
                    + s[sIdx + 4] * w[wIdx + 4]
                    + s[sIdx + 5] * w[wIdx + 5]
                    + s[sIdx + 6] * w[wIdx + 6]
                    + s[sIdx + 7] * w[wIdx + 7];
               k += 8;
               sIdx += 8;
               wIdx += 8;
            }
            while(k < inDim)
            {
               sum += s[sIdx] * w[wIdx];
               k++;
               sIdx++;
               wIdx++;
            }
            dst[dstOff + o] = applyGelu ? gelu(sum) : sum;
         }
      }

      public static function evaluate(
         observation:Object,
         legalActions:Array,
         data:Object,
         weights:Vector.<Number>,
         temperature:Number = 0.0,
         actionUniforms:Array = null
      ) : Object
      {
         var job:Object = beginEval(observation,legalActions,data,weights,temperature,actionUniforms);
         while(!stepEval(job,1000000)) {}
         return job.result;
      }

      public static function beginEval(
         observation:Object,
         legalActions:Array,
         data:Object,
         weights:Vector.<Number>,
         temperature:Number = 0.0,
         actionUniforms:Array = null
      ) : Object
      {
         var vocab:Object = buildVocab(data.CARD_IDS as Array);
         var off:Object = data.OFFSETS;
         var D:int = int(data.D_MODEL);
         var H:int = int(data.N_HEADS);
         var Dh:int = int(D / H);
         var nLayers:int = int(data.N_LAYERS);
         var ffnDim:int = int(data.FFN_DIM);
         var useHistory:Boolean = Boolean(data.USE_HISTORY);
         var scale:Number = 1.0 / Math.sqrt(Number(Dh));

         var seat:int = int(observation.seat);
         var oppSeat:int = 1 - seat;
         var ownP:Object = observation.players[seat];
         var oppP:Object = observation.players[oppSeat];
         if(ownP.seat != seat)
         {
            ownP = observation.players[0].seat == seat ? observation.players[0] : observation.players[1];
            oppP = observation.players[0].seat == oppSeat ? observation.players[0] : observation.players[1];
         }

         // Weather & horn flags
         var wFlags:Array = [false,false,false];
         var weatherList:Array = observation.weather as Array;
         var i:int;
         var j:int;
         var r:int;
         if(weatherList != null)
         {
            for(i = 0; i < weatherList.length; i++)
            {
               var wvid:int = int(vocab[String(weatherList[i].template_id)]);
               if(wvid > 0)
               {
                  var weffs:Array = data.CARD_EFFECTS[wvid - 1] as Array;
                  if(hasEffect(weffs,23)) wFlags[0] = true;
                  if(hasEffect(weffs,24)) wFlags[1] = true;
                  if(hasEffect(weffs,25)) wFlags[2] = true;
               }
            }
         }

         var ownHorns:Array = [false,false,false];
         var oppHorns:Array = [false,false,false];
         var ownModH:Array = [false,false,false];
         var oppModH:Array = [false,false,false];
         var ownRowsTids:Array = [[],[],[]];
         var oppRowsTids:Array = [[],[],[]];
         var ownRowScores:Array = [0,0,0];
         var oppRowScores:Array = [0,0,0];
         var instMap:Object = {};
         var ownBoardLiveScore:Object = {};

         // Index hand instances
         var handList:Array = observation.hand as Array;
         if(handList != null)
         {
            for(i = 0; i < handList.length; i++)
            {
               if(handList[i].instance_id != null) instMap[String(handList[i].instance_id)] = int(handList[i].template_id);
            }
         }
         if(ownP.leader != null && ownP.leader.instance_id != null) instMap[String(ownP.leader.instance_id)] = int(ownP.leader.template_id);
         if(oppP.leader != null && oppP.leader.instance_id != null) instMap[String(oppP.leader.instance_id)] = int(oppP.leader.template_id);

         var ownMaxNH:int = 0;
         var oppMaxNH:int = 0;
         for(r = 0; r < 3; r++)
         {
            var rName:String = String(ROWS[r]);
            var modC:Object = ownP.modifiers != null ? ownP.modifiers[rName] : null;
            if(modC != null)
            {
               var mvid:int = int(vocab[String(modC.template_id)]);
               if(mvid > 0 && hasHornDef(data.CARD_EFFECTS[mvid - 1] as Array))
               {
                  ownModH[r] = true;
                  ownHorns[r] = true;
               }
            }
            modC = oppP.modifiers != null ? oppP.modifiers[rName] : null;
            if(modC != null)
            {
               mvid = int(vocab[String(modC.template_id)]);
               if(mvid > 0 && hasHornDef(data.CARD_EFFECTS[mvid - 1] as Array))
               {
                  oppModH[r] = true;
                  oppHorns[r] = true;
               }
            }

            var rCards:Array = ownP.rows[rName].cards as Array;
            for(i = 0; i < rCards.length; i++)
            {
               var cObj:Object = rCards[i];
               var ctid:int = int(cObj.template_id);
               var cvid:int = int(vocab[String(ctid)]);
               var csc:int = cObj.score != null ? int(cObj.score) : int(cObj.power);
               (ownRowsTids[r] as Array).push(ctid);
               ownRowScores[r] = int(ownRowScores[r]) + csc;
               if(cObj.instance_id != null)
               {
                  instMap[String(cObj.instance_id)] = ctid;
                  ownBoardLiveScore[String(cObj.instance_id)] = csc;
               }
               if(cvid > 0)
               {
                  var ceffs:Array = data.CARD_EFFECTS[cvid - 1] as Array;
                  var ctypes:int = int(data.CARD_TYPES[cvid - 1]);
                  if(hasEffect(ceffs,27) || (r == 0 && hasEffect(ceffs,18)) || (r == 1 && hasEffect(ceffs,19)) || (r == 2 && hasEffect(ceffs,20)))
                     ownHorns[r] = true;
                  if((ctypes & 8) != 0 && (ctypes & 128) == 0 && csc > ownMaxNH) ownMaxNH = csc;
               }
            }

            rCards = oppP.rows[rName].cards as Array;
            for(i = 0; i < rCards.length; i++)
            {
               cObj = rCards[i];
               ctid = int(cObj.template_id);
               cvid = int(vocab[String(ctid)]);
               csc = cObj.score != null ? int(cObj.score) : int(cObj.power);
               (oppRowsTids[r] as Array).push(ctid);
               oppRowScores[r] = int(oppRowScores[r]) + csc;
               if(cObj.instance_id != null) instMap[String(cObj.instance_id)] = ctid;
               if(cvid > 0)
               {
                  ceffs = data.CARD_EFFECTS[cvid - 1] as Array;
                  ctypes = int(data.CARD_TYPES[cvid - 1]);
                  if(hasEffect(ceffs,27) || (r == 0 && hasEffect(ceffs,18)) || (r == 1 && hasEffect(ceffs,19)) || (r == 2 && hasEffect(ceffs,20)))
                     oppHorns[r] = true;
                  if((ctypes & 8) != 0 && (ctypes & 128) == 0 && csc > oppMaxNH) oppMaxNH = csc;
               }
            }
         }
         // Also index graveyards for medic/choice targets
         var gList:Array = ownP.graveyard as Array;
         if(gList != null) for(i = 0; i < gList.length; i++) if(gList[i].instance_id != null) instMap[String(gList[i].instance_id)] = int(gList[i].template_id);
         gList = oppP.graveyard as Array;
         if(gList != null) for(i = 0; i < gList.length; i++) if(gList[i].instance_id != null) instMap[String(gList[i].instance_id)] = int(gList[i].template_id);

         var globalMaxNH:int = ownMaxNH > oppMaxNH ? ownMaxNH : oppMaxNH;
         var ownAtMax:int = 0;
         var oppAtMax:int = 0;
         if(globalMaxNH > 0)
         {
            for(r = 0; r < 3; r++)
            {
               rName = String(ROWS[r]);
               rCards = ownP.rows[rName].cards as Array;
               for(i = 0; i < rCards.length; i++)
               {
                  cvid = int(vocab[String(rCards[i].template_id)]);
                  csc = rCards[i].score != null ? int(rCards[i].score) : int(rCards[i].power);
                  if(cvid > 0 && (int(data.CARD_TYPES[cvid - 1]) & 8) != 0 && (int(data.CARD_TYPES[cvid - 1]) & 128) == 0 && csc == globalMaxNH) ownAtMax++;
               }
               rCards = oppP.rows[rName].cards as Array;
               for(i = 0; i < rCards.length; i++)
               {
                  cvid = int(vocab[String(rCards[i].template_id)]);
                  csc = rCards[i].score != null ? int(rCards[i].score) : int(rCards[i].power);
                  if(cvid > 0 && (int(data.CARD_TYPES[cvid - 1]) & 8) != 0 && (int(data.CARD_TYPES[cvid - 1]) & 128) == 0 && csc == globalMaxNH) oppAtMax++;
               }
            }
         }

         // === ENCODE ENTITIES ===
         var entIds:Vector.<int> = new Vector.<int>(MAX_ENTITIES,true);
         var entZones:Vector.<int> = new Vector.<int>(MAX_ENTITIES,true);
         var entFeats:Vector.<Number> = new Vector.<Number>(MAX_ENTITIES * ENTITY_FEAT_DIM,true);
         var entCount:int = 0;

         var pushEnt:Function = function(tid:int, zone:int, liveSc:Number, copyCnt:Number, scT:Boolean, wth:Boolean, hrn:Boolean) : void
         {
            if(entCount >= MAX_BOARD_ENTITIES) return;
            var vid:int = int(vocab[String(tid)]);
            if(vid <= 0) return;
            entIds[entCount] = vid;
            entZones[entCount] = zone;
            fillCardFeats(entFeats,entCount * ENTITY_FEAT_DIM,int(data.CARD_POWER[vid - 1]),int(data.CARD_TYPES[vid - 1]),data.CARD_EFFECTS[vid - 1] as Array,liveSc,copyCnt,scT,wth,hrn);
            entCount++;
         };

         // 1. Own hand
         if(handList != null)
         {
            for(i = 0; i < handList.length; i++)
            {
               ctid = int(handList[i].template_id);
               cvid = int(vocab[String(ctid)]);
               if(cvid > 0) pushEnt(ctid,ZONE_OWN_HAND,Number(data.CARD_POWER[cvid - 1]),1.0,false,false,false);
            }
         }
         // 2. Own board rows
         var ownRowZones:Array = [ZONE_OWN_MELEE,ZONE_OWN_RANGED,ZONE_OWN_SIEGE];
         for(r = 0; r < 3; r++)
         {
            rCards = ownP.rows[ROWS[r]].cards as Array;
            for(i = 0; i < rCards.length; i++)
            {
               ctid = int(rCards[i].template_id);
               cvid = int(vocab[String(ctid)]);
               csc = rCards[i].score != null ? int(rCards[i].score) : int(rCards[i].power);
               if(cvid > 0)
               {
                  var isH:Boolean = (int(data.CARD_TYPES[cvid - 1]) & 128) != 0;
                  pushEnt(ctid,int(ownRowZones[r]),Number(csc),1.0,globalMaxNH > 0 && !isH && csc == globalMaxNH,Boolean(wFlags[r]),Boolean(ownHorns[r]));
               }
            }
         }
         // 3. Opp board rows
         var oppRowZones:Array = [ZONE_OPP_MELEE,ZONE_OPP_RANGED,ZONE_OPP_SIEGE];
         for(r = 0; r < 3; r++)
         {
            rCards = oppP.rows[ROWS[r]].cards as Array;
            for(i = 0; i < rCards.length; i++)
            {
               ctid = int(rCards[i].template_id);
               cvid = int(vocab[String(ctid)]);
               csc = rCards[i].score != null ? int(rCards[i].score) : int(rCards[i].power);
               if(cvid > 0)
               {
                  isH = (int(data.CARD_TYPES[cvid - 1]) & 128) != 0;
                  pushEnt(ctid,int(oppRowZones[r]),Number(csc),1.0,globalMaxNH > 0 && !isH && csc == globalMaxNH,Boolean(wFlags[r]),Boolean(oppHorns[r]));
               }
            }
         }
         // 4. Leaders, Weather, Modifiers
         if(ownP.leader != null) pushEnt(int(ownP.leader.template_id),ZONE_OWN_LEADER,ownP.leader_used ? 0.0 : 1.0,1.0,false,false,false);
         if(oppP.leader != null) pushEnt(int(oppP.leader.template_id),ZONE_OPP_LEADER,oppP.leader_used ? 0.0 : 1.0,1.0,false,false,false);
         if(weatherList != null)
         {
            for(i = 0; i < weatherList.length; i++) pushEnt(int(weatherList[i].template_id),ZONE_WEATHER,0.0,1.0,false,true,false);
         }
         var ownModZones:Array = [ZONE_OWN_MOD_MELEE,ZONE_OWN_MOD_RANGED,ZONE_OWN_MOD_SIEGE];
         var oppModZones:Array = [ZONE_OPP_MOD_MELEE,ZONE_OPP_MOD_RANGED,ZONE_OPP_MOD_SIEGE];
         for(r = 0; r < 3; r++)
         {
            if(ownP.modifiers != null && ownP.modifiers[ROWS[r]] != null)
               pushEnt(int(ownP.modifiers[ROWS[r]].template_id),int(ownModZones[r]),0.0,1.0,false,false,true);
            if(oppP.modifiers != null && oppP.modifiers[ROWS[r]] != null)
               pushEnt(int(oppP.modifiers[ROWS[r]].template_id),int(oppModZones[r]),0.0,1.0,false,false,true);
         }
         // 5. Known opponent hand (strictly public/decoyed/revealed cards only)
         var oppKnownMap:Object = {};
         var oppKnownArr:Array = observation.opp_known_hand as Array;
         if(oppKnownArr != null)
         {
            for(i = 0; i < oppKnownArr.length; i++)
            {
               var ktid:String = String(oppKnownArr[i]);
               oppKnownMap[ktid] = oppKnownMap.hasOwnProperty(ktid) ? int(oppKnownMap[ktid]) + 1 : 1;
            }
            var sortedK:Array = sortNumericKeys(oppKnownMap);
            for(i = 0; i < sortedK.length; i++)
            {
               ctid = int(sortedK[i]);
               cvid = int(vocab[String(ctid)]);
               if(cvid > 0) pushEnt(ctid,ZONE_OPP_KNOWN_HAND,Number(data.CARD_POWER[cvid - 1]),Number(oppKnownMap[String(ctid)]),false,false,false);
            }
         }
         // 6. Own & opp graveyards (sorted by template_id)
         var ownGMap:Object = {};
         gList = ownP.graveyard as Array;
         if(gList != null)
         {
            for(i = 0; i < gList.length; i++)
            {
               ktid = String(gList[i].template_id);
               ownGMap[ktid] = ownGMap.hasOwnProperty(ktid) ? int(ownGMap[ktid]) + 1 : 1;
            }
            sortedK = sortNumericKeys(ownGMap);
            for(i = 0; i < sortedK.length; i++)
            {
               ctid = int(sortedK[i]);
               cvid = int(vocab[String(ctid)]);
               if(cvid > 0) pushEnt(ctid,ZONE_OWN_GRAVE,Number(data.CARD_POWER[cvid - 1]),Number(ownGMap[String(ctid)]),false,false,false);
            }
         }
         var oppGMap:Object = {};
         gList = oppP.graveyard as Array;
         if(gList != null)
         {
            for(i = 0; i < gList.length; i++)
            {
               ktid = String(gList[i].template_id);
               oppGMap[ktid] = oppGMap.hasOwnProperty(ktid) ? int(oppGMap[ktid]) + 1 : 1;
            }
            sortedK = sortNumericKeys(oppGMap);
            for(i = 0; i < sortedK.length; i++)
            {
               ctid = int(sortedK[i]);
               cvid = int(vocab[String(ctid)]);
               if(cvid > 0) pushEnt(ctid,ZONE_OPP_GRAVE,Number(data.CARD_POWER[cvid - 1]),Number(oppGMap[String(ctid)]),false,false,false);
            }
         }
         // 7. Own remaining deck multiset (order erased!)
         var ownDeckMap:Object = {};
         var ownDeckArr:Array = observation.own_rem_deck as Array;
         if(ownDeckArr != null)
         {
            for(i = 0; i < ownDeckArr.length; i++)
            {
               ktid = String(ownDeckArr[i]);
               ownDeckMap[ktid] = ownDeckMap.hasOwnProperty(ktid) ? int(ownDeckMap[ktid]) + 1 : 1;
            }
            sortedK = sortNumericKeys(ownDeckMap);
            for(i = 0; i < sortedK.length; i++)
            {
               ctid = int(sortedK[i]);
               cvid = int(vocab[String(ctid)]);
               if(cvid > 0) pushEnt(ctid,ZONE_OWN_REM_DECK,Number(data.CARD_POWER[cvid - 1]),Number(ownDeckMap[String(ctid)]),false,false,false);
            }
         }
         // 8. Public Turn History (last 28 events)
         var histArr:Array = useHistory ? (observation.public_history as Array) : null;
         if(histArr != null && histArr.length > 0)
         {
            var hStart:int = histArr.length > MAX_HIST_ENTITIES ? histArr.length - MAX_HIST_ENTITIES : 0;
            var hN:int = histArr.length - hStart;
            for(var hi:int = 0; hi < hN; hi++)
            {
               if(entCount >= MAX_ENTITIES) break;
               var ev:Object = histArr[hStart + hi];
               var evKind:int = int(ev.kind);
               var isRE:Boolean = evKind == 5;
               var evSeat:int = int(ev.seat);
               var hZone:int = (evKind == 1 || evKind == 3) ? (evSeat == seat ? ZONE_HIST_OWN_PLAY : ZONE_HIST_OPP_PLAY) : ZONE_HIST_EVENT;
               var evTid:int = int(ev.template_id);
               var evVid:int = evTid != 0 ? int(vocab[String(evTid)]) : 0;
               entIds[entCount] = evVid;
               entZones[entCount] = hZone;
               var hb:int = entCount * ENTITY_FEAT_DIM;
               var recency:Number = hN <= 1 ? 1.0 : Number(hi) / Number(hN - 1);
               var sOwnH:Number;
               var sOppH:Number;
               var hOwnH:Number;
               var hOppH:Number;
               var aSign:Number;
               if(isRE)
               {
                  var s0:Number = Number(ev.score_actor);
                  var s1:Number = Number(ev.score_opp);
                  var hh0:Number = Number(ev.hand_actor);
                  var hh1:Number = Number(ev.hand_opp);
                  aSign = evSeat == seat ? 1.0 : (evSeat == oppSeat ? -1.0 : 0.0);
                  if(seat == 0) { sOwnH = s0; sOppH = s1; hOwnH = hh0; hOppH = hh1; }
                  else { sOwnH = s1; sOppH = s0; hOwnH = hh1; hOppH = hh0; }
               }
               else
               {
                  if(evSeat == seat)
                  {
                     sOwnH = Number(ev.score_actor); sOppH = Number(ev.score_opp);
                     hOwnH = Number(ev.hand_actor); hOppH = Number(ev.hand_opp);
                     aSign = 1.0;
                  }
                  else
                  {
                     sOwnH = Number(ev.score_opp); sOppH = Number(ev.score_actor);
                     hOwnH = Number(ev.hand_opp); hOppH = Number(ev.hand_actor);
                     aSign = -1.0;
                  }
               }
               entFeats[hb + 0] = evVid > 0 ? Number(data.CARD_POWER[evVid - 1]) / 15.0 : 0.0;
               entFeats[hb + 1] = recency;
               entFeats[hb + 2] = Number(ev.round_index) / 2.0;
               entFeats[hb + 3] = aSign;
               if(evKind >= 1 && evKind <= 5) entFeats[hb + 3 + evKind] = 1.0;
               entFeats[hb + 9] = clamp(sOwnH / 100.0,-2.0,2.0);
               entFeats[hb + 10] = clamp(sOppH / 100.0,-2.0,2.0);
               entFeats[hb + 11] = clamp((sOwnH - sOppH) / 100.0,-2.0,2.0);
               entFeats[hb + 12] = hOwnH / 12.0;
               entFeats[hb + 13] = hOppH / 12.0;
               entFeats[hb + 14] = ev.opp_passed ? 1.0 : 0.0;
               entFeats[hb + 15] = Number(ev.row) / 3.0;
               if(evVid > 0)
               {
                  var heffs:Array = data.CARD_EFFECTS[evVid - 1] as Array;
                  var htypes:int = int(data.CARD_TYPES[evVid - 1]);
                  var hClear:Boolean = hasEffect(heffs,30);
                  entFeats[hb + 16] = (htypes & 128) != 0 ? 1.0 : 0.0;
                  entFeats[hb + 17] = (htypes & 256) != 0 ? 1.0 : 0.0;
                  entFeats[hb + 18] = hasEffect(heffs,33) ? 1.0 : 0.0;
                  entFeats[hb + 19] = (htypes == (32 | 512) && hasEffect(heffs,26)) ? 1.0 : 0.0;
                  entFeats[hb + 20] = isScorchDef(htypes,heffs) ? 1.0 : 0.0;
                  entFeats[hb + 21] = hasEffect(heffs,31) ? 1.0 : 0.0;
                  entFeats[hb + 22] = ((htypes & 16) != 0 && !hClear) ? 1.0 : 0.0;
                  entFeats[hb + 23] = hClear ? 1.0 : 0.0;
               }
               entCount++;
            }
         }
         if(entCount == 0) entCount = 1;

         // === ENCODE GLOBAL FEATURES (64) ===
         var gFeats:Vector.<Number> = new Vector.<Number>(GLOBAL_FEAT_DIM,true);
         var ownScore:Number = Number(observation.scores[seat]);
         var oppScore:Number = Number(observation.scores[oppSeat]);
         var ownLives:int = int(observation.lives[seat]);
         var oppLives:int = int(observation.lives[oppSeat]);
         var ownPassed:Boolean = Boolean(ownP.passed);
         var oppPassed:Boolean = Boolean(oppP.passed);
         var roundIdx:int = int(observation.round) - 1;
         if(observation.round_index != null) roundIdx = int(observation.round_index);
         if(roundIdx < 0) roundIdx = 0;
         if(roundIdx > 2) roundIdx = 2;
         var roundsPlayed:int = observation.rounds_played != null ? int(observation.rounds_played) : (observation.round_results is Array ? observation.round_results.length : 0);

         gFeats[roundIdx] = 1.0;
         gFeats[3] = Number(roundsPlayed) / 3.0;
         gFeats[4] = int(observation.initiative) == seat ? 1.0 : 0.0;
         gFeats[5] = Number(ownLives) / 2.0;
         gFeats[6] = Number(oppLives) / 2.0;
         gFeats[7] = ownLives <= 1 ? 1.0 : 0.0;
         gFeats[8] = oppLives <= 1 ? 1.0 : 0.0;
         gFeats[9] = clamp(ownScore / 100.0,-2.0,2.0);
         gFeats[10] = clamp(oppScore / 100.0,-2.0,2.0);
         gFeats[11] = clamp((ownScore - oppScore) / 100.0,-2.0,2.0);
         gFeats[12] = ownScore > oppScore ? 1.0 : (ownScore < oppScore ? -1.0 : 0.0);
         for(r = 0; r < 3; r++)
         {
            gFeats[13 + r] = clamp(Number(ownRowScores[r]) / 50.0,0.0,3.0);
            gFeats[16 + r] = clamp(Number(oppRowScores[r]) / 50.0,0.0,3.0);
            gFeats[19 + r] = wFlags[r] ? 1.0 : 0.0;
            gFeats[22 + r] = ownHorns[r] ? 1.0 : 0.0;
            gFeats[25 + r] = oppHorns[r] ? 1.0 : 0.0;
         }
         var ownH:Number = Number(ownP.hand_count);
         var oppH:Number = Number(oppP.hand_count);
         gFeats[28] = ownH / 12.0;
         gFeats[29] = oppH / 12.0;
         gFeats[30] = clamp((ownH - oppH) / 10.0,-1.5,1.5);
         gFeats[31] = Number(ownP.deck_count) / 30.0;
         gFeats[32] = Number(oppP.deck_count) / 30.0;
         gFeats[33] = ownPassed ? 1.0 : 0.0;
         gFeats[34] = oppPassed ? 1.0 : 0.0;
         gFeats[35] = (ownP.leader != null && !ownP.leader_used) ? 1.0 : 0.0;
         gFeats[36] = (oppP.leader != null && !oppP.leader_used) ? 1.0 : 0.0;
         var ownFac:int = int(ownP.faction);
         var oppFac:int = int(oppP.faction);
         if(ownFac >= 0 && ownFac < 6) gFeats[37 + ownFac] = 1.0;
         if(oppFac >= 0 && oppFac < 6) gFeats[43 + oppFac] = 1.0;
         gFeats[49 + pendingIndex(observation.pending_choice)] = 1.0;
         gFeats[57] = Number(globalMaxNH) / 20.0;
         gFeats[58] = Number(ownMaxNH) / 20.0;
         gFeats[59] = Number(oppMaxNH) / 20.0;
         gFeats[60] = Number(ownAtMax) / 4.0;
         gFeats[61] = Number(oppAtMax) / 4.0;
         gFeats[62] = Number(ownP.graveyard != null ? ownP.graveyard.length : 0) / 20.0;
         gFeats[63] = Number(oppP.graveyard != null ? oppP.graveyard.length : 0) / 20.0;

         // === FORWARD PASS: EMBEDDING + TRANSFORMER TRUNK ===
         var S:int = 1 + entCount;
         var tokens:Vector.<Number> = new Vector.<Number>(S * D,true);
         var tmpD:Vector.<Number> = new Vector.<Number>(D,true);
         var tmpD2:Vector.<Number> = new Vector.<Number>(D,true);
         var isHistTok:Vector.<Boolean> = new Vector.<Boolean>(S,true);

         // Global token [0]
         linearVec(gFeats,0,tmpD,0,weights,int(off.global_proj_0_w),int(off.global_proj_0_b),GLOBAL_FEAT_DIM,D,true);
         linearVec(tmpD,0,tokens,0,weights,int(off.global_proj_2_w),int(off.global_proj_2_b),D,D,false);

         // Entity tokens [1..S-1]
         var cardEmbOff:int = int(off.card_emb);
         var zoneEmbOff:int = int(off.zone_emb);
         var efW:int = int(off.entity_feat_proj_w);
         var efB:int = int(off.entity_feat_proj_b);
         for(var ei:int = 0; ei < entCount; ei++)
         {
            var tOff:int = (1 + ei) * D;
            var vidE:int = entIds[ei];
            var zE:int = entZones[ei];
            isHistTok[1 + ei] = zE >= 21;
            linearVec(entFeats,ei * ENTITY_FEAT_DIM,tokens,tOff,weights,efW,efB,ENTITY_FEAT_DIM,D,false);
            var cEmbRow:int = cardEmbOff + vidE * D;
            var zEmbRow:int = zoneEmbOff + zE * D;
            for(j = 0; j < D; j++)
            {
               tokens[tOff + j] += weights[cEmbRow + j] + weights[zEmbRow + j];
            }
         }

         // Prepare frame-sliced execution job (returns immediately after <2ms encoding!)
         var lDis:Boolean = leadersDisabled(ownP,oppP,vocab,data);
         return {
            done:false,
            result:null,
            stage:0,
            li:0,
            si:0,
            h:0,
            ai:0,
            observation:observation,
            legalActions:legalActions,
            data:data,
            weights:weights,
            temperature:temperature,
            actionUniforms:actionUniforms,
            vocab:vocab,
            off:off,
            D:D,
            H:H,
            Dh:Dh,
            nLayers:nLayers,
            ffnDim:ffnDim,
            scale:scale,
            S:S,
            A:legalActions.length < MAX_ACTIONS ? legalActions.length : MAX_ACTIONS,
            hbOff:useHistory && off.hasOwnProperty("history_attn_bias") ? int(off.history_attn_bias) : -1,
            tokens:tokens,
            isHistTok:isHistTok,
            tmpD:tmpD,
            tmpD2:tmpD2,
            normBuf:new Vector.<Number>(S * D,true),
            qkvBuf:new Vector.<Number>(S * 3 * D,true),
            attnOut:new Vector.<Number>(S * D,true),
            attnScores:new Vector.<Number>(S,true),
            ffnBuf:new Vector.<Number>(ffnDim,true),
            kvNorm:new Vector.<Number>(D,true),
            kvBuf:new Vector.<Number>(S * 2 * D,true),
            actInVec:new Vector.<Number>(200,true),
            cardProjVec:new Vector.<Number>(64,true),
            targProjVec:new Vector.<Number>(64,true),
            actTok:new Vector.<Number>(D,true),
            qVec:new Vector.<Number>(D,true),
            xOut:new Vector.<Number>(D,true),
            polCat:new Vector.<Number>(2 * D,true),
            polNorm:new Vector.<Number>(2 * D,true),
            polOut1:new Vector.<Number>(1,true),
            logits:[],
            winProb:0.5,
            pL:0.0,
            pD:0.0,
            pW:0.0,
            instMap:instMap,
            ownBoardLiveScore:ownBoardLiveScore,
            ownRowsTids:ownRowsTids,
            oppRowsTids:oppRowsTids,
            wFlags:wFlags,
            ownModH:ownModH,
            oppModH:oppModH,
            ownHalfW:halfWeatherEnabled(ownP,lDis,vocab,data),
            oppHalfW:halfWeatherEnabled(oppP,lDis,vocab,data),
            dblSpy:doubleSpyEnabled(ownP,oppP,lDis,vocab,data),
            beforeOwn:int(ownRowScores[0]) + int(ownRowScores[1]) + int(ownRowScores[2]),
            beforeOpp:int(oppRowScores[0]) + int(oppRowScores[1]) + int(oppRowScores[2]),
            ownScore:ownScore,
            oppScore:oppScore,
            ownLives:ownLives,
            oppLives:oppLives,
            oppPassed:oppPassed,
            pendingNull:observation.pending_choice == null,
            ownWinsTie:ownFac == 2 && oppFac != 2,
            oppWinsTie:oppFac == 2 && ownFac != 2,
            ownDeckMap:ownDeckMap,
            handList:handList
         };
      }

      public static function stepEval(job:Object, budgetMs:int = 8) : Boolean
      {
         if(job == null || job.done) return true;
         var timed:Boolean = budgetMs > 0 && budgetMs < 100000;
         var startMs:int = timed ? getTimer() : 0;
         var data:Object = job.data;
         var weights:Vector.<Number> = job.weights as Vector.<Number>;
         var off:Object = job.off;
         var vocab:Object = job.vocab;
         var D:int = int(job.D);
         var H:int = int(job.H);
         var Dh:int = int(job.Dh);
         var nLayers:int = int(job.nLayers);
         var ffnDim:int = int(job.ffnDim);
         var scale:Number = Number(job.scale);
         var S:int = int(job.S);
         var A:int = int(job.A);
         var hbOff:int = int(job.hbOff);
         var tokens:Vector.<Number> = job.tokens as Vector.<Number>;
         var isHistTok:Vector.<Boolean> = job.isHistTok as Vector.<Boolean>;
         var tmpD:Vector.<Number> = job.tmpD as Vector.<Number>;
         var tmpD2:Vector.<Number> = job.tmpD2 as Vector.<Number>;
         var normBuf:Vector.<Number> = job.normBuf as Vector.<Number>;
         var qkvBuf:Vector.<Number> = job.qkvBuf as Vector.<Number>;
         var attnOut:Vector.<Number> = job.attnOut as Vector.<Number>;
         var attnScores:Vector.<Number> = job.attnScores as Vector.<Number>;
         var ffnBuf:Vector.<Number> = job.ffnBuf as Vector.<Number>;
         var kvNorm:Vector.<Number> = job.kvNorm as Vector.<Number>;
         var kvBuf:Vector.<Number> = job.kvBuf as Vector.<Number>;
         var j:int;
         var d:int;
         var sj:int;

         while(!job.done)
         {
            var stage:int = int(job.stage);
            if(stage == 0)
            {
               // Layer norm1 + QKV projection
               var pfx:String = "b" + String(job.li) + "_";
               var n1W:int = int(off[pfx + "norm1_w"]);
               var n1B:int = int(off[pfx + "norm1_b"]);
               var qkvW:int = int(off[pfx + "qkv_w"]);
               var qkvB:int = int(off[pfx + "qkv_b"]);
               while(int(job.si) < S)
               {
                  var si:int = int(job.si);
                  layerNormVec(tokens,si * D,normBuf,si * D,weights,n1W,n1B,D);
                  linearVec(normBuf,si * D,qkvBuf,si * 3 * D,weights,qkvW,qkvB,D,3 * D,false);
                  job.si = si + 1;
                  if(timed && (getTimer() - startMs) >= budgetMs) return false;
               }
               job.stage = 1;
               job.h = 0;
               job.si = 0;
            }
            else if(stage == 1)
            {
               // Multi-head self-attention
               while(int(job.h) < H)
               {
                  var h:int = int(job.h);
                  var hOff:int = h * Dh;
                  var hBias:Number = hbOff >= 0 ? weights[hbOff + h] : 0.0;
                  while(int(job.si) < S)
                  {
                     si = int(job.si);
                     var qBase:int = si * 3 * D + hOff;
                     var maxSc:Number = -1e30;
                     for(sj = 0; sj < S; sj++)
                     {
                        var kBase:int = sj * 3 * D + D + hOff;
                        var dot:Number = 0.0;
                        for(d = 0; d < Dh; d++) dot += qkvBuf[qBase + d] * qkvBuf[kBase + d];
                        var sc:Number = dot * scale + (isHistTok[sj] ? hBias : 0.0);
                        attnScores[sj] = sc;
                        if(sc > maxSc) maxSc = sc;
                     }
                     var expSum:Number = 0.0;
                     for(sj = 0; sj < S; sj++)
                     {
                        var eVal:Number = Math.exp(attnScores[sj] - maxSc);
                        attnScores[sj] = eVal;
                        expSum += eVal;
                     }
                     var invExp:Number = 1.0 / expSum;
                     var outBase:int = si * D + hOff;
                     for(d = 0; d < Dh; d++) attnOut[outBase + d] = 0.0;
                     for(sj = 0; sj < S; sj++)
                     {
                        var prob:Number = attnScores[sj] * invExp;
                        var vBase:int = sj * 3 * D + 2 * D + hOff;
                        for(d = 0; d < Dh; d++) attnOut[outBase + d] += prob * qkvBuf[vBase + d];
                     }
                     job.si = si + 1;
                     if(timed && ((si & 3) == 3) && (getTimer() - startMs) >= budgetMs) return false;
                  }
                  job.h = h + 1;
                  job.si = 0;
                  if(timed && (getTimer() - startMs) >= budgetMs) return false;
               }
               job.stage = 2;
               job.si = 0;
            }
            else if(stage == 2)
            {
               // Output projection + Residual + Norm2 + FFN + Residual
               pfx = "b" + String(job.li) + "_";
               var prjW:int = int(off[pfx + "proj_w"]);
               var prjB:int = int(off[pfx + "proj_b"]);
               var n2W:int = int(off[pfx + "norm2_w"]);
               var n2B:int = int(off[pfx + "norm2_b"]);
               var f0W:int = int(off[pfx + "ffn0_w"]);
               var f0B:int = int(off[pfx + "ffn0_b"]);
               var f2W:int = int(off[pfx + "ffn2_w"]);
               var f2B:int = int(off[pfx + "ffn2_b"]);
               while(int(job.si) < S)
               {
                  si = int(job.si);
                  var tkBase:int = si * D;
                  linearVec(attnOut,tkBase,tmpD,0,weights,prjW,prjB,D,D,false);
                  for(j = 0; j < D; j++) tokens[tkBase + j] += tmpD[j];
                  layerNormVec(tokens,tkBase,tmpD,0,weights,n2W,n2B,D);
                  linearVec(tmpD,0,ffnBuf,0,weights,f0W,f0B,D,ffnDim,true);
                  linearVec(ffnBuf,0,tmpD2,0,weights,f2W,f2B,ffnDim,D,false);
                  for(j = 0; j < D; j++) tokens[tkBase + j] += tmpD2[j];
                  job.si = si + 1;
                  if(timed && (getTimer() - startMs) >= budgetMs) return false;
               }
               job.li = int(job.li) + 1;
               job.si = 0;
               job.stage = int(job.li) < nLayers ? 0 : 3;
            }
            else if(stage == 3)
            {
               // Final norm + ActionCrossAttention KV precompute + Fair value head
               var fnW:int = int(off.final_norm_w);
               var fnB:int = int(off.final_norm_b);
               var actNkvW:int = int(off.act_norm_kv_w);
               var actNkvB:int = int(off.act_norm_kv_b);
               var actKvW:int = int(off.act_kv_w);
               var actKvB:int = int(off.act_kv_b);
               while(int(job.si) < S)
               {
                  si = int(job.si);
                  layerNormVec(tokens,si * D,normBuf,si * D,weights,fnW,fnB,D);
                  layerNormVec(normBuf,si * D,kvNorm,0,weights,actNkvW,actNkvB,D);
                  linearVec(kvNorm,0,kvBuf,si * 2 * D,weights,actKvW,actKvB,D,2 * D,false);
                  job.si = si + 1;
                  if(timed && (getTimer() - startMs) >= budgetMs) return false;
               }
               // Fair value head -> [loss, draw, win]
               layerNormVec(normBuf,0,tmpD,0,weights,int(off.val_norm_w),int(off.val_norm_b),D);
               linearVec(tmpD,0,tmpD2,0,weights,int(off.val_l1_w),int(off.val_l1_b),D,D,true);
               var valLogits:Vector.<Number> = new Vector.<Number>(3,true);
               linearVec(tmpD2,0,valLogits,0,weights,int(off.val_l2_w),int(off.val_l2_b),D,3,false);
               var vMax:Number = valLogits[0];
               if(valLogits[1] > vMax) vMax = valLogits[1];
               if(valLogits[2] > vMax) vMax = valLogits[2];
               var e0:Number = Math.exp(valLogits[0] - vMax);
               var e1:Number = Math.exp(valLogits[1] - vMax);
               var e2:Number = Math.exp(valLogits[2] - vMax);
               var vSum:Number = e0 + e1 + e2;
               job.pL = e0 / vSum;
               job.pD = e1 / vSum;
               job.pW = e2 / vSum;
               job.winProb = Number(job.pW) + 0.5 * Number(job.pD);
               job.stage = 4;
               job.ai = 0;
               if(timed && (getTimer() - startMs) >= budgetMs) return false;
            }
            else if(stage == 4)
            {
               // Score legal actions one by one
               var legalActions:Array = job.legalActions as Array;
               var actInVec:Vector.<Number> = job.actInVec as Vector.<Number>;
               var cardProjVec:Vector.<Number> = job.cardProjVec as Vector.<Number>;
               var targProjVec:Vector.<Number> = job.targProjVec as Vector.<Number>;
               var actTok:Vector.<Number> = job.actTok as Vector.<Number>;
               var qVec:Vector.<Number> = job.qVec as Vector.<Number>;
               var xOut:Vector.<Number> = job.xOut as Vector.<Number>;
               var polCat:Vector.<Number> = job.polCat as Vector.<Number>;
               var polNorm:Vector.<Number> = job.polNorm as Vector.<Number>;
               var polOut1:Vector.<Number> = job.polOut1 as Vector.<Number>;
               var logits:Array = job.logits as Array;
               var cardEmbOff:int = int(off.card_emb);
               var kindEmbOff:int = int(off.kind_emb);
               var rowEmbOff:int = int(off.row_emb);
               var acpW:int = int(off.action_card_proj_w);
               var acpB:int = int(off.action_card_proj_b);
               var instMap:Object = job.instMap;
               var ownBoardLiveScore:Object = job.ownBoardLiveScore;
               var ownDeckMap:Object = job.ownDeckMap;
               var handList:Array = job.handList as Array;
               var ownScore:Number = Number(job.ownScore);
               var oppScore:Number = Number(job.oppScore);
               var ownLives:int = int(job.ownLives);
               var oppLives:int = int(job.oppLives);
               var oppPassed:Boolean = Boolean(job.oppPassed);
               var ownWinsTie:Boolean = Boolean(job.ownWinsTie);
               var oppWinsTie:Boolean = Boolean(job.oppWinsTie);

               while(int(job.ai) < A)
               {
                  var ai:int = int(job.ai);
                  var act:Object = legalActions[ai];
                  var aKind:int = actionKindId(String(act.kind));
                  var aCardTid:int = -1;
                  if(act.template_id != null) aCardTid = int(act.template_id);
                  else if(act.instance_id != null && instMap.hasOwnProperty(String(act.instance_id))) aCardTid = int(instMap[String(act.instance_id)]);
                  else if(act.instanceId != null && instMap.hasOwnProperty(String(act.instanceId))) aCardTid = int(instMap[String(act.instanceId)]);

                  var aTargId:Object = act.target_instance_id != null ? act.target_instance_id : act.targetInstanceId;
                  var aTargTid:int = (aTargId != null && instMap.hasOwnProperty(String(aTargId))) ? int(instMap[String(aTargId)]) : -1;

                  var aCardVid:int = aCardTid >= 0 && vocab.hasOwnProperty(String(aCardTid)) ? int(vocab[String(aCardTid)]) : 0;
                  var aTargVid:int = aTargTid >= 0 && vocab.hasOwnProperty(String(aTargTid)) ? int(vocab[String(aTargTid)]) : 0;
                  var aRowId:int = rowIdFromName(act.row);
                  var cDef:Object = cardDefByVocab(aCardVid,data);
                  var tDef:Object = cardDefByVocab(aTargVid,data);

                  var af:Vector.<Number> = new Vector.<Number>(ACTION_FEAT_DIM,true);
                  if(aKind >= 0 && aKind < 6) af[aKind] = 1.0;

                  var ownDelta:Number = 0.0;
                  var oppDelta:Number = 0.0;
                  var proj:Array = projectActionDelta(
                     act,cDef,job.ownRowsTids as Array,job.oppRowsTids as Array,job.wFlags as Array,
                     job.ownModH as Array,job.oppModH as Array,Boolean(job.ownHalfW),Boolean(job.oppHalfW),
                     Boolean(job.dblSpy),int(job.beforeOwn),int(job.beforeOpp),Boolean(job.pendingNull),vocab,data
                  );
                  if(proj != null)
                  {
                     ownDelta = Number(proj[0]);
                     oppDelta = Number(proj[1]);
                  }
                  else if(cDef != null)
                  {
                     var cTypesA:int = int(cDef.types);
                     var cEffsA:Array = cDef.effects as Array;
                     if(act.kind == "play" && (cTypesA & 256) != 0)
                     {
                        oppDelta = Number(cDef.power);
                     }
                     else if(act.kind == "play" && cTypesA == (32 | 512) && hasEffect(cEffsA,26) && aTargId != null)
                     {
                        if(ownBoardLiveScore.hasOwnProperty(String(aTargId))) ownDelta = -Number(ownBoardLiveScore[String(aTargId)]);
                     }
                  }

                  var netSwing:Number = ownDelta - oppDelta;
                  var ownAfter:Number = ownScore + ownDelta;
                  var oppAfter:Number = oppScore + oppDelta;
                  af[6] = clamp(ownDelta / 30.0,-3.0,3.0);
                  af[7] = clamp(oppDelta / 30.0,-3.0,3.0);
                  af[8] = clamp(netSwing / 30.0,-3.0,3.0);
                  af[9] = ownAfter > oppAfter ? 1.0 : 0.0;
                  af[10] = Math.abs(ownAfter - oppAfter) < 0.5 ? 1.0 : 0.0;
                  var winsRN:Boolean = ownAfter > oppAfter || (Math.abs(ownAfter - oppAfter) < 0.5 && ownWinsTie);
                  var tiesRN:Boolean = Math.abs(ownAfter - oppAfter) < 0.5 && !ownWinsTie && !oppWinsTie;
                  af[11] = (oppPassed && winsRN) ? 1.0 : 0.0;
                  af[12] = (oppPassed && oppLives <= 1 && (winsRN || (tiesRN && ownLives > 1))) ? 1.0 : 0.0;

                  if(cDef != null)
                  {
                     var ctA:int = int(cDef.types);
                     var ceA:Array = cDef.effects as Array;
                     var isScA:Boolean = isScorchDef(ctA,ceA);
                     af[13] = (isScA && ownDelta < -0.5 && oppDelta > -0.5) ? 1.0 : 0.0;
                     af[14] = (hasEffect(ceA,30) && oppDelta > ownDelta + 0.5) ? 1.0 : 0.0;
                     af[15] = Number(cDef.power) / 15.0;
                     af[17] = (ctA & 128) != 0 ? 1.0 : 0.0;
                     af[18] = (ctA & 256) != 0 ? 1.0 : 0.0;
                     af[19] = hasEffect(ceA,33) ? 1.0 : 0.0;
                     af[20] = (ctA == (32 | 512) && hasEffect(ceA,26)) ? 1.0 : 0.0;
                     af[21] = isScA ? 1.0 : 0.0;
                     af[22] = hasEffect(ceA,31) ? 1.0 : 0.0;
                     if(hasEffect(ceA,31) && cDef.summons != null && (cDef.summons as Array).length > 0)
                     {
                        var sums:Array = cDef.summons as Array;
                        var pulls:int = 0;
                        for(j = 0; j < sums.length; j++)
                        {
                           var sk:String = String(sums[j]);
                           if(ownDeckMap.hasOwnProperty(sk)) pulls += int(ownDeckMap[sk]);
                        }
                        if(handList != null)
                        {
                           var aInstId:Object = act.instance_id != null ? act.instance_id : act.instanceId;
                           for(j = 0; j < handList.length; j++)
                           {
                              if(aInstId != null && handList[j].instance_id == aInstId) continue;
                              if(sums.indexOf(int(handList[j].template_id)) >= 0) pulls++;
                           }
                        }
                        af[23] = Number(pulls) / 4.0;
                     }
                  }
                  if(tDef != null) af[16] = Number(tDef.power) / 15.0;

                  var kClamped:int = aKind < 0 ? 0 : (aKind > 7 ? 7 : aKind);
                  var rClamped:int = aRowId < 0 ? 0 : (aRowId > 3 ? 3 : aRowId);
                  for(j = 0; j < 32; j++) actInVec[j] = weights[kindEmbOff + kClamped * 32 + j];
                  linearVec(weights,cardEmbOff + aCardVid * D,cardProjVec,0,weights,acpW,acpB,D,64,false);
                  linearVec(weights,cardEmbOff + aTargVid * D,targProjVec,0,weights,acpW,acpB,D,64,false);
                  for(j = 0; j < 64; j++)
                  {
                     actInVec[32 + j] = cardProjVec[j];
                     actInVec[96 + j] = targProjVec[j];
                  }
                  for(j = 0; j < 16; j++) actInVec[160 + j] = weights[rowEmbOff + rClamped * 16 + j];
                  for(j = 0; j < 24; j++) actInVec[176 + j] = af[j];

                  linearVec(actInVec,0,tmpD,0,weights,int(off.action_in_0_w),int(off.action_in_0_b),200,D,true);
                  linearVec(tmpD,0,actTok,0,weights,int(off.action_in_2_w),int(off.action_in_2_b),D,D,false);

                  layerNormVec(actTok,0,tmpD,0,weights,int(off.act_norm_q_w),int(off.act_norm_q_b),D);
                  linearVec(tmpD,0,qVec,0,weights,int(off.act_q_w),int(off.act_q_b),D,D,false);
                  for(h = 0; h < H; h++)
                  {
                     hOff = h * Dh;
                     hBias = hbOff >= 0 ? weights[hbOff + h] : 0.0;
                     maxSc = -1e30;
                     for(sj = 0; sj < S; sj++)
                     {
                        kBase = sj * 2 * D + hOff;
                        dot = 0.0;
                        for(d = 0; d < Dh; d++) dot += qVec[hOff + d] * kvBuf[kBase + d];
                        sc = dot * scale + (isHistTok[sj] ? hBias : 0.0);
                        attnScores[sj] = sc;
                        if(sc > maxSc) maxSc = sc;
                     }
                     expSum = 0.0;
                     for(sj = 0; sj < S; sj++)
                     {
                        eVal = Math.exp(attnScores[sj] - maxSc);
                        attnScores[sj] = eVal;
                        expSum += eVal;
                     }
                     invExp = 1.0 / expSum;
                     for(d = 0; d < Dh; d++) xOut[hOff + d] = 0.0;
                     for(sj = 0; sj < S; sj++)
                     {
                        prob = attnScores[sj] * invExp;
                        vBase = sj * 2 * D + D + hOff;
                        for(d = 0; d < Dh; d++) xOut[hOff + d] += prob * kvBuf[vBase + d];
                     }
                  }
                  linearVec(xOut,0,tmpD,0,weights,int(off.act_out_w),int(off.act_out_b),D,D,false);
                  for(j = 0; j < D; j++) actTok[j] += tmpD[j];

                  for(j = 0; j < D; j++)
                  {
                     polCat[j] = normBuf[j];
                     polCat[D + j] = actTok[j];
                  }
                  layerNormVec(polCat,0,polNorm,0,weights,int(off.policy_norm_w),int(off.policy_norm_b),2 * D);
                  linearVec(polNorm,0,tmpD,0,weights,int(off.policy_l1_w),int(off.policy_l1_b),2 * D,D,true);
                  linearVec(tmpD,0,polOut1,0,weights,int(off.policy_l2_w),int(off.policy_l2_b),D,1,false);
                  logits.push(polOut1[0]);

                  job.ai = ai + 1;
                  if(timed && (getTimer() - startMs) >= budgetMs) return false;
               }
               job.stage = 5;
            }
            else
            {
               // Stage 5: Action selection
               var selLogits:Array = job.logits as Array;
               var temp:Number = Number(job.temperature);
               var actionUniforms:Array = job.actionUniforms as Array;
               var bestIdx:int = 0;
               if(temp <= 0.0 || A <= 1)
               {
                  var bestL:Number = Number(selLogits[0]);
                  for(ai = 1; ai < A; ai++)
                  {
                     if(Number(selLogits[ai]) > bestL)
                     {
                        bestL = Number(selLogits[ai]);
                        bestIdx = ai;
                     }
                  }
               }
               else
               {
                  var maxL:Number = Number(selLogits[0]);
                  for(ai = 1; ai < A; ai++) if(Number(selLogits[ai]) > maxL) maxL = Number(selLogits[ai]);
                  var probs:Array = [];
                  var pSum:Number = 0.0;
                  for(ai = 0; ai < A; ai++)
                  {
                     var pe:Number = Math.exp((Number(selLogits[ai]) - maxL) / temp);
                     probs.push(pe);
                     pSum += pe;
                  }
                  var u:Number = (actionUniforms != null && actionUniforms.length > 0) ? Number(actionUniforms[0]) : Math.random();
                  var target:Number = u * pSum;
                  var acc:Number = 0.0;
                  bestIdx = A - 1;
                  for(ai = 0; ai < A; ai++)
                  {
                     acc += Number(probs[ai]);
                     if(acc >= target) { bestIdx = ai; break; }
                  }
               }
               job.result = {
                  chosen_index:bestIdx,
                  chosen_action:(job.legalActions as Array)[bestIdx],
                  value:Number(job.winProb),
                  wdl:[Number(job.pL),Number(job.pD),Number(job.pW)],
                  logits:selLogits
               };
               job.done = true;
               return true;
            }
         }
         return true;
      }
   }
