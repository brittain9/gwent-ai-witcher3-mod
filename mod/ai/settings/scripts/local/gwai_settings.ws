function GwaiSettingsInitialize() : void
{
    var cfg : CInGameConfigWrapper = theGame.GetInGameConfigWrapper();
    var enabled : string = cfg.GetRawConfigValueByStr("W3GwentAI", "GwaiEnabled");
    var settingsVersion : string = cfg.GetRawConfigValueByStr("W3GwentAI", "GwaiSettingsVersion");
    var signal : string = cfg.GetRawConfigValueByStr("W3GwentAI", "GwaiSignal");
    var normalizedEnabled : string = "true";
    var normalizedSignal : string = "false";
    var menuGroupIndex : int = cfg.GetGroupIdx("W3GwentAI");
    // Keep native matrix-loading evidence in the local --report output.
    cfg.SetRawConfigValueByStr("W3GwentAI", "GwaiMenuGroupIndex", IntToString(menuGroupIndex));
    if (menuGroupIndex >= 0)
    {
        cfg.SetRawConfigValueByStr("W3GwentAI", "GwaiMenuPath", cfg.GetGroupDisplayName('W3GwentAI'));
        cfg.SetRawConfigValueByStr("W3GwentAI", "GwaiMenuVarCount", IntToString(cfg.GetVarsNumByGroupName('W3GwentAI')));
    }
    if (settingsVersion == "" || settingsVersion == "1")
    {
        cfg.SetRawConfigValueByStr("W3GwentAI", "GwaiEnabled", "true");
        enabled = "true";
    }
    if (settingsVersion != "3")
    {
        cfg.SetRawConfigValueByStr("W3GwentAI", "GwaiSettingsVersion", "3");
    }
    if (enabled == "false" || enabled == "0")
    {
        normalizedEnabled = "false";
    }
    if (enabled != normalizedEnabled)
    {
        cfg.SetRawConfigValueByStr("W3GwentAI", "GwaiEnabled", normalizedEnabled);
    }
    if (signal == "true" || signal == "1")
    {
        normalizedSignal = "true";
    }
    if (signal != normalizedSignal)
    {
        cfg.SetRawConfigValueByStr("W3GwentAI", "GwaiSignal", normalizedSignal);
    }
    GwaiSettingsNormalizeOff(cfg, "GwaiCoach");
    GwaiSettingsNormalizeOff(cfg, "GwaiTrace");
    GwaiSettingsNormalizeOff(cfg, "GwaiStory");
    GwaiSettingsNormalizeOff(cfg, "GwaiLab");
    GwaiSettingsNormalizeOff(cfg, "GwaiMeter");
    GwaiSettingsNormalizeOff(cfg, "GwaiPostGame");
    GwaiSettingsNormalizeOff(cfg, "GwaiBlunder");
    GwaiSettingsNormalizeOff(cfg, "GwaiStats");
    if (cfg.GetRawConfigValueByStr("W3GwentAI", "GwaiDevOpponent") == "")
    {
        cfg.SetRawConfigValueByStr("W3GwentAI", "GwaiDevOpponent", "0");
    }
    if (cfg.GetRawConfigValueByStr("W3GwentAI", "GwaiDevTier") != "0" && cfg.GetRawConfigValueByStr("W3GwentAI", "GwaiDevTier") != "1" && cfg.GetRawConfigValueByStr("W3GwentAI", "GwaiDevTier") != "2")
    {
        cfg.SetRawConfigValueByStr("W3GwentAI", "GwaiDevTier", "2");
    }
}

function GwaiPracticeAI() : int
{
    if (!GwaiSettingsEnabled())
    {
        return 1;
    }
    return 2;
}

function GwaiPracticeTier() : int
{
    var raw : string = theGame.GetInGameConfigWrapper().GetRawConfigValueByStr("W3GwentAI", "GwaiDevTier");
    if (raw == "0" || raw == "1" || raw == "2")
    {
        return StringToInt(raw, 2) + 1;
    }
    return 3;
}

