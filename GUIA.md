# Guía rápida

Riggear un personaje en Maya sin tocar un solo hueso a mano.

---

## Lo que necesitás antes de empezar

Dos cosas que hay que comprar e instalar aparte, porque son productos con
licencia y el instalador no tiene derecho a bajarlos:

1. **Maya** (2022 o más nuevo).
2. **AdvancedSkeleton 6.x** — se baja de
   <https://www.animationstudios.com.au/advanced-skeleton> y su setup se
   instala en `Documentos\maya\scripts`.

Todo lo demás lo instala el instalador solo.

---

## Paso 1 — Instalar (o actualizar)

Abrí **Codex** o **Claude Code** — el que uses — y pegale este mensaje:

> Instalá https://github.com/chafamaster2000/maya-autorig y dejámelo configurado.

Eso es todo. El agente baja la herramienta y corre el instalador, que instala
Python, Node, el puente con Maya, esta herramienta, y la deja conectada **en
los dos agentes** (Codex y Claude Code), aunque vos uses uno solo. Al final
te muestra una tabla con cada paso en verde y un bloque **"What changed ->
what to do"** que dice, en orden, qué tenés que hacer vos: a veces es
*reiniciar Maya*, a veces *abrir una sesión nueva del agente*, y a veces
*nada*. El agente te lo repite en castellano. Hacé exactamente eso, ni más
ni menos.

La primera vez, cada agente te va a pedir que inicies sesión una vez
(`codex login`, o abrir `claude`). Eso lo hacés vos: el instalador no puede.

**Para actualizar, pegale el mismo link otra vez.** No hace falta que sepas
qué versión tenés: el comando compara tu copia con la de GitHub y, si es la
misma y está todo en su lugar, te dice *ya está al día, no hay nada que
reiniciar*. Si hay una nueva, la baja y te dice qué reiniciar.

Si preferís verlo antes sin que toque nada, o correrlo vos mismo:

```
install.bat -DryRun        (Windows, desde la carpeta bajada)
./install.sh --dry-run     (macOS / Linux)
```

**Si algo sale en rojo**, la tabla te dice cuál fue y por qué. Lo más común es
que falte Maya o AdvancedSkeleton: instalalos y volvé a pegar el link.
Correrlo dos veces no rompe nada.

**Cuánto tarda:** la primera vez unos 5-10 minutos (baja Python, Node y los
agentes); después, un minuto; si no hay nada nuevo, segundos.

---

## Paso 2 — Abrir Maya

Abrí Maya normalmente. **Una sola ventana de Maya**, no dos.

Al abrirse, Maya se conecta sola. No hay que apretar nada.

---

## Paso 3 — Riggear

Abrí Codex o Claude Code en la carpeta donde tenés tus personajes y pedile:

> Cargá la skill maya-autorig y riggeá `C:\personajes\heroe.fbx`

O si preferís el comando exacto:

```
gauntlet_run(source="C:/personajes/heroe.fbx", pose="A")
```

En unos 10 segundos de máquina tenés el personaje riggeado y con la piel
puesta (más el minuto o dos que tardes vos en mover markers, si los corregís).

**`pose`** es cómo está parado tu personaje en el archivo:

| Si los brazos están... | poné |
|---|---|
| en diagonal hacia abajo (la más común) | `A` |
| en cruz, horizontales | `T` |
| colgando pegados al cuerpo | `down` |

Si le errás no pasa nada grave: la herramienta se guía por los marcadores, no
por la pose.

**Si querés corregir los marcadores a mano** (`markers_propose` primero,
`harness_run` después): al proponerlos, Maya queda lista para eso solo:
ves el cuerpo en rayos X con los puntos adentro, el clic agarra siempre un
punto (nunca el cuerpo), la cámara mira de frente y la herramienta Move ya
está activa. Movés un punto de un lado y el del otro lado se mueve solo,
espejado, como en Mixamo; si tu personaje es asimétrico, apagá `mirror` en
el grupo `AutoRigMarkers`. Cuando el rig termina, todo vuelve a la normalidad
solo.

---

## Dónde quedan las pruebas de que salió bien

Adentro del proyecto que Maya tenga abierto, en
`autorig_evidence\<personaje>\<fecha>\`. Si no tocaste nada, eso es
`Documentos\maya\projects\default\autorig_evidence\`. La herramienta le
pregunta a Maya dónde está el proyecto, así que si tenés Documentos en OneDrive
o cambiaste de proyecto, las evidencias van igual donde corresponde.

Cada etapa deja un JSON con lo que midió y una captura del viewport en rayos X,
donde se ven los huesos y los controles a través del cuerpo. Si algo falló, la
etapa no sigue: te lo dice y ahí se queda.

---

## Si algo no anda

**Maya abierta pero el agente no le habla.** Se cayó el puente. Abrí una terminal
en la carpeta y corré:

```
powershell -ExecutionPolicy Bypass -File tools\repair_gateway.ps1
```

Después destildá y volvé a tildar `dcc_mcp_maya_plugin` en el Plug-in Manager
de Maya (Windows > Settings/Preferences > Plug-in Manager). Después, en Claude
Code escribí `/mcp` para reconectar; en Codex cerrá la sesión y abrí una nueva
(Codex lee sus servidores al arrancar).

Esto pasa sobre todo si abriste **dos Mayas a la vez**. Abrí de a una.

**"No encuentra AdvancedSkeleton".** Instalalo, o si lo tenés en una carpeta
rara, decile dónde está creando una variable de entorno `ADVANCEDSKELETON_DIR`
que apunte a la carpeta donde está el archivo `AdvancedSkeleton.mel`.

**Doble clic en un `.ps1` y no pasa nada.** Windows bloquea los `.ps1` bajados
de internet. Por eso existe `install.bat`: usá ese.

---

## Preguntas que te vas a hacer

**¿Funciona con cualquier personaje?** Con humanoides. Gordos, flacos, bajitos,
estilizados, con abrigo, con guantes, con o sin dedos. Los dedos los saca de la
malla: si tu personaje tiene cinco, le pone cinco cadenas; si tiene un mitón,
le pone una.

**¿Y los accesorios?** Una mochila, una bomba, un arma: se conservan y se atan
rígidos al hueso que les corresponde. No se deforman como si fueran carne.

**¿Para qué motor sirve?** Está pensado para juegos: 4 huesos por vértice, que
es la calidad Standard de Unity, y skinning lineal.

**¿Me va a quedar perfecto?** No. Te va a quedar un rig completo y verificado
en segundos, que un artista después retoca. La colocación automática de
marcadores es un punto de partida: los podés mover a mano antes de seguir.

**¿Puedo comparar mi rig contra uno bueno?** Sí. Abrí un rig que te guste y
corré `rig_profile(label="mibase", save_as_bar=true)`. Desde ahí,
`gauntlet_run(..., bar="mibase")` te dice fila por fila en qué está peor el
automático que el bueno.
