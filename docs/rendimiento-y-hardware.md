# Rendimiento y hardware

Todo lo que importa es el **sweep**. Una ronda del editor tarda milisegundos.
Las cifras son de **ejecución nativa** (Python 3.12, NumPy 2.4.5) en un Apple M3
Pro (6 núcleos de rendimiento + 6 de eficiencia, 18 GB). No se ha medido cuánto
más lento es Pyodide en el navegador.

## Qué hace el sweep

Con `batch` (`round_batch` en `barrido.py`): para cada `p` genera un tensor de
aleatorios `(S, n, n)`, lo compara contra la matriz y reduce sobre los emisores.
No hay álgebra lineal. Medido (n = 60, S = 3000, una ronda de FV):

| Operación | % del tiempo |
|---|---|
| `fired * rand(S,n,n)` (llaves de selección de FV) | 35 % |
| `np.random.rand(S,n,n)` (sorteo de aristas) | 30 % |
| `argmax` | 26 % |
| comparación, máscara, `any` | 9 % |

- **Un solo hilo**: NumPy no paraleliza estas operaciones. Más núcleos no aceleran
  nada hoy; solo importa la velocidad de *un* núcleo.
- **El 65 % es generar aleatorios.**
- **No lo limita la memoria** con un núcleo (~6.9 GB/s efectivos).

Sin `batch` (`run_one_simulation_naive`): una simulación a la vez, entre 13 y 40
veces más lento (medido con 6 y 20 nodos).

## Cifras

Simulaciones-ronda por segundo, un núcleo, con `batch`:

| n | sim-rondas/s |
|---|---|
| 10 | 754 000 |
| 30 | 121 000 |
| 100 | 10 400 |
| 300 | 1 110 |
| 1000 | 108 |

El costo crece con **n²**. Un sweep típico (201 valores de `p`, 5000
simulaciones):

| n | 1 ronda | 10 rondas |
|---|---|---|
| 10 | 1 s | 13 s |
| 30 | 8 s | 1 min |
| 100 | 2 min | 16 min |
| 300 | 15 min | 2.5 h |
| 1000 | 2.6 h | 1.1 días |

## Memoria

El tensor vivo es `S_por_iteración × n × n × 8` bytes, con 3 o 4 vivos a la vez.
Con los valores por omisión (5000 simulaciones en 50 iteraciones = 100 por
iteración) es poco incluso con n = 1000. Se vuelve el límite con **pocas
iteraciones y muchas simulaciones**: bajar `iterations` a 1 «para ir más rápido»
multiplica `S`. 18 GB alcanzan para n ≤ 300; para n ≥ 500, conviene tener 64 GB.

## Mejoras posibles

| Mejora | Ganancia | Dónde | Salvedad |
|---|---|---|---|
| Repartir los `p` entre procesos (`ProcessPoolExecutor`) | ×4 medido (se estanca en 10 procesos) | el bucle de `p` en `iter_sweep` | solo nativo; en el navegador harían falta varios Workers |
| `float32` en los tensores | ×1.47 y la mitad de memoria | `who_gets_message_batch` y los `batch` | cambia los números: regrabar con justificación |
| GPU (CuPy) | no medido | los `batch` | solo nativo |
| Generador PCG64 | ×0.76 (más lento) | — | descartado |

## Qué priorizar al comprar

1. **Rendimiento de un solo núcleo**: es lo que cuenta hoy.
2. **64 GB de RAM**, si se van a usar grafos grandes.
3. **Más núcleos de rendimiento**, solo si se implementa el reparto entre procesos.
4. **GPU NVIDIA**, solo si se implementa CuPy y después de una prueba de concepto.
5. El almacenamiento no importa: no hay lectura ni escritura de disco durante el
   cálculo.

## Reproducir

```bash
cd python && python3 -c "
import time, numpy as np
from simulation.algoritmos import fv_batch
from simulation.barrido import round_batch
n, S = 100, 2000
m = np.where(np.add.outer(range(n), range(n)) % 3 == 0, 0.5, 0.0); np.fill_diagonal(m, 0)
v = np.arange(n) % 2
round_batch(fv_batch, m, v, True, 100)
t = time.perf_counter(); round_batch(fv_batch, m, v, True, S, times=3)
print(f'{S*3/(time.perf_counter()-t):,.0f} sim-rondas/s')"
```

Para medir el navegador: correr un sweep con parámetros conocidos, cronometrarlo
y compararlo con el mismo cálculo nativo.