// Fun features are opt-in: a missing or unknown value is off.
function GwaiSettingsNormalizeOff(cfg : CInGameConfigWrapper, id : string) : void
{
    var raw : string = cfg.GetRawConfigValueByStr("W3GwentAI", id);
    var normalized : string = "false";
    if (raw == "true" || raw == "1")
    {
        normalized = "true";
    }
    if (raw != normalized)
    {
        cfg.SetRawConfigValueByStr("W3GwentAI", id, normalized);
    }
}

function GwaiSettingsToggleOn(id : string) : bool
{
    var raw : string = theGame.GetInGameConfigWrapper().GetRawConfigValueByStr("W3GwentAI", id);
    return raw == "true" || raw == "1";
}

// Lab tools (meter, post-game card, blunder alert, match stats) are on only under Lab mode.
function GwaiLabToggleOn(id : string) : bool
{
    return GwaiSettingsToggleOn("GwaiLab") && GwaiSettingsToggleOn(id);
}

function GwaiStatsCount(key : string) : int
{
    return StringToInt(theGame.GetInGameConfigWrapper().GetRawConfigValueByStr("W3GwentAIStats", key), 0);
}

function GwaiStatsSend(packet : CScriptedFlashObject, deckName : string) : void
{
    var buckets : array< string >;
    var outcomes : array< string >;
    var i : int;
    var j : int;
    buckets.PushBack("vanilla");
    buckets.PushBack("easy");
    buckets.PushBack("normal");
    buckets.PushBack("hard");
    outcomes.PushBack("won");
    outcomes.PushBack("lost");
    outcomes.PushBack("drawn");
    for (i = 0; i < buckets.Size(); i += 1)
    {
        for (j = 0; j < outcomes.Size(); j += 1)
        {
            packet.SetMemberFlashInt("stats_" + buckets[i] + "_" + outcomes[j], GwaiStatsCount(buckets[i] + "_" + outcomes[j]));
        }
    }
    for (j = 0; j < outcomes.Size(); j += 1)
    {
        packet.SetMemberFlashInt("stats_deck_" + outcomes[j], GwaiStatsCount("vs_" + deckName + "_" + outcomes[j]));
    }
}

function GwaiSettingsSignal() : bool
{
    var raw : string = theGame.GetInGameConfigWrapper().GetRawConfigValueByStr("W3GwentAI", "GwaiSignal");
    return raw == "true" || raw == "1";
}

function GwaiSettingsEnabled() : bool
{
    var raw : string = theGame.GetInGameConfigWrapper().GetRawConfigValueByStr("W3GwentAI", "GwaiEnabled");
    return raw == "true" || raw == "1";
}

@wrapMethod(CR4IngameMenu)
function OnConfigUI()
{
    GwaiSettingsInitialize();
    wrappedMethod();
}

function GwaiNameOptions(entries : CScriptedFlashArray, groupID : int) : void
{
    var index : int;
    var childIndex : int;
    var optionGroup : CScriptedFlashObject;
    var children : CScriptedFlashArray;
    var child : CScriptedFlashObject;
    if (!entries || groupID < 0)
    {
        return;
    }
    for (index = 0; index < entries.GetLength(); index += 1)
    {
        optionGroup = entries.GetElementFlashObject(index);
        if (optionGroup)
        {
            children = optionGroup.GetMemberFlashArray("subElements");
            if (children && optionGroup.GetMemberFlashString("id") == "mainmenu_gwent")
            {
                for (childIndex = 0; childIndex < children.GetLength(); childIndex += 1)
                {
                    child = children.GetElementFlashObject(childIndex);
                    if (child && child.GetMemberFlashInt("groupID") == groupID)
                    {
                        optionGroup.SetMemberFlashString("label", "Gwent: A Worthy Opponent");
                        optionGroup.SetMemberFlashString("listTitle", "Gwent: A Worthy Opponent");
                    }
                }
            }
            GwaiNameOptions(children, groupID);
        }
    }
}

