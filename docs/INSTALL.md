# Instalación — MCP + skill maya-autorig

Este auto-rigger no habla con Maya directamente: corre **adentro** de Maya a
través del ecosistema **DCC-MCP**. La cadena es:

```
Codex / Claude Code ──HTTP──> dcc-mcp gateway (127.0.0.1:9765) ──> Maya (adapter embebido)
                       │                                      │
                       └── descubre skills (tools.yaml) ──────┘
                            maya-dev, maya-render, … y ESTE: maya-autorig
```

Verificado en esta máquina el 2026-09-09 con Maya 2027 (mayapy 3.13.9),
`dcc-mcp-core` 0.20.24, `dcc-mcp-server` 0.20.24, `dcc-mcp-maya` 0.9.26.

## 1. Instalar los paquetes DCC-MCP

Tres paquetes (el adapter arrastra los otros dos):

```bash
python3 -m pip install --user dcc-mcp-maya      # trae dcc-mcp-core + dcc-mcp-server
```

- `dcc-mcp-core` — runtime de skills (catálogo, contrato, semántica).
- `dcc-mcp-server` — binario Rust: **gateway**, sidecar y bridge stdio. Instala
  el ejecutable `dcc-mcp-server` en `~/Library/Python/<ver>/bin`.
- `dcc-mcp-maya` — adapter de Maya: embebe un server MCP Streamable-HTTP dentro
  de Maya. Instala el CLI `dcc-mcp-maya`.

Repos upstream: https://github.com/dcc-mcp/dcc-mcp-maya ·
https://github.com/dcc-mcp/dcc-mcp-core

> Asegurate de que `~/Library/Python/3.x/bin` esté en el `PATH`.

## 2. Enganchar el adapter a Maya

El CLI `dcc-mcp-maya` corre el "Install SOP": deja un módulo de Maya
(`~/Library/Preferences/Autodesk/maya/modules/dcc-mcp-maya` + `.mod`) y un
`userSetup.py` que arranca el server embebido cada vez que Maya abre.

```bash
dcc-mcp-maya install --yes           # idempotente; --dry-run para ver qué toca
dcc-mcp-maya status                  # confirma módulo, userSetup y versión
dcc-mcp-maya verify
```

Subcomandos: `install · status · verify · uninstall · upgrade`
(`--dcc-path`, `--python`, `--module-zip`, `--json`).

Abrí Maya después de instalar. En el arranque el adapter registra la instancia
y el gateway la ve en `gateway://instances`.

## 3. Levantar el gateway

El gateway es el único puerto que toca el agente. En esta máquina corre así:

```bash
dcc-mcp-server gateway --host 127.0.0.1 --port 9765 \
  --remote-host 0.0.0.0 --remote-port 59765 \
  --gateway-idle-timeout-secs 300 --name dcc-mcp-gateway@$(hostname -s)
```

(Suele quedar levantado como sidecar `dcc-mcp-s`. Si no responde, este comando
lo revive. Estado en `~/.dcc-mcp/` — receipts y logs de bootstrap.)

## 4. Conectar el agente al gateway (Claude Code y Codex)

Los dos hablan con el gateway por HTTP (streamable). El instalador registra
el servidor en los dos; a mano:

```bash
claude mcp add --transport http maya http://127.0.0.1:9765/mcp
codex  mcp add maya --url http://127.0.0.1:9765/mcp
```

que dejan, respectivamente, en `~/.claude.json` (`mcpServers`):

```json
"maya": { "type": "http", "url": "http://127.0.0.1:9765/mcp" }
```

y en `~/.codex/config.toml`:

```toml
[mcp_servers.maya]
url = "http://127.0.0.1:9765/mcp"
```

Los dos leen sus servidores **al arrancar**: después de agregarlo, Claude Code
reconecta con `/mcp`; Codex necesita una sesión nueva.

El server MCP `maya` expone **cuatro** tools de workflow — `search`, `describe`,
`load_skill`, `call` — más recursos (`gateway://instances`,
`gateway://catalog`, `gateway://docs/agent-workflows`). No fan-out de acciones.
Cada cliente los prefija a su manera (Claude Code: `mcp__maya__call`); los
verbos son los mismos.

**`load_skill` registra tools, no devuelve instrucciones.** El texto del flujo
(`SKILL.md`: orden de uso, una sola revisión, crítico ciego) le llega al agente
como skill propia: el instalador lo copia a `~/.codex/skills/maya-autorig/` y
`~/.claude/skills/maya-autorig/`. Sin esa copia el agente tiene las
herramientas y no sabe el orden.

## 5. Registrar la skill `maya-autorig`

Las skills built-in viven en el paquete
(`…/site-packages/dcc_mcp_maya/skills/`). Las **propias** van en el directorio
de usuario, que el gateway también escanea:

```bash
tools/install_skill.sh          # copia skill/maya-autorig -> ~/.dcc-mcp/maya/skills/maya-autorig
```

