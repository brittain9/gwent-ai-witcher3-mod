"""Compile the authored settings menu into native WitcherScript UI controls."""

from __future__ import annotations

import json
import re
import xml.etree.ElementTree as ET

MARKER = "// @gwai-menu-controls@"
KINDS = {
    "TOGGLE": "IGMActionType_Toggle",
    "OPTIONS": "IGMActionType_List",
    "BUTTON": "IGMActionType_Button",
    "SEPARATOR": "IGMActionType_Separator",
}
TAGS = {"nonLocalizedName", "customNames", "nonLocalized", "indent"}


def assemble_settings(source: str, menu: str) -> str:
    """Return the complete script; reject menu features that would change meaning."""
    if source.count(MARKER) != 1:
        raise ValueError("settings script must contain exactly one menu marker")
    root = ET.fromstring(menu)
    groups = root.findall("Group")
    if root.tag != "UserConfig" or root.attrib or len(groups) != 1 or len(root) != 1:
        raise ValueError("settings menu must have exactly one UserConfig group")
    group = groups[0]
    if group.attrib != {
        "id": "W3GwentAI",
        "displayName": "mainmenu_mods.mainmenu_gwent",
    }:
        raise ValueError("settings menu must use the W3GwentAI group")
    visible = group.find("VisibleVars")
    if (
        visible is None
        or len(group) != 1
        or visible.attrib
        or any(child.tag != "Var" for child in visible)
    ):
        raise ValueError("settings menu must contain VisibleVars/Var controls")
    controls = []
    accepted = []
    seen = set()
    for var in visible:
        key, label, kind = (
            var.get(name, "") for name in ("id", "displayName", "displayType")
        )
        tags = set(filter(None, var.get("tags", "").split(";")))
        if (
            not re.fullmatch(r"Gwai[A-Za-z0-9_]+", key)
            or key in seen
            or not label
            or kind not in KINDS
            or tags - TAGS
            or "nonLocalizedName" not in tags
            or set(var.attrib) - {"id", "displayName", "displayType", "tags"}
        ):
            raise ValueError(f"unsupported or duplicate settings control: {key}")
        seen.add(key)
        options = var.findall("OptionsArray/Option")
        if any(child.tag != "OptionsArray" for child in var):
            raise ValueError(f"unsupported child in settings control: {key}")
        labels = []
        values = []
        if kind == "OPTIONS":
            if (
                not options
                or not {"customNames", "nonLocalized"} <= tags
                or len(var) != 1
                or var[0].attrib
                or any(child.tag != "Option" for child in var[0])
            ):
                raise ValueError(f"list must use literal option names: {key}")
            for index, option in enumerate(options):
                entries = option.findall("Entry")
                if (
                    option.get("id") != str(index)
                    or not option.get("displayName")
                    or set(option.attrib) != {"id", "displayName"}
                    or len(entries) != 1
                    or entries[0].attrib != {"varId": key, "value": str(index)}
                    or list(entries[0])
                    or len(option) != 1
                ):
                    raise ValueError(
                        f"list must use zero-based native positions: {key}"
                    )
                labels.append(option.get("displayName"))
                values.append(str(index))
        elif options or list(var):
            raise ValueError(f"only lists may declare options: {key}")
        if kind == "TOGGLE":
            values = ["false", "true"]
        controls.append(
            f"    option = GwaiMenuControl(storage, '{key}', {json.dumps(label)}, "
            f"{KINDS[kind]}, {str('indent' in tags).lower()});"
        )
        if labels:
            controls.append("    choices = storage.CreateTempFlashArray();")
            controls.extend(
                f"    choices.PushBackFlashString({json.dumps(label)});"
                for label in labels
            )
            controls.append('    option.SetMemberFlashArray("subElements", choices);')
        controls.append("    entries.PushBackFlashObject(option);")
        if values:
            condition = " || ".join(f"value == {json.dumps(value)}" for value in values)
            accepted.extend(
                [
                    f"    if (optionName == '{key}')",
                    "    {",
                    f"        return {condition};",
                    "    }",
                ]
            )
    if not controls:
        raise ValueError("settings menu must not be empty")
    generated = "\n".join(
        [
            "// Generated from the authored settings XML during package assembly.",
            "function GwaiMenuControls(storage : CScriptedFlashValueStorage) : CScriptedFlashArray",
            "{",
            "    var entries : CScriptedFlashArray = storage.CreateTempFlashArray();",
            "    var option : CScriptedFlashObject;",
            "    var choices : CScriptedFlashArray;",
            *controls,
            "    return entries;",
            "}",
            "",
            "function GwaiMenuAcceptsValue(optionName : name, value : string) : bool",
            "{",
            *accepted,
            "    return false;",
            "}",
        ]
    )
    return source.replace(MARKER, generated)
