---
name: maya-autorig
description: >-
  Mixamo-style guided auto-rigging for Maya on top of AdvancedSkeleton: propose
  8 body markers from the mesh, let the user correct them, derive and verify the
  FitSkeleton, build the rig, bind and prove the deformation -- every stage with
  JSON + render evidence. Use for fast character turnaround. Not for generic Maya
  rigging without an installed AdvancedSkeleton.
license: MIT
compatibility: "Python 3.9+; Maya 2024-2027 (numpy inside Maya); AdvancedSkeleton 6.x"
metadata:
  dcc-mcp:
    dcc: maya
    version: "0.2.0"
    layer: domain
    tags: ["maya", "rigging", "skinning", "advancedskeleton", "autorig", "mixamo", "markers"]
    search-hint: "auto rig, autorig, mixamo markers, fit skeleton, build rig, bind skin, deformation test"
    tools: tools.yaml
---

# Maya Auto-Rig (marker-guided)

## Order of use

1. Ask the user which pose the model is in (**A, T, or arms down**). The
   answer only steers where the wrist/elbow placeholders go if the arm
   detector finds nothing; the classifier is a cross-check, never a
   requirement. **The markers are the pose**: wherever the user leaves the
   wrists/elbows (and optionally shoulders) defines the arm angle, exactly
   like Mixamo. Model must be cm, frozen, history-free.
2. `markers_propose(mesh, pose)` — creates 8 required (red) + 4 optional
   (yellow) locators under `AutoRigMarkers`, pre-placed from geometry, plus a
   front render. Hand over: the user drags whatever is wrong and says done.
3. `harness_run(mesh)` — fit → checkpoint → build → checkpoint → bind →
   deformation test → checkpoint. Stops at the first failing stage; the
   previous checkpoint is the recovery point.
4. `harness_run` returns a `review` block. If `skip_review` is true, the
   content (fit positions, bind settings) is unchanged since the last PASS:
   **do not review again**. Otherwise make **ONE** reviewer-subagent call
   (**sonnet**) with the `manifest` path — every render of the run with its
   stage `expectation`, judged in one pass — then `review_mark(evidence_dir,
   verdict)` so the next unchanged run skips it. Never load the images into
   the main context.

Individual tools (`fit_from_markers`, `verify_fit`, `build_rig`, `bind_skin`,
`verify_skin`, `checkpoint`) exist for re-running one stage.

## Gauntlet: grade a rig against one you trust

"Good enough" is not a number you can invent, so the grid never has an
absolute threshold: it compares a run against a **yardstick rig** you
supply, row by row, with every tolerance derived from the bar's own
numbers. A rig wins when every hard row is at or better than the bar.

**No yardstick ships with the skill.** Yours is a rig you trust, usually
one an artist built and animation is happy with. Open it and run
`rig_profile(label="mybar", save_as_bar=true)`; from then on
`bar="mybar"` works anywhere a bar is accepted. Without a bar the loop
still rigs and profiles, it just does not grade.

1. `gauntlet_run(source="/path/char.fbx", pose=A|T|down, bar="mybar")` —
   new scene, `prep_mesh`, automatic markers, fit/build/bind/verify/props,
   `rig_profile`, then `rig_compare`. Returns `score`, `hard` (e.g.
   69/70), `wins`, the `failed` rows with bar vs ours, `grid_md` and
   `critic_manifest`. Omit `source` to gauntlet the mesh already open;
   omit `bar` to rig without grading.
2. Make **ONE** fresh-context critic call (**sonnet**) with the
   `critic_manifest` path only; it must not open `critic_key.json`. It
   sees anonymised A/B pairs of the same pose on both rigs and does not
   know which is which. Feed its `{id: A|B|tie}` to
   `critic_verdicts(ours_dir, picks)`, then `rig_compare(bar_path,
   ours_path, critic_path)` so the blind A/B rows become hard rows.
3. Read `failed`, fix the heuristic (or run
   `skin_sweep(scene=<...>__build.mb, bar="mybar")` for the skinning
   half), run again. The loop exits when the run wins, never after N
   rounds.

