"""Count complete effective source and require compiled terminal controller bindings."""

import hashlib
import re

from .host import _body


def package_function_counts(native, helpers):
    """Derive QName call counts from final native classes and exact helper bodies."""
    if (
        not isinstance(native, dict)
        or not isinstance(helpers, dict)
        or "AIPlayerController" not in native
    ):
        raise ValueError("complete explicit native/helper sources required")
    source = dict(native)
    ai = source["AIPlayerController"]
    for helper in helpers.values():
        if not isinstance(helper, str) or not helper or ai.count(helper) != 1:
            raise ValueError("one exact appended helper source required")
        ai = ai.replace(helper, "", 1)
    source["AIPlayerController"] = ai
    if set(source) & set(helpers):
        raise ValueError("native/helper class identities overlap")
    source.update(helpers)
    counts = {
        name: {
            function: text.count(function + "(")
            for function in ("getTimer", "getQualifiedClassName")
        }
        for name, text in source.items()
    }
    return {name: row for name, row in counts.items() if any(row.values())}


def verify_terminal_bindings(native, helpers, *, enabled):
    """Static compiled trait/call/config proof; actual compiled-body cases are separate."""
    if type(enabled) is not bool:
        raise ValueError("explicit terminal flag required")
    try:
        controller = helpers["GwaiSharedController"]
        config = helpers["GwaiSharedConfig"]
        ai = native["AIPlayerController"]
        flow = native["GwintGameFlowController"]
        if not re.search(r"public\s+static\s+function\s+terminalPass\s*\(", controller):
            raise ValueError("compiled exact terminal predicate is missing")
        start, end = _body(controller, "decide")
        body = controller[start:end]
        if (
            len(re.findall(r"GwaiSharedController\s*\.\s*terminalPass\s*\(", body)) != 1
            or not re.search(r'sourceKind\s*=\s*"terminal-pass-guard"', body)
            or not re.search(r"modelCall\s*=\s*false", body)
        ):
            raise ValueError("compiled terminal choice/attribution differs")
        start, end = _body(config, "terminalPassEnabled")
        if not re.fullmatch(
            r"\s*return\s+" + str(enabled).lower() + r"\s*;\s*", config[start:end]
        ):
            raise ValueError("compiled terminal flag differs")
        if len(re.findall(r"GwaiSharedController\s*\.\s*terminalPass\s*\(", ai)) != 1:
            raise ValueError("compiled native terminal-first factory differs")
        start, end = _body(flow, "gwaiNotifyDecision")
        if any(
            '"' + name + '"' not in flow[start:end]
            for name in (
                "terminal-pass-guard",
                "rule",
                "fair-search-v1",
                "search",
                "network",
            )
        ):
            raise ValueError("compiled source classifier differs")
    except (KeyError, TypeError) as error:
        raise ValueError("complete compiled terminal hosts/helpers required") from error
    return {
        "contract": "compiled-terminal-controller-bindings-v1",
        "controller_sha256": hashlib.sha256(controller.encode()).hexdigest(),
        "enabled": enabled,
        "avm2_execution_verified": False,
    }
