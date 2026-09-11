# maya-autorig — instructions for the agent reading this

You are an AI agent (Codex, Claude Code, or any MCP-speaking client) and a
person -- usually an artist, not a programmer -- has pointed you at this
repository. This file tells you what to do; keep the human's part to the two
things only they can do: open Maya, and drag markers.

## If you were asked to install it -- or given the link again

One command does install **and** update. Run it and nothing else:

| Platform | Command |
|---|---|
| Windows | `powershell -ExecutionPolicy Bypass -c "irm https://raw.githubusercontent.com/chafamaster2000/maya-autorig/main/tools/bootstrap.ps1 \| iex"` |
| macOS / Linux | `curl -fsSL https://raw.githubusercontent.com/chafamaster2000/maya-autorig/main/install.sh \| bash` |

It keeps **one** clone in `~/maya-autorig`: the first time it clones, every
later time it pulls the clone forward and prints `updated: <old> -> <new>`
or `already up to date`. Then it runs the installer, which is idempotent.
So when the person pastes the repository link a second time, run the same
command -- that *is* the update; do not clone a second copy somewhere else.
If you already have a clone elsewhere, `git pull --ff-only` there and run
`./install.sh` (macOS/Linux) or `install.bat` (Windows) from it.

The installer sets up the DCC-MCP packages and the Maya adapter, copies this
skill where the gateway scans, and **registers the `maya` MCP server in both
Codex and Claude Code** (`codex mcp add maya --url ...` and `claude mcp add
--transport http maya ...`), installing either CLI when it is missing. It
also drops `skill/maya-autorig/SKILL.md` into `~/.codex/skills` and
`~/.claude/skills`, because the gateway's `load_skill` registers tools but
never returns instructions -- that file is the only way the workflow reaches
you.

It does **not** install Maya or AdvancedSkeleton (licensed products) and
says so in its summary. When the summary warns about either, tell the person
where to get them and stop there. Each agent CLI also needs its own login
once (`codex login` / `claude`); the installer cannot do that for them.

## What to tell the person afterwards -- the rule

The installer ends with a block titled **"What changed -> what to do"**.
Relay it to the person **verbatim, in their language, complete** -- every
numbered step, and nothing that is not there. It is computed from what
actually changed, so it is the only correct answer to "do I have to restart
something?":

- **Nothing changed** -> the block says so and that nothing needs a restart.
  Do not tell them to restart anything.
- **Skill copy or Maya adapter changed** -> Maya must be closed and opened
  again *if it is open* (the gateway reads the skill copy at start-up); if
  it is closed, just open it. The block already knows which.
- **`maya` MCP server newly registered in Codex** -> Codex needs a **new
  session**; an open one will not see the server. In **Claude Code** `/mcp`
  in the open session is enough.
- **A CLI was just installed** -> it needs its login once (`codex login`, or
  run `claude`); the installer cannot do that for them.
- **Maya or AdvancedSkeleton missing** -> a warning in the summary. Tell
  them where to get it (the summary says) and stop; nothing can be rigged
  without both.

If you are the agent that was just registered (the block says to restart
you), say so explicitly: *"I was just connected to Maya; start a new
session with me and then ask me to rig."*

**Versions.** The repository carries a `VERSION` file; every installed copy
carries the same file. The bootstrap compares the clone's `VERSION` with the
one on GitHub and prints `installed: X  available: Y`. Same version and
everything in place -> it stops with *already up to date, nothing to do,
nothing to restart*. The person does not need to know version numbers: they
paste the link, you run the command, the command decides.

## If you were asked to rig something

Read `skill/maya-autorig/SKILL.md` first (or the copy in your skills
directory). The short version:

1. `resources/read gateway://instances` -> the live Maya's `instance_id`.
2. `load_skill(skill_name="maya-autorig", instance_id=...)`.
3. `gauntlet_run(source="<file>", pose="A"|"T"|"down")` from a raw file, or
   `markers_propose` -> (the person drags locators) -> `harness_run`.
4. Every stage writes JSON and a viewport capture under
   `<Maya project>/autorig_evidence/<scene>/<run>/`. Read the JSON of a
   failed stage; never load the images into your own context -- one
   fresh-context review per run, and none when `skip_review` is true
   (`python3 tools/review_run.py <manifest>` does it from the shell).

## Working on the code

- After editing `skill/`, re-run `tools/install_skill.sh` (or `.ps1`) -- the
  gateway reads a **copy**, never the repo -- then `tools/mcp_rescan.py`.
- Evidence images are viewport X-ray captures on purpose (points and helpers
  through the mesh), never software renders.
- Nothing is changed without an observed symptom; a fix ships with the
  measurement that showed the problem.