@wrapMethod(CR4IngameMenu)
function showOptionsPanel() : void
{
    var entries : CScriptedFlashArray;
    wrappedMethod();
    entries = IngameMenu_FillOptionsSubMenuData(m_flashValueStorage, isMainMenu);
    GwaiNameOptions(entries, theGame.GetInGameConfigWrapper().GetGroupIdx("W3GwentAI"));
    m_flashValueStorage.SetFlashArray("ingamemenu.options.entries", entries);
}

// Use the game's test-match route, without entering an NPC's quest or wager.
// The deck builder keeps the player's existing collection and selected deck.
@wrapMethod(CR4IngameMenu)
function OnButtonClicked(groupId : int, optionName : name)
{
    var cfg : CInGameConfigWrapper;
    var manager : CR4GwintManager;
    var pauseMenu : CR4CommonIngameMenu;
    var opponent : name;
    var selection : string;
    var handled : bool;
    handled = wrappedMethod(groupId, optionName);
    cfg = theGame.GetInGameConfigWrapper();
    if (cfg.GetGroupName(groupId) != 'W3GwentAI')
    {
        return handled;
    }
    if (optionName == 'GwaiDevReport')
    {
        theGame.GetGuiManager().ShowUserDialogAdv(0, "Gwent: A Worthy Opponent", GwaiVerificationText(), false, UDB_Ok);
        return true;
    }
    if (optionName != 'GwaiDevStart')
    {
        return handled;
    }
    pauseMenu = (CR4CommonIngameMenu)GetRootMenu();
    if (isMainMenu || !thePlayer || !pauseMenu)
    {
        theGame.GetGuiManager().ShowUserDialogAdv(0, "Gwent: A Worthy Opponent", "Load a saved game, then open Settings > Mods to start a practice match.", false, UDB_Ok);
        return true;
    }
    if (thePlayer.IsInCombat() || theGame.IsDialogOrCutscenePlaying())
    {
        theGame.GetGuiManager().ShowUserDialogAdv(0, "Gwent: A Worthy Opponent", "Finish combat or dialogue before starting a practice match.", false, UDB_Ok);
        return true;
    }
    manager = theGame.GetGwintManager();
    if (manager.gameRequested || manager.testMatch || manager.gwaiDevActive)
    {
        theGame.GetGuiManager().ShowUserDialogAdv(0, "Gwent: A Worthy Opponent", "A Gwent match is already opening or active.", false, UDB_Ok);
        return true;
    }
    selection = cfg.GetRawConfigValueByStr("W3GwentAI", "GwaiDevOpponent");
    opponent = GwaiPracticeOpponent(StringToInt(selection, 0) + 1);
    manager.GwaiDevBegin(opponent);
    pauseMenu.CloseMenu();
    theGame.RequestMenu('DeckBuilder');
    return true;
}

// Native testMatch suppresses a fade, not result or tutorial writes. Keep the
// manager's previous state until practice ends, including deck-builder cancel.
@addField(CR4GwintManager) var gwaiDevActive : bool;
@addField(CR4GwintManager) var gwaiDevOldState : EMinigameState;
@addField(CR4GwintManager) var gwaiDevOldTutorial : bool;
@addField(CR4GwintManager) var gwaiDevOldDeckTutorial : bool;
@addField(CR4GwintManager) var gwaiDevOldDoubleAI : bool;
@addField(CR4GwintManager) var gwaiDevOldFaction : eGwintFaction;
@addField(CR4GwintManager) var gwaiDevOldEnemy : name;
@addField(CR4GwintManager) var gwaiDevOldExtra : array<name>;
@addField(CR4GwintManager) var gwaiDevAI : int;
@addField(CR4GwintManager) var gwaiDevTier : int;
@addField(CR4GwintManager) var gwaiDevLabel : string;

