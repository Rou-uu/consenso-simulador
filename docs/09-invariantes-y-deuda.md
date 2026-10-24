# 09 — Invariantes, decisiones y deuda

## Invariantes: romperlos rompe el sistema

| Qué | Por qué |
|---|---|
| `scripts/equivalencia_baseline.json` solo se regraba con justificación | `--grabar` congela lo que haya, incluido un error ([07](07-verificacion.md)) |
| El contrato JSON del grafo | es el del original; sus ejemplos se importan sin adaptar ([02](02-modelo-de-datos.md)) |
| `PYTHON_FILES` y `SWEEP_FILES` al día | no hay descubrimiento de archivos; falta uno → `ModuleNotFoundError` |
| Pyodide `314.0.2` en los 4 sitios | cambiar tres de cuatro deja la app y el Worker en versiones distintas |
| `.nojekyll` | sin él, producción da 404 en `__init__.py` ([08](08-despliegue.md)) |
| `retain()` en todo callback que cruce a JS | sin él, el evento muere sin error ([03](03-interfaz.md)) |
| Las dos orientaciones: `messages[target, source]` en `ronda`, `fired[s, source, target]` en `batch` | un `batch` que reduce sobre el eje equivocado da resultados plausibles y falsos, sin error |
| El motor no importa nada del editor | el Worker no monta `app.py` ni `models.py` |
| El motor no corre en el hilo principal | un algoritmo pegado que no termina congelaría la pestaña; en el Worker se mata a los 15 s |
| El algoritmo propio vive en la clave fija `user_alg` | así no puede tapar a `fv` ni a `amp` |
| Toda diferencia con `api.py` está marcada | `# Cambio respecto a api.py` en el código y una fila en [MOTOR.md](MOTOR.md) |

## Decisiones deliberadas

- **Pyodide directo, no PyScript**: control total del arranque y del Worker.
- **SVG a mano, no ReactFlow ni Recharts**: el JavaScript solo arranca el runtime.
- **No se portó la suite de tests del original**: lo prohíbe `AGENTS.md`.
- **El SVG se regenera entero**: más simple que parchear; el umbral de 6 px del
  arrastre existe por eso.
- **El sweep ignora los pesos**: solo usa qué aristas existen (`weight > 0`) y pone
  la misma `p` en todas. Un peso `0` borra la arista del sweep aunque se vea en el
  editor.
- **`np.ceil(matrix)` como máscara** en `who_gets_message`, tal como en el backend.
- **El reparto del sweep nunca excede lo pedido** (ver [MOTOR.md](MOTOR.md)).
- **Se publica el repositorio completo** (`path: .`).
- **`publicar` no depende de `verificar`** ([08](08-despliegue.md)).
- **Las etiquetas de `parametros` se insertan como HTML** para poder usar
  `<code>`: vienen de `algoritmos.py`, no de quien usa la app.
- **Un algoritmo propio no puede declarar `parametros`**: la interfaz arma sus
  campos en el hilo principal, que no ve ese código.

## Deuda conocida

Cosas que están mal y se saben. Antes de arreglarlas, lee la causa.

| Síntoma | Causa | ¿Seguro arreglarlo? |
|---|---|---|
| Tras simular, todas las aristas se ven del mismo color; solo cambia la animación | `render_canvas` escribe `stroke` como atributo de presentación y el CSS de `.edge-path` lo pisa; las reglas `.is-updated`, `.is-delivered`, etc. de `app.css` nunca se aplican | sí; arreglar las dos cosas a la vez |
| Seleccionar un nodo o arista no se ve, pero Delete lo borra | falta el estilo de la selección | sí, y es urgente: se borra a ciegas |
| Un error del sweep se ve igual que el progreso | `.sweep-error` no tiene regla en `app.css` | sí |
| Un arranque fallido se ve igual que uno lento | no hay regla para `[data-state="error"]` | sí |
| Al conectar con Shift no se ve la línea | falta la regla `.connection-line` | sí |
| El minimapa no dibuja aristas | sus `<line>` no tienen `stroke` | sí |
| La ayuda promete «snap a múltiplos de 10» con Shift, y no existe | el original lo tiene (commit `72768fe` de `../Simulador`) y no se portó; aquí Shift sobre un nodo conecta | sí: portarlo del original |
| Con aristas paralelas, ambas se marcan igual | `get_adj` colapsa el par en una celda y `apply_propagation` empareja por par de nodos | no sin cambiar el contrato de `propagation` |
| En un grafo no dirigido solo se resalta una dirección | `apply_propagation` busca el par en el orden en que se dibujó la arista | sí, pero primero hay que decidir qué debe verse |
| El sweep pierde los límites máximos del formulario | `positive_int` solo aplica el mínimo; el máximo lo impone `_entero` en el Worker y llega como error | sí, tocando los dos lados |
| Cada sweep vuelve a bajar Pyodide | `finish_sweep` termina el Worker también cuando acaba bien | sí; reutilizarlo como `ENGINE` |
| La memoria crece durante la sesión | `PROXIES` nunca se vacía | con cuidado: liberar un proxy en uso reproduce el bug de `retain` |
| Arrastrar nodos salta con cientos de aristas | `render_canvas` es O(E²) por `has_reverse`, en cada `pointermove` | sí: precalcular un `set` de pares |
| Un `"value": "0.5"` (string) rompe al simular | `Node.from_json` no convierte `value` a número | con cuidado: cambia lo que recibe el motor |
| «Ajustar vista» acerca menos de lo que permite la rueda | `fit_transform` recorta el zoom a 2.0; la rueda, a 3.0 | sí |
| Escape no cierra los diálogos de confirmación | el listener está en el `<input>`, que en confirmaciones no tiene foco | sí |
| La banda del IC baja de 0 cerca de `p = 0` | el IC no se recorta, igual que en el backend | es esperado; si molesta, recorta solo al dibujar |
| Un sweep sin `batch` es lento y no avisa | el respaldo ingenuo es de 13 a 40 veces más lento | sí: un aviso en `#sweep-status` |

## Síntomas

| Veo | Probable | Miro |
|---|---|---|
| El overlay no desaparece | un `.py` falta en `PYTHON_FILES`, falta `.nojekyll`, o hay un `SyntaxError` | consola y pestaña Red; `#loader-detail` |
| Un botón no responde y la consola está limpia | `retain()` olvidado, o el listener está sobre contenido que se regenera | [03](03-interfaz.md) |
| «Simular» no hace nada por 15 s y luego avisa | el algoritmo no termina | el código del algoritmo |
| «Error al ejecutar la simulación: 'node_7'» | una arista apunta a un nodo que no existe (JSON importado) | el JSON |
| El sweep no arranca | un `.py` falta en `SWEEP_FILES`, un parámetro fuera de rango, o un grafo sin nodos | `#sweep-status` y consola |
| El sweep muere a media curva | memoria: el tensor es `S × n × n × 8` bytes, y bajar las iteraciones sube `S` | [rendimiento](rendimiento-y-hardware.md) |
| La curva del sweep sale plana | todos los nodos empiezan con el mismo valor, o un `batch` reduce sobre el eje equivocado | probar en un grafo dirigido y asimétrico |
| El arnés falla en casi todos los casos | cambió el orden o la cantidad de aleatoriedad consumida, un `.pyc` viejo, u otra versión de NumPy | [07](07-verificacion.md); no regrabar |
| «Resetear» no devuelve mi grafo | moviste nodos con una simulación en pantalla: `initial_graph` no se actualiza | [02](02-modelo-de-datos.md) |