**Copia, no symlink:** el scanner de skills (Rust, walkdir) no sigue symlinks
de directorio — una skill linkeada queda silenciosamente invisible (verificado:
`scan_and_load_strict` sobre el dir con el symlink descubre 0 skills). Después
de editar la skill, re-corré el instalador y hacé que el server la re-escanee
sin reiniciar Maya:

```
run_script tools/mcp_rescan.py   # inst.reload_skill_paths() + load_skill("maya-autorig")
```

(o reiniciá Maya: el server escanea `~/.dcc-mcp/maya/skills` al arrancar).
Validación previa, sin tocar Maya — el arranque hace un scan *estricto* y una
skill inválida en ese dir lanza excepción:

```bash
mayapy -c 'import sys,os; sys.path.insert(0,os.path.expanduser("~/Library/Python/3.13/lib/python/site-packages")); \
  from dcc_mcp_core._core import scan_and_load_strict as f; print(f(extra_paths=["skill"], dcc_name="maya"))'
```

Detalles del contrato que el runtime impone (aprendidos registrándola): cada
tool corre `main(**params)` de su `source_file` — la clave `entrypoint` no se
honra, por eso los tools que mapean a otra función tienen un wrapper de tres
líneas (`scripts/harness_run.py` → `harness.run`, etc.); `scripts/` entra al
`sys.path` al ejecutar, así los módulos se importan entre sí; `next-tools`,
`output_schema` y `timeout_hint_secs` son extensiones aceptadas.

Después:

```
search(kind="skill", query="autorig")   ->  maya-autorig
load_skill(skill_name="maya-autorig")
```

### Puente mientras tanto: correr vía `maya-dev`

Hasta registrarla, la skill se ejecuta con la built-in **maya-dev**, que ya
sabe cargar un proyecto y correr una función:

```
load_skill("maya-dev")
attach_project("<repo>/skill/maya-autorig/scripts",
               package_prefixes=[<todos los módulos>])   # hot-reload
run_entrypoint("markers:propose", {mesh:"Mesh", pose:"A", evidence_dir:"…"})
run_entrypoint("harness:run",     {mesh:"Mesh", evidence_dir:"…"})
```

`maya-render` da `render_frame` para la evidencia. Este es el modo con el que
se verificó todo el pipeline; ver `README.md`.

## 6. Prerequisito de contenido: AdvancedSkeleton

El rig lo arma **AdvancedSkeleton 6.x** (probado 6.910), instalado en Maya
aparte (es contenido, no pip). `discover_procs` reporta qué procs resuelven en
la versión instalada; corrélo primero en una máquina nueva.

## 6.b Instaladores

Todo lo anterior en un script por plataforma, con el mismo formato y los
mismos pasos: `tools/install_windows.ps1` (PowerShell 5.1, el que trae
Windows, o 7) y `tools/install_unix.sh` (macOS / Linux). Los dos terminan con
un bloque **"What changed -> what to do"** calculado a partir de lo que
cambió de verdad: qué reiniciar (Maya si cambió la copia de la skill o el
adapter y estaba abierta; sesión nueva de Codex si recién se registró el
servidor; `/mcp` en Claude Code; login si recién se instaló un CLI) o que no
hay nada que reiniciar.

### Sin clonar: el link

```
powershell -ExecutionPolicy Bypass -c "irm https://raw.githubusercontent.com/chafamaster2000/maya-autorig/main/tools/bootstrap.ps1 | iex"
curl -fsSL https://raw.githubusercontent.com/chafamaster2000/maya-autorig/main/install.sh | bash
```

Clonan en `~/maya-autorig` la primera vez y corren el instalador. Las veces
siguientes comparan el archivo `VERSION` del clon con el de GitHub: si es el
mismo y la copia instalada y los registros MCP están en su lugar, terminan
con *already up to date, nothing to restart*; si no, hacen `git pull
--ff-only`, imprimen `updated: X -> Y` y corren el instalador. `-Reinstall`
/ `--reinstall` fuerza el instalador igual.

### Windows

Hacé doble clic en `install.bat`. Si preferís una terminal:

```
install.bat                 rem instala todo
install.bat -DryRun         rem muestra qué haría, sin tocar nada
install.bat -SkipPrereqs    rem no instala Python / Node / Claude Code / Codex
install.bat -SkipCodex      rem sólo Claude Code (-SkipClaude: sólo Codex)
```

`install.bat` existe porque Windows bloquea los `.ps1` bajados de internet:
al hacerles doble clic no pasa nada. El bat los lanza con la política
correcta. Por debajo es exactamente esto, si lo querés a mano:

```powershell
powershell -ExecutionPolicy Bypass -File tools\install_windows.ps1 -DryRun
powershell -ExecutionPolicy Bypass -File tools\install_windows.ps1
```

Hace, en orden, y cada paso reporta OK / SKIP / WARN / FAIL:

1. **Preflight** — PowerShell, Python (`py -3` o `python`), Maya bajo
   `Program Files\Autodesk`, y **AdvancedSkeleton**. AS es contenido, no un
   paquete: el script lo busca y te dice dónde ponerlo, no lo instala.