@addMethod(CR4GwintManager)
function GwaiDevBegin(opponent : name)
{
    var extraCards : array<name>;
    gwaiDevOldEnemy = selectedEnemyDeck;
    gwaiDevOldState = thePlayer.GetGwintMinigameState();
    gwaiDevOldTutorial = GetHasDoneTutorial();
    gwaiDevOldDeckTutorial = GetHasDoneDeckTutorial();
    gwaiDevOldDoubleAI = getDoubleAIEnabled();
    gwaiDevOldFaction = GetForcedFaction();
    gwaiDevOldExtra = GetAdditionalCards();
    gwaiDevActive = true;
    gwaiDevAI = GwaiPracticeAI();
    gwaiDevTier = GwaiPracticeTier();
    gwaiDevLabel = GwaiPracticeOpponentLabel(StringToInt(theGame.GetInGameConfigWrapper().GetRawConfigValueByStr("W3GwentAI", "GwaiDevOpponent"), 0) + 1);
    SetHasDoneTutorial(true);
    SetHasDoneDeckTutorial(true);
    SetEnemyDeck(opponent);
    SetAdditionalCards(extraCards);
    SetForcedFaction(GwintFaction_Neutral);
    setDoubleAIEnabled(false);
    testMatch = true;
    gameRequested = true;
}

@addMethod(CR4GwintManager)
function GwaiDevRestore()
{
    if (!gwaiDevActive)
    {
        return;
    }
    gwaiDevActive = false;
    thePlayer.SetGwintMinigameState(gwaiDevOldState);
    SetHasDoneTutorial(gwaiDevOldTutorial);
    SetHasDoneDeckTutorial(gwaiDevOldDeckTutorial);
    SetEnemyDeck(gwaiDevOldEnemy);
    SetAdditionalCards(gwaiDevOldExtra);
    SetForcedFaction(gwaiDevOldFaction);
    setDoubleAIEnabled(gwaiDevOldDoubleAI);
    testMatch = false;
    gameRequested = false;
    gwaiDevAI = 0;
    gwaiDevTier = 0;
    gwaiDevLabel = "";
    gwaiDevOldExtra.Clear();
}

@wrapMethod(CR4GwintGameMenu)
function OnClosingMenu()
{
    wrappedMethod();
    GwaiDiagnosticsArchive();
    OnW3GwentPlayTraceEnd("interrupted");
    theGame.GetGwintManager().GwaiDevRestore();
    theGame.SaveUserSettings();
}

@wrapMethod(CR4DeckBuilderMenu)
function OnClosingMenu()
{
    var startingMatch : bool;
    startingMatch = theGame.GetGwintManager().gameRequested;
    wrappedMethod();
    if (!startingMatch)
    {
        theGame.GetGwintManager().GwaiDevRestore();
    }
}

@wrapMethod(CR4GwintGameMenu)
function OnConfigUI()
{
    var packet : CScriptedFlashObject;
    var deck : SDeckDefinition;
    var deckName : string;
    var practice : bool;
    var enabled : bool;
    var policyDifficulty : int;
    GwaiSettingsInitialize();
    GwaiDiagnosticsInitialize();
    wrappedMethod();
    practice = gwintManager.gwaiDevActive;
    enabled = GwaiSettingsEnabled();
    policyDifficulty = FactsQueryLatestValue('gwent_difficulty');
    if (practice)
    {
        enabled = enabled && gwintManager.gwaiDevAI == 2;
        policyDifficulty = gwintManager.gwaiDevTier;
    }
    deckName = "";
    if (practice || GwaiSettingsToggleOn("GwaiStory") || GwaiLabToggleOn("GwaiStats") || GwaiSettingsToggleOn("GwaiTrace"))
    {
        deck = gwintManager.GetCurrentAIDeck();
        deckName = NameToString(deck.deckName);
    }
    packet = m_flashValueStorage.CreateTempFlashObject();
    packet.SetMemberFlashInt("version", 1);
    packet.SetMemberFlashBool("enabled", enabled);
    packet.SetMemberFlashInt("vanillaDifficulty", policyDifficulty);
    packet.SetMemberFlashBool("practice", practice);
    packet.SetMemberFlashString("practiceOpponent", gwintManager.gwaiDevLabel);
    packet.SetMemberFlashString("practiceDeck", deckName);
    packet.SetMemberFlashInt("practiceDeckDifficulty", FactsQueryLatestValue('gwent_difficulty'));
    packet.SetMemberFlashBool("signal", GwaiSettingsSignal());
    packet.SetMemberFlashBool("trace", GwaiSettingsToggleOn("GwaiTrace"));
    packet.SetMemberFlashBool("coach", GwaiSettingsToggleOn("GwaiCoach"));
    packet.SetMemberFlashBool("story", !practice && GwaiSettingsToggleOn("GwaiStory"));
    packet.SetMemberFlashBool("lab", GwaiSettingsToggleOn("GwaiLab"));
    packet.SetMemberFlashBool("meter", GwaiSettingsToggleOn("GwaiMeter"));
    packet.SetMemberFlashBool("postGame", GwaiSettingsToggleOn("GwaiPostGame"));
    packet.SetMemberFlashBool("blunder", GwaiSettingsToggleOn("GwaiBlunder"));
    packet.SetMemberFlashBool("stats", GwaiSettingsToggleOn("GwaiStats"));
    packet.SetMemberFlashString("opponentDeck", deckName);
    if (GwaiLabToggleOn("GwaiStats"))
    {
        GwaiStatsSend(packet, deckName);
    }
    m_flashValueStorage.SetFlashObject("gwai.mod.settings", packet);
}

