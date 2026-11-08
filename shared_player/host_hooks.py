"""Authored native hooks for the one-model candidate; no old builder dependency."""

AI_MEMBERS = r"""      // FFDec resolves native slot types before appended internal classes.
      // Keep this host boundary dynamic; construction binds the one shared controller.
      private var _gwaiRunner:Object = null;
      private var _gwaiCapturedDecisionId:int = -1;
      private var _gwaiView:Object = null;
      private var _gwaiMoves:Array = null;
      private var _gwaiProfile:int = -1;
      private var _gwaiStartedAt:int = -1;

      public function gwaiCancel() : void
      {
         if(this._gwaiRunner != null) this._gwaiRunner.cancel();
         this._gwaiRunner = null;
         this._gwaiView = null;
         this._gwaiMoves = null;
         this._gwaiProfile = -1;
         this._gwaiStartedAt = -1;
         this._gwaiCapturedDecisionId = -1;
      }

      public function gwaiBegin() : Boolean
      {
         this.gwaiCancel();
         if(!GwaiSharedConfig.enabled()) return false;
         var profile:int = this.gameFlowControllerRef.gwaiProfileForPlayer(this.playerID);
         if(profile < 0)
         {
            this.gameFlowControllerRef.gwaiNotifyStockTurn();
            return false;
         }
         var stage:String = "capture";
         try
         {
            this._gwaiStartedAt = getTimer();
            var manager:CardManager = CardManager.getInstance();
            this._gwaiView = GwaiCapture.capture(manager,this.gameFlowControllerRef,this.playerID);
            stage = "vocabulary";
            var unknownCards:Array = GwaiNetPolicy.unknownCards(
               this._gwaiView.observation,GwaiSharedConfig.data().CARD_IDS);
            if(unknownCards.length > 0)
            {
               this.gwaiReportDecision(profile,true,"unknown card vocabulary");
               this.gwaiCancel();
               return false;
            }
            stage = "legal moves";
            this._gwaiMoves = GwaiCapture.legalMoves(manager,this.playerID);
            if(this._gwaiMoves.length == 0)
            {
               this.gwaiReportDecision(profile,true,"no legal moves");
               this.gwaiCancel();
               return false;
            }
            this._gwaiProfile = profile;
            stage = "runner setup";
            this._gwaiCapturedDecisionId = this.gameFlowControllerRef.gwaiPolicyDecisionId();
            this._gwaiRunner = new GwaiSharedController(profile);
            this._gwaiRunner.begin(this._gwaiView.observation,this._gwaiMoves);
            this._decidedCardTransaction = null;
            this.attitude = TACTIC_NONE;
            return true;
         }
         catch(error:Error)
         {
            this.gwaiReportDecision(profile,true,stage + " failed: " + error.toString());
            this.gwaiCancel();
            return false;
         }
      }

      public function gwaiPump() : Boolean
      {
         if(this._gwaiRunner == null) return false;
         var profile:int = this._gwaiProfile;
         var stage:String = "inference";
         try
         {
            if(this.gameFlowControllerRef.gwaiMatchEnded())
            {
               this.gwaiCancel();
               return true;
            }
            if(this.gameFlowControllerRef.gwaiPolicyDecisionId() != this._gwaiCapturedDecisionId
               || this.gameFlowControllerRef.currentPlayer != this.playerID)
            {
               this.gwaiReportDecision(profile,true,"stale primary context cancelled");
               this.gwaiCancel();
               return true;
            }
            if(!GwaiSharedConfig.enabled()
               || this.gameFlowControllerRef.gwaiProfileForPlayer(this.playerID) != this._gwaiProfile)
            {
               this.gwaiCancel();
               this.gwaiReportDecision(profile,true,"settings changed while thinking");
               this.gwaiNativeFallback();
               return false;
            }
            if(!this._gwaiRunner.advance()) return true;
            stage = "transaction";
            var selected:Object = this._gwaiRunner.output;
            if(this._gwaiRunner.status != "complete" || selected == null)
               throw new Error("shared controller refused decision: " + this._gwaiRunner.errorMessage);
            var move:Object = null;
            for each(var canonical:Object in this._gwaiMoves)
            {
               if(canonical.id == selected.id)
               {
                  if(move != null) throw new Error("ambiguous canonical primary action");
                  move = canonical;
               }
            }
            if(move == null) throw new Error("shared controller selected an unknown canonical action");
            var startedAt:int = this._gwaiStartedAt;
            this._gwaiRunner = null;
            this._gwaiView = null;
            this._gwaiMoves = null;
            this._gwaiProfile = -1;
            this._gwaiStartedAt = -1;
            this._gwaiCapturedDecisionId = -1;
            if(move.kind == "pass")
            {
               this._decidedCardTransaction = null;
               this.attitude = TACTIC_PASS;
               var decisionMs:int = startedAt < 0 ? -1 : getTimer() - startedAt;
               this.gwaiReportDecision(profile,false,"pass" + " shared-policy " + decisionMs + "ms");
               return false;
            }
            var manager:CardManager = CardManager.getInstance();
            if(move.kind == "leader")
            {
               var leader:CardLeaderInstance = manager.getCardLeader(this.playerID);
               if(leader == null || !leader.canBeUsed || leader.instanceId != int(move.instanceId))
                  throw new Error("selected leader action is no longer legal for this seat");
               leader.recalculatePowerPotential(manager);
               var leaderTransaction:CardTransaction = leader.getOptimalTransaction();
               if(leaderTransaction == null || leaderTransaction.strategicValue == -1)
                  throw new Error("selected leader action has no legal transaction");
               this._decidedCardTransaction = leaderTransaction;
               this.attitude = TACTIC_MAXIMIZE_WIN;
               decisionMs = startedAt < 0 ? -1 : getTimer() - startedAt;
               this.gwaiReportDecision(profile,false,"leader" + " shared-policy " + decisionMs + "ms");
               return false;
            }
            if(move.kind != "play") throw new Error("policy returned an unknown action");
            var card:CardInstance = manager.getCardInstance(int(move.instanceId));
            var targetSlot:int = move.slotId == undefined ? int(move.row) : int(move.slotId);
            var targetInstanceId:Number = move.targetInstanceId == undefined ? -1 : Number(move.targetInstanceId);
            var targetCard:CardInstance = targetInstanceId < 0 ? null : manager.getCardInstance(int(targetInstanceId));
            var isGlobalScorch:Boolean = card != null && targetInstanceId < 0 && card.templateRef.isType(CardTemplate.CardType_Global_Effect) && card.templateRef.hasEffect(CardTemplate.CardEffect_Scorch);
            var legalTarget:Boolean = targetInstanceId >= 0 ? targetCard != null && card != null && card.canBeCastOn(targetCard) : isGlobalScorch || card != null && card.canBePlacedInSlot(targetSlot,int(move.targetPlayer));
            if(card == null || card.listsPlayer != this.playerID || card.inList != CardManager.CARD_LIST_LOC_HAND || !legalTarget)
               throw new Error("selected move is no longer legal for this seat");
            var transaction:CardTransaction = new CardTransaction();
            transaction.sourceCardInstanceRef = card;
            transaction.targetSlotID = targetInstanceId >= 0 || isGlobalScorch ? CardManager.CARD_LIST_LOC_INVALID : targetSlot;
            transaction.targetPlayerID = targetInstanceId >= 0 ? this.playerID : (isGlobalScorch ? CardManager.PLAYER_INVALID : int(move.targetPlayer));
            transaction.targetCardInstanceRef = targetInstanceId >= 0 ? targetCard : null;
            transaction.powerChangeResult = Number(move.scoreGain);
            transaction.strategicValue = Number(move.scoreGain);
            this._decidedCardTransaction = transaction;
            this.attitude = TACTIC_MAXIMIZE_WIN;
            decisionMs = startedAt < 0 ? -1 : getTimer() - startedAt;
            this.gwaiReportDecision(profile,false,"play " + card.templateId + " shared-policy " + decisionMs + "ms");
            return false;
         }
         catch(error:Error)
         {
            this.gwaiReportDecision(profile,true,stage + " failed: " + error.toString());
            this.gwaiCancel();
            this.gwaiNativeFallback();
            return false;
         }
      }

      private function gwaiReportDecision(profile:int, fallback:Boolean, detail:String) : void
      {
         var difficulty:String = profile == 1 ? "Easy" : (profile == 2 ? "Medium" : "Hard");
         if(fallback)
         {
            CardManager.log("#W3AI# fallback: " + detail);
            this.gameFlowControllerRef.gwaiNotifyDecision(profile,true,detail);
         }
         else
         {
            CardManager.log("#W3AI# decision: " + difficulty + " " + detail + " " + "checkpoint=" + GwaiSharedConfig.data().CHECKPOINT_SHA256 + " weights=" + GwaiSharedConfig.data().EXPORT_SHA256);
            this.gameFlowControllerRef.gwaiNotifyDecision(profile,false,detail);
         }
      }

      private function gwaiNativeFallback() : void
      {
__GWAI_NATIVE_FALLBACK_BODY__
      }
"""

