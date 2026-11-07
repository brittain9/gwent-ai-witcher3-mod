// Public, local-only play-by-play. Every received event is flushed immediately.
@addField( CR4GwintGameMenu ) var gwaiTraceMatchId : int;
@addField( CR4GwintGameMenu ) var gwaiTraceChunkIndex : int;
@addField( CR4GwintGameMenu ) var gwaiTraceActive : bool;

function GwaiPlayTracePad(value : int) : string
{
    var result : string = IntToString(value);
    while (StrLen(result) < 9)
    {
        result = "0" + result;
    }
    return result;
}

@addMethod( CR4GwintGameMenu ) event OnW3GwentPlayTraceStart(build : string)
{
    var cfg : CInGameConfigWrapper = theGame.GetInGameConfigWrapper();
    var raw : string = cfg.GetRawConfigValueByStr("W3GwentAI", "GwaiTrace");
    var prior : string = cfg.GetRawConfigValueByStr("W3GwentPlayTrace", "active_match");
    var nextMatch : int;
    var prefix : string;
    gwaiTraceActive = false;
    if (raw != "true" && raw != "1")
    {
        return false;
    }
    if (prior != "" && cfg.GetRawConfigValueByStr("W3GwentPlayTrace", prior + "_status") == "active")
    {
        cfg.SetRawConfigValueByStr("W3GwentPlayTrace", prior + "_status", "interrupted");
    }
    nextMatch = StringToInt(cfg.GetRawConfigValueByStr("W3GwentPlayTrace", "next_match"), 0) + 1;
    if (nextMatch < 1)
    {
        nextMatch = 1;
    }
    if (nextMatch >= 999999999)
    {
        return false;
    }
    prefix = "t" + GwaiPlayTracePad(nextMatch);
    while (cfg.GetRawConfigValueByStr("W3GwentPlayTrace", prefix + "_status") != "" || cfg.GetRawConfigValueByStr("W3GwentPlayTrace", prefix + "_count") != "")
    {
        nextMatch += 1;
        if (nextMatch >= 999999999)
        {
            return false;
        }
        prefix = "t" + GwaiPlayTracePad(nextMatch);
    }
    gwaiTraceMatchId = nextMatch;
    gwaiTraceChunkIndex = 0;
    gwaiTraceActive = true;
    cfg.SetRawConfigValueByStr("W3GwentPlayTrace", "next_match", IntToString(nextMatch));
    cfg.SetRawConfigValueByStr("W3GwentPlayTrace", "active_match", prefix);
    cfg.SetRawConfigValueByStr("W3GwentPlayTrace", prefix + "_status", "active");
    cfg.SetRawConfigValueByStr("W3GwentPlayTrace", prefix + "_build", build);
    cfg.SetRawConfigValueByStr("W3GwentPlayTrace", prefix + "_count", "0");
    theGame.SaveUserSettings();
    return true;
}

@addMethod( CR4GwintGameMenu ) event OnW3GwentPlayTraceRecord(record : string)
{
    var cfg : CInGameConfigWrapper = theGame.GetInGameConfigWrapper();
    var prefix : string = "t" + GwaiPlayTracePad(gwaiTraceMatchId);
    var body : string = record + "\n";
    var offset : int = 0;
    var part : string;
    var needed : int = (StrLen(body) + 779) / 780;
    if (!gwaiTraceActive)
    {
        return false;
    }
    // Bound one match to 20,000 chunks (15.6 million characters). Stop
    // explicitly before writing any part of an event that would exceed it.
    if (gwaiTraceChunkIndex + needed > 20000)
    {
        cfg.SetRawConfigValueByStr("W3GwentPlayTrace", prefix + "_status", "truncated");
        cfg.SetRawConfigValueByStr("W3GwentPlayTrace", "active_match", "");
        gwaiTraceActive = false;
        theGame.SaveUserSettings();
        return false;
    }
    while (offset < StrLen(body))
    {
        part = StrMid(body, offset, 780);
        cfg.SetRawConfigValueByStr("W3GwentPlayTrace", prefix + "_" + GwaiPlayTracePad(gwaiTraceChunkIndex), part);
        gwaiTraceChunkIndex += 1;
        offset += 780;
    }
    cfg.SetRawConfigValueByStr("W3GwentPlayTrace", prefix + "_count", IntToString(gwaiTraceChunkIndex));
    theGame.SaveUserSettings();
    return true;
}

@addMethod( CR4GwintGameMenu ) event OnW3GwentPlayTraceEnd(reason : string)
{
    var cfg : CInGameConfigWrapper = theGame.GetInGameConfigWrapper();
    var prefix : string = "t" + GwaiPlayTracePad(gwaiTraceMatchId);
    if (!gwaiTraceActive)
    {
        return false;
    }
    if (reason != "final" && reason != "interrupted" && reason != "error")
    {
        reason = "error";
    }
    cfg.SetRawConfigValueByStr("W3GwentPlayTrace", prefix + "_status", reason);
    cfg.SetRawConfigValueByStr("W3GwentPlayTrace", "active_match", "");
    gwaiTraceActive = false;
    theGame.SaveUserSettings();
    return true;
}

// Plain-string error channel: never depends on the encoder that just failed.
@addMethod( CR4GwintGameMenu ) event OnW3GwentPlayTraceError(message : string)
{
    var cfg : CInGameConfigWrapper = theGame.GetInGameConfigWrapper();
    var prefix : string = "t" + GwaiPlayTracePad(gwaiTraceMatchId);
    if (!gwaiTraceActive)
    {
        return false;
    }
    cfg.SetRawConfigValueByStr("W3GwentPlayTrace", prefix + "_error", StrLeft(message, 160));
    cfg.SetRawConfigValueByStr("W3GwentPlayTrace", prefix + "_status", "error");
    cfg.SetRawConfigValueByStr("W3GwentPlayTrace", "active_match", "");
    gwaiTraceActive = false;
    theGame.SaveUserSettings();
    return true;
}
