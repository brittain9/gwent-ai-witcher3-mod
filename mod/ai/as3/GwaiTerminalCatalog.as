// Read public template definitions only: visible cards and their summon graph.
internal class GwaiTerminalCatalog
{
   public static function capture(manager:CardManager, view:Object) : Object
   {
      if(manager == null || view == null || !(view.hand is Array) || !(view.players is Array) || !(view.weather is Array)) throw new Error("invalid public catalog view");
      var ids:Array = []; var catalog:Object = {};
      function addCard(card:Object) : void
      {
         if(card != null) ids.push(card.template_id);
      }
      function addCards(cards:Array) : void
      {
         if(cards == null) throw new Error("invalid visible zone");
         for each(var card:Object in cards) addCard(card);
      }
      addCards(view.hand); addCards(view.weather);
      for each(var player:Object in view.players)
      {
         if(player == null || player.rows == null || player.modifiers == null) throw new Error("invalid visible player");
         addCard(player.leader);
         for each(var row:String in ["melee","ranged","siege"])
         {
            if(player.rows[row] == null) throw new Error("invalid visible row");
            addCards(player.rows[row].cards);
            addCard(player.modifiers[row]);
         }
      }
      var count:int = 0;
      while(ids.length > 0)
      {
         var id:* = ids.pop();
         if(typeof id != "number" || !isFinite(Number(id)) || Number(id) < 0 || Number(id) > 2147483646 || Number(id) != Math.floor(Number(id))) throw new Error("invalid template id");
         if(catalog.hasOwnProperty(String(id))) continue;
         if(++count > 256) throw new Error("public template closure too large");
         var template:CardTemplate = manager.getCardTemplate(int(id));
         if(template == null || template.index != int(id)) throw new Error("public template id mismatch");
         var effects:Array = template.effectFlags == null ? [] : template.effectFlags.concat();
         var summons:Array = template.summonFlags == null ? [] : template.summonFlags.concat();
         catalog[String(id)] = {id:Number(id),power:template.power,types:Number(template.typeArray),effects:effects,summons:summons};
         if(effects.indexOf(31) >= 0) for each(var summoned:* in summons) ids.push(summoned);
      }
      return catalog;
   }
}