function GwaiDiagnosticsInitialize() : void
{
    var cfg : CInGameConfigWrapper = theGame.GetInGameConfigWrapper();
    var practice : bool = theGame.GetGwintManager().gwaiDevActive;
    var enabled : bool = GwaiSettingsEnabled();
    var policyTier : int = FactsQueryLatestValue('gwent_difficulty');
    var matchMode : string = "story";
    var opponent : string = "";
    var deck : SDeckDefinition;
    if (policyTier < 1) { policyTier = 1; }
    if (policyTier > 3) { policyTier = 3; }
    if (practice)
    {
        matchMode = "practice";
        opponent = theGame.GetGwintManager().gwaiDevLabel;
        enabled = enabled && theGame.GetGwintManager().gwaiDevAI == 2;
        policyTier = theGame.GetGwintManager().gwaiDevTier;
        if (policyTier < 1 || policyTier > 3) { policyTier = 3; }
    }
    else
    {
        deck = theGame.GetGwintManager().GetCurrentAIDeck();
        opponent = NameToString(deck.deckName);
    }
    cfg.SetRawConfigValueByStr("W3GwentAIDiagnostics", "enabled", "false");
    if (enabled) { cfg.SetRawConfigValueByStr("W3GwentAIDiagnostics", "enabled", "true"); }
    cfg.SetRawConfigValueByStr("W3GwentAIDiagnostics", "match_mode", matchMode);
    cfg.SetRawConfigValueByStr("W3GwentAIDiagnostics", "network", "0");
    cfg.SetRawConfigValueByStr("W3GwentAIDiagnostics", "greedy", "0");
    cfg.SetRawConfigValueByStr("W3GwentAIDiagnostics", "vanilla", "0");
    cfg.SetRawConfigValueByStr("W3GwentAIDiagnostics", "stock", "0");
    cfg.SetRawConfigValueByStr("W3GwentAIDiagnostics", "first_error", "");
    cfg.SetRawConfigValueByStr("W3GwentAIDiagnostics", "last_error", "");
    cfg.SetRawConfigValueByStr("W3GwentAIDiagnostics", "vanilla_difficulty", IntToString(FactsQueryLatestValue('gwent_difficulty')));
    cfg.SetRawConfigValueByStr("W3GwentAIDiagnostics", "build", "0.1.0");
    cfg.SetRawConfigValueByStr("W3GwentAIDiagnostics", "practice", IntToString(theGame.GetGwintManager().gwaiDevAI));
    cfg.SetRawConfigValueByStr("W3GwentAIDiagnostics", "policy_tier", IntToString(policyTier));
    cfg.SetRawConfigValueByStr("W3GwentAIDiagnostics", "opponent", opponent);
    theGame.SaveUserSettings();
}

