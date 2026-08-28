← [Índice](../DOCUMENTACION.md)

# 01 · Runtime y arranque

Cubre la capa que **no es lógica de aplicación**: el esqueleto HTML, los estilos,
los dos cargadores JavaScript y el servidor de desarrollo.

---

## `index.html` — el esqueleto

196 líneas de marcado **sin nada de lógica**. Define todos los contenedores que
Python luego rellena o conmuta. Conviene leerlo como el "contrato de ids" entre
el HTML y `app.py`: si un `id` cambia aquí, `by_id(...)` devuelve `None` allá.

### Inventario de elementos por zona

**Overlay de carga**

| `id` | Rol | Quién lo controla |
|---|---|---|
| `python-loader` | Capa a pantalla completa mientras arranca Pyodide. | `bootstrap.js` (`hidden`, `dataset.state`) |
| `loader-detail` | Texto de progreso ("Cargando NumPy…"). | `bootstrap.js` |

**Navegación**

| `id` | Rol |
|---|---|
| `view-editor` / `view-analysis` | Conmutan `STATE.active_view`. Solo muestran/ocultan `#sweep-panel`; el canvas nunca se desmonta. |
| `graph-tabs` | Contenedor de pestañas de grafos. Su `innerHTML` lo regenera `render_tabs()` **completo**, incluido el botón `+ Nueva`. |

**Barra lateral**

| `id` | Rol |
|---|---|
| `node-template` | Círculo "Nodo". Es `draggable`; además responde a `click` como alternativa accesible al arrastre. |
| `directed-toggle` / `directed-description` | Checkbox de grafo dirigido y su leyenda. |
| `show-json-btn`, `import-btn`, `export-btn`, `clear-btn` | Acciones de archivo. |
| `algorithm-select` | `fv` / `amp`. |
| `agreed-wrapper` / `agreed-input` | Campo `agreed`; **solo visible en AMP**. |
| `simulate-btn` | Nace `disabled`; se habilita cuando hay nodos. |
| `simulation-results`, `toggle-simulation-btn`, `reset-simulation-btn`, `simulation-status` | Bloque que aparece tras simular. |
| `shift-indicator` | Aviso "⚡ Modo: Conectar nodos". |
| `file-input` | `<input type=file>` oculto que dispara `import-btn`. |

**Canvas**

```html
<svg id="graph-canvas">
  <defs>
    <pattern id="grid-dots">   <!-- cuadrícula de puntos, 12×12 -->
    <marker id="arrowhead">    <!-- punta de flecha de aristas dirigidas -->
  </defs>
  <rect class="canvas-grid" fill="url(#grid-dots)">
  <g id="edges-layer">        <!-- trazos de arista -->
  <g id="edge-labels-layer">  <!-- cajitas con el peso -->
  <g id="nodes-layer">        <!-- círculos + valor -->
  <g id="connection-layer">   <!-- línea provisional al conectar -->
</svg>
```

El **orden de las capas es el orden de pintado**: los nodos tapan a las aristas
y la línea de conexión queda encima de todo. Las cuatro capas reciben el mismo
`transform` de zoom/pan; el `<rect>` de la cuadrícula **no**, por eso el fondo
punteado no se mueve ni escala.

**Minimapa, sweep, JSON, diálogo**

| `id` | Rol |
|---|---|
| `minimap-svg`, `minimap-edges`, `minimap-nodes`, `minimap-viewport` | Vista general en `viewBox="0 0 180 110"`. |
| `sweep-panel`, `p-points-input`, `simulations-input`, `iterations-input`, `rounds-input`, `sweep-run-btn`, `sweep-stop-btn`, `sweep-status` | Panel de barrido. |
| `sweep-chart-svg` | Gráfica en `viewBox="0 0 400 320"`. Los ejes y la rejilla están **escritos a mano en el HTML**; `render_chart()` solo reescribe `#chart-mean-line`, `#chart-confidence-band` y `#chart-axis-labels`. |
| `chart-tooltip` | Globito posicionado en píxeles sobre la gráfica. |
| `json-panel`, `json-content` | Visor del JSON del grafo. |
| `app-dialog` + `-title`, `-message`, `-input`, `-cancel`, `-accept` | Diálogo modal propio. |

