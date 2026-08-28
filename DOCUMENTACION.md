# Documentación técnica

Explica qué hace cada parte del proyecto y qué función cumple cada script.
Complementa a `README.md` (instalación y uso) y `AGENTS.md` (reglas de trabajo).

## Índice

| Documento | Cubre |
|---|---|
| Este archivo | Visión general, mapa mental, flujos de punta a punta. |
| [01 · Runtime y arranque](docs/01-runtime-y-arranque.md) | `index.html`, `css/app.css`, `js/bootstrap.js`, `js/sweep-worker.js`, `scripts/serve.py`. |
| [02 · Modelos y geometría](docs/02-modelos-y-geometria.md) | `python/models.py`, `python/geometry.py`. |
| [03 · Motor de simulación](docs/03-motor-simulacion.md) | `python/simulation/engine.py` — una ronda, con la matemática. |
| [04 · Barrido (sweep)](docs/04-barrido-sweep.md) | `python/simulation/sweep.py` — miles de réplicas vectorizadas. |
| [05 · Controlador](docs/05-controlador-app.md) | `python/app.py` — estado, DOM, eventos, render SVG. |
| [06 · Formatos, flujos y detalles](docs/06-formatos-flujos-y-detalles.md) | JSON, secuencias completas, cabos sueltos, glosario. |
| [07 · Rendimiento y hardware](docs/07-rendimiento-y-hardware.md) | Perfil de cómputo, mediciones, mejoras futuras y criterios para cotizar equipo. |

---

## 1. Qué es

Un **editor visual de grafos** acoplado a un **simulador de propagación de
valores**, que corre **completamente en el navegador**. No hay backend: el HTML
carga Pyodide (Python compilado a WebAssembly) y toda la lógica —estado,
manipulación del DOM, render SVG y los algoritmos numéricos— está escrita en
Python.

El problema que modela: dado un grafo donde cada nodo tiene un valor y cada
arista una probabilidad, ¿qué pasa con los valores cuando los nodos se mandan
mensajes al azar? La métrica de interés es la **discrepancia**
(`max(valores) − min(valores)`): cuando llega a 0, la red alcanzó consenso.

Dos algoritmos:

- **FV (Flip Value)** — un nodo que recibe mensajes elige **uno** al azar y
  **copia su valor**. El consenso emerge (o no) de la topología.
- **AMP (Agreement)** — un nodo que recibe cualquier mensaje adopta un valor
  fijo `agreed`. El consenso se impone desde afuera.

## 2. Las dos mitades del proyecto

| | **Editor** | **Análisis de Convergencia** |
|---|---|---|
| Pestaña | `Editor` | `Análisis de Convergencia` |
| Qué hace | Dibujar el grafo y correr **una ronda**, viendo qué arista transmitió qué. | Barrer `p` de 0 a 1 y graficar la discrepancia media con IC 95 %. |
| Escala | 1 simulación | Hasta 1001 valores de `p` × 50 000 simulaciones cada uno |
| Dónde corre | Hilo principal | **Web Worker** con su propio Pyodide |
| Motor | `simulation/engine.py` (bucles Python) | `simulation/sweep.py` (tensores NumPy) |
| Entrada | `run_round(grafo, ...)` | `iter_sweep(grafo, ...)` |

La separación no es cosmética: un sweep típico son millones de operaciones. Si
corriera en el hilo principal congelaría la interfaz, así que vive en un Worker
que publica resultados parciales por `postMessage`.

## 3. Mapa de archivos

```
PruebasLocura/
├── index.html              Esqueleto del DOM (contenedores vacíos, sin lógica)
├── css/app.css             Tema oscuro completo, por variables CSS
├── js/
│   ├── bootstrap.js        Arranque del runtime Python principal
│   └── sweep-worker.js     Runtime Python secundario, dentro del Worker
├── python/
│   ├── app.py       1169 L Controlador: estado ↔ DOM ↔ eventos ↔ SVG
│   ├── models.py     140 L Dataclasses del estado (sin DOM)
│   ├── geometry.py   110 L Color, curvas de arista, encuadre
│   └── simulation/
│       ├── __init__.py  88 L Re-exporta la API pública
│       ├── algorithms.py 234 L ← AQUÍ se agrega un algoritmo nuevo
│       ├── registry.py 138 L Registro de algoritmos (módulo hoja)
│       ├── rng.py     47 L Muestreo compartido (módulo hoja)
│       ├── engine.py 443 L Motor de una ronda
│       └── sweep.py  482 L Motor batched con NumPy
├── scripts/
│   ├── serve.py            Servidor HTTP estático de desarrollo
│   ├── equivalencia.py     Arnés: congela el comportamiento del motor
│   └── smoke_ui.py         Smoke de la interfaz con DOM simulado
└── docs/                   Esta documentación
```