function GwaiDifficultyLabel(tier : int) : string
{
    if (tier <= 1) { return "Easy"; }
    if (tier == 2) { return "Medium"; }
    return "Hard";
}

function GwaiCurrentConfigurationText(separator : string) : string
{
    var text : string = "Current configuration";
    if (GwaiSettingsEnabled())
    {
        text += separator + "AI: Trained network";
    }
    else
    {
        text += separator + "AI: Stock game AI";
    }
    text += separator + "Practice difficulty: " + GwaiDifficultyLabel(GwaiPracticeTier());
    text += separator + "Story difficulty: the game's Gwent setting";
    if (GwaiSettingsToggleOn("GwaiStory"))
    {
        text += " (stronger story opponents enabled)";
    }
    return text;
}

function GwaiReportValue(value : string, richText : bool) : string
{
    if (richText)
    {
        value = StrReplaceAll(value, "&", "&amp;");
        value = StrReplaceAll(value, "<", "&lt;");
        value = StrReplaceAll(value, ">", "&gt;");
    }
    return value;
}

function GwaiLatestMatchText(separator : string, richText : bool) : string
{
    var cfg : CInGameConfigWrapper = theGame.GetInGameConfigWrapper();
    var network : int = StringToInt(cfg.GetRawConfigValueByStr("W3GwentAIDiagnostics", "network"), 0);
    var stock : int = StringToInt(cfg.GetRawConfigValueByStr("W3GwentAIDiagnostics", "stock"), 0);
    var vanilla : int = StringToInt(cfg.GetRawConfigValueByStr("W3GwentAIDiagnostics", "vanilla"), 0);
    var greedy : int = StringToInt(cfg.GetRawConfigValueByStr("W3GwentAIDiagnostics", "greedy"), 0);
    var buildID : string = cfg.GetRawConfigValueByStr("W3GwentAIDiagnostics", "build");
    var enabled : string = cfg.GetRawConfigValueByStr("W3GwentAIDiagnostics", "enabled");
    var opponent : string = cfg.GetRawConfigValueByStr("W3GwentAIDiagnostics", "opponent");
    var mode : string = cfg.GetRawConfigValueByStr("W3GwentAIDiagnostics", "match_mode");
    var selected : string = "Not recorded";
    var status : string = "No decisions recorded yet";
    var error : string = cfg.GetRawConfigValueByStr("W3GwentAIDiagnostics", "first_error");
    var text : string = "Latest recorded match";
    if (buildID == "")
    {
        return text + separator + "No match recorded yet.";
    }
    if (enabled == "false") { selected = "Stock game AI"; }
    if (enabled == "true")
    {
        selected = "Trained AI (" + GwaiDifficultyLabel(StringToInt(cfg.GetRawConfigValueByStr("W3GwentAIDiagnostics", "policy_tier"), 3)) + ")";
    }
    if (stock > 0 && network == 0 && vanilla == 0 && greedy == 0) { status = "Stock AI only"; }
    if (network == 0 && (vanilla > 0 || greedy > 0)) { status = "Fallback AI used; no network choice recorded"; }
    if (network > 0) { status = "Network used"; }
    if (network > 0 && (vanilla > 0 || greedy > 0)) { status = "Network used, with fallbacks"; }
    if (opponent == "") { opponent = "Not recorded"; }
    if (mode == "") { mode = "Not recorded"; }
    text += separator + status;
    text += separator + "Requested AI: " + selected;
    text += separator + "Match: " + GwaiReportValue(mode, richText) + "; Opponent: " + GwaiReportValue(opponent, richText);
    text += separator + "Network choices: " + IntToString(network) + "; selected stock: " + IntToString(stock);
    text += separator + "Vanilla fallbacks: " + IntToString(vanilla) + "; simple-policy fallbacks: " + IntToString(greedy);
    text += separator + "Build: " + GwaiReportValue(buildID, richText);
    if (error != "") { text += separator + "First error: " + GwaiReportValue(StrLeft(error, 220), richText); }
    return text;
}