### Por qué hay un diálogo propio

`prompt()` y `confirm()` están bloqueados en varios contextos (iframes con
sandbox, algunas políticas de navegador). `#app-dialog` los reemplaza y funciona
igual en todos lados. El código aún **intenta primero** el nativo y cae al
propio si lanza excepción — ver `notify()` y `clear_graph()` en
[05](05-controlador-app.md).

### Accesibilidad presente en el marcado

`role="tablist"`/`role="tab"` con `aria-selected` en ambas barras de pestañas,
`aria-live="polite"` en el loader y en `#sweep-status`, `role="dialog"` +
`aria-modal` en el diálogo, `aria-label` en los botones de icono
(`+`, `−`, `⌖`, `✕`) y `tabindex="0"` en el contenedor del canvas.

---

## `css/app.css` — el tema

Hoja única, 79 líneas muy densas (varias reglas por línea). Tema oscuro completo.

### Sistema de color

Todo sale de variables en `:root`:

| Variable | Valor | Uso |
|---|---|---|
| `--ink` / `--ink-2` | `#151221` / `#100e1a` | Fondo de app / fondo del canvas |
| `--panel` / `--panel-2` | `#1f1b30` / `#292340` | Barras y paneles / controles |
| `--line` / `--line-soft` | `#372f52` / `#2a2440` | Bordes |
| `--text` / `--muted` / `--faint` | `#f1edf8` / `#9a90b4` / `#6e6690` | Jerarquía tipográfica |
| `--v0` / `--v1` | `#6e7cf2` azul / `#f4b45c` naranja | **Los extremos del degradado de valor** |
| `--danger` | `#e8688b` | Limpiar, cerrar, detener |
| `--v0-a12/24/40` | `--v0` al 12/24/40 % | Fondos y sombras de acento |

`--v0` y `--v1` son los mismos colores que `geometry.value_color()` interpola en
Python (ahí escritos como tuplas RGB `(110,124,242)` y `(244,180,92)`). **Si
cambias uno, cambia el otro** o el degradado de los nodos dejará de coincidir con
el acento de la interfaz.

### Bloques principales

| Selector | Qué resuelve |
|---|---|
| `.python-loader` + `@keyframes loader-pulse` | Overlay con tres puntos que laten en secuencia (retardos `.14s` y `.28s`). |
| `.app-container` → `.main-content` | Layout: columna general, y dentro una fila `sidebar (280px fija) │ canvas (flexible) │ sweep-panel (440px fija)`. |
| `.tab`, `.tab-new`, `.tab-close` | Pestañas tipo píldora. |
| `.graph-canvas` | `touch-action: none` — imprescindible para que los `pointerdown/move` no compitan con el scroll táctil. |
| `.edge-animated .edge-path` + `@keyframes edge-flow` | El "flujo" de las aristas activas: `stroke-dasharray: 9 7` animando `stroke-dashoffset`. |
| `.minimap`, `.minimap-viewport` | Caja fija de 180×110 abajo a la derecha. |
| `.sweep-chart`, `.chart-confidence-band`, `.chart-mean-line` | Banda de IC al 18 % de opacidad y línea de media en naranja de 2.5 px. |
| `.json-panel` | Panel flotante `position: absolute` con `z-index: 1000`. |
| `.app-dialog-backdrop` | `z-index: 2200` + `backdrop-filter: blur(3px)`. |
| `@media (prefers-reduced-motion: reduce)` | Anula **todas** las animaciones y transiciones. |

### Scrollbars

`.sidebar` y `.sweep-panel` estilizan `::-webkit-scrollbar` (8 px, pulgar que se
tiñe de `--v0` al pasar el cursor). La barra de pestañas usa una variante
horizontal de 4 px.

---

## `js/bootstrap.js` — cargador principal

85 líneas. Único módulo ES que carga `index.html`. Su trabajo entero es: **dejar
a Python corriendo con el DOM disponible**.

### Constantes