2. **`pip install --user dcc-mcp-maya`** (arrastra core + server).
3. **PATH** — los console scripts caen en el `Scripts` de usuario, que Windows
   no pone en el PATH; el instalador lo agrega a esta sesión y al PATH del
   usuario, o el CLI `dcc-mcp-maya` "no existe" sin razón.
4. **`dcc-mcp-maya install --yes`** — módulo de Maya + `userSetup.py`.
5. **La skill** — `tools\install_skill.ps1`, copia a
   `%USERPROFILE%\.dcc-mcp\maya\skills\maya-autorig` con `robocopy /MIR`.
   **Copia, nunca junction ni symlink**: el scanner no sigue links y la skill
   queda invisible sin decir nada.
6. **Agentes** — instala el CLI que falte (`@anthropic-ai/claude-code`,
   `@openai/codex`, por npm) y registra el gateway en cada uno: `claude mcp
   add --transport http maya <url>` y `codex mcp add maya --url <url>`. Antes
   de registrar pregunta si ya estaba (`<cli> mcp get maya`): eso decide si
   ese agente necesita sesión nueva. Si un CLI no está y no se pudo
   instalar, imprime lo que hay que pegar a mano.
7. **Verificación** — scan estricto de la skill instalada, `dcc-mcp-maya
   verify`, y los tests offline cuando hay `tests/`.

Flags: `-DryRun`, `-SkipPackages`, `-SkipAdapter`, `-SkipClaude`,
`-SkipCodex`, `-GatewayUrl`, `-Python`. Sale con código 1 si falló un paso
requerido, así sirve de gate en el setup de una máquina.

### macOS / Linux

```bash
./install.sh --dry-run
./install.sh
```

Mismos pasos y mismos flags en minúscula (`--skip-codex`, `--gateway-url`...).
Agrega el `bin` de Python de usuario al PATH de la sesión y del perfil de la
shell (`~/.zprofile` o `~/.bashrc`, con un comentario `maya-autorig` para no
duplicarlo). Node viene de Homebrew cuando hay; si no, avisa y sigue.

### What the installer does and does not install

Installs, on a machine that already has Maya and Python:

| | |
|---|---|
| `dcc-mcp-core` | the skill runtime (catalog, contract) |
| `dcc-mcp-server` | the gateway binary and the per-DCC sidecar |
| `dcc-mcp-maya` | the Maya adapter: a Maya module plus the `userSetup.py` that starts the embedded MCP server every time Maya opens |
| the per-user `Scripts` directory on `PATH` | Windows leaves it off, which makes the adapter CLI "not found" for no reason |
| `maya-autorig` | this skill, copied into the dcc-mcp user skills directory |
| the agent-side skill | `SKILL.md` + `VERSION` into `~/.codex/skills/maya-autorig` and `~/.claude/skills/maya-autorig` |
| the Claude Code entry | `claude mcp add --transport http maya http://127.0.0.1:9765/mcp` |
| the Codex entry | `codex mcp add maya --url http://127.0.0.1:9765/mcp` |

The first three come from a single `pip install dcc-mcp-maya`, which pulls
the other two. numpy is not installed: the skill runs inside Maya, which
ships its own.

**Does not install, and tells you so:**

- **Maya.** Checked under `Program Files\Autodesk`; a warning, not a failure,
  since the files can be laid down before Maya exists.
- **AdvancedSkeleton.** It is commercial content from Animation Studios, not a
  package. The installer looks for it and points at where to put it. Nothing
  can be rigged without it.
- **Python.** Required, checked, and the run fails without it.
- **Logins.** Claude Code and Codex are installed when missing (npm), but
  each needs its own login once; the installer says so in its final block.

**Nothing starts a gateway.** That is on purpose: the sidecar inside Maya
launches one when Maya opens, and a hand-started gateway competes with it.
Open Maya, and the chain comes up.

### Cuando el MCP se calla con Maya abierta

Síntoma: Maya corriendo, plugin cargado, y toda llamada MCP falla con
transport error. Nadie escucha en el 9765.

```powershell
powershell -ExecutionPolicy Bypass -File tools\repair_gateway.ps1 -DryRun
powershell -ExecutionPolicy Bypass -File tools\repair_gateway.ps1
```

Causa (vista de verdad el 2026-09-10): un crash de Maya, o dos Mayas abiertas
a la vez, dejan filas `__gateway__` en el registro reclamando un puerto donde
no escucha nadie. Cada gateway nuevo las sonda, no obtiene respuesta y en vez
de tomar el puerto sale con *"found an existing owner; exiting"*. Nadie liga
el puerto, cada sidecar espera 15 s y se va, y las filas sobreviven a todos
los reinicios: una fila de gateway **no lleva pid**, así que el barrendero no
tiene con qué probar que está muerta.

Por eso una fila de gateway se juzga **por su puerto**, no por un proceso: si
algo contesta ahí, la fila está viva y no se toca (incluida la del gateway que
está funcionando ahora). Las demás filas se juzgan por su pid.
`services.json` se respalda antes de reescribirse.
