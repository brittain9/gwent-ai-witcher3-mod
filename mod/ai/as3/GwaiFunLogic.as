internal class GwaiFunLogic
{
   private static const STAT_PREFIXES:Array = ["vanilla","easy","normal","hard","deck"];
   private static const OUTCOMES:Array = ["won","lost","drawn"];

   // Coach and story are player settings. The meter, the post-game card, the blunder alert and the
   // match stats are analysis tools: each is on only while Lab mode is on too.
   public static function featureFlags(data:Object) : Object
   {
      var lab:Boolean = Boolean(data.lab);
      var flags:Object = {
         lab:lab,coach:Boolean(data.coach),story:Boolean(data.story),
         meter:lab && Boolean(data.meter),postGame:lab && Boolean(data.postGame),
         blunder:lab && Boolean(data.blunder),stats:lab && Boolean(data.stats),
         deck:data.opponentDeck == undefined ? "" : String(data.opponentDeck),
         counts:{},storyTier:0
      };
      for(var prefixIndex:int = 0; prefixIndex < STAT_PREFIXES.length; prefixIndex++)
      {
         for(var outcomeIndex:int = 0; outcomeIndex < OUTCOMES.length; outcomeIndex++)
         {
            var key:String = String(STAT_PREFIXES[prefixIndex]) + "_" + String(OUTCOMES[outcomeIndex]);
            var packetKey:String = "stats_" + key;
            flags.counts[key] = Math.max(0,int(data[packetKey]));
         }
      }
      return flags;
   }

   // The game's Gwent difficulty (Easy for 0), raised to the opponent's story
   // tier when the story setting found one. The story never lowers a tier.
   public static function tierFor(vanillaDifficulty:int, storyTier:int) : int
   {
      var tier:int = vanillaDifficulty <= 1 ? 1 : (vanillaDifficulty >= 3 ? 3 : vanillaDifficulty);
      if(storyTier > tier && storyTier <= 3) return storyTier;
      return tier;
   }

   public static function percent(probability:Number) : String
   {
      if(isNaN(probability)) return "?";
      return String(Math.round(Math.max(0,Math.min(1,probability)) * 100)) + "%";
   }

   public static function moveLabel(kind:String, title:String, row:String, targetTitle:String, theirSide:Boolean) : String
   {
      if(kind == "pass") return "pass";
      if(kind == "leader") return "your leader ability";
      var label:String = title == null || title == "" ? "a card" : title;
      if(targetTitle != null && targetTitle != "") return label + " on " + targetTitle;
      if(row != null && row != "") return label + (theirSide ? " to their " : " to ") + row;
      return label;
   }

   public static function coachLine(pickLabel:String, winChance:Number) : String
   {
      var line:String = "Coach: " + pickLabel;
      if(!isNaN(winChance)) line += " (your win chance " + percent(winChance) + ")";
      return line;
   }

   public static function meterSuffix(winChance:Number) : String
   {
      if(isNaN(winChance)) return "";
      return "; AI win chance " + percent(winChance);
   }

   public static function blunderLine(drop:Number, actualLabel:String, pickLabel:String) : String
   {
      return "Coach: since your last move (" + actualLabel + "), your win chance fell about "
         + String(Math.round(drop * 100)) + " points. The network's pick was " + pickLabel + ".";
   }

   public static function isBlunder(record:Object, minimumDrop:Number) : Boolean
   {
      if(record == null || record.agreed === true || record.atResult === true) return false;
      var drop:Number = Number(record.before) - Number(record.after);
      return !isNaN(drop) && drop >= minimumDrop;
   }

   // Did the human's card land where the network's pick would have put it? A Decoy's target is
   // back in the hand; an instant effect has no slot to compare.
   public static function samePlacement(pick:Object, inList:int, listsPlayer:int, hand:Array,
                                        invalidSlot:int, invalidPlayer:int) : Boolean
   {
      if(pick == null) return false;
      if(pick.targetInstanceId != undefined) return hand.indexOf(int(pick.targetInstanceId)) >= 0;
      if(pick.slotId == undefined || int(pick.slotId) == invalidSlot) return true;
      if(inList != int(pick.slotId)) return false;
      if(pick.targetPlayer == undefined || int(pick.targetPlayer) == invalidPlayer) return true;
      return int(pick.targetPlayer) == listsPlayer;
   }

   public static function costliest(records:Array, minimumDrop:Number) : int
   {
      var best:int = -1;
      var bestDrop:Number = minimumDrop;
      for(var index:int = 0; index < records.length; index++)
      {
         var record:Object = records[index];
         if(record == null || record.agreed === true) continue;
         var drop:Number = Number(record.before) - Number(record.after);
         if(isNaN(drop)) continue;
         if(drop > bestDrop)
         {
            best = index;
            bestDrop = drop;
         }
      }
      return best;
   }

   public static function agreement(records:Array) : Array
   {
      var agreed:int = 0;
      var total:int = 0;
      for(var index:int = 0; index < records.length; index++)
      {
         var record:Object = records[index];
         if(record == null || record.actualLabel == null || record.actualLabel == "") continue;
         total++;
         if(record.agreed === true) agreed++;
      }
      return [agreed,total];
   }

   public static function postGameLines(records:Array, minimumDrop:Number) : Array
   {
      var counts:Array = agreement(records);
      var summary:String = "You matched the network's pick on " + counts[0] + " of " + counts[1] + " moves.";
      var index:int = costliest(records,minimumDrop);
      if(index < 0) return ["Post-game: no costly move found. " + summary];
      var record:Object = records[index];
      return [
         "Costliest move: round " + record.round + ", " + record.actualLabel
            + " (your win chance " + percent(Number(record.before)) + " before it, "
            + percent(Number(record.after)) + (record.atResult === true ? " at the result)." : " at your next turn)."),
         "The network's pick was " + record.pickLabel + ". " + summary
      ];
   }

   public static function outcomeName(humanWon:Boolean, aiWon:Boolean) : String
   {
      if(humanWon) return "won";
      if(aiWon) return "lost";
      return "drawn";
   }

   public static function tierBucket(enabled:Boolean, tier:int) : String
   {
      if(!enabled) return "vanilla";
      if(tier == 1) return "easy";
      if(tier == 2) return "normal";
      if(tier == 3) return "hard";
      throw new Error("difficulty outside 1..3");
   }

   public static function bucketLabel(bucket:String) : String
   {
      if(bucket == "vanilla") return "the vanilla AI";
      return bucket.charAt(0).toUpperCase() + bucket.substr(1);
   }

   public static function safeKey(value:String) : String
   {
      var result:String = "";
      if(value == null) return result;
      for(var index:int = 0; index < value.length; index++)
      {
         var code:Number = value.charCodeAt(index);
         if((code >= 48 && code <= 57) || (code >= 65 && code <= 90) || (code >= 97 && code <= 122))
            result += value.charAt(index);
      }
      return result;
   }

   // Per-opponent stats only for names that are their own settings key (all vanilla decks are).
   public static function statsDeckKey(deck:String) : String
   {
      if(deck == null || deck == "" || safeKey(deck) != deck) return "";
      return deck;
   }

   // A user.settings line is key=value: a value keeps no '=' and no line break.
   public static function settingsText(value:String) : String
   {
      if(value == null) return "";
      return value.split("=").join(" ").split("\r").join(" ").split("\n").join(" ");
   }

   public static function recordText(won:int, lost:int, drawn:int) : String
   {
      return won + " won, " + lost + " lost" + (drawn > 0 ? ", " + drawn + " drawn" : "");
   }

   public static function countsAfter(counts:Object, prefix:String, outcome:String) : Array
   {
      var won:int = Math.max(0,int(counts[prefix + "_won"]));
      var lost:int = Math.max(0,int(counts[prefix + "_lost"]));
      var drawn:int = Math.max(0,int(counts[prefix + "_drawn"]));
      if(outcome == "won") won++;
      else if(outcome == "lost") lost++;
      else drawn++;
      return [won,lost,drawn];
   }

   public static function statsLine(bucketText:String, bucketCounts:Array, deckText:String, deckCounts:Array) : String
   {
      var line:String = "Record against " + bucketText + ": "
         + recordText(int(bucketCounts[0]),int(bucketCounts[1]),int(bucketCounts[2]));
      if(deckText != null && deckText != "")
         line += "; against " + deckText + ": " + recordText(int(deckCounts[0]),int(deckCounts[1]),int(deckCounts[2]));
      return line + ".";
   }
}
