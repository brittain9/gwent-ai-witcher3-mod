internal class GwaiProbeChoice
{
   public static function firstSupportedTransaction(controller:AIPlayerController) : CardTransaction
   {
      var manager:CardManager = null;
      var hand:Vector.<CardInstance> = null;
      var card:CardInstance = null;
      var transaction:CardTransaction = null;
      var rowOrder:Array = [CardManager.CARD_LIST_LOC_MELEE,CardManager.CARD_LIST_LOC_RANGED,CardManager.CARD_LIST_LOC_SEIGE];
      var rowIndex:int = 0;
      var slotID:int = CardManager.CARD_LIST_LOC_INVALID;
      var i:int = 0;

      if(controller == null || controller.playerID < CardManager.PLAYER_1 || controller.playerID > CardManager.PLAYER_2)
      {
         return null;
      }
      manager = CardManager.getInstance();
      if(manager == null)
      {
         return null;
      }
      hand = manager.getCardInstanceList(CardManager.CARD_LIST_LOC_HAND,controller.playerID);
      if(hand == null)
      {
         return null;
      }
      for(i = 0; i < hand.length; i++)
      {
         card = hand[i];
         if(!GwaiProbeChoice.thisIsSafeCreature(card,controller.playerID))
         {
            continue;
         }
         transaction = new CardTransaction();
         transaction.sourceCardInstanceRef = card;
         transaction.targetPlayerID = controller.playerID;
         transaction.powerChangeResult = 0;
         transaction.strategicValue = 0;
         for(rowIndex = 0; rowIndex < rowOrder.length; rowIndex++)
         {
            slotID = int(rowOrder[rowIndex]);
            if(card.canBePlacedInSlot(slotID,controller.playerID))
            {
               transaction.targetSlotID = slotID;
               return transaction;
            }
         }
      }
      return null;
   }

   private static function thisIsSafeCreature(card:CardInstance, playerID:int) : Boolean
   {
      var template:CardTemplate = null;
      var effectIndex:int = 0;
      if(card == null || card.templateRef == null || card.instanceId < 0)
      {
         return false;
      }
      if(card.inList != CardManager.CARD_LIST_LOC_HAND || card.listsPlayer != playerID)
      {
         return false;
      }
      template = card.templateRef;
      if(!template.isType(CardTemplate.CardType_Creature) || template.isType(CardTemplate.CardType_Hero) || template.isType(CardTemplate.CardType_Spy))
      {
         return false;
      }
      if(template.effectFlags != null)
      {
         for(effectIndex = 0; effectIndex < template.effectFlags.length; effectIndex++)
         {
            if(template.effectFlags[effectIndex] == null || int(template.effectFlags[effectIndex]) != CardTemplate.CardEffect_None)
            {
               return false;
            }
         }
      }
      if(template.summonFlags != null && template.summonFlags.length != 0)
      {
         return false;
      }
      return true;
   }
}
