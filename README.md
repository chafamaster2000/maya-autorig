# maya-autorig

**[English](#english) · [Castellano](#castellano)**

Marker-guided automatic rigging for Maya, on top of **AdvancedSkeleton**,
driven from an agent through [DCC-MCP](https://github.com/dcc-mcp/dcc-mcp-maya).

```
markers.propose  ->  fit_from_markers  ->  build_rig  ->  bind_skin
                            |                                |
                       verify_fit                       verify_skin
                                                        attach_props
                                                        pose_gallery
```

---

## English

Point it at a character mesh and it places the fit skeleton, builds the rig,
binds the skin and **proves** the result: every stage writes JSON and a
viewport capture, and the pipeline refuses to advance past a stage that failed
its own checks.

Written for game characters: 4 bones per vertex (Unity's Standard quality),
linear skinning, no dependency on a T-pose.

### What it does that a one-click auto-rigger does not

- **The markers are the pose.** Eight body markers (chin, groin, wrists,
  elbows, knees) are proposed from the mesh and can be dragged; the arm angle
  comes from where they are, so A-pose, T-pose and arms-down all work without
  a detector deciding for you.
- **Fingers come from the mesh.** The hand is sliced along its own axis and
  finger lobes are found as connected components of the mesh, so a five-finger
  hand gets five chains, a four-lobe glove gets four, and a mitten gets one.
  The thumb is identified separately, and the wrist is re-derived from the
  palm when the arm walk stopped inside a sleeve.
- **Eyes come from the mesh** too, when the model has eyeballs: a mirror pair
  of small closed shells inside the head beats any proportional guess.
- **Props ride a bone.** A bag, a bomb or a weapon keeps its own mesh and gets
  a one-influence bind to the bone whose segment runs closest; anything
  resting on the ground rides the root instead.
- **Nothing is asserted without evidence.** Each stage records what it
  measured, and the pictures are viewport X-ray captures with the joints and
  controls drawn through the mesh, meant to be read by a cheap reviewer model
  rather than loaded into your main context.
- **Grading is comparative.** The gauntlet holds a run against a yardstick rig
  *you* supply, row by row, with tolerances derived from that rig's own
  numbers. There is no invented threshold and no yardstick in this repo.

### Install

**Windows** — double-click `install.bat`, or from a shell:

```
install.bat -DryRun     rem see what it would do, change nothing
install.bat             rem install
```

It installs Python and Node.js (winget), Claude Code (npm), the DCC-MCP
packages, the Maya adapter, this skill and the Claude Code entry. It does
**not** install Maya or AdvancedSkeleton: those are licensed products this
script has no right to fetch, and it tells you where to get them.

**macOS / Linux**

```bash
python3 -m pip install --user dcc-mcp-maya
dcc-mcp-maya install --yes
tools/install_skill.sh
```

Then open Maya and, from your agent, `load_skill("maya-autorig")`.
Full chain and per-platform detail in [`docs/INSTALL.md`](docs/INSTALL.md).

### Use

```
markers_propose(mesh="Mesh", pose="A")     # drag the mk_* locators if needed
harness_run(mesh="Mesh")                   # fit -> build -> bind -> verify -> props
```

or the whole thing from a raw file:

```
gauntlet_run(source="C:/path/character.fbx", pose="A")
```

`pose` is how the character stands in the file: `A` (arms down at an angle,
the common case), `T` (arms horizontal) or `down` (arms against the body).
Getting it wrong is not fatal: the markers carry the pose, not a detector.

Evidence lands in `autorig_evidence/<scene>/<run>/` **inside Maya's current
project**, which the skill asks Maya for rather than guessing: on a default
install that is `~/Documents/maya/projects/default`, and on Windows it follows
a redirected Documents folder (OneDrive, a roaming profile) instead of writing
somewhere Maya never looks. `MAYA_AUTORIG_EVIDENCE` overrides it.

### Requirements

- Maya 2022 or newer, with **AdvancedSkeleton 6.x** installed (it is content,
  not a package; set `ADVANCEDSKELETON_DIR` if it lives somewhere unusual).
- `dcc-mcp-maya` (pulls `dcc-mcp-core` and the `dcc-mcp-server` gateway).
- numpy, which ships inside Maya.

### Layout

```
skill/maya-autorig/     the skill: tools.yaml + scripts/ (the whole pipeline)
tools/                  installers and operator helpers
docs/INSTALL.md         the MCP chain, end to end, per platform
```

### Notes

The rig itself is built by AdvancedSkeleton, which is commercial software by
Animation Studios and is not included or redistributed here. This project
automates driving it.

MIT licensed. See [`LICENSE`](LICENSE).

---

## Castellano

> **¿Sólo querés riggear tu personaje y listo? Andá a [`GUIA.md`](GUIA.md),
> que es esto mismo en cuatro pasos.**

Le apuntás a la malla de un personaje y coloca el esqueleto, arma el rig, pone
la piel y **demuestra** que salió bien: cada etapa deja un JSON con lo que
midió y una captura del viewport, y el pipeline no avanza sobre una etapa que
falló sus propios chequeos.

Pensado para personajes de juego: 4 huesos por vértice, que es la calidad
Standard de Unity, skinning lineal, y sin depender de una T-pose.

### Qué hace que un auto-rigger de un clic no hace

- **Los marcadores son la pose.** Ocho marcadores (mentón, entrepierna,
  muñecas, codos, rodillas) se proponen desde la malla y se pueden arrastrar.
  El ángulo del brazo sale de dónde están, así que A-pose, T-pose y brazos
  colgando funcionan sin que un detector decida por vos.
- **Los dedos salen de la malla.** La mano se corta en rodajas sobre su propio
  eje y los dedos se encuentran como componentes conexas del mesh: una mano de
  cinco dedos recibe cinco cadenas, un guante de cuatro lóbulos recibe cuatro,
  y un mitón recibe una. El pulgar se identifica aparte, y la muñeca se
  recalcula desde la palma cuando la detección del brazo frenó dentro de una
  manga.
- **Los ojos también salen de la malla**, cuando el modelo los tiene: un par
  espejo de esferas cerradas dentro de la cabeza le gana a cualquier
  estimación por proporción.
- **Los accesorios van pegados a un hueso.** Una mochila, una bomba o un arma
  conservan su propia malla y reciben un bind de una sola influencia al hueso
  cuyo segmento pasa más cerca. Lo que está apoyado en el piso va a la raíz.
- **Nada se afirma sin evidencia.** Cada etapa registra lo que midió, y las
  imágenes son capturas del viewport en rayos X con los huesos y los controles
  dibujados a través del cuerpo, pensadas para que las lea un modelo revisor
  barato y no para cargarlas en tu contexto principal.
- **La calificación es comparativa.** El *gauntlet* mide una corrida contra un
  rig de referencia que **vos** elegís, fila por fila, con las tolerancias
  sacadas de los números de ese mismo rig. No hay umbrales inventados, y este
  repo no trae ninguna referencia adentro.

### Instalación

**Windows** — doble clic en `install.bat`, o desde una terminal:

```
install.bat -DryRun     rem muestra qué haría, sin tocar nada
install.bat             rem instala
```

Instala Python y Node.js (winget), Claude Code (npm), los paquetes DCC-MCP, el
adapter de Maya, esta herramienta y la entrada en Claude Code. **No** instala
Maya ni AdvancedSkeleton: son productos con licencia que el script no tiene
derecho a bajar, y te dice de dónde sacarlos.

**macOS / Linux**

```bash
python3 -m pip install --user dcc-mcp-maya
dcc-mcp-maya install --yes
tools/install_skill.sh
```

Después abrí Maya y, desde tu agente, `load_skill("maya-autorig")`.
La cadena completa está en [`docs/INSTALL.md`](docs/INSTALL.md).

### Uso

```
markers_propose(mesh="Mesh", pose="A")     # arrastrá los locators mk_* si hace falta
harness_run(mesh="Mesh")                   # fit -> build -> bind -> verify -> props
```

o todo de una desde el archivo crudo:

```
gauntlet_run(source="C:/ruta/personaje.fbx", pose="A")
```

`pose` es cómo está parado el personaje en el archivo: `A` (brazos en
diagonal hacia abajo, lo más común), `T` (brazos horizontales) o `down`
(brazos pegados al cuerpo). Errarle no es grave: la pose la llevan los
marcadores, no un detector.

Las evidencias quedan en `autorig_evidence\<escena>\<corrida>\` **adentro del
proyecto actual de Maya**, que la skill le pregunta a Maya en vez de adivinar:
en una instalación por defecto eso es `Documentos\maya\projects\default`, y
en Windows sigue la carpeta Documentos aunque esté redirigida (OneDrive, perfil
móvil), en vez de escribir en un lugar donde Maya nunca mira. Se cambia con
`MAYA_AUTORIG_EVIDENCE`.

### Requisitos

- Maya 2022 o más nuevo, con **AdvancedSkeleton 6.x** instalado (es contenido,
  no un paquete; usá `ADVANCEDSKELETON_DIR` si lo tenés en una carpeta rara).
- `dcc-mcp-maya` (arrastra `dcc-mcp-core` y el gateway `dcc-mcp-server`).
- numpy, que ya viene adentro de Maya.

### Estructura

```
skill/maya-autorig/     la skill: tools.yaml + scripts/ (todo el pipeline)
tools/                  instaladores y utilidades de operación
docs/INSTALL.md         la cadena MCP completa, por plataforma
```

### Notas

El rig lo arma AdvancedSkeleton, que es software comercial de Animation
Studios y no está incluido ni redistribuido acá. Este proyecto automatiza
manejarlo.

Licencia MIT. Ver [`LICENSE`](LICENSE).
