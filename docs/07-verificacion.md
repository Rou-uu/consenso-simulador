# 07 — Verificación

No hay suite de tests, a propósito: `AGENTS.md` prohíbe portar la del original.
Hay dos scripts y el recorrido en navegador.

```bash
PYTHONDONTWRITEBYTECODE=1 python3 scripts/equivalencia.py   # OK — 943 casos idénticos a la línea base.
PYTHONDONTWRITEBYTECODE=1 python3 scripts/smoke_ui.py       # OK — todas las comprobaciones de la interfaz pasaron.
```

Siempre con `PYTHONDONTWRITEBYTECODE=1`: un `.pyc` viejo con el mismo mtime que el
fuente ya hizo pasar por bueno un código que no lo era.

## `equivalencia.py`: el motor no cambió de resultados

Corre unos 1050 casos con semillas fijas (`np.random.seed`) y los compara, valor por
valor, contra `scripts/equivalencia_baseline.json`. Cubre:

- `get_adj` y `get_values` en 13 grafos escritos a mano;
- `who_gets_message` y `run_round` con `fv` y `amp`, varios `agreed` y semillas;
- `simulate_graph` (la respuesta de `POST /simulate`) y `update_graph_data`;
- `round_batch` e `iter_sweep` completos, con y sin `batch`;
- el modo `por_emisor` por esos mismos caminos, más propiedades que debe cumplir
  (todo o nada con pesos iguales, cada arista conserva su `p`);
- el reparto de simulaciones por iteración;
- los mensajes de error.

Cada grafo defiende un bug concreto. Por ejemplo, `valores_casi_iguales` detecta
si `np.isclose` se cambia por `!=`, y `pesos_fuera_de_rango` detecta si
`np.ceil(matrix)` se cambia por `matrix > 0`.

**Cuándo correrlo:** antes y después de tocar `python/simulation/`.

**Si falla:** el cambio alteró un resultado. Es un hallazgo y se investiga. El
script imprime solo las primeras 40 diferencias, y las ausencias van primero.

**`--grabar`** reescribe la línea base con lo que haya, **incluido un error**. Solo
se usa cuando el cambio de resultados es intencional y está justificado, y se
comprueba qué claves cambiaron. Agregar un algoritmo a la tupla `ALGORITMOS` del
script y regrabar es legítimo: agrega claves sin tocar las viejas.

**La sección ADAPTADOR** es lo único del script que conoce las firmas del motor.
Si cambian, se edita esa sección y **no** se regraba: así sigue detectando cambios
de resultado.

**Control negativo:** al cambiar el arnés, perturba el motor a propósito (por
ejemplo, `argmax` → `argmin` en `fv_batch`), comprueba que falle y restaura. Un
arnés que siempre pasa no sirve.

Límites:
- Compara con `==` sobre JSON: no distingue `1` de `1.0`.
- `ronda` y `batch` consumen la aleatoriedad en distinto orden: **nunca**
  coinciden bit a bit, y solo se comparan estadísticamente.
- La línea base se grabó con NumPy 2.4.5 y Python 3.12 en macOS arm64. En otra
  plataforma puede diferir en el último bit; si pasa, se reporta y no se regraba.

## `smoke_ui.py`: la interfaz sin navegador

Sustituye `js`, `document` y el Worker por dobles en Python e importa `app.py`.
Comprueba el selector, los campos de parámetros, la gráfica, la ida y vuelta de
la ronda por el Worker y la ranura del algoritmo propio.

**No** cubre estilos, eventos reales, el arranque de Pyodide ni un `retain()`
olvidado (el doble de `create_proxy` es la identidad). Por eso existe el
recorrido en navegador.

## El recorrido en navegador

`python3 scripts/serve.py`, abrir `http://localhost:8000` con la consola abierta:

1. El overlay desaparece solo y el selector tiene FV, AMP y «Algoritmo propio…».
2. Dos nodos, una arista con Shift, «Simular»: cambian los valores.
3. AMP con otro `agreed`: los nodos que recibieron toman ese valor.
4. «Algoritmo propio…» con la plantilla: simula. Un error a propósito muestra su
   línea.
5. Sweep corto (21 puntos, 200 simulaciones, 5 iteraciones): llega a `5/5`.
6. Consola sin errores.

## CI

`.github/workflows/pages.yml` corre los dos scripts en cada push a `main`, pero
**publicar no depende de verificar**: todavía no se comprobó que la línea base dé
idéntica en Linux x86-64. Cuando se confirme, `needs: verificar` en el job
`publicar` lo convierte en candado. Ver [08](08-despliegue.md).
