# 01 — Arranque

Sin backend y sin paso de compilación: un servidor estático entrega los
archivos tal cual y el navegador hace todo lo demás.

## La página principal

1. `index.html` carga `css/app.css` y `<script type="module" src="./js/bootstrap.js">`.
   Muestra el overlay `#python-loader` mientras tanto.
2. `js/bootstrap.js` (`startApplication`):
   - baja Pyodide `314.0.2` desde jsDelivr y, en paralelo, los `.py` de
     `PYTHON_FILES` con `fetch(..., {cache: "no-store"})`;
   - carga NumPy y networkx (este por la URL de su wheel, que sale del lockfile:
     pedirlo por nombre arrastra matplotlib);
   - escribe los `.py` en `/python/` del sistema de archivos de Pyodide;
   - ejecuta `runpy.run_path("/python/app.py", run_name="__main__")`.
3. `app.py` → `main()`: `bind_events()` (todos los `listen(...)`),
   `start_engine_worker()`, `render_all()`, `render_chart([])` (dibuja el eje Y vacío).
4. El overlay se oculta. Si algo falla, queda en `data-state="error"` con el
   mensaje en `#loader-detail`.

`PYTHON_FILES` monta 8 archivos: `app.py`, `models.py`, `geometry.py`, `pdf.py`,
`topologias.py` (que no se importa hasta que se pide una topología, porque
importar networkx tarda y no siempre se usa) y, del
motor, solo `__init__.py`, `algoritmos.py` y `usuario.py`. El hilo principal no
calcula nada: solo lee `ALGORITMOS` para armar el selector.

## Los Workers

`js/sweep-worker.js` es un Worker con su **propio** Pyodide y NumPy. Monta 6
archivos (`SWEEP_FILES`): `__init__.py`, `algoritmos.py`, `ronda.py`,
`barrido.py`, `teoria.py` y `usuario.py`. **No** monta `app.py` ni `models.py`, así que el motor
no puede depender del editor: si lo hiciera, el Worker no arrancaría.

Se crean varias instancias del mismo archivo: una para las rondas y, en cada
sweep, una por algoritmo palomeado (corren a la vez):

| | `ENGINE` (rondas) | `WORKERS[clave]` (sweep) |
|---|---|---|
| Nace | al cargar la página (`start_engine_worker`) | al pulsar «Ejecutar sweep» (`start_sweep_worker`, uno por algoritmo) |
| Muere | solo si una ronda pasa de 15 s; entonces se levanta otro | al terminar o fallar el suyo, o al detener el sweep (`finish_sweep`) |
| Mensaje | `{type: "ronda", params}` → `{type: "ronda", resultado}` | `{type: "run", params}` → eventos `iteration`… `end` |

Si el algoritmo es el propio, el mensaje trae `params.fuente`. El Worker lo
compila con `usuario.instalar` (solo cuando cambia) antes de correr, y en caso de
error responde `{type: "error", etapa, message, traza}`.

## Por qué los `.py` se enumeran a mano

Un sitio estático no puede listar un directorio, así que `bootstrap.js` y
`sweep-worker.js` tienen sus listas escritas a mano. Un `.py` nuevo que no esté
en la lista falla con `ModuleNotFoundError` dentro de Pyodide: en la página, el
overlay se queda cargando; en el Worker, el sweep o la ronda dan error.

## `.nojekyll`

Archivo vacío en la raíz. Sin él, GitHub Pages pasa el sitio por Jekyll, que no
publica los `__init__.py` (empiezan con `_`). Localmente todo funciona y en
producción el arranque da 404. Ver [08](08-despliegue.md).
