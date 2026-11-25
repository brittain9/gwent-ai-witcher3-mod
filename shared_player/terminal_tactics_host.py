"""Revalidate a proved native move immediately before card application."""

from .host import HostIntegrationError, _body


def patch_proof_commit(sources):
    result = dict(sources)
    try:
        base = result["BasePlayerController"]
        start, _ = _body(base, "state_begin_ApplyingCard")
        guard = """
         var gwaiProofAI:AIPlayerController = this as AIPlayerController;
         if(gwaiProofAI != null && !gwaiProofAI.gwaiTerminalProofCommit())
         {
            this.declineCardTransaction();
            this._decidedCardTransaction = null;
            this._stateMachine.ChangeState("ChoosingMove");
            return;
         }
"""
        if "var gwaiProofAI:" in base:
            raise HostIntegrationError("proof commit already present")
        result["BasePlayerController"] = base[:start] + guard + base[start:]
    except (KeyError, HostIntegrationError) as error:
        raise HostIntegrationError("terminal proof commit seam differs") from error
    return result
