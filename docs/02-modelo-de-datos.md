# 02 — Modelo de datos

## Las dataclasses (`python/models.py`)

| Clase | Campos | Para qué |
|---|---|---|
| `Node` | `id`, `value`, `x`, `y` | un nodo; `x, y` salen de `position` en el JSON |
| `Edge` | `id`, `source`, `target`, `weight` (string), `highlight` | una arista; `highlight` es solo de presentación |
| `GraphTab` | `id`, `name`, `nodes`, `edges`, `directed` | una pestaña del editor |
| `SimulationState` | `before`, `after`, `propagation` | el resultado de una ronda, para «Ver Antes» |
| `AppState` | pestañas, vista, algoritmo y sus parámetros, selección, zoom, estado de ronda y sweep, `user_source` | **todo** el estado; vive en `STATE` de `app.py` |

## El contrato JSON

```json
{
  "directed": true,
  "nodes": [{"id": "node_0", "value": 1, "position": {"x": 0, "y": 0}}],
  "edges": [{"id": "edge_0", "source": "node_0", "target": "node_1", "weight": "0.5"}]
}
```

Es **idéntico** al del proyecto de referencia y al de su notebook: un archivo de
`../Simulador/back/examples/` se importa sin adaptar. No se cambia.

- `weight` es **string** porque así lo produce y lo consume el original.
  `get_adj` lo convierte con `float(...)`.
- `Edge.highlight` **no** sale en `to_json`: es estado de presentación y no
  pertenece al grafo.
- El `id` de arista se regenera como `edge_<índice>` al importar y al exportar.

## Del modelo al motor y de vuelta

`GraphTab.graph_data()` produce ese JSON, y eso es lo que viaja al Worker. El
motor nunca ve dataclasses. `get_adj` lo convierte en matriz:

- el orden de filas y columnas es el orden de `nodes`;
- `matrix[source, target] = float(weight)`; en un grafo no dirigido también
  `matrix[target, source]`;
- dos aristas entre el mismo par: **gana la última** del JSON;
- un self-loop se conserva en la diagonal;
- una arista que apunta a un nodo inexistente da `KeyError` con ese id.

La respuesta de `run_round` vuelve como JSON. `apply_round` (en `app.py`) copia
los valores nuevos a una copia del grafo y marca las aristas con la
`propagation`.

## Antes y después

- `STATE.initial_graph` guarda el grafo antes de la primera ronda, y «Resetear»
  vuelve a él.
- `mark_graph_changed()` lo actualiza **solo** si no hay una simulación en
  pantalla. Todo handler que modifique el grafo debe llamarla.
- «Ver Antes» alterna entre `simulation.before` y `simulation.after`.

## Agregar un campo

1. `from_json` y `to_json` de la clase en `models.py`.
2. Si debe ir al JSON exportado, recuerda que rompe la igualdad con el original:
   evítalo.
3. Exportar, limpiar e importar en el navegador debe devolver el mismo grafo.