### Cómo agregar un algoritmo de consenso

Se edita **solo** `python/simulation/algorithms.py`: se escriben sus funciones
y se llama a `register(...)`. El motor, el sweep, el selector de la interfaz y
los campos de parámetros salen del registro. No hay que tocar `engine.py`,
`sweep.py`, `app.py`, `index.html` ni el JavaScript.

Un algoritmo aporta hasta tres piezas, todas con firma uniforme:

| Pieza | Para qué | Obligatoria |
|---|---|---|
| `scalar` | Una ronda nodo por nodo, en el editor | Sí |
| `batch` | La versión NumPy, para el sweep | No — sin ella no aparece en el barrido |
| `updated` | Si una arista entregada cuenta como actualización (coloreado) | Sí |

Los parámetros propios se declaran con `ParamSpec` y viajan en el diccionario
`params` que reciben todas las piezas, de modo que agregar un parámetro nuevo
no cambia ninguna firma de la cadena.

### Cómo verificar que un cambio no alteró resultados

```bash
python3 scripts/equivalencia.py
```

Compara ~1000 casos con semillas fijas contra una línea base grabada. Si un
refactor cambió un número, lo dice y muestra cuál. Para grabar una línea base
nueva (solo cuando el cambio de resultados es intencional):
`python3 scripts/equivalencia.py --grabar`.

### Reglas de arquitectura que sostienen el diseño

1. **JavaScript solo arranca el runtime.** Ninguna regla del editor ni de la
   simulación vive en JS. Los dos archivos `.js` juntos son ~170 líneas y hacen
   lo mismo: bajar Pyodide, montar `.py`, ejecutar.
2. **El núcleo no conoce el navegador.** `models.py`, `geometry.py` y
   `simulation/` **no importan `js` ni `pyodide`**. Se pueden importar desde
   Python de escritorio con NumPy y probarse sin navegador. Solo `app.py` toca
   el DOM.
3. **El estado es un único objeto.** Todo vive en `STATE: AppState`. No hay
   estado escondido en el DOM: el SVG se **regenera** desde `STATE`, no se
   parchea.
4. **Fidelidad sobre elegancia.** El motor es un port literal de la lógica del
   backend original (`../simulador/back/api.py`). Varias expresiones se
   conservan tal cual aunque hubiera formas "más limpias", para no alterar el
   comportamiento observable. Ver [03](docs/03-motor-simulacion.md).

## 4. Flujo de arranque

```
1. index.html
       └─ <script type="module" src="./js/bootstrap.js">
2. bootstrap.js
       ├─ fetch de los 6 archivos .py          ─┐  (en paralelo)
       └─ loadPyodide(v314.0.2 desde jsDelivr) ─┘
3. pyodide.loadPackage("numpy")
4. Monta los .py en el FS virtual de Pyodide → /python/...
5. sys.path.insert(0, "/python")
6. runpy.run_path("/python/app.py", run_name="__main__")
       └─ main(): bind_events() → render_all() → fit_view()
7. Se oculta #python-loader; window.graphSimulatorReady = True
```

Si algo falla, el overlay queda en estado `error` con el mensaje visible; no hay
pantalla en blanco silenciosa.

## 5. Ciclo de vida de una interacción

Todas las interacciones del editor siguen la misma forma:

```
evento del DOM
   → handler Python (registrado con listen() + retain())
   → muta STATE
   → mark_graph_changed()          [si tocó el grafo]
   → render_all() o render_canvas()
   → innerHTML de las capas SVG
```

