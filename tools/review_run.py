#!/usr/bin/env python3
"""Run the one visual review of a run -- or the gauntlet's blind critic -- in a
fresh context, from the shell, with whichever agent CLI is installed.

The manifests the skill writes (`review_manifest.json`, `critic_manifest.json`)
already carry the images and the instructions; what was left to the agent was
to open a subagent, hand it the file, and copy the verdict back. That step is
client-specific (an Agent-tool subagent in Claude Code, `spawn_agent` in
Codex), so this script does it the same way everywhere:

    python3 tools/review_run.py <evidence_dir>/review_manifest.json
    python3 tools/review_run.py <profile_dir>/critic_manifest.json --client codex

It builds the prompt, attaches the images (`codex exec -i`, or `claude -p`
reading them with its Read tool), parses the verdict, and writes it back
through the skill's own pure functions (`review.mark`, `review.critic_verdicts`),
so the calling agent never sees a pixel. Pure Python, no Maya.

`--dry-run` prints the prompt and the command and calls nothing.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
from typing import Any, Dict, List, Optional, Sequence, Tuple

HERE = os.path.dirname(os.path.abspath(__file__))
SKILL_SCRIPTS = os.path.normpath(os.path.join(HERE, "..", "skill", "maya-autorig", "scripts"))

CLIENTS = ("codex", "claude")
DEFAULT_MODEL = {"codex": None, "claude": "sonnet"}   # codex: whatever config.toml says


# --------------------------------------------------------------------------- #
# Prompt
# --------------------------------------------------------------------------- #
def manifest_kind(m: Dict[str, Any]) -> str:
    if "pairs" in m:
        return "critic"
    if "stages" in m:
        return "review"
    raise ValueError("not a review or critic manifest (no 'stages' and no 'pairs')")


def image_list(m: Dict[str, Any]) -> List[Tuple[str, str]]:
    """(label, path) in the order the images are attached; the label is what
    the prompt calls that image, since an attached image carries no name."""
    out: List[Tuple[str, str]] = []
    if manifest_kind(m) == "critic":
        for pair in m["pairs"]:
            out.append(("pair {} / A".format(pair["id"]), pair["A"]))
            out.append(("pair {} / B".format(pair["id"]), pair["B"]))
    else:
        for stage in m["stages"]:
            for path in stage["images"]:
                out.append(("stage {} / {}".format(stage["stage"], os.path.basename(path)), path))
    return out


def build_prompt(m: Dict[str, Any], attached: bool) -> str:
    """The manifest's own instructions, plus a map from image number to what
    it is. `attached`: the images travel with the prompt (numbered); otherwise
    the reviewer must open the listed paths itself."""
    kind = manifest_kind(m)
    lines = [m.get("instructions", "").strip(), ""]
    images = image_list(m)
    if attached:
        lines.append("The {} images are attached in this exact order:".format(len(images)))
    else:
        lines.append("Open and look at these {} image files (absolute paths), in this order:".format(len(images)))
    for i, (label, path) in enumerate(images, 1):
        lines.append("  {}. {}{}".format(i, label, "" if attached else "  ->  " + path))
    lines.append("")
    if kind == "critic":
        for pair in m["pairs"]:
            lines.append("pair {}: pose '{}' -- should read as: {}".format(pair["id"], pair["pose"], pair["reads_as"]))
        lines.append("")
        lines.append("Answer with ONLY the JSON object {\"<pair id>\": \"A\"|\"B\"|\"tie\", ...}, one entry per pair.")
    else:
        for stage in m["stages"]:
            lines.append("stage {} ({} image(s)) -- expectation: {}".format(
                stage["stage"], len(stage["images"]), stage.get("expectation") or "(none recorded)"))
        lines.append("")
        lines.append("End with exactly one line: 'OVERALL: PASS' or 'OVERALL: FAIL (reasons)'.")
    return "\n".join(lines)


# --------------------------------------------------------------------------- #
# Verdict parsing
# --------------------------------------------------------------------------- #
def parse_review(text: str) -> Tuple[Optional[str], str]:
    """('PASS'|'FAIL'|None, reasons). The last OVERALL line wins."""
    verdict, notes = None, ""
    for line in text.splitlines():
        mo = re.search(r"OVERALL:\s*(PASS|FAIL)\b\s*(.*)", line, re.IGNORECASE)
        if mo:
            verdict, notes = mo.group(1).upper(), mo.group(2).strip().strip("()").strip()
    return verdict, notes


def parse_critic(text: str) -> Dict[str, str]:
    """The last JSON object of A/B/tie picks in the text; {} when none."""
    picks: Dict[str, str] = {}
    for mo in re.finditer(r"\{[^{}]*\}", text, re.DOTALL):
        try:
            obj = json.loads(mo.group(0))
        except ValueError:
            continue
        if isinstance(obj, dict) and obj and all(str(v).strip().upper() in ("A", "B", "TIE") for v in obj.values()):
            picks = {str(k): str(v).strip().upper() for k, v in obj.items()}
    return {k: ("tie" if v == "TIE" else v) for k, v in picks.items()}


# --------------------------------------------------------------------------- #
# Clients
# --------------------------------------------------------------------------- #
def pick_client(wanted: str) -> str:
    if wanted != "auto":
        if not shutil.which(wanted):
            raise SystemExit("{} is not on PATH".format(wanted))
        return wanted
    for c in CLIENTS:
        if shutil.which(c):
            return c
    raise SystemExit("neither codex nor claude is on PATH; install one or pass --client")


def command_for(client: str, prompt: str, images: Sequence[str], model: Optional[str],
                workdir: str, out_file: str) -> List[str]:
    if client == "codex":
        # Images attach to the initial prompt in order; read-only sandbox,
        # nothing persisted, last message to a file we can parse. The prompt
        # goes in on stdin, not as an argument: `-i` takes one or more paths,
        # so a trailing positional prompt is swallowed as another image and
        # codex then reports "No prompt provided via stdin".
        cmd = ["codex", "exec", "--skip-git-repo-check", "--ephemeral", "-s", "read-only",
               "-C", workdir, "-o", out_file]
        if model:
            cmd += ["-m", model]
        for img in images:
            cmd += ["-i", img]
        cmd.append("-")
        return cmd
    # claude -p: no image flag, so the prompt lists the paths and Read is the
    # one tool it may use to open them.
    cmd = ["claude", "-p", prompt, "--output-format", "json", "--allowedTools", "Read"]
    if model:
        cmd += ["--model", model]
    return cmd


def run_client(client: str, cmd: List[str], out_file: str, timeout: int, prompt: str = "") -> str:
    stdin = prompt if client == "codex" else None
    proc = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout, input=stdin)
    if proc.returncode != 0:
        raise SystemExit("{} failed ({}):\n{}\n{}".format(client, proc.returncode, proc.stdout[-2000:], proc.stderr[-2000:]))
    if client == "codex":
        try:
            with open(out_file) as fh:
                return fh.read()
        except OSError:
            return proc.stdout
    try:
        env = json.loads(proc.stdout)
        return env.get("result") if isinstance(env, dict) else proc.stdout
    except ValueError:
        return proc.stdout


# --------------------------------------------------------------------------- #
def write_back(kind: str, m: Dict[str, Any], manifest_path: str, verdict: Any, notes: str) -> Dict[str, Any]:
    sys.path.insert(0, SKILL_SCRIPTS)
    import review  # pure python: no Maya import

    if kind == "review":
        return review.mark(m["evidence_dir"], verdict, notes)
    return review.critic_verdicts(os.path.dirname(os.path.abspath(manifest_path)), verdict)


def main(argv: Optional[Sequence[str]] = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("manifest", help="review_manifest.json or critic_manifest.json")
    ap.add_argument("--client", choices=("auto",) + CLIENTS, default="auto")
    ap.add_argument("--model", help="model for the reviewer (default: sonnet for claude, the config default for codex)")
    ap.add_argument("--timeout", type=int, default=600)
    ap.add_argument("--no-mark", action="store_true", help="do not write the verdict back (review_mark / critic_verdicts)")
    ap.add_argument("--dry-run", action="store_true", help="print the prompt and the command; call nothing")
    args = ap.parse_args(argv)

    manifest_path = os.path.abspath(args.manifest)
    with open(manifest_path) as fh:
        m = json.load(fh)
    kind = manifest_kind(m)
    images = [p for _, p in image_list(m)]
    missing = [p for p in images if not os.path.isfile(p)]
    if missing:
        raise SystemExit("missing images:\n  " + "\n  ".join(missing))
    if kind == "review" and m.get("skip_review"):
        print(json.dumps({"kind": kind, "skipped": True, "why": "manifest says skip_review: content unchanged since the last PASS"}))
        return 0

    client = "codex" if args.dry_run and not shutil.which("codex") and not shutil.which("claude") else pick_client(args.client)
    model = args.model if args.model is not None else DEFAULT_MODEL[client]
    attached = client == "codex"
    prompt = build_prompt(m, attached=attached)
    workdir = os.path.dirname(manifest_path)
    tmp = tempfile.mkdtemp(prefix="review_run_")
    out_file = os.path.join(tmp, "last_message.md")
    cmd = command_for(client, prompt, images, model, workdir, out_file)

    if args.dry_run:
        print(json.dumps({"kind": kind, "client": client, "model": model, "images": len(images),
                          "command": cmd[:12] + (["..."] if len(cmd) > 12 else []), "prompt": prompt}, indent=2))
        return 0

    text = run_client(client, cmd, out_file, args.timeout, prompt) or ""
    transcript = os.path.join(workdir, "{}_verdict.txt".format(kind))
    with open(transcript, "w") as fh:
        fh.write(text)

    out: Dict[str, Any] = {"kind": kind, "client": client, "model": model, "images": len(images),
                           "transcript": transcript}
    if kind == "review":
        verdict, notes = parse_review(text)
        out.update({"verdict": verdict, "notes": notes})
        if verdict is None:
            out["error"] = "no 'OVERALL: PASS|FAIL' line in the reviewer's answer"
        elif not args.no_mark:
            out["marked"] = write_back(kind, m, manifest_path, verdict, notes)
    else:
        picks = parse_critic(text)
        out["picks"] = picks
        if not picks:
            out["error"] = "no {id: A|B|tie} object in the critic's answer"
        elif not args.no_mark:
            out["verdicts"] = write_back(kind, m, manifest_path, picks, "")
    print(json.dumps(out, indent=2, default=str))
    return 0 if "error" not in out else 1


if __name__ == "__main__":
    sys.exit(main())
