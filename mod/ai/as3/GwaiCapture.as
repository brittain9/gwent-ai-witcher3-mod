internal class GwaiCapture
{
   public static function capture(manager:CardManager, flow:GwintGameFlowController, playerID:int) : Object
   {
      var opponentID:int = playerID == CardManager.PLAYER_1 ? CardManager.PLAYER_2 : CardManager.PLAYER_1;
      var opponentHand:Vector.<CardInstance> = manager.getCardInstanceList(CardManager.CARD_LIST_LOC_HAND,opponentID);
      var opponentHandCount:int = opponentHand.length;
      var ownHand:Vector.<CardInstance> = manager.getCardInstanceList(CardManager.CARD_LIST_LOC_HAND,playerID);
      var rounds:Array = [];
      var lives:Array = [2,2];
      for(var roundIndex:int = 0; roundIndex < manager.roundResults.length; roundIndex++)
      {
         var result:GwintRoundResult = manager.roundResults[roundIndex];
         if(!result.played) continue;
         var winner:int = result.getRoundWinner();
         rounds.push({
            scores:[result.getPlayerScore(CardManager.PLAYER_1),result.getPlayerScore(CardManager.PLAYER_2)],
            winner:winner == CardManager.PLAYER_INVALID ? null : winner
         });
         if(winner == CardManager.PLAYER_1 || winner == CardManager.PLAYER_INVALID)
            lives[CardManager.PLAYER_2] = Math.max(0,lives[CardManager.PLAYER_2] - 1);
         if(winner == CardManager.PLAYER_2 || winner == CardManager.PLAYER_INVALID)
            lives[CardManager.PLAYER_1] = Math.max(0,lives[CardManager.PLAYER_1] - 1);
      }

      var players:Array = [
         playerObservation(manager,flow,CardManager.PLAYER_1,playerID,ownHand.length,opponentHandCount),
         playerObservation(manager,flow,CardManager.PLAYER_2,playerID,ownHand.length,opponentHandCount)
      ];
      var observation:Object = {
         seat:playerID,
         initiative:flow.gwaiPolicyInitiative(),
         decision_id:flow.gwaiPolicyDecisionId(),
         round:flow.gwaiPolicyRound(),
         rounds_played:rounds.length,
         lives:lives,
         scores:[manager.currentPlayerScores[CardManager.PLAYER_1],manager.currentPlayerScores[CardManager.PLAYER_2]],
         round_results:rounds,
         hand:cardList(ownHand),
         players:players,
         weather:cardList(manager.getCardInstanceList(CardManager.CARD_LIST_LOC_WEATHERSLOT,CardManager.PLAYER_INVALID)),
         pending_choice:null,
         terminal:false
      };
      var opponentController:BasePlayerController = flow.playerControllers[opponentID];
      return {
         playerID:playerID,
         opponentID:opponentID,
         observation:observation,
         policyMode:"network",
         hand:observation.hand,
         board:{
            player1:board(manager,CardManager.PLAYER_1),
            player2:board(manager,CardManager.PLAYER_2),
            weather:observation.weather
         },
         graveyards:{
            player1:cardList(manager.getCardInstanceList(CardManager.CARD_LIST_LOC_GRAVEYARD,CardManager.PLAYER_1)),
            player2:cardList(manager.getCardInstanceList(CardManager.CARD_LIST_LOC_GRAVEYARD,CardManager.PLAYER_2))
         },
         scores:observation.scores,
         lives:lives,
         roundResults:rounds,
         opponentHandCount:opponentHandCount,
         opponentPassed:opponentController.currentRoundStatus == BasePlayerController.ROUND_PLAYER_STATUS_DONE,
         leaders:[leaderView(manager.getCardLeader(CardManager.PLAYER_1)),leaderView(manager.getCardLeader(CardManager.PLAYER_2))],
         deckFactions:[players[CardManager.PLAYER_1].faction,players[CardManager.PLAYER_2].faction]
      };
   }

   private static function playerObservation(manager:CardManager, flow:GwintGameFlowController,
                                             seat:int, playerID:int, ownHandCount:int,
                                             opponentHandCount:int) : Object
   {
      var leader:CardLeaderInstance = manager.getCardLeader(seat);
      var handCount:int = seat == playerID ? ownHandCount : opponentHandCount;
      var deck:GwintDeck = manager.playerDeckDefinitions[seat];
      var controller:BasePlayerController = flow.playerControllers[seat];
      return {
         seat:seat,
         faction:deck.getDeckFaction(),
         hand_count:handCount,
         deck_count:deck.cardIndicesInDeck.length,
         leader:leader == null ? null : cardView(leader),
         leader_used:leader == null ? false : leader.hasBeenUsed,
         graveyard:cardList(manager.getCardInstanceList(CardManager.CARD_LIST_LOC_GRAVEYARD,seat)),
         rows:{
            melee:{cards:cardList(manager.getCardInstanceList(CardManager.CARD_LIST_LOC_MELEE,seat))},
            ranged:{cards:cardList(manager.getCardInstanceList(CardManager.CARD_LIST_LOC_RANGED,seat))},
            siege:{cards:cardList(manager.getCardInstanceList(CardManager.CARD_LIST_LOC_SEIGE,seat))}
         },
         modifiers:{
            melee:singleCard(manager.getCardInstanceList(CardManager.CARD_LIST_LOC_MELEEMODIFIERS,seat)),
            ranged:singleCard(manager.getCardInstanceList(CardManager.CARD_LIST_LOC_RANGEDMODIFIERS,seat)),
            siege:singleCard(manager.getCardInstanceList(CardManager.CARD_LIST_LOC_SEIGEMODIFIERS,seat))
         },
         passed:controller.currentRoundStatus == BasePlayerController.ROUND_PLAYER_STATUS_DONE
      };
   }

   public static function legalMoves(manager:CardManager, playerID:int) : Array
   {
      var moves:Array = [{id:"pass",kind:"pass"}];
      var hand:Vector.<CardInstance> = manager.getCardInstanceList(CardManager.CARD_LIST_LOC_HAND,playerID);
      var rows:Array = [CardManager.CARD_LIST_LOC_MELEE,CardManager.CARD_LIST_LOC_RANGED,CardManager.CARD_LIST_LOC_SEIGE];
      for(var cardIndex:int = 0; cardIndex < hand.length; cardIndex++)
      {
         var card:CardInstance = hand[cardIndex];
         if(card.templateRef.hasEffect(CardTemplate.CardEffect_UnsummonDummy))
         {
            for(var decoyRowIndex:int = 0; decoyRowIndex < rows.length; decoyRowIndex++)
            {
               var decoyRow:int = int(rows[decoyRowIndex]);
               var decoyTargets:Vector.<CardInstance> = manager.getCardInstanceList(decoyRow,playerID);
               for(var targetIndex:int = 0; targetIndex < decoyTargets.length; targetIndex++)
               {
                  var target:CardInstance = decoyTargets[targetIndex];
                  if(card.canBeCastOn(target)) moves.push(playMove(card,null,CardManager.CARD_LIST_LOC_INVALID,playerID,target.instanceId));
               }
            }
         }
         else
         {
            for(var rowIndex:int = 0; rowIndex < rows.length; rowIndex++)
            {
               var row:int = int(rows[rowIndex]);
               var rowName:String = rowNameForSlot(row);
               var targetPlayer:int = card.templateRef.isType(CardTemplate.CardType_Spy) ? (playerID == CardManager.PLAYER_1 ? CardManager.PLAYER_2 : CardManager.PLAYER_1) : playerID;
               if(card.canBePlacedInSlot(row,targetPlayer)) moves.push(playMove(card,rowName,row,targetPlayer));
            }
            if(card.canBePlacedInSlot(CardManager.CARD_LIST_LOC_WEATHERSLOT,CardManager.PLAYER_INVALID))
               moves.push(playMove(card,null,CardManager.CARD_LIST_LOC_WEATHERSLOT,CardManager.PLAYER_INVALID));
            for(var modifierIndex:int = 0; modifierIndex < rows.length; modifierIndex++)
            {
               var modifierRow:int = int(rows[modifierIndex]);
               var modifierSlot:int = modifierSlotForRow(modifierRow);
               for(var side:int = 0; side <= 1; side++)
               {
                  var modifierPlayer:int = side == 0 ? playerID : (playerID == CardManager.PLAYER_1 ? CardManager.PLAYER_2 : CardManager.PLAYER_1);
                  if(card.canBePlacedInSlot(modifierSlot,modifierPlayer))
                     moves.push(playMove(card,rowNameForSlot(modifierRow),modifierSlot,modifierPlayer));
               }
            }
            if(card.templateRef.isType(CardTemplate.CardType_Global_Effect) && card.templateRef.hasEffect(CardTemplate.CardEffect_Scorch))
               moves.push(playMove(card,null,CardManager.CARD_LIST_LOC_INVALID,CardManager.PLAYER_INVALID));
         }
      }
      var leader:CardLeaderInstance = manager.getCardLeader(playerID);
      if(leader != null && leader.canBeUsed)
      {
         leader.recalculatePowerPotential(manager);
         var leaderTransaction:CardTransaction = leader.getOptimalTransaction();
         if(leaderTransaction != null && leaderTransaction.strategicValue != -1)
            moves.push({
               id:"leader:" + leader.instanceId,
               kind:"leader",
               instance_id:leader.instanceId,
               instanceId:leader.instanceId,
               scoreGain:leaderTransaction.powerChangeResult
            });
      }
      return moves;
   }

   private static function playMove(card:CardInstance, rowName:String, slot:int, targetPlayer:int,
                                    targetInstanceId:Number = -1) : Object
   {
      var actionId:String = "play:" + card.instanceId;
      if(targetInstanceId >= 0) actionId += ":target:" + targetInstanceId;
      else if(rowName != null) actionId += ":" + rowName;
      var move:Object = {
         id:actionId,
         kind:"play",
         instance_id:card.instanceId,
         instanceId:card.instanceId,
         row:rowName,
         slotId:slot,
         targetPlayer:targetPlayer,
         scoreGain:card.templateRef.isType(CardTemplate.CardType_Spy) ? 0 : card.templateRef.power
      };
      if(targetInstanceId >= 0)
      {
         move.target_instance_id = targetInstanceId;
         move.targetInstanceId = targetInstanceId;
      }
      return move;
   }

   private static function rowNameForSlot(slot:int) : String
   {
      if(slot == CardManager.CARD_LIST_LOC_MELEE) return "melee";
      if(slot == CardManager.CARD_LIST_LOC_RANGED) return "ranged";
      if(slot == CardManager.CARD_LIST_LOC_SEIGE) return "siege";
      return null;
   }

   private static function modifierSlotForRow(row:int) : int
   {
      if(row == CardManager.CARD_LIST_LOC_MELEE) return CardManager.CARD_LIST_LOC_MELEEMODIFIERS;
      if(row == CardManager.CARD_LIST_LOC_RANGED) return CardManager.CARD_LIST_LOC_RANGEDMODIFIERS;
      return CardManager.CARD_LIST_LOC_SEIGEMODIFIERS;
   }

   private static function singleCard(cards:Vector.<CardInstance>) : Object
   {
      return cards == null || cards.length == 0 ? null : cardView(cards[0]);
   }

   private static function board(manager:CardManager, playerID:int) : Object
   {
      return {
         melee:cardList(manager.getCardInstanceList(CardManager.CARD_LIST_LOC_MELEE,playerID)),
         ranged:cardList(manager.getCardInstanceList(CardManager.CARD_LIST_LOC_RANGED,playerID)),
         siege:cardList(manager.getCardInstanceList(CardManager.CARD_LIST_LOC_SEIGE,playerID)),
         meleeModifiers:cardList(manager.getCardInstanceList(CardManager.CARD_LIST_LOC_MELEEMODIFIERS,playerID)),
         rangedModifiers:cardList(manager.getCardInstanceList(CardManager.CARD_LIST_LOC_RANGEDMODIFIERS,playerID)),
         siegeModifiers:cardList(manager.getCardInstanceList(CardManager.CARD_LIST_LOC_SEIGEMODIFIERS,playerID))
      };
   }

   private static function cardList(cards:Vector.<CardInstance>) : Array
   {
      var result:Array = [];
      if(cards == null) return result;
      for(var index:int = 0; index < cards.length; index++) result.push(cardView(cards[index]));
      return result;
   }

   private static function cardView(card:CardInstance) : Object
   {
      var effects:Array = card.templateRef.effectFlags == null ? [] : card.templateRef.effectFlags.concat();
      return {
         instance_id:card.instanceId,
         template_id:card.templateId,
         power:card.templateRef.power,
         score:card.getTotalPower(),
         effects:effects,
         types:types(card.templateRef)
      };
   }

   private static function types(template:CardTemplate) : Object
   {
      return {
         creature:template.isType(CardTemplate.CardType_Creature),
         hero:template.isType(CardTemplate.CardType_Hero),
         spy:template.isType(CardTemplate.CardType_Spy),
         melee:template.isType(CardTemplate.CardType_Melee),
         ranged:template.isType(CardTemplate.CardType_Ranged),
         siege:template.isType(CardTemplate.CardType_Siege),
         weather:template.isType(CardTemplate.CardType_Weather),
         rowModifier:template.isType(CardTemplate.CardType_Row_Modifier)
      };
   }

   private static function leaderView(leader:CardLeaderInstance) : Object
   {
      if(leader == null) return {present:false,usable:false,used:false,templateId:-1};
      return {present:true,usable:leader.canBeUsed,used:leader.hasBeenUsed,templateId:leader.templateId};
   }
}
