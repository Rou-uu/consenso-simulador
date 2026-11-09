#!/usr/bin/env python3
"""Arnés de equivalencia: congela el comportamiento observable del motor.

Sirve para refactorizar sin cambiar resultados. Captura la salida de cada API
pública del motor con semillas fijas y la compara contra una línea base
grabada antes del cambio.

Uso::

    python3 scripts/equivalencia.py --grabar   # graba la línea base
    python3 scripts/equivalencia.py            # compara contra la línea base

Determinismo
------------
Todos los casos fijan ``np.random.seed`` y el motor usa ``np.random`` cuando no
recibe un ``rng`` explícito, así que la secuencia es reproducible. El generador
heredado de NumPy (MT19937 vía ``RandomState``) tiene garantía de estabilidad
entre versiones, por lo que la línea base también vale dentro de Pyodide.

Cómo sobrevive a un cambio de firmas
------------------------------------
La sección ADAPTADOR es el único lugar que conoce cómo se llama al motor. Si
una fase del refactor cambia las firmas (por ejemplo ``agreed`` -> ``params``),
se edita **solo** esa sección: la línea base no se vuelve a grabar y sigue
detectando cualquier cambio de resultado.
"""

from __future__ import annotations

import argparse
import itertools
import json
import math
import sys
from pathlib import Path
from typing import Any

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT / "python"))

import numpy as np  # noqa: E402

from simulation.algoritmos import ALGORITMOS as MOTOR  # noqa: E402
from simulation.barrido import iter_sweep, round_batch, who_gets_message_batch  # noqa: E402
from simulation.ronda import (  # noqa: E402
    get_adj,
    get_values,
    run_round,
    simulate_graph,
    update_graph_data,
    who_gets_message,
)


BASELINE_PATH = PROJECT_ROOT / "scripts" / "equivalencia_baseline.json"


# ==========================================================================
# ADAPTADOR — lo único que se edita cuando cambian las firmas del motor.
# ==========================================================================

def _params(agreed):
    """Traduce el `agreed` de los casos al diccionario de parámetros del motor.

    Fase 1 movió `agreed: float` a `params: dict`. Los casos y la línea base no
    cambian: solo cambia cómo se le habla al motor.
    """
    return {"agreed": agreed}


def llamar_run_round(grafo, algoritmo, agreed):
    return run_round(grafo, algorithm=algoritmo, params=_params(agreed))


def llamar_simulate_graph(grafo, algoritmo, agreed):
    return simulate_graph(grafo, algorithm=algoritmo, params=_params(agreed))


def llamar_round_batch(matriz, valores, dirigido, algoritmo, sims, rondas, agreed):
    return round_batch(
        MOTOR[algoritmo]["batch"], matriz, valores, dirigido, sims,
        times=rondas, params=_params(agreed),
    )


def llamar_run_round_sin_params(grafo, algoritmo):
    """Camino en que el llamador omite los parámetros y manda el default.

    Sin este caso, cambiar el valor por omisión de un algoritmo pasaría
    desapercibido: el resto del arnés siempre pasa `params` explícito.
    """
    return run_round(grafo, algorithm=algoritmo)


def llamar_round_batch_sin_params(matriz, valores, dirigido, algoritmo, sims, rondas):
    return round_batch(MOTOR[algoritmo]["batch"], matriz, valores, dirigido, sims, times=rondas)


def llamar_iter_sweep(grafo, algoritmo, agreed, puntos, sims, iteraciones, rondas,
                      modo="por_arista"):
    return iter_sweep(
        grafo,
        algoritmo,
        _params(agreed),
        num_p_points=puntos,
        total_simulations_per_p=sims,
        iterations=iteraciones,
        rounds=rondas,
        modo=modo,
    )


def registrar_solo_escalar():
    """Un algoritmo sin `batch`: FV sin su versión vectorizada.

    El sweep debe correrlo simulación por simulación (el respaldo ingenuo).
    """
    MOTOR["solo_escalar"] = {"nombre": "Solo escalar", "ronda": MOTOR["fv"]["ronda"]}