No hay diffing ni framework reactivo: `render_canvas()` reconstruye el
`innerHTML` de las cuatro capas SVG desde cero en cada cambio. Es simple y, al
tamaño de grafo que maneja el editor, suficientemente rápido.

## 6. Los dos flujos de simulación, de punta a punta

### Simulación individual (una ronda)

```
clic en «Simular»
   → app.simulate()
   → before = grafo inicial guardado (no el actual)
   → engine.run_round(before.graph_data(), algorithm, agreed)
        ├─ get_adj()          grafo JSON  → matriz n×n
        ├─ get_values()       grafo JSON  → vector n
        ├─ who_gets_message() sorteo      → messages, probabilities
        ├─ fv() o amp()       decisión    → [(valor, emisor)]
        └─ propagation_from_round()       → un registro por mensaje
   → apply_propagation(): colorea cada arista updated/delivered/quiet
   → STATE.simulation = SimulationState(before, after, propagation)
   → render_all()
```

**Detalle importante:** siempre parte del grafo **inicial**, no del resultado
anterior. Pulsar «Simular» dos veces son dos rondas independientes desde el mismo
punto de partida (con azar distinto), no dos rondas encadenadas.

### Barrido (sweep)

```
clic en «Ejecutar sweep»
   → app.start_sweep(): arma params, crea el Worker
   → postMessage({type:"run", params})
   ══════ frontera del Worker ══════
   → sweep-worker.js: Pyodide + NumPy propios
   → sweep.iter_sweep(...)  ← generador
        yield {type:"start", ...}
        para cada iteración (refinamiento):
            para cada p en linspace(0,1,N):
                matrix = topología con TODOS los pesos = p
                round_batch() → (S, n) valores finales
                acumula suma y suma de cuadrados
            yield {type:"iteration", points:[{p, mean, ciLow, ciHigh}], ...}
   → cada yield se reenvía con postMessage
   ══════ vuelta al hilo principal ══════
   → app.on_message(): render_chart(points) + texto de progreso
```

**Refinamiento progresivo:** en vez de calcular todo y mostrar al final, cada
iteración agrega simulaciones a **todos** los `p` a la vez y publica la curva
completa. Visualmente, la banda de confianza se va estrechando.

## 7. Por dónde empezar según lo que quieras cambiar

| Quiero cambiar… | Voy a… |
|---|---|
| **Agregar un algoritmo de consenso** | `python/simulation/algorithms.py`, y nada más |
| Una regla del editor (arrastre, atajos, validación) | `python/app.py` → [05](docs/05-controlador-app.md) |
| El resultado numérico de una ronda | `python/simulation/engine.py` → [03](docs/03-motor-simulacion.md) |
| El barrido o la estadística de la gráfica | `python/simulation/sweep.py` → [04](docs/04-barrido-sweep.md) |
| Dónde/cómo se dibuja algo (posición, color) | `python/geometry.py` → [02](docs/02-modelos-y-geometria.md) |
| Cómo se ve algo (tipografía, espaciado, tema) | `css/app.css` → [01](docs/01-runtime-y-arranque.md) |
| Agregar un control a la interfaz | `index.html` (marcado) + `app.py` (`bind_events`, `render_controls`) |
| Agregar un archivo Python nuevo | `js/bootstrap.js` → `PYTHON_FILES` (y `SWEEP_FILES` si aplica) |

## 8. Las tres trampas del proyecto

Cosas que rompen el código de forma no obvia:

1. **Todo callback que cruce a JavaScript debe pasar por `retain()`.** Si no, el
   recolector de basura de Python libera el proxy y el evento deja de responder
   sin ningún error visible.
2. **Los archivos `.py` no se descubren solos.** Están enumerados a mano en
   `PYTHON_FILES` (`bootstrap.js`) y `SWEEP_FILES` (`sweep-worker.js`). Un módulo
   nuevo que no se registre falla con `ModuleNotFoundError` dentro de Pyodide.
3. **Pyodide está fijado en `314.0.2` en dos archivos.** Cambiar la versión
   obliga a actualizar ambos y a revalidar la simulación (`AGENTS.md`).

Ver [06 · Detalles](docs/06-formatos-flujos-y-detalles.md) para más cabos sueltos.
