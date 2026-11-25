internal class GwaiTerminalTactics
{
   private static const ROWS:Array = ["melee","ranged","siege"];
   private static const UNIT_MASK:int = 143;
   private static const BOARD_EFFECTS:Array = [0,27,31,32,33,35];
   private static const LEADER_EFFECTS:Array = [0,10,22,43];

   private static function number(x:*) : Boolean
   {
      return typeof x == "number" && isFinite(Number(x)) && Number(x) >= 0 && Number(x) <= 2147483646 && Number(x) == Math.floor(Number(x));
   }

   private static function typesMatch(actual:*, expected:int) : Boolean
   {
      if(typeof actual == "number") return actual === expected;
      if(actual == null || typeof actual != "object") return false;
      var names:Array = ["melee","ranged","siege","creature","weather","rowModifier","hero","spy"];
      var bits:Array = [1,2,4,8,16,64,128,256];
      for(var i:int = 0; i < names.length; i++)
         if(typeof actual[names[i]] != "boolean" || actual[names[i]] !== ((expected & int(bits[i])) != 0)) return false;
      return true;
   }

   private static function sameInts(a:*, b:*) : Boolean
   {
      if(!(a is Array) || !(b is Array) || a.length != b.length) return false;
      for(var i:int = 0; i < a.length; i++) if(a[i] !== b[i]) return false;
      return true;
   }

   private static function definition(card:Object, catalog:Object) : Object
   {
      if(card == null || !GwaiTerminalTactics.number(card.template_id) || !catalog.hasOwnProperty(String(card.template_id))) return null;
      var d:Object = catalog[String(card.template_id)];
      if(d == null || card.power !== d.power || !GwaiTerminalTactics.typesMatch(card.types,int(d.types)) || !GwaiTerminalTactics.sameInts(card.effects,d.effects)) return null;
      return d;
   }

   private static function closureSafe(id:Number, catalog:Object) : Boolean
   {
      var todo:Array = [id]; var seen:Object = {}; var count:int = 0;
      while(todo.length > 0)
      {
         var next:* = todo.pop();
         if(!GwaiTerminalTactics.number(next) || !catalog.hasOwnProperty(String(next))) return false;
         if(seen.hasOwnProperty(String(next))) continue;
         if(++count > 256) return false;
         seen[String(next)] = true;
         var d:Object = catalog[String(next)];
         if(!GwaiTerminalTactics.number(d.types) || !(int(d.types) & 8) || !(int(d.types) & 7) || (int(d.types) & ~GwaiTerminalTactics.UNIT_MASK) != 0 ||
            !GwaiTerminalTactics.number(d.power) || d.power > 1000 || !(d.effects is Array) || !(d.summons is Array)) return false;
         for(var e:int = 0; e < d.effects.length; e++) if(d.effects[e] !== 0 && d.effects[e] !== 31) return false;
         if(d.summons.length > 0 && d.effects.indexOf(31) < 0) return false;
         for(var j:int = 0; j < d.summons.length; j++) todo.push(d.summons[j]);
      }
      return true;
   }

   private static function boardSafe(view:Object, catalog:Object) : Boolean
   {
      if(!(view.players is Array) || view.players.length != 2 || !(view.scores is Array) || view.scores.length != 2) return false;
      if(!GwaiTerminalTactics.number(view.scores[0]) || !GwaiTerminalTactics.number(view.scores[1])) return false;
      var seen:Object = {};
      for(var i:int = 0; i < 2; i++)
      {
         var p:Object = view.players[i];
         if(p == null || (p.seat !== 0 && p.seat !== 1) || seen.hasOwnProperty(String(p.seat))) return false;
         seen[String(p.seat)] = true;
         if(p.leader != null)
         {
            var leader:Object = GwaiTerminalTactics.definition(p.leader,catalog);
            if(leader == null || leader.types !== 0 || leader.effects.length != 1 || GwaiTerminalTactics.LEADER_EFFECTS.indexOf(leader.effects[0]) < 0) return false;
         }
         if(p.rows == null || p.modifiers == null) return false;
         var total:Number = 0; var boardCount:int = 0;
         for(var r:int = 0; r < GwaiTerminalTactics.ROWS.length; r++)
         {
            var row:String = String(GwaiTerminalTactics.ROWS[r]); var zone:Object = p.rows[row];
            if(zone == null || !(zone.cards is Array)) return false;
            boardCount += zone.cards.length;
            for(var j:int = 0; j < zone.cards.length; j++)
            {
               var c:Object = zone.cards[j]; var d:Object = GwaiTerminalTactics.definition(c,catalog);
               if(d == null || !GwaiTerminalTactics.number(d.types) || !(int(d.types) & 8) || (int(d.types) & ~GwaiTerminalTactics.UNIT_MASK) != 0 || !GwaiTerminalTactics.number(d.power) || d.power > 1000 || !(d.effects is Array) || !GwaiTerminalTactics.number(c.score)) return false;
               for(var e:int = 0; e < d.effects.length; e++) if(GwaiTerminalTactics.BOARD_EFFECTS.indexOf(d.effects[e]) < 0) return false;
               total += Number(c.score);
            }
            var modifier:Object = p.modifiers[row];
            if(modifier != null)
            {
               d = GwaiTerminalTactics.definition(modifier,catalog);
               if(d == null || d.types !== 71 || !GwaiTerminalTactics.sameInts(d.effects,[27])) return false;
            }
         }
         if(boardCount > 100 || total !== view.scores[int(p.seat)]) return false;
      }
      if(!seen.hasOwnProperty("0") || !seen.hasOwnProperty("1") || !(view.weather is Array)) return false;
      for(i = 0; i < view.weather.length; i++)
      {
         d = GwaiTerminalTactics.definition(view.weather[i],catalog);
         if(d == null || d.types !== 16 || !(d.effects is Array) || d.effects.length == 0) return false;
         for(e = 0; e < d.effects.length; e++) if([23,24,25].indexOf(d.effects[e]) < 0) return false;
      }
      return true;
   }

   private static function unitLowerBound(view:Object, players:Object, seat:int, row:String, d:Object) : Number
   {
      var base:Number = Number(d.power);
      if((int(d.types) & 128) != 0) return base;
      var effect:int = 23 + GwaiTerminalTactics.ROWS.indexOf(row); var affected:Boolean = false;
      for(var i:int = 0; i < view.weather.length; i++)
         if(view.weather[i].effects.indexOf(effect) >= 0) affected = true;
      if(!affected) return base;
      var own:Object = players[String(seat)].leader; var other:Object = players[String(1-seat)].leader;
      if(own == null || other == null) return 0;
      if(own.effects[0] === 43 && other.effects[0] !== 22) return Math.floor(base / 2);
      return Math.min(1,base);
   }

   public static function certify(view:Object, legal:Array, catalog:Object, profile:String, host:Object) : Object
   {
      try
      {
         if(view == null || host == null || host.ready !== true || host.effectApplying !== false ||
            !GwaiTerminalTactics.number(host.ticket) || Number(host.ticket) <= 0 || host.ticket !== host.capturedTicket ||
            host.actor !== view.seat || host.counter !== view.decision_id) return null;
         if(view.terminal !== false || view.pending_choice !== null || (view.seat !== 0 && view.seat !== 1) ||
            (view.hasOwnProperty("to_move") && view.to_move !== view.seat) || !(legal is Array) ||
            !(view.players is Array) || view.players.length != 2 || !(view.scores is Array) || view.scores.length != 2 ||
            !(view.lives is Array) || view.lives.length != 2) return null;
         var seat:int = int(view.seat); var other:int = 1 - seat; var players:Object = {};
         for(var i:int = 0; i < 2; i++)
         {
            var player:Object = view.players[i];
            if(player == null || (player.seat !== 0 && player.seat !== 1) || players.hasOwnProperty(String(player.seat))) return null;
            players[String(player.seat)] = player;
         }
         if(!players.hasOwnProperty("0") || !players.hasOwnProperty("1") || players[String(seat)].passed !== false || players[String(other)].passed !== true ||
            (view.lives[seat] !== 1 && view.lives[seat] !== 2) || view.lives[other] !== 1 || !GwaiTerminalTactics.number(view.scores[seat]) || !GwaiTerminalTactics.number(view.scores[other])) return null;
         if(profile == "full-faction-v1" && view.scores[seat] === view.scores[other] && players[String(seat)].faction === 2 &&
            [1,3,4,5].indexOf(players[String(other)].faction) >= 0)
         {
            var pass:Object = null;
            for(i = 0; i < legal.length; i++) if(legal[i] != null && legal[i].id === "pass" && legal[i].kind === "pass")
            {
               if(pass != null) return null;
               for(var key:String in legal[i]) if(key != "id" && key != "kind") return null;
               pass = legal[i];
            }
            if(pass != null) return {action:pass,source:"nilfgaard-terminal-pass"};
         }
         if(!GwaiTerminalTactics.boardSafe(view,catalog) || !(view.hand is Array)) return null;
         var own:Object = players[String(seat)];
         if(!GwaiTerminalTactics.number(own.hand_count) || own.hand_count !== view.hand.length || own.hand_count > 100 ||
            !GwaiTerminalTactics.number(own.deck_count) || own.deck_count > 100) return null;
         var hand:Object = {};
         for(i = 0; i < view.hand.length; i++)
         {
            var held:Object = view.hand[i];
            if(held == null || !GwaiTerminalTactics.number(held.instance_id) || hand.hasOwnProperty(String(held.instance_id))) return null;
            hand[String(held.instance_id)] = held;
         }
         for(i = 0; i < legal.length; i++)
         {
            var action:Object = legal[i];
            if(action == null || action.kind != "play" || GwaiTerminalTactics.ROWS.indexOf(action.row) < 0 || action.hasOwnProperty("target_instance_id") ||
               (action.hasOwnProperty("targetPlayer") && action.targetPlayer !== seat) || !GwaiTerminalTactics.number(action.instance_id)) continue;
            held = hand[String(action.instance_id)];
            if(held == null) continue;
            var d:Object = GwaiTerminalTactics.definition(held,catalog);
            if(d == null || !GwaiTerminalTactics.closureSafe(Number(d.id),catalog) || (int(d.types) & (1 << GwaiTerminalTactics.ROWS.indexOf(action.row))) == 0 ||
               action.id !== "play:" + action.instance_id + ":" + action.row) continue;
            var lower:Number = GwaiTerminalTactics.unitLowerBound(view,players,seat,String(action.row),d);
            if(lower > 0 && Number(view.scores[seat]) + lower > Number(view.scores[other]))
               return {action:action,source:"positive-unit-closure"};
         }
      }
      catch(error:Error) { return null; }
      return null;
   }
}