```js
const PYODIDE_INDEX_URL = "https://cdn.jsdelivr.net/pyodide/v314.0.2/full/";
const PYTHON_FILES = [
  "python/app.py", "python/models.py", "python/geometry.py",
  "python/simulation/__init__.py",
  "python/simulation/engine.py", "python/simulation/sweep.py",
];
```

La lista es **manual**. Un módulo nuevo que no se agregue aquí no existe dentro
de Pyodide.

### Funciones

| Firma | Qué hace |
|---|---|
| `showRuntimeState(state, text)` | Escribe `loader.dataset.state` (`loading`/`ready`/`error`), ajusta `aria-busy` y pone `text` en `#loader-detail`. |
| `fetchPythonFile(sourcePath) → {destination, source}` | Resuelve la ruta relativa a `import.meta.url`, hace `fetch` con **`cache: "no-store"`** (para no servir código Python viejo durante el desarrollo) y lanza si el status no es OK. `destination` queda como `/python/...`. |
| `mountPythonFiles(pyodide, files)` | Por cada archivo: `FS.mkdirTree(directorioPadre)` y `FS.writeFile(destino, fuente, {encoding:"utf8"})`. Escribe en el **sistema de archivos virtual en memoria** de Pyodide (MEMFS), no en disco. |
| `startApplication()` | La orquestación completa. |

### La orquestación, paso a paso

```js
showRuntimeState("loading", "Cargando Python en el navegador…");

// 1. Dispara la descarga de los .py SIN esperarla…
const pythonFilesPromise = Promise.all(PYTHON_FILES.map(fetchPythonFile));
// 2. …y en paralelo baja Pyodide (~10 MB de wasm).
const pyodide = await loadPyodide({ indexURL: PYODIDE_INDEX_URL });

// 3. NumPy y los .py se resuelven juntos.
const [, pythonFiles] = await Promise.all([
  pyodide.loadPackage("numpy"),
  pythonFilesPromise,
]);

// 4. Monta y ejecuta.
mountPythonFiles(pyodide, pythonFiles);
await pyodide.runPythonAsync(`
import runpy, sys
if "/python" not in sys.path:
    sys.path.insert(0, "/python")
runpy.run_path("/python/app.py", run_name="__main__")
`);
```

Dos decisiones que vale la pena notar:

- **El paralelismo es deliberado.** Los `.py` (unos 60 KB) se descargan mientras
  baja el WASM de Pyodide, que domina el tiempo de carga. Encadenarlos sumaría
  latencia gratis.
- **`run_name="__main__"`** es lo que hace que se dispare el
  `if __name__ == "__main__": main()` al final de `app.py`. Sin eso el módulo se
  cargaría pero nunca arrancaría.

### Manejo de errores

```js
startApplication().catch((error) => { … showRuntimeState("error", …) });
```

Cualquier fallo (CDN caído, un `.py` con error de sintaxis, una excepción en
`main()`) deja el overlay visible con el mensaje. No hay pantalla en blanco.

---

## `js/sweep-worker.js` — runtime del barrido

86 líneas. Web Worker de tipo módulo. Es **casi** un clon de `bootstrap.js`, con
tres diferencias importantes:

| | `bootstrap.js` | `sweep-worker.js` |
|---|---|---|
| Archivos que monta | Los 6 | Solo `simulation/*` (3) |
| Ejecuta | `app.py` como `__main__` | Nada al arrancar; espera mensajes |
| Acceso al DOM | Sí | **No** (los workers no lo tienen) |

Por eso el motor **no puede importar `js` ni tocar el DOM**: es literalmente el
mismo código corriendo en un contexto sin `document`.

### Arranque perezoso

```js
const runtimePromise = createRuntime();   // se dispara al cargar el módulo
```

`createRuntime()` se invoca una sola vez y su promesa se reutiliza. Como el
Worker se crea al pulsar «Ejecutar sweep», el usuario **paga la carga de Pyodide
otra vez** (unos segundos) en el primer sweep de la sesión — el navegador sirve
el WASM desde caché, pero la instanciación se repite. Por eso `start_sweep()`
muestra "Cargando motor Python del sweep…" antes de que llegue el primer evento.

### El puente

