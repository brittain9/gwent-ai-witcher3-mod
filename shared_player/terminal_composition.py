"""Compose a default-off exact pass proof with existing native authority and sinks."""

from .host import HostIntegrationError, _body, _once


class TerminalCompositionError(ValueError):
    """The original host/source classifier no longer matches this composition."""


def patch_terminal_host(sources, *, search=False):
    """Run the sufficient-condition proof before owned draws/history, never physics."""
    result = dict(sources)
    try:
        ai = result["AIPlayerController"]
        if "gwaiTerminal" in ai:
            raise TerminalCompositionError("terminal host is already patched")
        authority = "GwaiSharedConfig.terminalPassEnabled() && GwaiSharedController.terminalPass(this._gwaiView.observation,this._gwaiMoves) != null"
        if search:
            anchor = 'if(GwaiSharedConfig.searchEnabled())\n            {\n               stage = "public history materialization";'
            replacement = (
                "var gwaiTerminal:Boolean = " + authority + ";\n"
                "            if(gwaiTerminal)\n            {\n"
                "               this._gwaiRunner = new GwaiSharedController(profile,null,true);\n"
                "            }\n            else " + anchor
            )
        else:
            anchor = "this._gwaiRunner = new GwaiSharedController(profile,this.gameFlowControllerRef.gwaiOwnedActionUniforms(profile,this.playerID,this.gameFlowControllerRef.gwaiPrimaryTicket(this.playerID)));"
            replacement = (
                "var gwaiTerminal:Boolean = " + authority + ";\n"
                "            this._gwaiRunner = new GwaiSharedController(profile,gwaiTerminal ? null : this.gameFlowControllerRef.gwaiOwnedActionUniforms(profile,this.playerID,this.gameFlowControllerRef.gwaiPrimaryTicket(this.playerID)),gwaiTerminal);"
            )
            ai = _once(
                ai,
                "private var _gwaiRunner:Object = null;",
                '\n      private var _gwaiSelectedController:String = "unselected";',
                "terminal source attribution state",
            )
            ai = _once(
                ai,
                "var selected:Object = this._gwaiRunner.output;",
                "\n            this._gwaiSelectedController = String(this._gwaiRunner.sourceKind);",
                "terminal source attribution before runner release",
            )
            old = "this.gameFlowControllerRef.gwaiNotifyDecision(profile,false,detail);"
            if ai.count(old) != 1:
                raise TerminalCompositionError("successful native notification differs")
            ai = ai.replace(
                old,
                "this.gameFlowControllerRef.gwaiNotifyDecision(profile,false,detail,this._gwaiSelectedController);",
                1,
            )
            ai = ai.replace(
                '" shared-policy "', '" " + this._gwaiSelectedController + " "'
            )
        if ai.count(anchor) != 1:
            raise TerminalCompositionError(
                "one admitted shared runner factory required"
            )
        ai = ai.replace(anchor, replacement, 1)
        result["AIPlayerController"] = ai
        flow = result["GwintGameFlowController"]
        if not search:
            signature = 'public function gwaiNotifyDecision(profile:int, fallback:Boolean, detail:String = "")'
            if flow.count(signature) != 1:
                raise TerminalCompositionError(
                    "original notification signature differs"
                )
            flow = flow.replace(
                signature, signature[:-1] + ', controllerSource:String = "network")', 1
            )
        if not search:
            flow = _once(
                flow,
                "private var _gwaiMatchEnded:Boolean = true;",
                '\n      private var _gwaiControllerSourceKind:String = "";',
                "terminal public source state",
            )
            selection = 'weights_sha256:data == null ? "" : String(data.EXPORT_SHA256)'
            if flow.count(selection) != 1:
                raise TerminalCompositionError("original selection identity differs")
            flow = flow.replace(
                selection,
                selection + ",controller_source_kind:this._gwaiControllerSourceKind",
                1,
            )
        start, end = _body(flow, "gwaiNotifyDecision")
        body = flow[start:end]
        if not search:
            body = (
                '\n         this._gwaiControllerSourceKind = fallback ? "vanilla" : controllerSource;'
                + body
            )
        anchor = 'this.gwaiRecordDecision(fallback ? "vanilla" : "network",detail);'
        if body.count(anchor) != 1:
            raise TerminalCompositionError("original decision classifier differs")
        classifier = (
            'var controllerKind:String = controllerSource == "terminal-pass-guard" ? "rule" : '
            '(controllerSource == "fair-search-v1" ? "search" : (controllerSource == "network" ? "network" : "vanilla"));\n'
            '         this.gwaiRecordDecision(fallback ? "vanilla" : controllerKind,detail);'
        )
        body = body.replace(anchor, classifier, 1)
        # Keep existing signal preferences. Detail contains the truthful source kind.
        body = body.replace(
            'if(detail.indexOf("shared-policy ") >= 0 && this.mcMessageQueue != null)',
            'if((controllerSource == "network" || controllerSource == "terminal-pass-guard" || controllerSource == "fair-search-v1") && this.mcMessageQueue != null)',
        )
        flow = flow[:start] + body + flow[end:]
        result["GwintGameFlowController"] = flow
    except (KeyError, HostIntegrationError) as error:
        raise TerminalCompositionError(
            "complete admitted primary/owned host required"
        ) from error
    return result