FLOW_METHODS = r"""      private var _gwaiSettings:Object = null;
      private var _gwaiSignalShown:Boolean = false;
      private var _gwaiInitiative:int = -1;
      private var _gwaiDecisionId:int = 0;
      private var _gwaiMatchEnded:Boolean = true;

      public function gwaiConfigureSettings(data:Object) : void
      {
         if(data == null || int(data.version) != 1)
         {
            this._gwaiSettings = null;
            return;
         }
         var vanillaDifficulty:int = int(data.vanillaDifficulty);
         if(vanillaDifficulty < 0 || vanillaDifficulty > 3) vanillaDifficulty = 2;
         var signal:Boolean = data.signal == undefined ? false : Boolean(data.signal);
         this._gwaiSettings = {
            enabled:Boolean(data.enabled),vanillaDifficulty:vanillaDifficulty,
            signal:signal,
            trace:Boolean(data.trace),
            practice:Boolean(data.practice),practiceOpponent:String(data.practiceOpponent),
            practiceDeck:String(data.practiceDeck),practiceDeckDifficulty:int(data.practiceDeckDifficulty)
         };
      }

      public function gwaiTier() : int
      {
         if(this._gwaiSettings == null) return -1;
         return Math.max(1,int(this._gwaiSettings.vanillaDifficulty));
      }

      public function gwaiProfileForPlayer(side:int) : int
      {
         if(side != CardManager.PLAYER_2 || this._gwaiSettings == null
            || !this._gwaiSettings.enabled || this._gwaiMatchEnded) return -1;
         return this.gwaiTier();
      }

      public function gwaiMatchEnded() : Boolean
      {
         return this._gwaiMatchEnded;
      }

      public function gwaiPolicyInitiative() : int
      {
         return this._gwaiInitiative < 0 ? this.currentPlayer : this._gwaiInitiative;
      }

      public function gwaiPolicyDecisionId() : int
      {
         return this._gwaiDecisionId;
      }

      public function gwaiPolicyRound() : int
      {
         return this.currentRound + 1;
      }

      public function gwaiTraceSelection() : Object
      {
         if(this._gwaiSettings == null) return null;
         var enabled:Boolean = GwaiSharedConfig.enabled() && Boolean(this._gwaiSettings.enabled);
         var tier:int = enabled ? this.gwaiTier() : 0;
         var data:Object = enabled ? GwaiSharedConfig.data() : null;
         return {enabled:enabled,requested_tier:Math.max(1,int(this._gwaiSettings.vanillaDifficulty)),
            effective_tier:tier,mode:this._gwaiSettings.practice ? "practice" : "story",
            opponent:String(this._gwaiSettings.practiceDeck),
            npc_difficulty:Math.max(1,Math.min(3,int(this._gwaiSettings.practiceDeckDifficulty))),
            checkpoint_sha256:data == null ? "" : String(data.CHECKPOINT_SHA256),
            weights_sha256:data == null ? "" : String(data.EXPORT_SHA256)};
      }

      public function gwaiNotifyDecision(profile:int, fallback:Boolean, detail:String = "") : void
      {
         this.gwaiRecordDecision(fallback ? "vanilla" : "network",detail);
         if(this._gwaiSettings == null || !this._gwaiSettings.signal || this.mcMessageQueue == null) return;
         if(fallback)
         {
            if(detail.indexOf("unknown card ") == 0)
               this.mcMessageQueue.PushMessage("W3 Gwent AI: " + detail + ", vanilla AI plays this turn");
            else
               this.mcMessageQueue.PushMessage("W3 Gwent AI: fallback (vanilla AI plays this turn)");
            return;
         }
         var difficulty:String = profile == 1 ? "Easy" : (profile == 2 ? "Medium" : "Hard");
         if(detail.indexOf("shared-policy ") >= 0 && this.mcMessageQueue != null)
         {
            this._gwaiSignalShown = true;
            this.mcMessageQueue.PushMessage("W3 Gwent AI: " + difficulty + " " + detail);
            return;
         }
         if(this._gwaiSignalShown) return;
         this._gwaiSignalShown = true;
         this.mcMessageQueue.PushMessage("W3 Gwent AI: " + difficulty);
      }

      public function gwaiRecordDecision(kind:String, detail:String) : void
      {
         // This bridge uses plain strings: diagnostics must not depend on JSON
         // support or on CardManager.debugVerbose being enabled in Scaleform.
         try
         {
            if(GwintGameMenu.mSingleton != null)
               GwintGameMenu.mSingleton.dispatchEvent(new red.core.events.GameEvent(red.core.events.GameEvent.CALL,
                  "OnW3GwentAIDiagnostic",[kind,detail]));
         }
         catch(diagnosticError:Error) {}
      }

      public function gwaiNotifyStockTurn() : void
      {
         if(this._gwaiSettings == null || this._gwaiSettings.enabled) return;
         var mode:String = this._gwaiSettings.practice ? "practice" : "story";
         this.gwaiRecordDecision("stock","selected for " + mode);
         try
         {
            if(this._gwaiSettings.signal && this.mcMessageQueue != null)
               this.mcMessageQueue.PushMessage("W3 Gwent AI: stock AI (selected for " + mode + ")");
         }
         catch(stockSignalError:Error) {}
      }

      public function gwaiPracticeBanner() : void
      {
         if(this._gwaiSettings == null || !this._gwaiSettings.practice || !this._gwaiSettings.signal || this.mcMessageQueue == null) return;
         try
         {
            var tier:int = this.gwaiTier();
            var policy:String = GwaiSharedConfig.enabled() && this._gwaiSettings.enabled ? "AI: " + ["Easy","Normal","Hard"][tier - 1] : "Stock AI";
            var level:int = int(this._gwaiSettings.practiceDeckDifficulty);
            var deckLevel:String = level == 1 ? "Easy" : (level == 3 ? "Hard" : "Normal");
            this.mcMessageQueue.PushMessage("Practice: " + this._gwaiSettings.practiceOpponent + " | " + policy + " | NPC deck: " + deckLevel);
         }
         catch(practiceError:Error) {}
      }

      public function gwaiCancelPending() : void
      {
         if(this.playerControllers == null) return;
         for each(var baseController:BasePlayerController in this.playerControllers)
         {
            var controller:AIPlayerController = baseController as AIPlayerController;
            if(controller != null) controller.gwaiCancel();
         }
      }
"""

MENU_METHOD = r"""
      public function gwaiConfigureModSettings(data:Object) : void
      {
         this.gameFlowController.gwaiConfigureSettings(data);
      }
"""
