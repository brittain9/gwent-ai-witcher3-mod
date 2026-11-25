"""Add public proof-boundary telemetry to an explicitly diagnostic native build."""


def diagnostic_proof_trace(patched, public_trace, *, diagnostic):
    if diagnostic is not True:
        raise ValueError(
            "proof telemetry is restricted to an explicit diagnostic build"
        )
    result = dict(patched)
    ai = result["AIPlayerController"]
    if "GwaiPlayTrace.terminalProof(" in ai:
        raise ValueError("diagnostic proof callback is already present")
    anchor = "         var valid:Boolean = this.gwaiTerminalProofStillValid();"
    if ai.count(anchor) != 1:
        raise ValueError("native proof commit seam differs")
    ai = ai.replace(
        anchor,
        anchor
        + "\n         try { GwaiPlayTrace.terminalProof(this.playerID,this._gwaiProofTicket,this._gwaiProofDecision,valid); } catch(proofTraceError:Error) {}",
        1,
    )
    anchor = "   private static function transactionRecord(transaction:CardTransaction) : Object"
    if ai.count(anchor) != 1 or public_trace.count(anchor) != 1:
        raise ValueError("public trace helper seam differs")
    addition = """   public static function terminalProof(seat:int, ticket:Number, decision:int, valid:Boolean) : void
   {
      if(!active) return;
      emit({event:"terminal_proof",seat:seat,ticket:ticket,decision_id:decision,valid:valid});
   }

"""
    result["AIPlayerController"] = ai.replace(anchor, addition + anchor, 1)
    public_trace = public_trace.replace(anchor, addition + anchor, 1)
    if public_trace not in result["AIPlayerController"]:
        raise ValueError("diagnostic trace helper and host differ")
    return result, public_trace
