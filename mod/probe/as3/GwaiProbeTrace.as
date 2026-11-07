internal class GwaiProbeTrace
{
   private static var nextDecisionID:int = 1;
   private static var pendingDecisionID:int = 0;
   private static var pendingPlayerID:int = CardManager.PLAYER_INVALID;
   private static var pendingTransaction:CardTransaction = null;

   public static function selected(playerID:int, transaction:CardTransaction) : void
   {
      var decisionID:int = 0;
      if(transaction == null || transaction.sourceCardInstanceRef == null)
      {
         return;
      }
      decisionID = nextDecisionID++;
      pendingDecisionID = decisionID;
      pendingPlayerID = playerID;
      pendingTransaction = transaction;
      emit({schema:"gwai-probe-trace-v1", schema_version:1, event:"selected", decision_id:decisionID,
         metrics_source:"probe_placeholder",
         player_id:playerID, source_instance_id:transaction.sourceCardInstanceRef.instanceId,
         source_template_id:transaction.sourceCardInstanceRef.templateId,
         target_slot_id:transaction.targetSlotID, target_player_id:transaction.targetPlayerID,
         power_change:transaction.powerChangeResult, power_change_bits:numberBits(transaction.powerChangeResult),
         strategic_value:transaction.strategicValue, strategic_value_bits:numberBits(transaction.strategicValue)});
   }

   public static function applyStarted(playerID:int, transaction:CardTransaction) : void
   {
      var source:CardInstance = null;
      var manager:CardManager = null;
      if(transaction == null || transaction !== pendingTransaction || playerID != pendingPlayerID || pendingDecisionID == 0)
      {
         return;
      }
      source = transaction.sourceCardInstanceRef;
      manager = CardManager.getInstance();
      if(source == null || manager == null || source.inList != CardManager.CARD_LIST_LOC_HAND || source.listsPlayer != playerID || manager.getCardInstance(source.instanceId) !== source)
      {
         reject("stale_probe_transaction");
         return;
      }
      emit({schema:"gwai-probe-trace-v1", schema_version:1, event:"apply_started", decision_id:pendingDecisionID,
         metrics_source:"probe_placeholder",
         player_id:playerID, source_instance_id:source.instanceId, source_template_id:source.templateId,
         target_slot_id:transaction.targetSlotID, target_player_id:transaction.targetPlayerID,
         power_change:transaction.powerChangeResult, power_change_bits:numberBits(transaction.powerChangeResult),
         strategic_value:transaction.strategicValue, strategic_value_bits:numberBits(transaction.strategicValue)});
      clearPending();
   }

   public static function reject(reason:String) : void
   {
      clearPending();
      emit({schema:"gwai-probe-trace-v1", schema_version:1, event:"rejected", reason:reason, action:null});
   }

   private static function clearPending() : void
   {
      pendingDecisionID = 0;
      pendingPlayerID = CardManager.PLAYER_INVALID;
      pendingTransaction = null;
   }

   private static function emit(record:Object) : void
   {
      try
      {
         if(GwintGameMenu.mSingleton != null)
         {
            GwintGameMenu.mSingleton.dispatchEvent(new GameEvent(GameEvent.CALL,"OnGwaiProbeTrace",[JSON.stringify(record)]));
         }
      }
      catch(error:Error)
      {
         // Recording must not interrupt the native transaction path.
      }
   }

   private static function numberBits(value:Number) : String
   {
      var bytes:ByteArray = new ByteArray();
      var high:uint = 0;
      var low:uint = 0;
      bytes.endian = Endian.BIG_ENDIAN;
      bytes.writeDouble(value);
      bytes.position = 0;
      high = bytes.readUnsignedInt();
      low = bytes.readUnsignedInt();
      return hexWord(high) + hexWord(low);
   }

   private static function hexWord(value:uint) : String
   {
      var digits:String = "0123456789abcdef";
      var result:String = "";
      var i:int = 0;
      for(i = 0; i < 8; i++)
      {
         result = digits.charAt(value & 15) + result;
         value >>>= 4;
      }
      return result;
   }
}