def patch_settings_asset(source):
    """Add two explicit counters to the existing sink; preserve all six controls."""
    if not isinstance(source, bytes):
        raise TerminalCompositionError("original settings bytes required")
    text = source.decode("utf-8")
    anchor = '    cfg.SetRawConfigValueByStr("W3GwentAIDiagnostics", "network", "0");'
    if text.count(anchor) != 1 or '"W3GwentAIDiagnostics", "rule", "0"' in text:
        raise TerminalCompositionError("original diagnostics initialization differs")
    text = text.replace(
        anchor,
        anchor
        + '\n    cfg.SetRawConfigValueByStr("W3GwentAIDiagnostics", "rule", "0");\n    cfg.SetRawConfigValueByStr("W3GwentAIDiagnostics", "search", "0");',
        1,
    )
    anchor = '    var network : int = StringToInt(cfg.GetRawConfigValueByStr("W3GwentAIDiagnostics", "network"), 0);'
    if text.count(anchor) != 1:
        raise TerminalCompositionError("original diagnostics report differs")
    text = text.replace(
        anchor,
        anchor
        + '\n    var rule : int = StringToInt(cfg.GetRawConfigValueByStr("W3GwentAIDiagnostics", "rule"), 0);\n    var search : int = StringToInt(cfg.GetRawConfigValueByStr("W3GwentAIDiagnostics", "search"), 0);',
        1,
    )
    anchor = '    if (network > 0 && (vanilla > 0 || greedy > 0)) { status = "Network used, with fallbacks"; }'
    if text.count(anchor) != 1:
        raise TerminalCompositionError("original diagnostics status differs")
    text = text.replace(
        anchor,
        anchor
        + '\n    if (search > 0) { status = "Search choices recorded"; }\n    if (rule > 0 && network == 0 && search == 0 && vanilla == 0 && greedy == 0) { status = "Proven match-winning pass recorded"; }\n    if ((search > 0 || rule > 0) && (vanilla > 0 || greedy > 0)) { status = "Shared controller used, with fallbacks"; }',
        1,
    )
    anchor = '    text += separator + "Network choices: " + IntToString(network) + "; selected stock: " + IntToString(stock);'
    if text.count(anchor) != 1:
        raise TerminalCompositionError("original diagnostics counts differ")
    text = text.replace(
        anchor,
        anchor
        + '\n    text += separator + "Search choices: " + IntToString(search) + "; Proven match-winning passes: " + IntToString(rule);',
        1,
    )
    anchor = (
        'kind != "network" && kind != "greedy" && kind != "vanilla" && kind != "stock"'
    )
    if text.count(anchor) != 1:
        raise TerminalCompositionError("original source kind sink differs")
    return text.replace(
        anchor, anchor + ' && kind != "rule" && kind != "search"', 1
    ).encode("utf-8")


def terminal_trace(source):
    """Record only the truthful public source kind, never optional hidden counts."""
    anchor = "weights_sha256:String(selection.weights_sha256)"
    if source.count(anchor) != 1:
        raise TerminalCompositionError("original public trace selection differs")
    return source.replace(
        anchor,
        anchor + ",controller_source_kind:String(selection.controller_source_kind)",
        1,
    )
