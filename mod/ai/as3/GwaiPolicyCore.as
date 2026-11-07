internal class GwaiPolicyCore
{
   public static function scoreMove(view:Object, move:Object) : Number
   {
      if(move == null || move.kind != "play") return -1000000;
      var score:Number = Number(move.scoreGain);
      if(isNaN(score)) return -1000000;
      return score;
   }

   public static function decide(view:Object, legalMoves:Array, difficulty:int = 2) : Object
   {
      if(view != null && view.policyMode == "network")
      {
         try
         {
            return GwaiNetPolicy.decide(view.observation,legalMoves,difficulty);
         }
         catch(error:Error)
         {
            view.policyFallback = true;
            view.policyFallbackReason = error.toString();
         }
      }
      return decideGreedy(view,legalMoves);
   }

   public static function decideGreedy(view:Object, legalMoves:Array) : Object
   {
      var ownScore:Number = Number(view.scores[view.playerID]);
      var opponentScore:Number = Number(view.scores[view.opponentID]);
      if(view.opponentPassed && ownScore > opponentScore) return {kind:"pass"};
      if(legalMoves == null || legalMoves.length == 0) return {kind:"pass"};
      var best:Object = null;
      var bestScore:Number = 0;
      for(var index:int = 0; index < legalMoves.length; index++)
      {
         var move:Object = legalMoves[index];
         var score:Number = scoreMove(view,move);
         if(score >= 0 && (best == null || score > bestScore))
         {
            best = move;
            bestScore = score;
         }
      }
      if(best == null) return {kind:"pass"};
      return best;
   }
}