function GwaiVerificationText() : string
{
    return GwaiCurrentConfigurationText("<br>") + "<br><br>" + GwaiLatestMatchText("<br>", true);
}

function GwaiDiagnosticsArchive() : void
{
    var cfg : CInGameConfigWrapper = theGame.GetInGameConfigWrapper();
    var next : int = StringToInt(cfg.GetRawConfigValueByStr("W3GwentAIDiagnostics", "history_next"), 0) % 8;
    var summary : string = GwaiLatestMatchText(" | ", false);
    cfg.SetRawConfigValueByStr("W3GwentAIDiagnostics", "history_" + IntToString(next), summary);
    cfg.SetRawConfigValueByStr("W3GwentAIDiagnostics", "history_next", IntToString((next + 1) % 8));
}

// Always collect integration failures, even when visual signals or Lab mode are off.
@addMethod(CR4GwintGameMenu) event OnW3GwentAIDiagnostic(kind : string, detail : string)
{
    var cfg : CInGameConfigWrapper = theGame.GetInGameConfigWrapper();
    var count : int;
    var message : string;
    if (kind != "network" && kind != "greedy" && kind != "vanilla" && kind != "stock")
    {
        return false;
    }
    count = StringToInt(cfg.GetRawConfigValueByStr("W3GwentAIDiagnostics", kind), 0);
    cfg.SetRawConfigValueByStr("W3GwentAIDiagnostics", kind, IntToString(count + 1));
    if (kind == "vanilla")
    {
        message = StrLeft(StrReplaceAll(StrReplaceAll(StrReplaceAll(detail, "=", " "), "\r", " "), "\n", " "), 512);
        if (cfg.GetRawConfigValueByStr("W3GwentAIDiagnostics", "first_error") == "")
        {
            cfg.SetRawConfigValueByStr("W3GwentAIDiagnostics", "first_error", message);
        }
        cfg.SetRawConfigValueByStr("W3GwentAIDiagnostics", "last_error", message);
    }
    // Flush the first observation of each source; later turns stay in memory
    // until match close. An interrupted test still leaves its first failure.
    if (count == 0)
    {
        theGame.SaveUserSettings();
    }
}

// The Flash screen reports finished matches here; each event writes one value of user.settings.
@addMethod(CR4GwintGameMenu) event OnW3GwentFunStat(key : string)
{
    var config : CInGameConfigWrapper = theGame.GetInGameConfigWrapper();
    var count : int;
    if (!GwaiLabToggleOn("GwaiStats"))
    {
        return false;
    }
    count = StringToInt(config.GetRawConfigValueByStr("W3GwentAIStats", key), 0);
    config.SetRawConfigValueByStr("W3GwentAIStats", key, IntToString(count + 1));
}

@addMethod(CR4GwintGameMenu) event OnW3GwentFunCard(card : string)
{
    theGame.GetInGameConfigWrapper().SetRawConfigValueByStr("W3GwentAIFun", "last_card", card);
}

@addMethod(CR4GwintGameMenu) event OnW3GwentFunNote(note : string)
{
    theGame.GetInGameConfigWrapper().SetRawConfigValueByStr("W3GwentAIFun", "last_match", note);
}

@addMethod(CR4GwintGameMenu) event OnW3GwentFunSave()
{
    theGame.SaveUserSettings();
}

