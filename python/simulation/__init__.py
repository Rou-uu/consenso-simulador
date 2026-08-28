"""API publica del motor de simulacion ejecutado por Pyodide.

La simulacion individual conserva el contrato del endpoint original mediante
``simulate``/``simulate_graph``. Los barridos progresivos se consumen con
``iter_sweep`` (Worker sincrono) o ``async_iter_sweep`` (controlador async).
Todos los resultados de esas APIs de alto nivel usan estructuras nativas y son
serializables; las funciones numericas de bajo nivel retornan ``ndarray`` para
evitar copias dentro del calculo.

Para agregar un algoritmo se edita unicamente ``algorithms.py``: el registro
alimenta al motor, al sweep y a la interfaz. La importacion explicita de abajo
garantiza que los algoritmos queden registrados sin importar por que submodulo
entre el consumidor.
"""

from . import algorithms  # noqa: F401 (registra fv y amp al importar el paquete)
from . import registry
from .algorithms import (
    amp,
    amp_batch,
    amp_updated,
    fv,
    fv_batch,
    fv_updated,
)
from .engine import (
    ALGORITHMS,
    PROPAGATION_PREDICATES,
    discrepancy,
    get_adj,
    get_values,
    propagation_from_round,
    run_round,
    run_simulation,
    simulate,
    simulate_graph,
    update_graph_data,
    who_gets_message,
)
from .registry import AlgorithmSpec, ParamSpec, register
from .sweep import (
    BATCH_ALGORITHMS,
    CancellationToken,
    SweepAccumulator,
    SweepCancelled,
    async_iter_sweep,
    is_cancelled,
    iter_sweep,
    round_batch,
    run_sweep,
    who_gets_message_batch,
)

__all__ = [
    "ALGORITHMS",
    "BATCH_ALGORITHMS",
    "PROPAGATION_PREDICATES",
    "AlgorithmSpec",
    "CancellationToken",
    "ParamSpec",
    "SweepAccumulator",
    "SweepCancelled",
    "algorithms",
    "amp",
    "amp_batch",
    "amp_updated",
    "async_iter_sweep",
    "discrepancy",
    "fv",
    "fv_batch",
    "fv_updated",
    "get_adj",
    "get_values",
    "is_cancelled",
    "iter_sweep",
    "propagation_from_round",
    "register",
    "registry",
    "round_batch",
    "run_round",
    "run_simulation",
    "run_sweep",
    "simulate",
    "simulate_graph",
    "update_graph_data",
    "who_gets_message",
    "who_gets_message_batch",
]
