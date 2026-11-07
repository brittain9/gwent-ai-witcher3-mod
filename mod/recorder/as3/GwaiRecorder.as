internal class GwaiRecorder
{
   private static var randomCallIndex:int = 0;
   private static var actionIndex:int = 0;
   private static var activeActionIndex:int = 0;

   public static function begin(manager:CardManager, controllers:Vector.<BasePlayerController>) : void
   {
      var seat:int = 0;
      var deck:GwintDeck = null;
      var templates:Array = [];
      var decks:Array = [];
      var factions:Array = [];
      var leaders:Array = [];
      var specialCards:Array = [];
      var seatKinds:Array = [];
      var template:CardTemplate = null;
      randomCallIndex = 0;
      actionIndex = 0;
      activeActionIndex = 0;
      try
      {
         if(GwintGameMenu.mSingleton != null)
         {
            GwintGameMenu.mSingleton.dispatchEvent(new GameEvent(GameEvent.CALL,"OnW3GwentRecorderStart",[]));
         }
      }
      catch(error:Error)
      {
      }
      for each(template in manager._cardTemplates)
      {
         templates.push({id:template.index,name:template.title,power:template.power,
            faction:template.factionIdx,types:template.typeArray,
            effects:template.effectFlags == null ? [] : template.effectFlags,
            summons:template.summonFlags == null ? [] : template.summonFlags});
      }
      templates.sortOn("id",Array.NUMERIC);
      for(seat = 0; seat < 2; seat++)
      {
         deck = manager.playerDeckDefinitions[seat];
         decks.push({cards:copyInts(deck.cardIndices),faction:deck.getDeckFaction(),
            leader_template_id:deck.selectedKingIndex,special_card:deck.specialCard});
         factions.push(deck.getDeckFaction());
         leaders.push(deck.selectedKingIndex);
         specialCards.push(deck.specialCard);
         seatKinds.push(controllers[seat] is AIPlayerController ? "stock_ai" : "human");
      }
      emit({event:"match_start",schema:"w3gwent-recorder-v1",schema_version:1,
         diagnostic:true,stock_ai:true,cards:templates,decks:decks,factions:factions,
         leaders:leaders,special_cards:specialCards,seat_kinds:seatKinds,rng_cursor:0});
   }

   public static function end(reason:String) : void
   {
      emit({event:"match_end",reason:reason,rng_cursor:randomCallIndex});
      try
      {
         if(GwintGameMenu.mSingleton != null)
         {
            GwintGameMenu.mSingleton.dispatchEvent(new GameEvent(GameEvent.CALL,"OnW3GwentRecorderEnd",[reason]));
         }
      }
      catch(error:Error)
      {
      }
   }

   public static function random(site:String) : Number
   {
      var value:Number = Math.random();
      emit({event:"random",call_index:randomCallIndex,site:site,value:value,rng_cursor:randomCallIndex + 1});
      randomCallIndex++;
      return value;
   }

   public static function firstPlayer(player:int) : void
   {
      emit({event:"first_player",player:player,rng_cursor:randomCallIndex});
   }

   public static function decision(seat:int, attitude:int, attitudeName:String, transaction:CardTransaction) : void
   {
      emit({event:"stock_decision",seat:seat,attitude:attitude,attitude_name:attitudeName,
         transaction:transactionRecord(transaction),rng_cursor:randomCallIndex});
   }

   public static function action(seat:int, transaction:CardTransaction) : void
   {
      activeActionIndex = ++actionIndex;
      emit({event:"action",action_id:activeActionIndex,seat:seat,
         transaction:transactionRecord(transaction),rng_cursor:randomCallIndex});
   }

   public static function stable(manager:CardManager, flow:GwintGameFlowController, seat:int, round:int) : void
   {
      if(activeActionIndex == 0) return;
      emit({event:"state",action_id:activeActionIndex,rng_cursor:randomCallIndex,
         state:stateRecord(manager,flow,round)});
      activeActionIndex = 0;
   }

   public static function pass(seat:int) : void
   {
      emit({event:"pass",seat:seat,rng_cursor:randomCallIndex});
   }

   public static function choice(seat:int, choiceName:String, value:*) : void
   {
      emit({event:"choice",action_id:activeActionIndex,seat:seat,
         choice:choiceName,value:value,rng_cursor:randomCallIndex});
   }

   public static function deckShuffled(deck:GwintDeck) : void
   {
      var seat:int = -1;
      var manager:CardManager = CardManager.getInstance();
      var cards:Array = [];
      if(manager.playerDeckDefinitions[0] === deck) seat = 0;
      else if(manager.playerDeckDefinitions[1] === deck) seat = 1;
      for each(var templateId:int in deck.cardIndicesInDeck) cards.push(templateId);
      emit({event:"shuffle",seat:seat,order:cards,rng_cursor:randomCallIndex});
   }

   public static function deckReadded(deck:GwintDeck, templateId:int) : void
   {
      var seat:int = -1;
      var cards:Array = [];
      var manager:CardManager = CardManager.getInstance();
      if(manager.playerDeckDefinitions[0] === deck) seat = 0;
      else if(manager.playerDeckDefinitions[1] === deck) seat = 1;
      for each(var value:int in deck.cardIndicesInDeck) cards.push(value);
      emit({event:"shuffle",kind:"random_reinsert",seat:seat,template_id:templateId,
         order:cards,rng_cursor:randomCallIndex});
   }

   public static function draw(seat:int, card:CardInstance) : void
   {
      emit({event:"draw",seat:seat,card:cardRecord(card),rng_cursor:randomCallIndex});
   }

   public static function chance(name:String, seat:int, candidates:Vector.<CardInstance>, selected:CardInstance) : void
   {
      var choices:Array = [];
      for each(var card:CardInstance in candidates) choices.push(cardRecord(card));
      emit({event:"chance",name:name,seat:seat,candidates:choices,
         selected:selected == null ? null : cardRecord(selected),rng_cursor:randomCallIndex});
   }

   public static function tiePick(name:String, card:CardInstance, value:Number) : Boolean
   {
      var chooseRanged:Boolean = value < 0.5;
      emit({event:"chance",name:name,seat:card.listsPlayer,
         instance_id:card.instanceId,template_id:card.templateId,
         candidates:["melee","ranged"],selected:chooseRanged ? "ranged" : "melee",
         value:value,rng_cursor:randomCallIndex});
      return chooseRanged;
   }

   private static function transactionRecord(transaction:CardTransaction) : Object
   {
      var source:CardInstance = null;
      var target:CardInstance = null;
      if(transaction == null) return null;
      source = transaction.sourceCardInstanceRef;
      target = transaction.targetCardInstanceRef;
      return {source_instance_id:source == null ? -1 : source.instanceId,
         source_template_id:source == null ? -1 : source.templateId,
         target_slot_id:transaction.targetSlotID,target_player_id:transaction.targetPlayerID,
         target_instance_id:target == null ? null : target.instanceId,
         target_template_id:target == null ? null : target.templateId,
         power_change:transaction.powerChangeResult,strategic_value:transaction.strategicValue};
   }

   private static function cardRecord(card:CardInstance) : Object
   {
      return {instance_id:card.instanceId,template_id:card.templateId,
         current_power:card.getTotalPower(),owner:card.owningPlayer,list:card.inList,seat:card.listsPlayer};
   }

   private static function cardList(manager:CardManager, list:int, seat:int) : Array
   {
      var result:Array = [];
      var cards:Vector.<CardInstance> = manager.getCardInstanceList(list,seat);
      for each(var card:CardInstance in cards) result.push(cardRecord(card));
      return result;
   }

   private static function stateRecord(manager:CardManager, flow:GwintGameFlowController, round:int) : Object
   {
      var seats:Array = [];
      var lives:Array = [2,2];
      var player:int = 0;
      var result:GwintRoundResult = null;
      var winner:int = -1;
      var controller:BasePlayerController = null;
      for each(result in manager.roundResults)
      {
         if(!result.played) break;
         winner = result.winningPlayer;
         if(winner == CardManager.PLAYER_1 || winner == CardManager.PLAYER_INVALID) lives[CardManager.PLAYER_2] = Math.max(0,lives[CardManager.PLAYER_2] - 1);
         if(winner == CardManager.PLAYER_2 || winner == CardManager.PLAYER_INVALID) lives[CardManager.PLAYER_1] = Math.max(0,lives[CardManager.PLAYER_1] - 1);
      }
      for(player = 0; player < 2; player++)
      {
         controller = flow.playerControllers[player];
         var hand:Vector.<CardInstance> = manager.getCardInstanceList(CardManager.CARD_LIST_LOC_HAND,player);
         var rowScores:Object = {
            melee:manager.calculatePlayerScore(CardManager.CARD_LIST_LOC_MELEE,player),
            ranged:manager.calculatePlayerScore(CardManager.CARD_LIST_LOC_RANGED,player),
            siege:manager.calculatePlayerScore(CardManager.CARD_LIST_LOC_SEIGE,player)};
         var playerState:Object = {seat:player,rows:{
            melee:cardList(manager,CardManager.CARD_LIST_LOC_MELEE,player),
            ranged:cardList(manager,CardManager.CARD_LIST_LOC_RANGED,player),
            siege:cardList(manager,CardManager.CARD_LIST_LOC_SEIGE,player)},
            row_scores:rowScores,score:manager.currentPlayerScores[player],lives:lives[player],
            hand_count:hand.length,graveyard:cardList(manager,CardManager.CARD_LIST_LOC_GRAVEYARD,player),
            leader:cardList(manager,CardManager.CARD_LIST_LOC_LEADER,player),
            deck_count:manager.playerDeckDefinitions[player].cardIndicesInDeck.length,
            passed:controller.currentRoundStatus == BasePlayerController.ROUND_PLAYER_STATUS_DONE,
            modifiers:{melee:cardList(manager,CardManager.CARD_LIST_LOC_MELEEMODIFIERS,player),
               ranged:cardList(manager,CardManager.CARD_LIST_LOC_RANGEDMODIFIERS,player),
               siege:cardList(manager,CardManager.CARD_LIST_LOC_SEIGEMODIFIERS,player)}};
         if(controller is AIPlayerController)
         {
            playerState.ai_hand = [];
            for each(var ownCard:CardInstance in hand) playerState.ai_hand.push(cardRecord(ownCard));
         }
         seats.push(playerState);
      }
      return {round:round,actor:flow.currentPlayer,players:seats,
         weather:cardList(manager,CardManager.CARD_LIST_LOC_WEATHERSLOT,CardManager.PLAYER_INVALID)};
   }

   private static function copyInts(source:Vector.<int>) : Array
   {
      var values:Array = [];
      for each(var value:int in source) values.push(value);
      return values;
   }

   private static function emit(record:Object) : void
   {
      try
      {
         if(GwintGameMenu.mSingleton != null)
         {
            GwintGameMenu.mSingleton.dispatchEvent(new GameEvent(GameEvent.CALL,
               "OnW3GwentRecorderRecord",[JSON.stringify(record)]));
         }
      }
      catch(error:Error)
      {
      }
   }
}