# ==========================================================================
# GRAFOS — deterministas y escritos a mano, para que un fallo sea legible.
# ==========================================================================

def _grafo(dirigido, valores, aristas):
    """Arma el JSON del editor a partir de valores y tuplas (i, j, peso)."""
    return {
        "directed": dirigido,
        "nodes": [
            {"id": f"node_{i}", "value": v, "position": {"x": 60.0 * i, "y": 40.0 * i}}
            for i, v in enumerate(valores)
        ],
        "edges": [
            {
                "id": f"edge_{k}",
                "source": f"node_{i}",
                "target": f"node_{j}",
                "weight": str(w),
            }
            for k, (i, j, w) in enumerate(aristas)
        ],
    }


GRAFOS: dict[str, dict[str, Any]] = {
    # Equivalente al examples/k2.json del proyecto de referencia.
    "k2": _grafo(True, [1, 0], [(0, 1, 1), (1, 0, 1)]),
    "triangulo_dirigido": _grafo(
        True, [1, 0, 0.5], [(0, 1, 0.3), (1, 2, 0.6), (2, 0, 0.9)]
    ),
    "camino_no_dirigido": _grafo(
        False, [1, 0, 0, 1], [(0, 1, 0.5), (1, 2, 0.75), (2, 3, 0.25)]
    ),
    "un_nodo": _grafo(True, [1], []),
    "sin_aristas": _grafo(True, [1, 0, 0.5], []),
    "self_loop": _grafo(True, [1, 0], [(0, 0, 0.5), (0, 1, 1)]),
    # Aristas repetidas entre el mismo par: la última gana al escribir la matriz.
    "aristas_duplicadas": _grafo(True, [1, 0], [(0, 1, 0.2), (0, 1, 0.8)]),
    "valores_fraccionarios": _grafo(
        True, [0.125, 0.875, 0.5], [(0, 1, 0.4), (1, 2, 0.4), (2, 0, 0.4)]
    ),
    # Valores a distancia menor que la tolerancia de np.isclose respecto de los
    # `agreed` que se prueban (0.5 y 0.0). Sin este caso, cambiar `isclose` por
    # `!=` en el flag updated_value de AMP pasaría desapercibido. Pesos en 1
    # para que todas las aristas disparen y siempre haya propagación que medir.
    "valores_casi_iguales": _grafo(
        True,
        [0.5, 0.5 + 1e-9, 1e-9, 0.75],
        [(i, j, 1) for i in range(4) for j in range(4) if i != j],
    ),
    # La UI limita los pesos a (0, 1], pero la importación de JSON no valida, así
    # que el motor puede recibirlos fuera de rango. `np.ceil(w)` y `(w > 0)` solo
    # difieren con pesos no enteros > 1 (con w=1.5 la arista dispara con
    # probabilidad 0.75, no 1.0). El motor conserva `ceil` deliberadamente.
    "pesos_fuera_de_rango": _grafo(
        True, [1, 0, 0.5], [(0, 1, 1.5), (1, 2, 2.5), (2, 0, 0.5)]
    ),
    "pesos_fuera_de_rango_no_dirigido": _grafo(
        False, [1, 0, 0.5], [(0, 1, 1.5), (1, 2, 2.5)]
    ),
    "denso_8": _grafo(
        True,
        [1, 0, 1, 0, 0.5, 0.25, 0.75, 0],
        [(i, j, 0.5) for i in range(8) for j in range(8) if i != j and (i + j) % 3 == 0],
    ),
    "denso_6_no_dirigido": _grafo(
        False,
        [1, 0, 0.5, 0.5, 0, 1],
        [(i, j, 0.6) for i in range(6) for j in range(6) if i < j and (i + j) % 2 == 0],
    ),
}

ALGORITMOS = ("fv", "amp")
SEMILLAS = (0, 1, 7, 42, 1234)


# ==========================================================================
# CAPTURA
# ==========================================================================