// BEGIN GENERATED PRACTICE OPPONENTS
function GwaiPracticeOpponent(id : int) : name
{
    if (id == 1) { return 'NKHard'; }
    if (id == 2) { return 'NilfHard'; }
    if (id == 3) { return 'ScoiaHard'; }
    if (id == 4) { return 'NMLHard'; }
    if (id == 5) { return 'SkelHard'; }
    if (id == 6) { return 'Baron'; }
    if (id == 7) { return 'BoatBuilder'; }
    if (id == 8) { return 'CardProdigy'; }
    if (id == 9) { return 'CircusGwentAddict'; }
    if (id == 10) { return 'Crach'; }
    if (id == 11) { return 'CrossroadsInnkeeper'; }
    if (id == 12) { return 'Dijkstra'; }
    if (id == 13) { return 'Gambler'; }
    if (id == 14) { return 'Gremista'; }
    if (id == 15) { return 'Halflings'; }
    if (id == 16) { return 'Hermit'; }
    if (id == 17) { return 'Lambert'; }
    if (id == 18) { return 'LugosTheMad'; }
    if (id == 19) { return 'MarkizaSerenity'; }
    if (id == 20) { return 'Mousesack'; }
    if (id == 21) { return 'Olgierd'; }
    if (id == 22) { return 'Olivier'; }
    if (id == 23) { return 'Roche'; }
    if (id == 24) { return 'ScoiaTrader'; }
    if (id == 25) { return 'Shani'; }
    if (id == 26) { return 'Sjusta'; }
    if (id == 27) { return 'Stjepan'; }
    if (id == 28) { return 'Thaler'; }
    if (id == 29) { return 'VimmeVivaldi'; }
    if (id == 30) { return 'Zoltan'; }
    if (id == 31) { return 'NKTournament'; }
    if (id == 32) { return 'NilfTournament'; }
    if (id == 33) { return 'ScoiaTournament'; }
    if (id == 34) { return 'NMLTournament'; }
    if (id == 35) { return 'ScoiaTournament2'; }
    if (id == 36) { return 'NMLTournament2'; }
    if (id == 37) { return 'NKTournament2'; }
    if (id == 38) { return 'NilfTournament2'; }
    if (id == 39) { return 'SkelTournament2'; }
    return 'NKHard';
}

function GwaiPracticeOpponentLabel(id : int) : string
{
    if (id == 1) { return "Generic: Northern Realms"; }
    if (id == 2) { return "Generic: Nilfgaard"; }
    if (id == 3) { return "Generic: Scoia'tael"; }
    if (id == 4) { return "Generic: Monsters"; }
    if (id == 5) { return "Generic: Skellige"; }
    if (id == 6) { return "Bloody Baron"; }
    if (id == 7) { return "Boat Builder"; }
    if (id == 8) { return "White Orchard scholar"; }
    if (id == 9) { return "Circus Gwent Addict"; }
    if (id == 10) { return "Crach"; }
    if (id == 11) { return "Crossroads Innkeeper"; }
    if (id == 12) { return "Dijkstra"; }
    if (id == 13) { return "Gambler"; }
    if (id == 14) { return "Gremista"; }
    if (id == 15) { return "Halflings"; }
    if (id == 16) { return "Hermit"; }
    if (id == 17) { return "Lambert"; }
    if (id == 18) { return "Madman Lugos"; }
    if (id == 19) { return "Marquise Serenity"; }
    if (id == 20) { return "Ermion"; }
    if (id == 21) { return "Olgierd"; }
    if (id == 22) { return "Olivier"; }
    if (id == 23) { return "Roche"; }
    if (id == 24) { return "Scoia'tael trader"; }
    if (id == 25) { return "Shani"; }
    if (id == 26) { return "Sjusta"; }
    if (id == 27) { return "Stjepan"; }
    if (id == 28) { return "Thaler"; }
    if (id == 29) { return "Vimme Vivaldi"; }
    if (id == 30) { return "Zoltan"; }
    if (id == 31) { return "the Northern Realms tournament deck"; }
    if (id == 32) { return "the Nilfgaard tournament deck"; }
    if (id == 33) { return "the Scoia'tael tournament deck"; }
    if (id == 34) { return "the Monsters tournament deck"; }
    if (id == 35) { return "the second Scoia'tael tournament deck"; }
    if (id == 36) { return "the second Monsters tournament deck"; }
    if (id == 37) { return "the second Northern Realms tournament deck"; }
    if (id == 38) { return "the second Nilfgaard tournament deck"; }
    if (id == 39) { return "the second Skellige tournament deck"; }
    return "Generic: Northern Realms";
}
// END GENERATED PRACTICE OPPONENTS
