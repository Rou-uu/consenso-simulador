"""Barrido vectorizado de discrepancia contra probabilidad ``p``.

El calculo es el port directo del endpoint ``/simulate/sweep`` del backend.
Para cada ``p`` reemplaza todos los pesos positivos de la topologia por el
mismo valor, corre ``S`` replicas NumPy en paralelo y acumula suma y suma de
cuadrados. La interfaz principal es ``iter_sweep``: un generador de diccionarios
nativos que un Web Worker puede publicar con ``postMessage`` sin serializar
objetos NumPy. ``async_iter_sweep`` ofrece el mismo contrato para controladores
asincronos de Pyodide.

La cancelacion es cooperativa. Se revisa antes de cada iteracion, cada punto de
``p`` y cada ronda batched. En un Worker que no pueda procesar mensajes mientras
Python esta ejecutando, terminar el Worker sigue siendo la cancelacion
inmediata; el token permite cancelacion limpia entre unidades de trabajo o
cuando se comparte una bandera consultable.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from typing import Any, AsyncIterator, Callable, Iterator, Mapping

import numpy as np

from . import registry
from .algorithms import amp_batch, fv_batch  # noqa: F401 (re-export)
from .engine import get_adj, get_values
from .rng import random_array as _random_array


#: Algoritmos con version vectorizada. Vista viva sobre el registro: un
#: algoritmo sin `batch` no aparece aqui y por tanto no se ofrece en el sweep.
BATCH_ALGORITHMS = registry.BATCH


class SweepCancelled(RuntimeError):
    """Interrupcion interna usada para salir limpiamente de un batch."""


class CancellationToken:
    """Bandera mutable de cancelacion cooperativa.

    ``cancel()`` es idempotente. La propiedad ``cancelled`` puede consultarse
    desde Python o exponerse mediante un proxy de Pyodide.
    """

    def __init__(self) -> None:
        self._cancelled = False

    @property
    def cancelled(self) -> bool:
        return self._cancelled

    def cancel(self) -> None:
        self._cancelled = True


def is_cancelled(cancel: Any | None) -> bool:
    """Interpreta tokens, callables o mappings con clave ``cancelled``.

    Esta flexibilidad permite usar ``CancellationToken``, una funcion que lea
    una bandera compartida o un objeto/proxy del controlador. ``None`` nunca
    cancela.
    """

    if cancel is None:
        return False
    if callable(cancel):
        return bool(cancel())
    if isinstance(cancel, Mapping):
        flag = cancel.get("cancelled", False)
    else:
        flag = getattr(cancel, "cancelled", False)
    return bool(flag() if callable(flag) else flag)


def _check_cancelled(cancel: Any | None) -> None:
    if is_cancelled(cancel):
        raise SweepCancelled("Sweep cancelado")


def who_gets_message_batch(
    matrix: np.ndarray,
    adjacency_mask: np.ndarray,
    directed: bool,
    simulations: int,
    *,
    rng: Any | None = None,
) -> np.ndarray:
    """Sortea transmisiones para ``simulations`` replicas en paralelo.

    ``fired[s, source, target]`` es verdadero cuando esa arista disparo. A
    diferencia del motor individual, este tensor *no* se transpone porque los
    algoritmos batched reducen directamente sobre el eje de emisores. En grafos
    no dirigidos la misma muestra se refleja para ambas direcciones, igual que
    en el backend.

    ``adjacency_mask`` debe ser ``ceil(matrix)`` y se recibe por separado para
    poder reutilizarlo durante varias rondas del mismo ``p``.
    """

    matrix = np.asarray(matrix)
    adjacency_mask = np.asarray(adjacency_mask)
    n_nodes = matrix.shape[0]
    probabilities = _random_array(rng, (simulations, n_nodes, n_nodes))
    probabilities *= adjacency_mask
    if not directed:
        upper = np.triu(probabilities)
        probabilities = upper + np.transpose(upper, (0, 2, 1))
    return matrix > probabilities


def round_batch(
    algorithm: str | Callable[..., np.ndarray],
    matrix: np.ndarray,
    values: np.ndarray,
    directed: bool,
    simulations: int,
    times: int = 1,
    params: Mapping[str, Any] | None = None,
    *,
    rng: Any | None = None,
    cancel: Any | None = None,
) -> np.ndarray:
    """Corre replicas en paralelo durante ``times`` rondas.

    Devuelve una matriz ``(simulations, n_nodes)`` de valores finales. ``values``
    se fuerza a float como en el backend batched. Se permite el nombre ``fv`` o
    ``amp`` y tambien la funcion batched correspondiente. Cero simulaciones o
    rondas no positivas se rechazan en vez de producir estadisticas ambiguas.

    ``params`` son los parametros propios del algoritmo y se pasan tal cual a la
    funcion batched. Una funcion propia debe aceptar la firma uniforme
    ``(values, fired, params, *, rng)``.
    """

    if simulations < 1:
        raise ValueError("simulations debe ser al menos 1")
    if times < 1:
        raise ValueError("times debe ser al menos 1")

    if isinstance(algorithm, str):
        algorithm_fn = registry.batch_of(algorithm)
    elif callable(algorithm):
        algorithm_fn = algorithm
    else:
        raise ValueError("algorithm debe ser 'fv', 'amp' o una funcion batched")

    matrix = np.asarray(matrix)
    values = np.asarray(values)
    if matrix.shape != (len(values), len(values)):
        raise ValueError(
            "La matriz debe ser cuadrada y coincidir con la cantidad de valores"
        )

    adjacency_mask = np.ceil(matrix)
    batched_values = np.tile(values.astype(float), (simulations, 1))
    for _ in range(times):
        _check_cancelled(cancel)
        fired = who_gets_message_batch(
            matrix,
            adjacency_mask,
            directed,
            simulations,
            rng=rng,
        )
        batched_values = algorithm_fn(
            batched_values,
            fired,
            params,
            rng=rng,
        )
    return batched_values


@dataclass
class SweepAccumulator:
    """Suma, suma de cuadrados y conteo comun para todos los bins de ``p``.

    Cada iteracion completa agrega exactamente el mismo numero de simulaciones
    a cada bin. ``points`` usa varianza muestral (``n - 1``) e intervalo normal
    de 95 % ``mean +/- 1.96 * SE``, sin recortar los limites, tal como el
    backend.
    """

    probabilities: np.ndarray
    cumulative_sum: np.ndarray = field(init=False)
    cumulative_sumsq: np.ndarray = field(init=False)
    simulations_per_p: int = 0

    def __post_init__(self) -> None:
        self.probabilities = np.asarray(self.probabilities, dtype=float)
        self.cumulative_sum = np.zeros(len(self.probabilities))
        self.cumulative_sumsq = np.zeros(len(self.probabilities))

    def add(self, probability_index: int, discrepancies: np.ndarray) -> None:
        """Acumula un batch para un bin; el conteo avanza al completar la pasada."""

        discrepancies = np.asarray(discrepancies, dtype=float)
        self.cumulative_sum[probability_index] += float(discrepancies.sum())
        self.cumulative_sumsq[probability_index] += float(
            (discrepancies**2).sum()
        )

    def finish_iteration(self, simulations: int) -> None:
        if simulations < 1:
            raise ValueError("simulations debe ser al menos 1")
        self.simulations_per_p += int(simulations)

    def points(self) -> list[dict[str, float]]:
        """Devuelve media e IC95 en tipos nativos para cada ``p``."""

        n_done = self.simulations_per_p
        if n_done < 1:
            raise ValueError("Aun no hay simulaciones acumuladas")

        mean = self.cumulative_sum / n_done
        if n_done > 1:
            variance = np.maximum(
                (
                    self.cumulative_sumsq
                    - n_done * mean**2
                )
                / (n_done - 1),
                0.0,
            )
        else:
            variance = np.zeros(len(self.probabilities))
        standard_error = np.sqrt(variance) / (n_done**0.5)
        ci_low = mean - 1.96 * standard_error
        ci_high = mean + 1.96 * standard_error

        return [
            {
                "p": float(probability),
                "mean": float(mean[index]),
                "ciLow": float(ci_low[index]),
                "ciHigh": float(ci_high[index]),
            }
            for index, probability in enumerate(self.probabilities)
        ]


def _validated_int(name: str, value: Any, minimum: int, maximum: int) -> int:
    """Valida limites equivalentes a los Fields de SweepRequest."""

    if isinstance(value, bool):
        raise ValueError(f"{name} debe ser entero")
    try:
        converted = int(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{name} debe ser entero") from exc
    if converted != value:
        raise ValueError(f"{name} debe ser entero")
    if not minimum <= converted <= maximum:
        raise ValueError(f"{name} debe estar entre {minimum} y {maximum}")
    return converted


def _prepare_sweep(
    graph: Mapping[str, Any],
    algorithm: str,
    num_p_points: Any,
    total_simulations_per_p: Any,
    iterations: Any,
    rounds: Any,
) -> tuple[np.ndarray, np.ndarray, bool, np.ndarray, int, int, int]:
    """Valida el request local y deriva constantes reutilizadas."""

    # Valida antes de emitir `start`, y con el mensaje que distingue un
    # algoritmo inexistente de uno sin version vectorizada.
    registry.batch_of(algorithm)

    num_p_points = _validated_int("num_p_points", num_p_points, 2, 1001)
    total_simulations_per_p = _validated_int(
        "total_simulations_per_p", total_simulations_per_p, 1, 50_000
    )
    iterations = _validated_int("iterations", iterations, 1, 1000)
    rounds = _validated_int("rounds", rounds, 1, 1000)

    base_matrix = get_adj(graph)
    values = get_values(graph)
    if len(values) == 0:
        raise ValueError("El grafo no tiene nodos")

    edge_mask = base_matrix > 0
    probabilities = np.linspace(0, 1, num_p_points)
    simulations_per_iteration = max(
        1, total_simulations_per_p // iterations
    )
    # El total solicitado nunca se excede. Cuando se piden menos simulaciones
    # que iteraciones, el `max(1, ...)` de arriba redondea hacia arriba el
    # tamano del batch y el backend original terminaba corriendo una
    # simulacion por iteracion, es decir mas de las pedidas (pedir 10 con 50
    # iteraciones corria 50). Aqui se recortan las iteraciones para que el
    # conteo final coincida con lo pedido. Si el total no es divisible entre
    # las iteraciones se sigue truncando hacia abajo, como antes.
    iterations = min(iterations, total_simulations_per_p // simulations_per_iteration)
    return (
        edge_mask,
        values,
        bool(graph["directed"]),
        probabilities,
        simulations_per_iteration,
        iterations,
        rounds,
    )


def iter_sweep(
    graph: Mapping[str, Any],
    algorithm: str = "fv",
    params: Mapping[str, Any] | None = None,
    *,
    num_p_points: int = 201,
    total_simulations_per_p: int = 5000,
    iterations: int = 50,
    rounds: int = 1,
    rng: Any | None = None,
    cancel: Any | None = None,
) -> Iterator[dict[str, Any]]:
    """Genera eventos progresivos del sweep, uno por iteracion completa.

    Contrato de eventos:

    * ``start``: metadatos equivalentes al primer registro ND-JSON del backend.
    * ``iteration``: curva completa acumulada, con ``done=True`` en la ultima.
    * ``cancelled``: terminacion cooperativa limpia; no presenta una curva
      parcial de una iteracion inconclusa.
    * ``error``: error ocurrido despues de ``start`` durante el calculo.

    Cada iteracion corre ``max(1, total // iterations)`` simulaciones por ``p``,
    y las iteraciones se recortan para que el acumulado **nunca exceda** el
    total solicitado. Si el total no es divisible entre las iteraciones, el
    conteo final es menor que lo pedido (se trunca hacia abajo).

    Esto se aparta del backend original a proposito: alli pedir menos
    simulaciones que iteraciones corria de mas (10 simulaciones con 50
    iteraciones corrian 50), porque el ``max(1, ...)`` elevaba el tamano del
    batch sin ajustar el numero de pasadas.
    """

    (
        edge_mask,
        values,
        directed,
        probabilities,
        simulations_per_iteration,
        total_iterations,
        rounds,
    ) = _prepare_sweep(
        graph,
        algorithm,
        num_p_points,
        total_simulations_per_p,
        iterations,
        rounds,
    )

    yield {
        "type": "start",
        "numPPoints": int(len(probabilities)),
        "iterations": int(total_iterations),
        "simsPerIteration": int(simulations_per_iteration),
        "algorithm": algorithm,
    }

    accumulator = SweepAccumulator(probabilities)
    current_iteration = 0
    try:
        for current_iteration in range(total_iterations):
            _check_cancelled(cancel)
            for probability_index, probability in enumerate(probabilities):
                _check_cancelled(cancel)
                matrix = np.where(edge_mask, probability, 0.0)
                final_values = round_batch(
                    algorithm,
                    matrix,
                    values,
                    directed,
                    simulations_per_iteration,
                    times=rounds,
                    params=params,
                    rng=rng,
                    cancel=cancel,
                )
                discrepancies = (
                    final_values.max(axis=1) - final_values.min(axis=1)
                )
                accumulator.add(probability_index, discrepancies)

            accumulator.finish_iteration(simulations_per_iteration)
            yield {
                "type": "iteration",
                "iteration": int(current_iteration),
                "totalIterations": int(total_iterations),
                "simulationsPerP": int(accumulator.simulations_per_p),
                "done": current_iteration == total_iterations - 1,
                "points": accumulator.points(),
            }
    except SweepCancelled:
        yield {
            "type": "cancelled",
            "iteration": int(current_iteration),
            "totalIterations": int(total_iterations),
            "simulationsPerP": int(accumulator.simulations_per_p),
            "done": True,
        }
    except Exception as exc:
        # Paridad con el stream del backend: fallas posteriores a start viajan
        # como evento y no derriban al consumidor del generador.
        yield {"type": "error", "message": str(exc)}


async def async_iter_sweep(
    graph: Mapping[str, Any],
    algorithm: str = "fv",
    params: Mapping[str, Any] | None = None,
    *,
    num_p_points: int = 201,
    total_simulations_per_p: int = 5000,
    iterations: int = 50,
    rounds: int = 1,
    delay_ms: int = 100,
    rng: Any | None = None,
    cancel: Any | None = None,
) -> AsyncIterator[dict[str, Any]]:
    """Version async de ``iter_sweep`` con pausa entre iteraciones.

    ``delay_ms`` se valida en ``[0, 2000]`` como el request original. El await
    sucede despues de entregar cada evento ``iteration`` y permite a la UI
    pintar la curva o actualizar una bandera de cancelacion.
    """

    delay_ms = _validated_int("delay_ms", delay_ms, 0, 2000)
    events = iter_sweep(
        graph,
        algorithm,
        params,
        num_p_points=num_p_points,
        total_simulations_per_p=total_simulations_per_p,
        iterations=iterations,
        rounds=rounds,
        rng=rng,
        cancel=cancel,
    )
    for event in events:
        yield event
        if event["type"] == "iteration":
            await asyncio.sleep(delay_ms / 1000)


def run_sweep(
    graph: Mapping[str, Any],
    algorithm: str = "fv",
    params: Mapping[str, Any] | None = None,
    *,
    on_event: Callable[[dict[str, Any]], Any] | None = None,
    **options: Any,
) -> dict[str, Any]:
    """Consume ``iter_sweep`` y devuelve el ultimo evento.

    Un Worker puede pasar ``on_event=post_message`` para publicar progreso sin
    guardar toda la historia. Sin callback, esta funcion sirve para una llamada
    sincrona simple y retorna la curva final (o el evento de error/cancelacion).
    """

    last_event: dict[str, Any] | None = None
    for event in iter_sweep(graph, algorithm, params, **options):
        last_event = event
        if on_event is not None:
            on_event(event)

    if last_event is None:  # El generador siempre emite start, salvo bug futuro.
        raise RuntimeError("El sweep no produjo eventos")
    return last_event


__all__ = [
    "BATCH_ALGORITHMS",
    "CancellationToken",
    "SweepAccumulator",
    "SweepCancelled",
    "amp_batch",
    "async_iter_sweep",
    "fv_batch",
    "is_cancelled",
    "iter_sweep",
    "round_batch",
    "run_sweep",
    "who_gets_message_batch",
]
