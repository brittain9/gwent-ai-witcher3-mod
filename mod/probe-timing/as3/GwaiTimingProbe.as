internal class GwaiTimingProbe
{
   private static const STUDENT_WIDTHS:Array = [192,128,96,64];
   private static const MAX_DECISION_RECORDS:int = 255;
   private static var _models:Object = {};
   private static var _samples:Object = null;
   private static var _records:Array = [];
   private static var _discardWidthSample:Object = {};
   private static var _decisionCount:int = 0;
   private static var _omittedDecisionCount:int = 0;
   private static var _finished:Boolean = false;
   private static var _fullWidth:int = 0;
   private static var _blocks:int = 0;
   private static var _studentModelBuildMs:Number = 0;
   private static var _studentModelWarmMs:Number = 0;
   private static var _setupError:String = "";
   private static var _randomState:uint = 0x4c6f6f70;

   public static function beginMatch() : void
   {
      var buildStarted:Number = 0;
      var warmStarted:Number = 0;
      var difficulty:int = 0;
      var width:int = 0;
      var data:Object = null;
      var student:Object = null;
      var modelKey:String = "";
      var warmedModels:Object = {};
      var warmObservation:Object = null;
      var warmActions:Array = null;
      var ignored:Object = null;
      var setupPhase:String = "build";
      emitBegin();
      _samples = {encoder:[],full:[],"192":[],"128":[],"96":[],"64":[]};
      _records = [];
      _discardWidthSample = {"192":true,"128":true,"96":true,"64":true};
      _decisionCount = 0;
      _omittedDecisionCount = 0;
      _finished = false;
      _fullWidth = 0;
      _blocks = 0;
      _studentModelBuildMs = 0;
      _studentModelWarmMs = 0;
      _setupError = "";
      _models = {};
      _randomState = 0x4c6f6f70;

      try
      {
         buildStarted = Number(getTimer());
         for(difficulty = 1; difficulty <= 3; difficulty++)
         {
            data = GwaiNetTierConfig.dataFor(difficulty);
            for each(width in STUDENT_WIDTHS)
               modelFor(width,int(data.OBSERVATION_SIZE),int(data.CARD_COUNT),int(data.BLOCKS));
         }
         _studentModelBuildMs = Number(getTimer()) - buildStarted;

         warmObservation = warmupObservation();
         warmActions = [{id:"pass",kind:"pass"}];
         setupPhase = "warm";
         warmStarted = Number(getTimer());
         for(difficulty = 1; difficulty <= 3; difficulty++)
         {
            data = GwaiNetTierConfig.dataFor(difficulty);
            for each(width in STUDENT_WIDTHS)
            {
               modelKey = width + ":" + int(data.OBSERVATION_SIZE) + ":"
                  + int(data.CARD_COUNT) + ":" + int(data.BLOCKS);
               if(Boolean(warmedModels[modelKey])) continue;
               warmedModels[modelKey] = true;
               student = modelFor(width,int(data.OBSERVATION_SIZE),int(data.CARD_COUNT),int(data.BLOCKS));
               ignored = GwaiNetPolicy.evaluate(
                  warmObservation,warmActions,data.CARD_IDS,data.CARD_EFFECTS,
                  student.weights,student.offsets,width,int(data.BLOCKS),0.0);
            }
         }
         _studentModelWarmMs = Number(getTimer()) - warmStarted;
      }
      catch(error:Error)
      {
         _setupError = error.toString();
         if(setupPhase == "build")
            _studentModelBuildMs = Number(getTimer()) - buildStarted;
         else
            _studentModelWarmMs = Number(getTimer()) - warmStarted;
         _records.push({schema:"w3-gwent-timing-probe-v1",event:"setup_error",reason:_setupError});
      }
   }

   public static function measure(observation:Object, legalActions:Array, data:Object,
                                 weights:Vector.<Number>, fullElapsed:Number) : void
   {
      var encoderStarted:Number = 0;
      var encoderElapsed:Number = 0;
      var student:Object = null;
      var ignored:Object = null;
      var width:int = 0;
      var widthStarted:Number = 0;
      var widthElapsed:Number = 0;
      var widthTimes:Object = {};
      var discardedWidths:Array = [];
      var record:Object = null;
      try
      {
         if(_samples == null) beginMatch();
         if(_setupError != "") return;
         if(_records.length >= MAX_DECISION_RECORDS)
         {
            _omittedDecisionCount++;
            return;
         }
         _fullWidth = int(data.WIDTH);
         _blocks = int(data.BLOCKS);

         encoderStarted = Number(getTimer());
         GwaiNetPolicy.encodeObservation(observation,data.CARD_IDS,data.CARD_EFFECTS);
         encoderElapsed = Number(getTimer()) - encoderStarted;
         _samples.encoder.push(encoderElapsed);
         _samples.full.push(fullElapsed);

         for each(width in STUDENT_WIDTHS)
         {
            student = modelFor(width,int(data.OBSERVATION_SIZE),int(data.CARD_COUNT),_blocks);
            widthStarted = Number(getTimer());
            ignored = GwaiNetPolicy.evaluate(
               observation,legalActions,data.CARD_IDS,data.CARD_EFFECTS,
               student.weights,student.offsets,width,_blocks,0.0);
            widthElapsed = Number(getTimer()) - widthStarted;
            widthTimes[String(width)] = widthElapsed;
            if(Boolean(_discardWidthSample[String(width)]))
            {
               _discardWidthSample[String(width)] = false;
               discardedWidths.push(width);
            }
            else
               _samples[String(width)].push(widthElapsed);
         }

         record = {
            schema:"w3-gwent-timing-probe-v1",
            event:"decision",
            decision_index:_decisionCount + 1,
            legal_action_count:legalActions.length,
            checkpoint_sha256:String(data.CHECKPOINT_SHA256),
            full_width:_fullWidth,
            blocks:_blocks,
            timer_resolution_ms:1,
            discarded_width_samples:discardedWidths,
            times_ms:{encoder:encoderElapsed,full:fullElapsed,
               "192":widthTimes["192"],"128":widthTimes["128"],
               "96":widthTimes["96"],"64":widthTimes["64"]}
         };
         _records.push(record);
         _decisionCount++;
      }
      catch(error:Error)
      {
         if(_records.length < MAX_DECISION_RECORDS)
            _records.push({schema:"w3-gwent-timing-probe-v1",event:"probe_error",
               decision_index:_decisionCount + 1,reason:error.toString()});
      }
   }

   public static function finish() : void
   {
      var metrics:Object = {};
      var summary:Object = null;
      var record:Object = null;
      var line:String = "";
      var index:int = 0;
      var flushed:Boolean = false;
      if(_finished || _samples == null) return;
      try
      {
         _finished = true;
         metrics.encoder = metric(_samples.encoder);
         metrics.full = metric(_samples.full);
         metrics.full.width = _fullWidth;
         metrics.full.weights = "checkpoint";
         metrics["192"] = metric(_samples["192"]);
         metrics["128"] = metric(_samples["128"]);
         metrics["96"] = metric(_samples["96"]);
         metrics["64"] = metric(_samples["64"]);
         metrics["192"].weights = "random_student";
         metrics["128"].weights = "random_student";
         metrics["96"].weights = "random_student";
         metrics["64"].weights = "random_student";
         summary = {schema:"w3-gwent-timing-probe-v1",event:"summary",
            decisions:_decisionCount,record_count:_records.length + 1,
            omitted_decisions:_omittedDecisionCount,setup_error:_setupError,
            record_cap:MAX_DECISION_RECORDS + 1,full_width:_fullWidth,blocks:_blocks,
            student_model_build_ms:_studentModelBuildMs,
            student_model_warm_ms:_studentModelWarmMs,
            timer_resolution_ms:1,metrics_ms:metrics};
         for(index = 0; index < _records.length; index++)
         {
            record = _records[index];
            emitRecord(record);
         }
         emitFinish(JSON.stringify(summary));
         flushed = true;
         line = "#W3AI# timing summary decisions=" + _decisionCount
            + " build=" + _studentModelBuildMs + "ms warm=" + _studentModelWarmMs + "ms"
            + " encoder=" + metricLabel(metrics.encoder)
            + " full-" + _fullWidth + "=" + metricLabel(metrics.full)
            + " 192=" + metricLabel(metrics["192"])
            + " 128=" + metricLabel(metrics["128"])
            + " 96=" + metricLabel(metrics["96"])
            + " 64=" + metricLabel(metrics["64"]);
         CardManager.log(line);
      }
      catch(error:Error)
      {
         if(!flushed)
         {
            for(index = 0; index < _records.length; index++)
            {
               record = _records[index];
               emitRecord(record);
            }
            emitFinish(JSON.stringify({schema:"w3-gwent-timing-probe-v1",
               event:"summary_error",decisions:_decisionCount,record_count:_records.length + 1,
               record_cap:MAX_DECISION_RECORDS + 1,reason:error.toString()}));
         }
      }
   }

   private static function metric(values:Array) : Object
   {
      var sorted:Array = values.concat();
      var sum:Number = 0;
      var index:int = 0;
      var median:Number = 0;
      var upper:int = 0;
      var p95Index:int = 0;
      sorted.sort(Array.NUMERIC);
      if(sorted.length == 0) return {samples:0,mean:null,median:null,p95:null};
      for(index = 0; index < sorted.length; index++) sum += Number(sorted[index]);
      upper = int(sorted.length / 2);
      median = sorted.length % 2 == 0
         ? (Number(sorted[upper - 1]) + Number(sorted[upper])) / 2.0
         : Number(sorted[upper]);
      p95Index = Math.max(0,Math.ceil(0.95 * sorted.length) - 1);
      return {samples:sorted.length,mean:sum / sorted.length,median:median,p95:Number(sorted[p95Index])};
   }

   private static function metricLabel(value:Object) : String
   {
      return String(value.mean) + "/" + String(value.median) + "/" + String(value.p95)
         + "ms n=" + String(value.samples);
   }

   private static function warmupObservation() : Object
   {
      var rows:Object = {melee:{cards:[]},ranged:{cards:[]},siege:{cards:[]}};
      var modifiers:Object = {melee:null,ranged:null,siege:null};
      var playerZero:Object = {
         seat:0,rows:rows,graveyard:[],leader:null,modifiers:modifiers,
         faction:0,hand_count:0,deck_count:0,passed:false,leader_used:false
      };
      var playerOne:Object = {
         seat:1,rows:rows,graveyard:[],leader:null,modifiers:modifiers,
         faction:0,hand_count:0,deck_count:0,passed:false,leader_used:false
      };
      return {
         seat:0,hand:[],weather:[],players:[playerZero,playerOne],initiative:0,
         round:1,rounds_played:0,scores:[0,0],lives:[2,2],round_results:[],
         terminal:false,decision_id:0,pending_choice:null
      };
   }

   private static function modelFor(width:int, observationSize:int, cardCount:int, blocks:int) : Object
   {
      var key:String = width + ":" + observationSize + ":" + cardCount + ":" + blocks;
      var model:Object = _models[key];
      if(model != null) return model;
      var count:int = width * observationSize + 3 * width;
      count += blocks * (2 * width * width + 4 * width);
      count += 6 * 24 + (cardCount + 1) * 48 + 4 * 8;
      count += width * 176 + width + width * 72 + width;
      count += 2 * (256 * width + 257) + (128 * width + 515);
      model = {weights:new Vector.<Number>(count,true),offsets:{},cursor:0};

      addLinear(model,"input",width,observationSize);
      addNorm(model,"input_norm",width);
      for(var block:int = 0; block < blocks; block++)
      {
         var prefix:String = "blocks." + block + ".";
         addNorm(model,prefix + "norm",width);
         addLinear(model,prefix + "first",width,width);
         addLinear(model,prefix + "second",width,width);
      }
      addEmbedding(model,"kind_embedding.weight",6 * 24);
      addEmbedding(model,"card_embedding.weight",(cardCount + 1) * 48);
      addEmbedding(model,"row_embedding.weight",4 * 8);
      addLinear(model,"action_projection",width,176);
      addLinear(model,"group_projection",width,72);
      addLinear(model,"local_actor.0",128,2 * width);
      addLinear(model,"local_actor.2",1,128);
      addLinear(model,"group_actor.0",128,2 * width);
      addLinear(model,"group_actor.2",1,128);
      addLinear(model,"value_head.0",128,width);
      addLinear(model,"value_head.2",3,128);
      if(int(model.cursor) != count) throw new Error("student weight layout size mismatch");
      _models[key] = model;
      return model;
   }

   private static function addLinear(model:Object, name:String, outputSize:int, inputSize:int) : void
   {
      var scale:Number = 1.0 / Math.sqrt(inputSize);
      addRandom(model,name + ".weight",outputSize * inputSize,scale);
      addRandom(model,name + ".bias",outputSize,scale);
   }

   private static function addNorm(model:Object, name:String, width:int) : void
   {
      addConstant(model,name + ".weight",width,1.0);
      addConstant(model,name + ".bias",width,0.0);
   }

   private static function addEmbedding(model:Object, name:String, count:int) : void
   {
      addRandom(model,name,count,0.05);
   }

   private static function addRandom(model:Object, name:String, count:int, scale:Number) : void
   {
      var index:int = 0;
      model.offsets[name] = int(model.cursor);
      for(index = 0; index < count; index++)
         model.weights[int(model.cursor++)] = (nextUnit() * 2.0 - 1.0) * scale;
   }

   private static function addConstant(model:Object, name:String, count:int, value:Number) : void
   {
      var index:int = 0;
      model.offsets[name] = int(model.cursor);
      for(index = 0; index < count; index++) model.weights[int(model.cursor++)] = value;
   }

   private static function nextUnit() : Number
   {
      _randomState ^= _randomState << 13;
      _randomState ^= _randomState >>> 17;
      _randomState ^= _randomState << 5;
      return Number(_randomState) / 4294967296.0;
   }

   private static function emitBegin() : void
   {
      try
      {
         if(GwintGameMenu.mSingleton != null)
            GwintGameMenu.mSingleton.dispatchEvent(
               new GameEvent(GameEvent.CALL,"OnW3GwentTimingBegin",[]));
      }
      catch(error:Error) {}
   }

   private static function emitRecord(record:Object) : void
   {
      try
      {
         if(GwintGameMenu.mSingleton != null)
            GwintGameMenu.mSingleton.dispatchEvent(
               new GameEvent(GameEvent.CALL,"OnW3GwentTimingRecord",[JSON.stringify(record)]));
      }
      catch(error:Error) {}
   }

   private static function emitFinish(summary:String) : void
   {
      try
      {
         if(GwintGameMenu.mSingleton != null)
            GwintGameMenu.mSingleton.dispatchEvent(
               new GameEvent(GameEvent.CALL,"OnW3GwentTimingFinish",[summary]));
      }
      catch(error:Error) {}
   }
}
