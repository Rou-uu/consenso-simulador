# 00 — Empezar aquí

## Qué modela

Un grupo de nodos, cada uno con un **valor** (un número). Las **aristas** son
canales de comunicación y su **peso** es la probabilidad de que transmitan en
una ronda: `1` siempre, `0` nunca, `0.5` la mitad de las veces.

En cada **ronda** se sortea qué aristas transmiten, y cada nodo que recibió
mensajes actualiza su valor según el algoritmo. La medida es la
**discrepancia**, `max(valores) − min(valores)`: vale `0` cuando hay
**consenso**.

| | FV (Flip Value) | AMP (Agreement) |
|---|---|---|
| El nodo que recibió | sortea uno de sus emisores y copia su valor | adopta el valor fijo `agreed` (0.5 por omisión) |
| El que no recibió | conserva su valor | conserva su valor |

Dos formas de correrlo:

1. **«Simular»**: una ronda sobre tu grafo, con tus pesos.
2. **«Análisis de Convergencia»** (sweep): ignora los pesos, pone la misma
   probabilidad `p` en todas las aristas que existen, barre `p` de 0 a 1 y grafica
   la discrepancia media con su IC 95 %.

La matemática es la de `back/api.py` del proyecto original. Ver [MOTOR.md](MOTOR.md).

## Cómo lo corro

```bash
python3 scripts/serve.py          # http://localhost:8000
```

- **No funciona con doble clic en `index.html`**: los módulos ES, `fetch` y el
  Worker exigen un origen HTTP.
- **La primera carga tarda** unos segundos: baja Pyodide y NumPy desde jsDelivr.
  El Worker baja su propia copia.
- `serve.py` acepta `--host` y `--port` (por omisión `127.0.0.1` y `8000`).

Para verificar (siempre con `PYTHONDONTWRITEBYTECODE=1`):

```bash
PYTHONDONTWRITEBYTECODE=1 python3 scripts/equivalencia.py   # el motor
PYTHONDONTWRITEBYTECODE=1 python3 scripts/smoke_ui.py       # la interfaz
```

## Primer recorrido

1. Arrastra el círculo «Nodo» al lienzo dos veces.
2. Con **Shift** presionado, arrastra de un nodo al otro: crea una arista.
3. Doble clic en un nodo para cambiar su valor; clic en la arista para su peso.
4. «Simular»: los valores cambian. «Ver Antes» y «Resetear» vuelven atrás.
5. «Análisis de Convergencia» → baja los parámetros (21 puntos, 200
   simulaciones, 5 iteraciones) → «Ejecutar sweep».

## El proyecto de referencia

`../Simulador` (FastAPI + React) es **de solo lectura**: no se edita ni se generan
archivos ahí. Su `back/api.py` es el origen del motor y sus
`back/examples/*.json` se importan sin adaptar.

## Constantes que viven en dos sitios

| Qué | Valor | Dónde |
|---|---|---|
| Pyodide | `314.0.2` | 4 sitios: líneas 1 y 3 de `js/bootstrap.js` y de `js/sweep-worker.js` |
| NumPy para verificar | `2.4.5`, con Python 3.12 | `.github/workflows/pages.yml` |
| Límites del sweep | puntos 2–1001, simulaciones 1–50 000, iteraciones 1–1000, rondas 1–1000 | atributos de los `<input>` en `index.html` **y** `_entero(...)` en `barrido.py` |
| `agreed` | `0.5` | `ALGORITMOS["amp"]["parametros"]` en `algoritmos.py` |

## Tres reglas que no están en el código

1. El proyecto de referencia no se toca.
2. `equivalencia.py --grabar` solo cuando el cambio de resultados es intencional
   y está justificado (ver [07](07-verificacion.md)).
3. Antes de dar algo por terminado, recorrerlo en el navegador.

Siguiente: [MAPA.md](MAPA.md).