def _nativo(valor):
    """Convierte escalares y arreglos NumPy a tipos JSON-serializables."""
    if isinstance(valor, np.generic):
        return valor.item()
    if isinstance(valor, np.ndarray):
        return valor.tolist()
    if isinstance(valor, dict):
        return {k: _nativo(v) for k, v in valor.items()}
    if isinstance(valor, (list, tuple)):
        return [_nativo(v) for v in valor]
    return valor


def _mensajes(n, emisores):
    """Lo que entrega un broadcast cuando solo `emisores` tuvieron éxito: messages[target, source]."""
    mensajes = np.zeros((n, n), dtype=int)
    for emisor in emisores:
        mensajes[:, emisor] = 1
        mensajes[emisor, emisor] = 0
    return mensajes


def _conjuntos(n):
    return [c for k in range(n + 1) for c in itertools.combinations(range(n), k)]


def _desacuerdos(algoritmo, valores):
    """c_j del paper: en cuántos conjuntos de j emisores termina habiendo desacuerdo."""
    n = len(valores)
    conteos = [0] * (n + 1)
    for emisores in _conjuntos(n):
        decisiones = MOTOR[algoritmo]["ronda"](None, np.array(valores, float), _mensajes(n, emisores), {})
        conteos[len(emisores)] += len({valor for valor, _ in decisiones}) > 1
    return conteos


def _peor_error(algoritmo, n, p):
    q = 1 - p
    return max(
        sum(c * p**j * q ** (n - j) for j, c in enumerate(_desacuerdos(algoritmo, valores)))
        for valores in itertools.product((0, 1), repeat=n)
    )


def _batch_igual_a_ronda(algoritmo, n):
    conjuntos = _conjuntos(n)
    fired = np.array([_mensajes(n, emisores).T.astype(bool) for emisores in conjuntos])
    for valores in itertools.product((0.0, 1.0), repeat=n):
        vals = np.tile(np.array(valores), (len(conjuntos), 1))
        salida = MOTOR[algoritmo]["batch"](vals, fired, {})
        for s, emisores in enumerate(conjuntos):
            decisiones = MOTOR[algoritmo]["ronda"](None, np.array(valores), _mensajes(n, emisores), {})
            if not np.array_equal(salida[s], [valor for valor, _ in decisiones]):
                return False
    return True


