# Simulador de propagación en Python para el navegador

Recreación client-side del proyecto de referencia ubicado en
`../simulador`. La aplicación conserva el editor visual de grafos, los
algoritmos de propagación y el análisis de convergencia, pero ejecuta su lógica
en Python dentro del navegador. No requiere FastAPI ni otro backend propio.

El proyecto de referencia es de solo lectura. Todo el código de esta recreación
vive en `PruebasLocura`.

## Ejecución local

Requisitos:

- Python 3.11 o posterior, únicamente para servir los archivos estáticos.
- Un navegador moderno con WebAssembly y módulos ES.
- Conexión a internet para descargar Pyodide y NumPy desde jsDelivr. El
  navegador puede reutilizarlos desde su caché en visitas posteriores.

Desde la raíz del proyecto:

```bash
python3 scripts/serve.py
```

Después abre [http://localhost:8000](http://localhost:8000).

El host y el puerto son configurables:

```bash
python3 scripts/serve.py --host 0.0.0.0 --port 8080
```

No abras `index.html` directamente con `file://`: los módulos ES, las cargas de
Python y los Web Workers necesitan un origen HTTP.

## Arquitectura

```text
PruebasLocura/
├── index.html                 # Estructura de la interfaz y overlay de arranque
├── css/                       # Estilos y fidelidad visual
├── js/
│   └── bootstrap.js           # Único cargador del runtime principal
├── python/
│   ├── app.py                 # Estado, eventos, DOM, SVG y punto de entrada
│   ├── models.py              # Nodos, aristas, pestañas y simulación
│   ├── geometry.py            # Color, curvas, intersecciones y fit-view
│   └── simulation/
│       ├── algorithms.py      # Algoritmos de consenso ← se edita para agregar uno
│       ├── registry.py        # Registro que alimenta motor, sweep e interfaz
│       ├── rng.py             # Muestreo aleatorio compartido
│       ├── engine.py          # Port del motor Python de back/api.py
│       └── sweep.py           # Barrido NumPy progresivo
├── scripts/
│   ├── serve.py               # Servidor HTTP estático para desarrollo
│   ├── equivalencia.py        # Arnés de equivalencia del motor
│   └── smoke_ui.py            # Smoke de la interfaz con DOM simulado
└── README.md
```

El flujo de arranque es:

1. `index.html` carga `js/bootstrap.js` como módulo ES.
2. El cargador obtiene Pyodide **314.0.2** desde la distribución fija de
   [jsDelivr](https://cdn.jsdelivr.net/pyodide/v314.0.2/full/).
3. Pyodide carga NumPy.
4. El cargador descarga `python/app.py`, lo monta en `/python`, agrega esa ruta
   a `sys.path` y lo ejecuta como `__main__`.
5. Los estados de carga, éxito o error se muestran mediante
   `#python-loader` y `#loader-detail`.

JavaScript se limita al arranque del runtime y, cuando corresponde, al puente
del Web Worker. Las reglas del editor, el estado de la aplicación y los
algoritmos de simulación pertenecen a los módulos Python.

## Uso

- Arrastra el círculo `Nodo` al canvas. También puedes hacerle clic para crear
  un nodo en el centro visible.
- Arrastra los nodos para moverlos y haz doble clic para cambiar su valor.
- Mantén `Shift` y arrastra desde un nodo hacia otro para crear una arista.
- Haz clic en una arista o en su etiqueta para editar el peso.
- Usa espacio + arrastre para desplazar el canvas, la rueda para zoom y los
  controles inferiores para zoom/ajuste.
- Importar abre el JSON en una pestaña nueva; exportar descarga `graph.json`.

## Fidelidad y diferencias conocidas

- ReactFlow y Recharts se sustituyeron por SVG renderizado desde Python. Se
  conservan la estructura, dimensiones, paleta, nodos, curvas, etiquetas,
  minimapa, gráfica e interacciones principales, aunque el antialiasing y los
  controles pueden diferir algunos píxeles.
- El motor individual y el barrido se portaron desde la lógica Python de
  `back/api.py`. El sweep usa NumPy dentro de un Worker para no bloquear la UI.
  Mantiene la distribución y las reglas originales, pero no pretende repetir
  la misma secuencia aleatoria que una ejecución previa.
- La edición de nodos y aristas usa un diálogo propio para funcionar igual en
  navegadores que bloquean `prompt`. La limpieza conserva un diálogo alternativo
  cuando el navegador no admite `confirm`.
- No se copió la suite de tests del proyecto de referencia, por decisión de
  alcance. La entrega se verificó con comprobaciones funcionales y recorridos
  reales en navegador.
- Pyodide y NumPy se descargan desde una versión fija del CDN durante la primera
  visita. La aplicación no realiza solicitudes a FastAPI ni necesita servidor
  de aplicación.

## Despliegue

La raíz completa puede publicarse en cualquier hosting de archivos estáticos.
El servidor debe entregar `.js`/`.mjs` como JavaScript, `.wasm` como
`application/wasm` y permitir las solicitudes HTTPS a jsDelivr. No se necesita
configurar rutas de API, CORS para un backend ni procesos residentes.

Pyodide está fijado deliberadamente en la versión `314.0.2`. Cualquier cambio
de versión debe validarse antes contra la simulación y la integración con el
navegador.
