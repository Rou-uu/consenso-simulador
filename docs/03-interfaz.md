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

## Las dos pantallas

La app abre en el **análisis** (la gráfica). Junto a él está la pestaña
**Código**, y el editor del grafo es la pantalla **Red**, a la que se entra con
«Editar red» y se sale con «← Volver al análisis». `STATE.active_view` vale
`"analysis"`, `"code"` o `"editor"`, y `render_controls()` pone la clase
`view-analysis` / `view-code` / `view-editor` en `#app`.

Hay **una sola** barra lateral. Lo que es de una pantalla lleva la clase
`only-analysis`, `only-code` u `only-editor` (o `not-code`) y el CSS lo esconde
en las demás; el bloque
«Algoritmo» no lleva ninguna y se ve en las dos, así que la ronda del editor y el
sweep usan siempre la misma configuración. Mostrar u ocultar algo es cuestión de
clases en `index.html`, no de Python.

Mientras el lienzo está oculto mide 0 × 0, así que `change_view("editor")` llama a
`fit_view()` al entrar. Fuera del editor, `on_key_down` no toca el grafo (Supr no
borra un nodo que no se ve).

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

## Los parámetros del sweep

Cada campo lleva su explicación en `index.html`, dentro de una burbuja
(`.info-tip`) que aparece con el cursor o con el foco sobre su icono «i»; el
campo la referencia con `aria-describedby`, así que un lector de pantalla la lee
aunque no se vea. «Ejecutar sweep» vive en `.sweep-actions`, el último hijo de la
barra lateral, con `position: sticky` para quedar siempre a la vista. `sweep_field()` los lleva
a sus límites (`SWEEP_LIMITS`, los mismos que valida `iter_sweep`), así que un
valor fuera de rango se acota en vez de terminar en un error del motor.

`sweep_plan()` repite el reparto de `iter_sweep` (simulaciones por pasada y
recorte de pasadas) para anunciar en `#sweep-plan` cuántas simulaciones van a
correr, incluso cuando el reparto deja menos de las pedidas. Es una copia de dos
líneas del motor: `smoke_ui.py` comprueba que coincida con el evento `start` de
`iter_sweep`. Si cambias una, cambia la otra.