```js
self.onmessage = async (event) => {
  if (event.data?.type !== "run") return;
  const pyodide = await runtimePromise;
  pyodide.globals.set("_sweep_params_json", JSON.stringify(event.data.params ?? {}));
  await pyodide.runPythonAsync(`
import json
from js import Object, postMessage
from pyodide.ffi import to_js
from simulation.sweep import iter_sweep

_params = json.loads(_sweep_params_json)
for _message in iter_sweep(_params["graph"], algorithm=…, agreed=…, …):
    postMessage(to_js(_message, dict_converter=Object.fromEntries))
`);
  self.postMessage({ type: "end" });
};
```

Cuatro detalles del puente:

1. **Los parámetros viajan como JSON string**, no como objeto JS. Es la forma
   más simple de cruzar la frontera sin pelear con la conversión de tipos: se
   inyecta en `pyodide.globals` y Python hace `json.loads`.
2. **`to_js(..., dict_converter=Object.fromEntries)`** convierte los `dict` de
   Python en **objetos JS planos**. Sin ese `dict_converter`, Pyodide produce
   `Map`, que no sobrevive la clonación estructural de `postMessage` de forma
   útil para el consumidor.
3. **Python llama a `postMessage` directamente** (importado desde `js`), dentro
   del `for`. Cada `yield` del generador se publica en cuanto ocurre: eso es lo
   que hace que la gráfica se refine en vivo.
4. **El `for` es síncrono y bloquea el Worker.** Mientras corre, el Worker no
   procesa mensajes entrantes — por eso la cancelación real es
   `WORKER.terminate()` desde el hilo principal y no el `CancellationToken` de
   `sweep.py` (ver [04](04-barrido-sweep.md)).

### Errores

El `try/catch` que envuelve todo publica `{type: "error", message}`. Cubre tanto
fallos de arranque (Pyodide, NumPy) como excepciones de validación de
`_prepare_sweep`, que se lanzan **antes** del primer `yield`.

---

## `scripts/serve.py` — servidor de desarrollo

62 líneas. Necesario porque los módulos ES, el `fetch` de los `.py` y los Web
Workers **no funcionan con `file://`**: exigen un origen HTTP.

### `DevelopmentHandler(SimpleHTTPRequestHandler)`

```python
extensions_map = {
    **SimpleHTTPRequestHandler.extensions_map,
    ".js":   "text/javascript; charset=utf-8",
    ".mjs":  "text/javascript; charset=utf-8",
    ".py":   "text/x-python; charset=utf-8",
    ".json": "application/json; charset=utf-8",
    ".wasm": "application/wasm",
}
```

Los MIME importan de verdad aquí: un `.mjs` servido como `text/plain` hace que
el navegador **rechace el módulo**, y `.wasm` fuera de `application/wasm` impide
la instanciación por streaming.

```python
def end_headers(self):
    self.send_header("Cache-Control", "no-cache, must-revalidate")
    self.send_header("Pragma", "no-cache")
    self.send_header("Expires", "0")
    super().end_headers()
```

Anti-caché agresivo: durante el desarrollo, editar un `.py` y recargar debe
bastar. (Combina con el `cache: "no-store"` del lado del `fetch`.)

### `parse_args()` y `main()`

| Flag | Default |
|---|---|
| `--host` | `127.0.0.1` |
| `--port` | `8000` |

`main()` sirve `PROJECT_ROOT` (la carpeta padre de `scripts/`, resuelta con
`Path(__file__).resolve().parents[1]`, así que funciona desde cualquier
directorio), usa `ThreadingHTTPServer` —varias conexiones simultáneas, que hacen
falta porque el navegador pide muchos archivos a la vez— e imprime la URL. Sale
limpio con `Ctrl+C` (`KeyboardInterrupt` → `server_close()` en el `finally`).

```bash
python3 scripts/serve.py
```

```bash
python3 scripts/serve.py --host 0.0.0.0 --port 8080
```

### En producción

No hace falta este script: cualquier hosting estático sirve, siempre que respete
los MIME de arriba y permita HTTPS hacia jsDelivr.

---

← [Índice](../DOCUMENTACION.md) · Siguiente: [02 · Modelos y geometría](02-modelos-y-geometria.md)
