# 03 — Interfaz (`python/app.py`)

## La idea

Todo el estado vive en `STATE` (un `AppState`). Cada evento modifica `STATE` y
vuelve a dibujar **desde cero**: el SVG se regenera con `innerHTML`, no se
parchea. No hay framework ni diffing.

| Función | Redibuja | Cuándo usarla |
|---|---|---|
| `render_canvas()` | el lienzo y el minimapa | arrastres y zoom: lo más barato |
| `render_controls()` | botones, selector, campos, caja de código | cambios de estado sin tocar el grafo |
| `render_all()` | pestañas + controles + lienzo + panel JSON | después de cualquier cambio estructural |

## `retain()`: el detalle que rompe todo en silencio

Todo callback que se entrega a JavaScript pasa por `retain()` (o por `listen()`,
que lo usa). Crea el proxy con `create_proxy` y lo guarda en `PROXIES`. Sin eso,
Python libera el proxy y el evento **deja de responder sin ningún error**. Si un
botón no responde y la consola está limpia, sospecha esto primero.

Los listeners van sobre contenedores que no se regeneran (`#graph-tabs`,
`#graph-canvas`, `#algorithm-params`) y delegan con `event.target`. Un listener
puesto sobre contenido regenerado se pierde en el siguiente render.

## El lienzo

Cuatro capas dentro de `#graph-canvas`, todas con la misma transformación de
pan y zoom: `edges-layer`, `edge-labels-layer`, `nodes-layer` y
`connection-layer` (la línea mientras conectas).

`geometry.py` es puro (no toca el DOM): `value_color` (color por valor),
`node_center`, `edge_geometry` (recta, o curva si hay arista de vuelta),
`has_reverse` y `fit_transform` («Ajustar vista»).

## Interacciones

| Gesto | Qué hace | Dónde |
|---|---|---|
| arrastrar «Nodo» al lienzo, o clic en él | crea un nodo | `on_drop`, `add_node_at_canvas_center` |
| arrastrar un nodo | lo mueve; empieza tras **6 px** para no perder el `dblclick` | `on_canvas_pointer_move` |
| Shift + arrastrar de un nodo a otro | crea una arista | `on_canvas_pointer_down/up` |
| Espacio + arrastrar | desplaza el lienzo | `on_canvas_pointer_move` |
| rueda, `+` / `−` | zoom hacia el cursor, entre 0.2 y 3.0 | `on_wheel`, `zoom_by` |
| doble clic en un nodo / clic en una arista | edita el valor / el peso | `open_node_editor`, `on_canvas_click` |
| Delete / Backspace | borra lo seleccionado | `on_key_down` |

Los editores usan el diálogo propio `#app-dialog` (`open_dialog`), no `prompt`.

## Simular

`simulate()` manda `{type: "ronda"}` al Worker `ENGINE` y termina ahí. El
resultado llega a `apply_round()`, que arma el grafo «después» y llama a
`finish_round()` para desbloquear el botón (también por la vía del error). Si
no llega en 15 s, `on_round_timeout` mata el Worker y levanta otro.

Errores: los del algoritmo propio van a la caja `#user-code-error`, con el
traceback recortado a las líneas del código pegado (`trim_traceback`); los demás,
a un diálogo.

## Selector y parámetros

`render_algorithm_options()` arma el `<select>` desde `ALGORITMOS` más la ranura
«Algoritmo propio…». `render_algorithm_params()` arma un campo por cada entrada de
`parametros`, y `algorithm_params()` devuelve los valores que viajan al motor. Lo
escrito se conserva al cambiar de algoritmo y volver.

El selector `#message-mode-select` es fijo en `index.html` (los modos son del
motor, no de cada algoritmo). `on_message_mode_change` guarda el valor en
`STATE.message_mode`, y la ronda y el sweep lo mandan como `modo`.

## La gráfica del sweep

`render_chart(points)` dibuja la media y la banda del IC en `#sweep-chart-svg`.
El eje Y usa números redondos (`nice_axis`), y el tooltip
(`on_chart_pointer_move`) muestra el punto más cercano a `p`.

## Deuda visible

Está en [09](09-invariantes-y-deuda.md#deuda-conocida). La más notoria: el color
de las aristas tras simular no se ve; solo se ve la animación.
