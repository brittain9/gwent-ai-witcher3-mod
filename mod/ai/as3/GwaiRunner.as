internal class GwaiRunner
{
   private static const MOVES_PER_FRAME:Array = [1,4,16];
   private static const MAX_EVALUATIONS:Array = [1,4,16];
   private var _view:Object;
   private var _legalMoves:Array;
   private var _evaluated:Array = [];
   private var _cursor:int = 0;
   private var _perFrame:int;
   private var _limit:int;
   private var _difficulty:int;
   public var done:Boolean = false;
   public var output:Object;
   public var winChance:Number = NaN;

   public function GwaiRunner(view:Object, legalMoves:Array, difficulty:int)
   {
      if(difficulty < 1 || difficulty > 3) throw new Error("difficulty outside 1..3");
      this._view = view;
      this._legalMoves = legalMoves;
      this._perFrame = int(MOVES_PER_FRAME[difficulty - 1]);
      this._limit = int(MAX_EVALUATIONS[difficulty - 1]);
      this._difficulty = difficulty;
   }

   public function advance() : Boolean
   {
      if(this.done) return true;
      if(this._view != null && this._view.policyMode == "network")
      {
         this.output = GwaiPolicyCore.decide(this._view,this._legalMoves,this._difficulty);
         this.winChance = this._view.policyFallback === true ? NaN : GwaiNetPolicy.lastWinChance();
         this.done = true;
         return true;
      }
      var completed:int = 0;
      var end:int = Math.min(this._legalMoves.length,this._limit);
      while(this._cursor < end && completed < this._perFrame)
      {
         var move:Object = this._legalMoves[this._cursor++];
         GwaiPolicyCore.scoreMove(this._view,move);
         this._evaluated.push(move);
         completed++;
      }
      if(this._cursor < end) return false;
      this.output = GwaiPolicyCore.decide(this._view,this._evaluated);
      this.done = true;
      return true;
   }

   public function cancel() : void
   {
      this._view = null;
      this._legalMoves = null;
      this._evaluated = [];
      this.output = null;
      this.done = true;
   }
}
