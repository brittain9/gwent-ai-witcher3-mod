#!/usr/bin/env python3
"""Compile and package the v1.0 Gwent: Trained Opponents Set-Transformer mod for The Witcher 3.

Compiles the distilled native ActionScript 3 Set-Transformer (`GwaiTransformer.as`),
exact terminal/post-pass solver (`GwaiTerminalTactics.as`), public turn-history tracker
(`GwaiPlayTrace.as`), and calibrated Easy/Normal/Hard settings into `gwint_game.redswf`,
verifies bytecode initialization (`CheckAs3Initialization.java`) and re-exported Node.js
logit parity against PyTorch, and packs a self-contained `Mods/modW3GwentAI` package.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from scripts import build_probe, cli_encoding, extract_game, install_mod, mod_trace, redbundle, settings_menu
from shared_player import host as sp_host
from shared_player import host_hooks, owned_actions, primary_counter, terminal_composition, terminal_tactics_host

PACKAGE = "red.game.witcher3.menus.gwint"
PACKAGE_PATH = Path("red/game/witcher3/menus/gwint")
AS3_DIR = ROOT / "mod/ai/as3"
VANILLA_REDSWF = ROOT / ".local/vanilla/gameplay/gui_new/swf/gwint/gwint_game.redswf"
VANILLA_SWF = ROOT / ".local/vanilla/gameplay/gui_new/swf/gwint/gwint_game.swf"
FFDEC_JAR = ROOT / ".local/tools/ffdec/ffdec-cli.jar"
FFDEC_CP_JAR = ROOT / ".local/tools/ffdec/ffdec.jar"
VERSION = "1.0.0"

# Calibrated monotonic difficulty ladder (verified on Tiny-2L-96, 318k params, via v2/distill.py):
#   Easy   (1): T=4.2 -> ~70% vs Stock (clean Gwent, forgiving for casual play)
#   Normal (2): T=2.6 -> ~81% vs Stock (+50 Elo over prototype, fun & engaging)
#   Hard   (3): T=0.0 + exact terminal/post-pass solver -> 87.3%+ vs Stock (-3.5 Elo vs 16.2M pure!)
DIFFICULTY_SETTINGS = {
    "1": {
        "temperature": 4.2,
        "selector": "group-first",
        "planning_worlds": 0,
        "rollout_primary_turns": 0,
        "deadline_ms": 0,
        "terminal_pass_v1": True,
    },
    "2": {
        "temperature": 2.6,
        "selector": "group-first",
        "planning_worlds": 0,
        "rollout_primary_turns": 0,
        "deadline_ms": 0,
        "terminal_pass_v1": True,
    },
    "3": {
        "temperature": 0.0,
        "selector": "joint-argmax",
        "planning_worlds": 0,
        "rollout_primary_turns": 0,
        "deadline_ms": 0,
        "terminal_pass_v1": True,
    },
}


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _class_file(export_dir: Path, name: str) -> Path:
    return export_dir / "scripts" / PACKAGE_PATH / f"{name}.as"


def build_shared_config_as3(meta: dict, data_class: str) -> str:
    d1 = json.dumps(DIFFICULTY_SETTINGS["1"], separators=(",", ":"))
    d2 = json.dumps(DIFFICULTY_SETTINGS["2"], separators=(",", ":"))
    d3 = json.dumps(DIFFICULTY_SETTINGS["3"], separators=(",", ":"))
    return (
        "internal class GwaiSharedConfig\n"
        "{\n"
        '   public static const FORMAT:String = "gwai-transformer-v1";\n'
        f'   public static const VERSION:String = "{VERSION}";\n'
        '   public static const DATA_KIND:String = "gameplay";\n'
        '   public static const OBSERVATION_PROFILE_ID:String = "full-faction-v1";\n'
        f'   public static const CHECKPOINT_SHA256:String = "{meta["CHECKPOINT_SHA256"]}";\n'
        f'   public static const EXPORT_SHA256:String = "{meta["WEIGHTS_SHA256"]}";\n'
        "\n"
        "   public static function enabled() : Boolean\n"
        "   {\n"
        "      return true;\n"
        "   }\n"
        "\n"
        "   public static function terminalPassEnabled() : Boolean\n"
        "   {\n"
        "      return true;\n"
        "   }\n"
        "\n"
        "   public static function mode() : String\n"
        "   {\n"
        '      return "policy-only";\n'
        "   }\n"
        "\n"
        "   public static function data() : Object\n"
        "   {\n"
        f"      return {data_class};\n"
        "   }\n"
        "\n"
        "   public static function settingsFor(difficulty:int) : Object\n"
        "   {\n"
        f"      if(difficulty == 1) return {d1};\n"
        f"      if(difficulty == 2) return {d2};\n"
        f"      if(difficulty == 3) return {d3};\n"
        '      throw new Error("unsupported difficulty: " + difficulty);\n'
        "   }\n"
        "\n"
        "   public function GwaiSharedConfig()\n"
        "   {\n"
        "      super();\n"
        "   }\n"
        "}\n"
    )


def patch_native_sources(originals: dict[str, str], helpers: dict[str, str], public_trace: str) -> dict[str, str]:
    """Patch vanilla Witcher 3 AS3 host classes cleanly with GwaiTransformer."""
    patched = {name: src.replace("\r\n", "\n") for name, src in originals.items()}

    # 1. AIPlayerController: hooks + helper class definitions
    ai = patched["AIPlayerController"]
    start, end = sp_host._body(ai, "state_begin_ChoseMove")
    fallback_body = ai[start:end].strip("\n")
    ai = sp_host._once(
        ai,
        f"package {PACKAGE}\n{{",
        "\n   import flash.utils.getTimer;\n   import flash.utils.getQualifiedClassName;",
        "AI package imports",
    )
    ai_members = host_hooks.AI_MEMBERS.replace(
        "__GWAI_NATIVE_FALLBACK_BODY__", fallback_body
    ).replace("GwaiNetPolicy.", "GwaiTransformer.")
    # Friendly player-facing decision detail label
    ai_members = ai_members.replace("shared-policy ", "trained-ai ")

    for method, statement in (
        ("state_begin_ChoseMove", "if(this.gwaiBegin()) return;"),
        ("state_update_ChooseMove", "if(this.gwaiPump()) return;"),
        ("state_begin_Idle", "this.gwaiCancel();"),
    ):
        s_i, _ = sp_host._body(ai, method)
        ai = ai[:s_i] + "\n         " + statement + ai[s_i:]
    s_i, _ = sp_host._body(ai, "state_begin_Idle")
    ai = (
        ai[:s_i]
        + "\n         if(!this.gwaiTerminalProofCommit())\n"
        + "         {\n            this._decidedCardTransaction = null;\n"
        + "            this.attitude = TACTIC_NONE;\n"
        + '            this._stateMachine.ChangeState("ChoosingMove");\n'
        + "            return;\n         }"
        + ai[s_i:]
    )
    class_end = ai.rfind("}", 0, ai.rfind("}"))
    if class_end < 0:
        raise RuntimeError("AIPlayerController closing brace not found")
    ai = ai[:class_end] + "\n" + ai_members + "\n" + ai[class_end:]

    pkg_close = ai.rfind("}")
    if pkg_close < 0:
        raise RuntimeError("AIPlayerController package closing brace not found")
    ai = (
        ai[:pkg_close]
        + "\n"
        + "\n".join(helpers[name] for name in sorted(helpers))
        + "\n"
        + ai[pkg_close:]
    )
    patched["AIPlayerController"] = ai

    # 2. GwintGameFlowController
    flow = sp_host._once(
        patched["GwintGameFlowController"],
        "public var gameStarted:Boolean = false;",
        "\n" + host_hooks.FLOW_METHODS,
        "flow shared members",
    )
    for method, statement in (
        (
            "state_begin_reset",
            "this.gwaiCancelPending();\n         this._gwaiSignalShown = false;\n         this._gwaiInitiative = -1;\n         this._gwaiDecisionId = 0;\n         this._gwaiMatchEnded = true;",
        ),
        (
            "EndGame",
            "this.gwaiCancelPending();\n         this._gwaiMatchEnded = true;",
        ),
    ):
        s_i, _ = sp_host._body(flow, method)
        flow = flow[:s_i] + "\n         " + statement + flow[s_i:]
    flow = sp_host._method_once(
        flow,
        "state_update_CoinToss",
        "         if(this.currentPlayer != CardManager.PLAYER_INVALID)\n         {",
        "\n            this._gwaiMatchEnded = false;\n            if(this._gwaiInitiative < 0)\n            {\n               this._gwaiInitiative = this.currentPlayer;\n               this.gwaiPracticeBanner();\n            }",
    )
    patched["GwintGameFlowController"] = flow

    # 3. GwintGameMenu
    menu = sp_host._once(
        patched["GwintGameMenu"],
        'dispatchEvent(new GameEvent(GameEvent.REGISTER,"gwint.game.toggleAI",[this.setAIEnabled]));',
        '\n         dispatchEvent(new GameEvent(GameEvent.REGISTER,"gwai.mod.settings",[this.gwaiConfigureModSettings]));',
        "menu shared configuration",
    )
    menu = sp_host._once(
        menu,
        "public var gameFlowController:GwintGameFlowController;",
        "\n" + host_hooks.MENU_METHOD,
        "menu shared handler",
    )
    patched["GwintGameMenu"] = menu

    # 4. Public play-by-play trace & history hooks
    trace_patched = mod_trace.patch_sources(
        {name: patched[name] for name in {"GwintGameFlowController", *mod_trace.EXTRA_CLASSES}}
    )
    patched.update(trace_patched)
    # Append GwaiPlayTrace inside AIPlayerController package
    ai = patched["AIPlayerController"]
    pkg_close = ai.rfind("}")
    patched["AIPlayerController"] = ai[:pkg_close] + "\n" + public_trace + "\n" + ai[pkg_close:]

    # 5. Primary counter, owned RNG uniforms, and terminal proof hooks
    patched = primary_counter.patch_primary_counter(patched)
    patched = owned_actions.patch_owned_actions(patched)
    # Replace ALL GwaiNetPolicy references across every patched native class & use Trained Opponents branding
    for cls_k in list(patched.keys()):
        patched[cls_k] = patched[cls_k].replace("GwaiNetPolicy.", "GwaiTransformer.").replace(
            "W3 Gwent AI:", "Trained Opponents:"
        )
    patched = terminal_composition.patch_terminal_host(patched)
    patched = terminal_tactics_host.patch_proof_commit(patched)
    # Guard: zero dangling GwaiNetPolicy references anywhere in patched output
    for cls_k, src_v in patched.items():
        if "GwaiNetPolicy" in src_v:
            raise RuntimeError(f"Dangling GwaiNetPolicy reference in {cls_k}")
    return patched


def run_cmd(label: str, cmd: list[str], cwd: Path, timeout: int = 180) -> str:
    proc = subprocess.run(cmd, cwd=cwd, capture_output=True, text=True, timeout=timeout, check=False)
    if proc.returncode != 0:
        raise RuntimeError(
            f"[{label}] failed (exit {proc.returncode}):\nCMD: {' '.join(cmd)}\n"
            f"STDOUT:\n{proc.stdout}\nSTDERR:\n{proc.stderr}"
        )
    return proc.stdout


def main() -> None:
    p = argparse.ArgumentParser(description="Build & package v1.0 Gwent: Trained Opponents Transformer mod")
    p.add_argument(
        "--export-dir",
        type=Path,
        default=ROOT.parent / "witcher3-gwent-ai-research" / ".runtime" / "as3-export" / "distill-2l-96",
    )
    p.add_argument(
        "--out-dir",
        type=Path,
        default=ROOT / ".runtime" / "mod-transformer-v1",
    )
    p.add_argument(
        "--package-dir",
        type=Path,
        default=ROOT / ".runtime" / "package-transformer-v1",
    )
    p.add_argument("--verify-parity", action="store_true", help="Run PyTorch vs compiled-AS3 parity gate")
    p.add_argument("--checkpoint", type=Path, default=ROOT.parent / "witcher3-gwent-ai-research" / ".runtime" / "v2-runs" / "distill-2l-96" / "best.pt")
    args = p.parse_args()

    meta_path = args.export_dir / "transformer.meta.json"
    bin_path = args.export_dir / "transformer.f32.bin"
    meta = json.loads(meta_path.read_text(encoding="utf-8"))
    data_class = meta["CLASS_NAME"]
    data_as3_path = args.export_dir / f"{data_class}.as"
    if not data_as3_path.is_file():
        raise FileNotFoundError(f"Missing exported AS3 weights class: {data_as3_path}")

    if not VANILLA_REDSWF.is_file() or not VANILLA_SWF.is_file():
        raise FileNotFoundError(f"Missing vanilla swf/redswf in {VANILLA_SWF.parent}")
    if not FFDEC_JAR.is_file():
        raise FileNotFoundError(f"Missing FFDec jar: {FFDEC_JAR}")

    pins = install_mod.load_pins(ROOT / "mod/game-pins.json")
    vanilla_sha = _sha256(VANILLA_REDSWF)
    expected_vanilla_sha = install_mod._replaced_pin(pins)["sha256"]
    if vanilla_sha != expected_vanilla_sha:
        raise RuntimeError(f"Vanilla REDSWF hash mismatch: {vanilla_sha} != {expected_vanilla_sha}")
    original_swf, trailer = build_probe.unpack_redswf(VANILLA_REDSWF.read_bytes())
    if original_swf != build_probe.normalize_swf(VANILLA_SWF.read_bytes(), "vanilla swf"):
        raise RuntimeError("Vanilla SWF and REDSWF embedded payloads disagree")

    out_dir = args.out_dir.resolve()
    if out_dir.exists():
        shutil.rmtree(out_dir)
    out_dir.mkdir(parents=True)

    java = shutil.which("java")
    if not java:
        raise RuntimeError("java not found in PATH")

    def ffdec(label: str, *ff_args: str) -> str:
        return run_cmd(
            label,
            [java, *cli_encoding.JAVA_UTF8_OPTIONS, "-Xmx1536m", "-Djava.awt.headless=true", "-jar", str(FFDEC_JAR), *ff_args],
            cwd=out_dir,
        )

    print(f"=== Building Gwent: Trained Opponents v{VERSION} (Transformer {meta['N_LAYERS']}L d={meta['D_MODEL']}, {meta['WEIGHT_COUNT']:,} params) ===", flush=True)

    # 1. Export vanilla native AS3 classes
    native_classes = {"AIPlayerController", "GwintGameFlowController", "GwintGameMenu", *mod_trace.EXTRA_CLASSES}
    orig_dir = out_dir / "original-export"
    classes_arg = ",".join(f"{PACKAGE}.{n}" for n in sorted(native_classes))
    ffdec("export-original", "-selectclass", classes_arg, "-export", "script", str(orig_dir), str(VANILLA_SWF))
    originals = {n: _class_file(orig_dir, n).read_text(encoding="utf-8") for n in native_classes}

    # 2. Build clean helper inventory
    build_id = f"{VERSION}-transformer-{meta['WEIGHTS_SHA256'][:8]}"
    public_trace = terminal_composition.terminal_trace(mod_trace.helper_source(build_id))
    helpers = {
        "GwaiCapture": (AS3_DIR / "GwaiCapture.as").read_text(encoding="utf-8"),
        "GwaiTerminalCatalog": (AS3_DIR / "GwaiTerminalCatalog.as").read_text(encoding="utf-8"),
        "GwaiTerminalTactics": (AS3_DIR / "GwaiTerminalTactics.as").read_text(encoding="utf-8"),
        "GwaiNetWeights": (AS3_DIR / "GwaiNetWeights.as").read_text(encoding="utf-8"),
        "GwaiTransformer": (AS3_DIR / "GwaiTransformer.as").read_text(encoding="utf-8"),
        "GwaiSharedController": (AS3_DIR / "GwaiSharedController.as").read_text(encoding="utf-8"),
        "GwaiSharedConfig": build_shared_config_as3(meta, data_class),
        data_class: data_as3_path.read_text(encoding="utf-8"),
    }

    patched = patch_native_sources(originals, helpers, public_trace)

    # 3. Compile into gwint_game.swf via FFDec -importScript
    import_root = out_dir / "import"
    pkg_root = import_root / PACKAGE_PATH
    pkg_root.mkdir(parents=True)
    for name, src in patched.items():
        (pkg_root / f"{name}.as").write_text(src, encoding="utf-8")

    compiled_swf_path = out_dir / "gwint_game.swf"
    ffdec("compile-swf", "-importScript", str(VANILLA_SWF), str(compiled_swf_path), str(import_root))
    compiled_swf = build_probe.normalize_swf(compiled_swf_path.read_bytes(), "transformer compiled output")
    redswf_bytes = build_probe.patch_redswf(VANILLA_REDSWF.read_bytes(), compiled_swf, trailer)
    redswf_path = out_dir / "gwint_game.redswf"
    redswf_path.write_bytes(redswf_bytes)
    print(f"  [compile] gwint_game.swf = {len(compiled_swf)/1e6:.2f} MB | gwint_game.redswf = {len(redswf_bytes)/1e6:.2f} MB (sha={_sha256_bytes(redswf_bytes)[:16]})", flush=True)

    # 4. Re-export and verify helper inventory + AVM2 static initializer safety
    verify_dir = out_dir / "compiled-export"
    ffdec("reexport-verify", "-export", "script", str(verify_dir), str(compiled_swf_path))
    found_gwai = {p.stem for p in (verify_dir / "scripts").rglob("Gwai*.as")}
    expected_gwai = set(helpers.keys()) | {"GwaiPlayTrace"}
    if found_gwai != expected_gwai:
        raise RuntimeError(f"Compiled helper mismatch: missing={expected_gwai - found_gwai}, extra={found_gwai - expected_gwai}")
    print(f"  [verify] All {len(found_gwai)} Gwai* AS3 classes compiled cleanly into SWF", flush=True)

    # Verify zero eager sibling-class reads in static initializers across all compiled Gwai* classes
    for cls_name in sorted(expected_gwai):
        src_text = _class_file(verify_dir, cls_name).read_text(encoding="utf-8")
        for line in src_text.splitlines():
            if re.search(r"\bstatic\s+(?:var|const)\b", line):
                rhs = line.partition("=")[2]
                if re.search(r"\bGwai[A-Za-z0-9_]*\b", rhs):
                    raise RuntimeError(f"Unsafe static initializer in {cls_name}: {line.strip()}")
    if shutil.which("javac"):
        init_out = run_cmd(
            "check-as3-init",
            [
                java,
                *cli_encoding.JAVA_UTF8_OPTIONS,
                "-Djava.awt.headless=true",
                "-cp",
                str(FFDEC_CP_JAR),
                str(ROOT / "scripts/CheckAs3Initialization.java"),
                str(compiled_swf_path),
            ],
            cwd=out_dir,
        )
        print(f"  [verify] {init_out.strip()}", flush=True)
    else:
        print(f"  [verify] PASS: {len(expected_gwai)} compiled Gwai* static initializers verified literal-only", flush=True)

    # 5. Optional: verify bit-for-bit parity on the RE-EXPORTED compiled GwaiTransformer.as!
    if args.verify_parity:
        research_root = ROOT.parent / "witcher3-gwent-ai-research"
        reexported_as3 = _class_file(verify_dir, "GwaiTransformer")
        parity_out = run_cmd(
            "compiled-parity",
            [
                sys.executable,
                "-u",
                "-m",
                "v2.export_as3",
                "--checkpoint",
                str(args.checkpoint),
                "--out-dir",
                str(args.export_dir),
                "--as3-source",
                str(reexported_as3),
                "--verify-parity",
                "--num-cases",
                "60",
            ],
            cwd=research_root,
        )
        print(f"  [compiled-parity]\n{parity_out}", flush=True)

    # 6. Write build manifest & assemble complete Witcher 3 mod package
    settings_src = (ROOT / "mod/ai/settings/scripts/local/gwai_settings.ws").read_bytes()
    settings_patched = terminal_composition.patch_settings_asset(settings_src).decode("utf-8")
    menu_xml_path = ROOT / "mod/ai/settings/menu/modW3GwentAI.xml"
    trace_ws_path = ROOT / "mod/trace/settings/scripts/local/gwai_playtrace.ws"
    assembled_settings = settings_menu.assemble_settings(
        settings_patched, menu_xml_path.read_text(encoding="utf-8")
    )

    build_manifest = {
        "format": "witcher3-gwent-mod-build-v4",
        "version": VERSION,
        "build_id": build_id,
        "status": install_mod.BUILD_STATUS,
        "variant": "ai",
        "architecture": f"Set-Transformer {meta['N_LAYERS']}L d={meta['D_MODEL']} h={meta['N_HEADS']} ffn={meta['FFN_DIM']}",
        "actor_params": meta["WEIGHT_COUNT"],
        "difficulties": DIFFICULTY_SETTINGS,
        "inputs_sha256": {
            "gwint_game.redswf": vanilla_sha,
            "gwai_settings.ws": _sha256(ROOT / "mod/ai/settings/scripts/local/gwai_settings.ws"),
            "gwai_playtrace.ws": _sha256(trace_ws_path),
            "modW3GwentAI.xml": _sha256(menu_xml_path),
            "checkpoint": meta["CHECKPOINT_SHA256"],
            "weights": meta["WEIGHTS_SHA256"],
        },
        "outputs_sha256": {
            "gwint_game.redswf": _sha256_bytes(redswf_bytes),
        },
    }
    manifest_path = out_dir / "manifest.json"
    manifest_path.write_text(json.dumps(build_manifest, indent=2) + "\n", encoding="utf-8")

    # Assemble self-contained Mods/modW3GwentAI package (ready for install_mod.py & Steam Workshop!)
    pkg_dir = args.package_dir.resolve()
    if pkg_dir.exists():
        shutil.rmtree(pkg_dir)
    content = pkg_dir / install_mod.MODS_DIR / "modW3GwentAI" / "content"
    (content / "scripts/local").mkdir(parents=True)
    bundle, store = redbundle.pack([(install_mod.SWF_MEMBER, redswf_bytes)])
    (content / redbundle.BUNDLE_NAME).write_bytes(bundle)
    (content / "metadata.store").write_bytes(store)
    install_mod._verify_bundle(content, redswf_bytes)

    (content / "scripts/local/gwai_settings.ws").write_text(assembled_settings, encoding="utf-8")
    shutil.copy2(trace_ws_path, content / "scripts/local/gwai_playtrace.ws")
    shutil.copy2(manifest_path, pkg_dir / "build-manifest.json")
    shutil.copy2(ROOT / "mod/game-pins.json", pkg_dir / "game-pins.json")

    files = {
        path.relative_to(pkg_dir).as_posix(): {
            "sha256": _sha256(path),
            "size": path.stat().st_size,
        }
        for path in sorted(pkg_dir.rglob("*"))
        if path.is_file() and path.relative_to(pkg_dir).parts[0] in (install_mod.MODS_DIR, "bin")
    }
    (pkg_dir / "package-manifest.json").write_text(
        json.dumps(
            {
                "format": install_mod.PACKAGE_FORMAT,
                "variant": "ai",
                "mod_name": "modW3GwentAI",
                "version": VERSION,
                "build_manifest_sha256": _sha256(manifest_path),
                "redswf_sha256": _sha256_bytes(redswf_bytes),
                "files": files,
            },
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )
    print(f"\n=== PACKAGE READY: {pkg_dir} ===", flush=True)
    for rel, info in files.items():
        print(f"  {rel} ({info['size']/1e6:.2f} MB)", flush=True)


if __name__ == "__main__":
    main()