def capturar() -> dict[str, Any]:
    resultado: dict[str, Any] = {}

    # --- construcción: matriz y valores derivados del JSON ---
    for nombre, grafo in GRAFOS.items():
        resultado[f"get_adj/{nombre}"] = _nativo(get_adj(grafo))
        resultado[f"get_values/{nombre}"] = _nativo(get_values(grafo))

    # --- sorteo de mensajes ---
    for nombre, grafo in GRAFOS.items():
        matriz = get_adj(grafo)
        for semilla in SEMILLAS:
            np.random.seed(semilla)
            mensajes, probabilidades = who_gets_message(matriz, grafo["directed"])
            resultado[f"who_gets_message/{nombre}/s{semilla}"] = {
                "messages": _nativo(mensajes),
                "probabilities": _nativo(probabilidades),
            }

    # --- una ronda: el contrato completo que consume la UI ---
    for nombre, grafo in GRAFOS.items():
        for algoritmo in ALGORITMOS:
            for agreed in (0.5, 0.0, 1.25):
                for semilla in SEMILLAS:
                    np.random.seed(semilla)
                    salida = llamar_run_round(grafo, algoritmo, agreed)
                    clave = f"run_round/{nombre}/{algoritmo}/a{agreed}/s{semilla}"
                    resultado[clave] = _nativo(salida)

    # --- mismos motores, pero dejando que el default del algoritmo actúe ---
    for nombre, grafo in GRAFOS.items():
        for algoritmo in ALGORITMOS:
            for semilla in SEMILLAS:
                np.random.seed(semilla)
                resultado[f"run_round_default/{nombre}/{algoritmo}/s{semilla}"] = _nativo(
                    llamar_run_round_sin_params(grafo, algoritmo)
                )
            matriz = get_adj(grafo)
            valores = get_values(grafo)
            if len(valores) == 0:
                continue
            np.random.seed(3)
            resultado[f"round_batch_default/{nombre}/{algoritmo}"] = _nativo(
                llamar_round_batch_sin_params(
                    matriz, valores, grafo["directed"], algoritmo, 50, 2
                )
            )

    # --- contrato del endpoint POST /simulate original ---
    for nombre, grafo in GRAFOS.items():
        for algoritmo in ALGORITMOS:
            np.random.seed(99)
            resultado[f"simulate_graph/{nombre}/{algoritmo}"] = _nativo(
                llamar_simulate_graph(grafo, algoritmo, 0.5)
            )

    # --- motor batched del sweep ---
    for nombre, grafo in GRAFOS.items():
        matriz = get_adj(grafo)
        valores = get_values(grafo)
        if len(valores) == 0:
            continue
        for algoritmo in ALGORITMOS:
            for rondas in (1, 3):
                for semilla in (0, 42):
                    # Se varía `agreed` para que un `params` que se perdiera en el
                    # camino (y cayera al default 0.5) no pase desapercibido. En
                    # FV también, donde congela el invariante de que lo ignora.
                    for agreed in (0.5, 0.9):
                        np.random.seed(semilla)
                        finales = llamar_round_batch(
                            matriz, valores, grafo["directed"], algoritmo, 50, rondas, agreed
                        )
                        clave = (
                            f"round_batch/{nombre}/{algoritmo}/r{rondas}"
                            f"/a{agreed}/s{semilla}"
                        )
                        resultado[clave] = _nativo(finales)

    # --- sweep completo: todos los eventos, incluida la curva ---
    for nombre in ("k2", "triangulo_dirigido", "camino_no_dirigido", "denso_8"):
        grafo = GRAFOS[nombre]
        for algoritmo in ALGORITMOS:
            for agreed in (0.5, 0.9):
                np.random.seed(11)
                eventos = list(
                    llamar_iter_sweep(grafo, algoritmo, agreed, 5, 40, 2, 2)
                )
                resultado[f"iter_sweep/{nombre}/{algoritmo}/a{agreed}"] = _nativo(eventos)

    # --- sustitución de valores en el grafo, incluida la guarda de NaN ---
    for nombre, grafo in GRAFOS.items():
        cantidad = len(grafo["nodes"])
        if cantidad == 0:
            continue
        planos = [0.25 * (i + 1) for i in range(cantidad)]
        resultado[f"update_graph_data/{nombre}/planos"] = _nativo(
            update_graph_data(grafo, planos)
        )
        # Tuplas (valor, emisor) como las devuelven fv/amp, con un NaN que debe
        # dejar intacto el valor previo del nodo.
        con_nan = [(float("nan") if i % 2 == 0 else planos[i], None) for i in range(cantidad)]
        resultado[f"update_graph_data/{nombre}/con_nan"] = _nativo(
            update_graph_data(grafo, con_nan)
        )
        # El argumento original no debe mutarse.
        resultado[f"update_graph_data/{nombre}/sin_mutar"] = _nativo(grafo)

    # --- reparto del trabajo: el acumulado nunca debe exceder lo pedido ---
    for total, iteraciones in [
        (1, 1), (1, 50), (10, 50), (10, 10), (10, 1),
        (100, 50), (5000, 50), (7, 3), (3, 5), (50_000, 1000),
    ]:
        np.random.seed(0)
        eventos = list(
            llamar_iter_sweep(GRAFOS["k2"], "fv", 0.5, 2, total, iteraciones, 1)
        )
        inicio = eventos[0]
        final = [e for e in eventos if e["type"] == "iteration"][-1]
        resultado[f"reparto/total{total}/iter{iteraciones}"] = {
            "simsPerIteration": inicio["simsPerIteration"],
            "iterations": inicio["iterations"],
            "simulacionesReales": final["simulationsPerP"],
            "excedeLoPedido": final["simulationsPerP"] > total,
        }

    # --- rutas de error: los mensajes también son comportamiento observable ---
    vacio = {"directed": True, "nodes": [], "edges": []}

    # Un algoritmo sin `batch` usa el sweep ingenuo: se congela su curva.
    registrar_solo_escalar()
    np.random.seed(11)
    resultado["iter_sweep_sin_batch/k2/fv"] = _nativo(
        list(llamar_iter_sweep(GRAFOS["k2"], "solo_escalar", 0.5, 5, 40, 2, 2))
    )

    # --- modo por emisor (broadcast): sus resultados, por los mismos caminos ---
    for nombre, grafo in GRAFOS.items():
        matriz = get_adj(grafo)
        for semilla in (0, 42):
            np.random.seed(semilla)
            mensajes, probabilidades = who_gets_message(matriz, grafo["directed"], "por_emisor")
            resultado[f"por_emisor/who_gets_message/{nombre}/s{semilla}"] = {
                "messages": _nativo(mensajes),
                "probabilities": _nativo(probabilidades),
            }
            for algoritmo in ALGORITMOS:
                np.random.seed(semilla)
                resultado[f"por_emisor/run_round/{nombre}/{algoritmo}/s{semilla}"] = _nativo(
                    run_round(grafo, algoritmo, _params(0.5), modo="por_emisor")
                )
        if len(grafo["nodes"]) == 0:
            continue
        for algoritmo in ALGORITMOS:
            np.random.seed(3)
            resultado[f"por_emisor/round_batch/{nombre}/{algoritmo}"] = _nativo(
                round_batch(
                    MOTOR[algoritmo]["batch"], matriz, get_values(grafo), grafo["directed"],
                    50, times=2, params=_params(0.5), modo="por_emisor",
                )
            )
    for algoritmo in (*ALGORITMOS, "solo_escalar"):
        np.random.seed(11)
        resultado[f"por_emisor/iter_sweep/k2/{algoritmo}"] = _nativo(
            list(llamar_iter_sweep(GRAFOS["k2"], algoritmo, 0.5, 5, 40, 2, 2, "por_emisor"))
        )

    # --- modo por emisor: lo que debe cumplir, no solo lo que dio al grabar ---
    # La línea base congela también un error; estas propiedades dicen qué es correcto.
    # Nodo 0 -> 1 y 2 con el mismo peso; 1 -> 2 con otro. Asimétrica a propósito.
    iguales = np.array([[0, 0.5, 0.5], [0, 0, 0.5], [0, 0, 0]])
    anidados = np.array([[0, 0.3, 0.7], [0, 0, 0], [0, 0, 0]])
    np.random.seed(5)
    sorteos = [who_gets_message(iguales, True, "por_emisor")[0] for _ in range(2000)]
    # `messages` viene transpuesta: messages[target, source].
    llega_a_1 = np.array([m[1, 0] for m in sorteos])
    llega_a_2 = np.array([m[2, 0] for m in sorteos])
    fired = who_gets_message_batch(iguales, np.ceil(iguales), True, 2000, "por_emisor")
    sorteos_anidados = np.array(
        [who_gets_message(anidados, True, "por_emisor")[0] for _ in range(20_000)]
    )
    resultado["por_emisor/propiedades"] = {
        "ronda_todo_o_nada": bool((llega_a_1 == llega_a_2).all()),
        "ronda_emisores_independientes": bool(
            (np.array([m[2, 1] for m in sorteos]) != llega_a_2).any()
        ),
        "batch_todo_o_nada": bool((fired[:, 0, 1] == fired[:, 0, 2]).all()),
        "batch_emisores_independientes": bool((fired[:, 1, 2] != fired[:, 0, 2]).any()),
        "conserva_p_por_arista": bool(
            abs(sorteos_anidados[:, 1, 0].mean() - 0.3) < 0.02
            and abs(sorteos_anidados[:, 2, 0].mean() - 0.7) < 0.02
        ),
        "pesos_distintos_anidados": bool(
            (sorteos_anidados[:, 1, 0] <= sorteos_anidados[:, 2, 0]).all()
        ),
    }

    # --- courteous y ciclo: lo que demuestra el paper, calculado sin azar ---
    # No van en la tupla ALGORITMOS: solo aceptan valores 0 y 1, y ciclo pide al
    # menos 4 nodos, así que fallarían en los grafos de arriba.
    tabla_1 = {
        (1, 1, 1, 1, 0): [1, 4, 0, 6, 0, 0],
        (0, 1, 1, 1, 0): [1, 4, 5, 4, 1, 0],
        (0, 0, 1, 1, 0): [1, 3, 6, 3, 1, 0],
        (0, 1, 0, 1, 0): [1, 4, 6, 3, 1, 0],
        (0, 0, 0, 1, 0): [1, 2, 6, 4, 1, 0],
        (0, 0, 0, 0, 0): [0, 0, 0, 0, 0, 0],
    }
    resultado["protocolos/ciclo/tabla_1"] = all(
        _desacuerdos("ciclo", valores) == esperado for valores, esperado in tabla_1.items()
    )
    # Fórmula (6) del paper; se cumple para cualquier n.
    resultado["protocolos/courteous/formula_6"] = all(
        _desacuerdos("courteous", valores) == [
            math.comb(n - sum(valores), j // 2) * math.comb(sum(valores), j // 2)
            if j % 2 == 0 and 0 < sum(valores) < n else 0
            for j in range(n + 1)
        ]
        for n in range(3, 7)
        for valores in itertools.product((0, 1), repeat=n)
    )
    # Peor caso exacto: C(p) y, desde p = 2/3, A(p).
    for p in (0.3, 0.5, 0.688042944721047, 0.8, 0.95):
        q = 1 - p
        resultado[f"protocolos/courteous/peor_es_C/p{p}"] = bool(np.isclose(
            _peor_error("courteous", 5, p), q**5 + 6 * p**2 * q**3 + 3 * p**4 * q
        ))
    for p in (0.7, 0.688042944721047, 0.8, 0.95):
        q = 1 - p
        resultado[f"protocolos/ciclo/peor_es_A/p{p}"] = bool(np.isclose(
            _peor_error("ciclo", 5, p), q - 2 * p * q**4
        ))
    for n in (4, 5, 6):
        resultado[f"protocolos/batch_igual_a_ronda/n{n}"] = {
            algoritmo: _batch_igual_a_ronda(algoritmo, n) for algoritmo in ("courteous", "ciclo")
        }
    # El camino por el motor, con la peor entrada de cada uno.
    completas = [(i, j, 0.5) for i in range(5) for j in range(5) if i != j]
    for algoritmo, valores in (("courteous", [0, 0, 1, 1, 1]), ("ciclo", [0, 0, 0, 1, 0])):
        grafo = _grafo(True, valores, completas)
        np.random.seed(11)
        resultado[f"protocolos/run_round/{algoritmo}"] = _nativo(
            run_round(grafo, algoritmo, modo="por_emisor")
        )
        np.random.seed(11)
        resultado[f"protocolos/iter_sweep/{algoritmo}"] = _nativo(
            list(llamar_iter_sweep(grafo, algoritmo, 0.5, 5, 40, 2, 1, "por_emisor"))
        )

    casos_error = {
        "protocolos/ciclo_pocos_nodos": lambda: run_round(GRAFOS["k2"], "ciclo"),
        "protocolos/ciclo_batch_pocos_nodos": lambda: MOTOR["ciclo"]["batch"](
            np.zeros((2, 3)), np.zeros((2, 3, 3), dtype=bool), {}
        ),
        "protocolos/courteous_no_binario": lambda: MOTOR["courteous"]["ronda"](
            None, np.array([0.5, 0.0]), np.zeros((2, 2), dtype=int), {}
        ),
        "protocolos/courteous_batch_no_binario": lambda: MOTOR["courteous"]["batch"](
            np.array([[0.5, 0.0]]), np.zeros((1, 2, 2), dtype=bool), {}
        ),
        "modo_desconocido/who_gets_message": lambda: who_gets_message(
            get_adj(GRAFOS["k2"]), True, "xx"
        ),
        "modo_desconocido/batch": lambda: who_gets_message_batch(
            get_adj(GRAFOS["k2"]), np.ceil(get_adj(GRAFOS["k2"])), True, 2, "xx"
        ),
        "algoritmo_desconocido/run_round": lambda: llamar_run_round(
            GRAFOS["k2"], "xx", 0.5
        ),
        "algoritmo_desconocido/simulate_graph": lambda: llamar_simulate_graph(
            GRAFOS["k2"], "xx", 0.5
        ),
        "sweep_grafo_vacio": lambda: list(llamar_iter_sweep(vacio, "fv", 0.5, 5, 40, 2, 1)),
        "sweep_puntos_fuera_de_rango": lambda: list(
            llamar_iter_sweep(GRAFOS["k2"], "fv", 0.5, 1, 40, 2, 1)
        ),
        "sweep_sims_cero": lambda: list(
            llamar_iter_sweep(GRAFOS["k2"], "fv", 0.5, 5, 0, 2, 1)
        ),
        "sweep_sims_demasiadas": lambda: list(
            llamar_iter_sweep(GRAFOS["k2"], "fv", 0.5, 5, 50_001, 2, 1)
        ),
    }
    for nombre, accion in casos_error.items():
        try:
            accion()
            resultado[f"error/{nombre}"] = "SIN ERROR"
        except Exception as exc:  # noqa: BLE001 - capturar el contrato observable
            resultado[f"error/{nombre}"] = f"{type(exc).__name__}: {exc}"

    return resultado


# ==========================================================================
# COMPARACIÓN
# ==========================================================================

def comparar(base: dict[str, Any], actual: dict[str, Any]) -> list[str]:
    diferencias: list[str] = []

    faltantes = sorted(set(base) - set(actual))
    nuevas = sorted(set(actual) - set(base))
    for clave in faltantes:
        diferencias.append(f"FALTA en la ejecución actual: {clave}")
    for clave in nuevas:
        diferencias.append(f"NUEVA, no está en la línea base: {clave}")

    for clave in sorted(set(base) & set(actual)):
        if base[clave] != actual[clave]:
            esperado = json.dumps(base[clave], ensure_ascii=False)
            obtenido = json.dumps(actual[clave], ensure_ascii=False)
            if len(esperado) > 220:
                esperado = esperado[:220] + "…"
            if len(obtenido) > 220:
                obtenido = obtenido[:220] + "…"
            diferencias.append(
                f"DIFIERE {clave}\n    esperado: {esperado}\n    obtenido: {obtenido}"
            )

    return diferencias


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Verifica que el motor conserve su comportamiento observable."
    )
    parser.add_argument(
        "--grabar",
        action="store_true",
        help="Graba la línea base en vez de comparar contra ella.",
    )
    args = parser.parse_args()

    actual = capturar()

    if args.grabar:
        if BASELINE_PATH.exists():
            previa = json.loads(BASELINE_PATH.read_text())
            if previa != actual:
                print(
                    f"AVISO: {BASELINE_PATH.name} ya existe y difiere de la captura "
                    "actual. Se sobrescribe: asegúrate de que el cambio sea "
                    "intencional."
                )
        BASELINE_PATH.write_text(
            json.dumps(actual, indent=1, ensure_ascii=False, sort_keys=True)
        )
        print(f"Línea base grabada: {BASELINE_PATH.relative_to(PROJECT_ROOT)}")
        print(f"  {len(actual)} casos capturados.")
        return 0

    if not BASELINE_PATH.exists():
        print(
            f"No existe {BASELINE_PATH.relative_to(PROJECT_ROOT)}.\n"
            "Ejecuta primero: python3 scripts/equivalencia.py --grabar"
        )
        return 2

    base = json.loads(BASELINE_PATH.read_text())
    diferencias = comparar(base, actual)

    if not diferencias:
        print(f"OK — {len(actual)} casos idénticos a la línea base.")
        return 0

    print(f"FALLA — {len(diferencias)} diferencia(s) sobre {len(actual)} casos:\n")
    for diferencia in diferencias[:40]:
        print(f"  {diferencia}")
    if len(diferencias) > 40:
        print(f"  … y {len(diferencias) - 40} más.")
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