**Semilla.** `seed_field()` lee el campo: vacío es `None`, y algo que no sea un
entero entre 0 y `SEED_MAX` (2³² − 1, lo que acepta `np.random.seed`) se rechaza
en vez de sortear otra en silencio. `start_sweep` siempre manda una: la escrita o
una sorteada con `random`, que queda en `STATE.sweep_seed` y se muestra en el
estado del sweep. El Worker se la pasa a `iter_sweep`, que siembra cada bloque
(pasada, p) por separado (ver [MOTOR](MOTOR.md#el-sweep-iter_sweep)).

La explicación de cada modo de paso de mensajes está en `MODOS_AYUDA`; un modo
nuevo en el selector necesita su entrada ahí (`smoke_ui` lo vigila).

## La gráfica del sweep

`render_chart(points)` dibuja la media y la banda del IC en `#sweep-chart-svg`
(viewBox de 800 de ancho; el área de trazado son las constantes `CHART_*`). El eje X
es siempre `p` en [0, 1], así que sus marcas y su rejilla están fijas en
`index.html` con esos mismos números; si cambian las constantes, cambian ahí también.
El eje Y usa números redondos (`nice_axis`), y el tooltip
(`on_chart_pointer_move`) muestra el punto más cercano a `p`.

## Tabla y curva teórica

Antes de la primera iteración el Worker manda `{type: "teorica", valores, motivo}`
(ver [MOTOR](MOTOR.md#la-curva-teórica-teoriapy)); se guarda en
`STATE.experiment`. `theory_for(points)` la devuelve solo si corresponde a esos
puntos. `render_chart` la dibuja punteada y la incluye en la escala;
`render_results` arma la leyenda (o dice por qué no hay curva) y la tabla
`#results-table`, una fila por p. El CSV y el JSON la llevan también.

## Desglose por rondas

Inspecciona **las simulaciones que corrió el sweep**, no corridas nuevas. Dos
deslizadores y un número:

| Control | Qué elige | Qué hace al cambiar |
|---|---|---|
| p | uno de los p del sweep | pide esa simulación |
| Simulación (número, con flechas) | cualquiera de las que corrió ese p: hasta `simulaciones_por_p` (crece con cada pasada) | pide esa simulación; `trace_sim_field` acota lo escrito y no pide nada mientras el campo está vacío, y al salir (`change`) `normalize_trace_sim` deja escrito el número acotado |
| Ronda | 0 (valores iniciales) a las rondas del experimento | solo redibuja (`render_trace`) |

`request_trace` manda `{type: "traza"}` con todos los parámetros del sweep más
`indiceP` y `simulacion` al Worker del **editor** (`ENGINE`, que vive todo el
tiempo; el del sweep muere al terminar), que corre
`barrido.simulacion_del_sweep`: vuelve a sembrar el bloque de esa simulación y lo
repite (ver [MOTOR](MOTOR.md#el-sweep-iter_sweep)). Cada pedido lleva un
número (`TRACE_PEDIDO`) y `receive_trace` descarta las respuestas que llegan tarde
al arrastrar. Un error del desglose se queda en su panel: no destraba la ronda del
editor.

La tabla tiene una fila por nodo: valor antes y después, de quién recibió (en
color, de quién copió, cuando se sabe: solo con algoritmos sin `batch`) y si
cambió. Arriba,
la discrepancia de cada ronda; con muchas rondas solo una ventana de 15 alrededor
de la actual.

## La pestaña «Código»

`render_code_view()` decide qué se ve: con «Algoritmo propio…» elegido, el editor
grande (`#user-code`); con un algoritmo de casa, su código de solo lectura. Los
errores del código propio siguen en la barra lateral (`#user-code-error`), que se
ve en las tres pantallas, porque el fallo puede venir de la ronda o del sweep.

`codigo_de(clave)` arma ese código con `inspect.getsource`: las funciones de la
entrada (`ronda`, `batch`, `actualizo`) y, de forma transitiva, las funciones de
`algoritmos.py` que llaman, más la línea `ALGORITMO = {...}`. No copia
`parametros` (el algoritmo propio recibe `params` vacío, así que cada
`params.get(...)` usa su valor por omisión; la cabecera de la copia lo dice).
Una entrada hecha con `lambda` no se puede copiar y lo dice en vez de dar código
roto. `smoke_ui.py` instala la copia de cada algoritmo de casa como algoritmo
propio y comprueba que una ronda (en los dos modos) y el sweep den exactamente lo
mismo que el original.

«Editar una copia» reemplaza el editor sin preguntar solo si conserva el
ejemplo de `index.html` (`PLANTILLA_USUARIO`); si tiene cambios, pide confirmación.

## Descargas

`start_sweep` guarda en `STATE.experiment` lo que mandó al Worker (red, algoritmo,
parámetros, modo, código propio y semilla), y cada evento `iteration` le pone la
curva más reciente y si ya terminó. Las descargas salen de ahí, no de los
controles: si después cambias la red o el algoritmo, el archivo sigue
describiendo la corrida que se ve.

| Botón | Constructor | Qué lleva |
|---|---|---|
| CSV | `experiment_csv` | `p,media,ic_bajo,ic_alto,simulaciones`, una fila por p |
| JSON | `experiment_json` | todo lo necesario para repetirlo (la red en el formato de importación) y la curva; `completo` es falso si se detuvo |
| SVG | `chart_svg` | la gráfica tal como se ve, con su estilo en `CHART_SVG_STYLE` porque fuera de la página no hay `app.css` |

Los constructores son puros y `smoke_ui.py` los prueba, incluido que con solo lo
que trae el JSON se repite la misma curva. `download()` es lo único que toca el
navegador (Blob + enlace), y también lo usa «Exportar JSON» del editor.

## Deuda visible

Está en [09](09-invariantes-y-deuda.md#deuda-conocida). La más notoria: el color
de las aristas tras simular no se ve; solo se ve la animación.
