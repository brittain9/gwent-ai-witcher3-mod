// Diagnostic-build-only actor observations. Stored separately from public play-by-play.
@addField( CR4GwintGameMenu ) var gwaiPolicyDiagnosticMatchId : int;
@addField( CR4GwintGameMenu ) var gwaiPolicyDiagnosticChunkIndex : int;
@addField( CR4GwintGameMenu ) var gwaiPolicyDiagnosticActive : bool;

function GwaiPolicyDiagnosticPad(value : int) : string
{
    var result : string = IntToString(value);
    while (StrLen(result) < 9)
    {
        result = "0" + result;
    }
    return result;
}

@addMethod( CR4GwintGameMenu ) event OnW3GwentPolicyDiagnosticStart(build : string)
{
    var cfg : CInGameConfigWrapper = theGame.GetInGameConfigWrapper();
    var prior : string = cfg.GetRawConfigValueByStr("W3GwentPolicyDiagnostic", "active_match");
    var nextMatch : int;
    var prefix : string;
    gwaiPolicyDiagnosticActive = false;
    if (prior != "" && cfg.GetRawConfigValueByStr("W3GwentPolicyDiagnostic", prior + "_status") == "active")
    {
        cfg.SetRawConfigValueByStr("W3GwentPolicyDiagnostic", prior + "_status", "interrupted");
    }
    nextMatch = StringToInt(cfg.GetRawConfigValueByStr("W3GwentPolicyDiagnostic", "next_match"), 0) + 1;
    if (nextMatch < 1)
    {
        nextMatch = 1;
    }
    if (nextMatch >= 999999999)
    {
        return false;
    }
    prefix = "t" + GwaiPolicyDiagnosticPad(nextMatch);
    while (cfg.GetRawConfigValueByStr("W3GwentPolicyDiagnostic", prefix + "_status") != "" || cfg.GetRawConfigValueByStr("W3GwentPolicyDiagnostic", prefix + "_count") != "")
    {
        nextMatch += 1;
        if (nextMatch >= 999999999)
        {
            return false;
        }
        prefix = "t" + GwaiPolicyDiagnosticPad(nextMatch);
    }
    gwaiPolicyDiagnosticMatchId = nextMatch;
    gwaiPolicyDiagnosticChunkIndex = 0;
    gwaiPolicyDiagnosticActive = true;
    cfg.SetRawConfigValueByStr("W3GwentPolicyDiagnostic", "next_match", IntToString(nextMatch));
    cfg.SetRawConfigValueByStr("W3GwentPolicyDiagnostic", "active_match", prefix);
    cfg.SetRawConfigValueByStr("W3GwentPolicyDiagnostic", prefix + "_status", "active");
    cfg.SetRawConfigValueByStr("W3GwentPolicyDiagnostic", prefix + "_build", build);
    cfg.SetRawConfigValueByStr("W3GwentPolicyDiagnostic", prefix + "_count", "0");
    theGame.SaveUserSettings();
    return true;
}

@addMethod( CR4GwintGameMenu ) event OnW3GwentPolicyDiagnosticRecord(record : string)
{
    var cfg : CInGameConfigWrapper = theGame.GetInGameConfigWrapper();
    var prefix : string = "t" + GwaiPolicyDiagnosticPad(gwaiPolicyDiagnosticMatchId);
    var body : string = record + "\n";
    var offset : int = 0;
    var part : string;
    var needed : int = (StrLen(body) + 779) / 780;
    if (!gwaiPolicyDiagnosticActive)
    {
        return false;
    }
    // Bound one match to 20,000 chunks (15.6 million characters). Stop
    // explicitly before writing any part of an event that would exceed it.
    if (gwaiPolicyDiagnosticChunkIndex + needed > 20000)
    {
        cfg.SetRawConfigValueByStr("W3GwentPolicyDiagnostic", prefix + "_status", "truncated");
        cfg.SetRawConfigValueByStr("W3GwentPolicyDiagnostic", "active_match", "");
        gwaiPolicyDiagnosticActive = false;
        theGame.SaveUserSettings();
        return false;
    }
    while (offset < StrLen(body))
    {
        part = StrMid(body, offset, 780);
        cfg.SetRawConfigValueByStr("W3GwentPolicyDiagnostic", prefix + "_" + GwaiPolicyDiagnosticPad(gwaiPolicyDiagnosticChunkIndex), part);
        gwaiPolicyDiagnosticChunkIndex += 1;
        offset += 780;
    }
    cfg.SetRawConfigValueByStr("W3GwentPolicyDiagnostic", prefix + "_count", IntToString(gwaiPolicyDiagnosticChunkIndex));
    theGame.SaveUserSettings();
    return true;
}

@addMethod( CR4GwintGameMenu ) event OnW3GwentPolicyDiagnosticEnd(reason : string)
{
    var cfg : CInGameConfigWrapper = theGame.GetInGameConfigWrapper();
    var prefix : string = "t" + GwaiPolicyDiagnosticPad(gwaiPolicyDiagnosticMatchId);
    if (!gwaiPolicyDiagnosticActive)
    {
        return false;
    }
    if (reason != "final" && reason != "interrupted" && reason != "error" && reason != "truncated")
    {
        reason = "error";
    }
    cfg.SetRawConfigValueByStr("W3GwentPolicyDiagnostic", prefix + "_status", reason);
    cfg.SetRawConfigValueByStr("W3GwentPolicyDiagnostic", "active_match", "");
    gwaiPolicyDiagnosticActive = false;
    theGame.SaveUserSettings();
    return true;
}

// Plain-string error channel: never depends on the encoder that just failed.
@addMethod( CR4GwintGameMenu ) event OnW3GwentPolicyDiagnosticError(message : string)
{
    var cfg : CInGameConfigWrapper = theGame.GetInGameConfigWrapper();
    var prefix : string = "t" + GwaiPolicyDiagnosticPad(gwaiPolicyDiagnosticMatchId);
    if (!gwaiPolicyDiagnosticActive)
    {
        return false;
    }
    cfg.SetRawConfigValueByStr("W3GwentPolicyDiagnostic", prefix + "_error", StrLeft(message, 160));
    cfg.SetRawConfigValueByStr("W3GwentPolicyDiagnostic", prefix + "_status", "error");
    cfg.SetRawConfigValueByStr("W3GwentPolicyDiagnostic", "active_match", "");
    gwaiPolicyDiagnosticActive = false;
    theGame.SaveUserSettings();
    return true;
}