What the grid measures: the generic humanoid under the artist's extras
(`rig_taxonomy` sorts coat/hair/prop joints out of the comparison), the
control rig, the skin (influences per vertex, weight locality, L/R
symmetry, smoothness) and the deformation (FK bends, wrist twist, and
seven poses including a fist that exercises the fingers). Target engine
defaults to 4 bones per vertex, which is Unity's Standard quality.

## Evidence pictures

Every PNG is a **viewport playblast in X-ray** (`EVIDENCE_MODE = "viewport"`
in `autorig_common`): mesh see-through, joints drawn as bones, control
curves and helpers visible, AdvancedSkeleton's labels on the FitSkeleton.
That is what a rigger looks at: the points and the helpers, not shading.
The old mayaSoftware render is only the fallback when Maya has no visible
model panel (mayapy/batch). Renders keep `cm_per_px` for the orthographic
front/side cameras.

## Cost discipline

The compute is negligible (~6 s from raw OBJ to a verified rig); tokens are
the cost. Three rules keep a run cheap:

- **Stage tools return slim by default** (`compact=true`): `passed`, the
  failed checks, render paths, key metrics, and `evidence_json`. The full
  dict (positions, ray votes, weight locality...) is in
  `<evidence_dir>/<stage>.json` — read it only when something failed. Pass
  `compact=false` only for debugging.
- **One visual review per run, sonnet, and none when nothing changed**
  (`review_manifest` / `review_mark`). Renders are 490×630: half the pixels
  of the original, joint dots still legible.
- **`attach_project` once per session**; repeat only if the package prefixes
  change.

## Contract

- Required markers: chin, groin, wrist_L/R, elbow_L/R, knee_L/R. The user is
  authoritative *along* each limb; the tool re-centres *across* it.
- Optional markers (shoulder, ankle) override their derivation while they
  exist; delete them to fall back.
- L/R pairs are averaged onto −X (AdvancedSkeleton's one-sided FitSkeleton);
  the disagreement is reported, never hidden.
- Fingers `auto` by default: `hand_geom` slices the hand beyond the wrist
  along its own axis and takes the mesh's connected components per slab —
  a finger is a lobe that stays separate down to its tip. The chains the
  mesh has get placed (knuckle → tip, four joints each; thumb from its
  root; Cup on the palm), the others are deleted; a mitten gets one
  `MiddleFinger` chain; `fingers=none` drops all and adds a `WristEnd`.
  The wrist moves *down* the arm when the marker stopped in a sleeve
  (first drop of the palm width, capped at the thumb root; never up).
  `fit_hand.png` is the close-up evidence; the grid scopes its finger rows
  to the lobes the mesh has (a four-lobe glove is not a missing pinky).
- After binding, `prune_hand_weights` moves forearm/shoulder weight off
  hand vertices onto their own wrist: past the wrist the arm is behind you.
  Without it the geodesic solver leaves 20-30 % of a hand vertex on the
  forearm twist and raising the arm shears the hand.
- `arms_up` is anatomical: the IK goes to 97 % of that arm's own reach, so
  the pose never stretches the IK and asks every body the same question.
- The gallery's `fist` pose closes both hands through AS's per-hand master
  control, so the skin over the fingers is measured, not just their
  placement. A curl attribute for a finger the mesh lacks is skipped.

## Props and cloth

Props (a bag, a bomb, a weapon) survive `prep_mesh` (`extras="keep"`) and
`attach_props` binds each to a single bone: the one whose segment runs
closest, or the root when the prop rests on the ground. A prop rides a
bone; it never blends or stretches.

Cloth is **not** covered. A coat or a skirt is skinned to whatever body
joints are near it, so it inherits the arm twist and scores badly on
weight locality (a coated character's coat: p95 0.333 against 0.170 for its
body). The yardstick has dedicated cloth joints; we add none. Measure any
character's with `tools/shell_skin_probe.py`.

## What is approximate

Marker proposal is a starting point, especially the chin, shoulders and the
spine depth on characters with capes/braids. Weights are a fresh geodesic
bind: expect candy-wrapper pinching at boots and gauntlets until an artist
paints them or a golden character's weights are transferred.

## Legacy

`auto_place_fit_joints` / `autorig_oneshot` wrap `asFitAutoPlace`, which is
not reliable (dies on arms-down models, hangs on five-finger hands). Kept for
reference only.
