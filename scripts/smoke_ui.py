#!/usr/bin/env python3
"""Smoke check de los controles de algoritmo, con un DOM simulado.

``app.py`` solo corre dentro de Pyodide porque importa ``js``. Aquí se sustituyen
``js`` y ``pyodide.ffi`` por dobles mínimos para poder ejercitar, fuera del
navegador, la parte de la interfaz que se genera desde el registro:

* el ``<select>`` se puebla con los algoritmos registrados,
* los campos de parámetros salen de la declaración de cada algoritmo,
* editar un campo actualiza el estado y llega a ``algorithm_params()``,
* cambiar de algoritmo conserva lo que se había escrito en el anterior,
* un algoritmo recién registrado aparece solo, sin tocar la interfaz,
* la ronda del editor va y vuelve por el worker sin dejar el botón trabado,
* la ranura de código propio compila, fuerza su clave y reporta sus errores.

No sustituye al recorrido visual en navegador: no valida estilos, ni eventos
reales, ni el arranque de Pyodide.

Uso::

    python3 scripts/smoke_ui.py
"""

from __future__ import annotations

import re
import sys
import types
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT / "python"))


# ==========================================================================
# Dobles del navegador
# ==========================================================================

class FakeClassList:
    def __init__(self) -> None:
        self.clases: set[str] = set()

    def add(self, nombre):
        self.clases.add(nombre)

    def remove(self, nombre):
        self.clases.discard(nombre)

    def toggle(self, nombre, activo=None):
        if activo:
            self.clases.add(nombre)
        else:
            self.clases.discard(nombre)


class FakeElement:
    def __init__(self, element_id: str) -> None:
        self.id = element_id
        self.innerHTML = ""
        self.textContent = ""
        self.value = ""
        self.hidden = False
        self.disabled = False
        self.checked = False
        self.classList = FakeClassList()
        self._atributos: dict[str, str] = {}

    def getAttribute(self, nombre):
        return self._atributos.get(nombre)

    def setAttribute(self, nombre, valor):
        self._atributos[nombre] = str(valor)

    def querySelector(self, _selector):
        return None

    def focus(self):
        pass

    def select(self):
        pass

    def getBoundingClientRect(self):
        # Medidas fijas: el canvas no existe, pero `render_canvas` mide para
        # encuadrar el minimapa y necesita numeros con los que dividir.
        return types.SimpleNamespace(
            left=0.0, top=0.0, right=800.0, bottom=600.0,
            width=800.0, height=600.0, x=0.0, y=0.0,
        )


class FakeDocument:
    def __init__(self) -> None:
        self.elementos: dict[str, FakeElement] = {}

    def getElementById(self, element_id):
        return self.elementos.setdefault(element_id, FakeElement(element_id))

    def createElement(self, _tag):
        return FakeElement("creado")

    @property
    def activeElement(self):
        return None


class FakeWorker:
    """Worker de mentira que corre el motor de verdad y contesta cuando se le pide.

    La ronda del editor viaja al worker, asi que ``simulate`` termina antes de
    que exista el resultado. Separar el envio de la respuesta es justo lo que
    permite comprobar el estado intermedio: boton bloqueado, ronda en vuelo.
    """

    ultimo: "FakeWorker | None" = None

    def __init__(self, _url, _options=None) -> None:
        self.oyentes: dict[str, list] = {}
        self.enviados: list[dict] = []
        FakeWorker.ultimo = self

    @classmethod
    def new(cls, url, options=None) -> "FakeWorker":
        return cls(url, options)

    def addEventListener(self, nombre, callback) -> None:
        self.oyentes.setdefault(nombre, []).append(callback)

    def postMessage(self, mensaje) -> None:
        self.enviados.append(mensaje)

    def terminate(self) -> None:
        pass

    def _entregar(self, data) -> None:
        for callback in self.oyentes.get("message", []):
            callback(types.SimpleNamespace(data=data))

    def anunciar_listo(self) -> None:
        """Lo que hace el worker real al terminar de cargar Pyodide y NumPy."""

        self._entregar({"type": "listo"})

    def responder(self) -> None:
        """Ejecuta el motor con el ultimo mensaje y devuelve el resultado."""

        import json as _json

        from simulation.ronda import run_round

        params = self.enviados.pop()["params"]
        resultado = run_round(
            params["graph"],
            algorithm=params.get("algorithm", "fv"),
            params=params.get("algorithmParams") or {},
            modo=params.get("modo", "por_arista"),
        )
        self._entregar({"type": "ronda", "resultado": _json.dumps(resultado)})

    def fallar(self, mensaje: str = "algo tronó") -> None:
        self.enviados.pop()
        self._entregar({"type": "error", "message": mensaje})


def instalar_dobles() -> FakeDocument:
    documento = FakeDocument()
    js = types.ModuleType("js")
    js.document = documento
    js.window = types.SimpleNamespace(
        alert=lambda *_a: None,
        confirm=lambda *_a: True,
        console=types.SimpleNamespace(error=lambda *_a: None),
        performance=types.SimpleNamespace(now=lambda: 0.0),
        setTimeout=lambda *_a: None,
        localStorage=types.SimpleNamespace(setItem=lambda *_a: None),
    )
    js.Blob = js.URL = types.SimpleNamespace()
    # `to_js` es la identidad aquí, pero app.py sí evalúa `Object.fromEntries`.
    js.Object = types.SimpleNamespace(fromEntries=lambda pares: dict(pares))
    js.Worker = FakeWorker
    ffi = types.ModuleType("pyodide.ffi")
    ffi.create_proxy = lambda fn: fn
    ffi.to_js = lambda obj, **_k: obj
    pyodide = types.ModuleType("pyodide")
    pyodide.ffi = ffi
    sys.modules.update({"js": js, "pyodide": pyodide, "pyodide.ffi": ffi})
    return documento


DOCUMENTO = instalar_dobles()

import app  # noqa: E402  (requiere los dobles ya instalados)
from models import Edge, Node  # noqa: E402
from simulation import usuario  # noqa: E402
from simulation.algoritmos import ALGORITMOS  # noqa: E402
from simulation.ronda import run_round  # noqa: E402


# ==========================================================================
# Comprobaciones
# ==========================================================================

FALLAS: list[str] = []


def comprobar(condicion: bool, mensaje: str) -> None:
    estado = "OK " if condicion else "FALLA"
    print(f"  {estado} {mensaje}")
    if not condicion:
        FALLAS.append(mensaje)


def opciones_del_selector() -> list[tuple[str, str]]:
    html = DOCUMENTO.getElementById("algorithm-select").innerHTML
    return re.findall(r'<option value="([^"]+)">([^<]*)</option>', html)


def campos_de_parametros() -> list[str]:
    html = DOCUMENTO.getElementById("algorithm-params").innerHTML
    return re.findall(r'data-param="([^"]+)"', html)


def cambiar_algoritmo(clave: str) -> None:
    app.STATE.algorithm = clave
    app.STATE.sweep_algorithms = [clave]
    app.render_algorithm_options()
    app.render_algorithm_params()


def editar_campo(nombre: str, valor: str) -> None:
    elemento = FakeElement(f"param-{nombre}")
    elemento.setAttribute("data-param", nombre)
    elemento.value = valor
    app.on_param_change(types.SimpleNamespace(target=elemento))


print("1. El selector se puebla desde ALGORITMOS")
cambiar_algoritmo("fv")
opciones = opciones_del_selector()
comprobar(
    [clave for clave, _ in opciones] == [*ALGORITMOS, usuario.CLAVE],
    f"opciones == ALGORITMOS + la ranura propia: {[c for c, _ in opciones]}",
)
comprobar(
    dict(opciones).get("amp") == ALGORITMOS["amp"]["nombre"],
    "la etiqueta visible sale de ALGORITMOS, no de index.html",
)
comprobar(
    DOCUMENTO.getElementById("algorithm-select").value == "fv",
    "el selector queda en el algoritmo activo",
)

print("\n2. Los campos salen de la declaración de parámetros")
comprobar(campos_de_parametros() == [], "FV no declara parámetros: sin campos")
comprobar(
    DOCUMENTO.getElementById("algorithm-params").hidden is True,
    "el contenedor se oculta cuando no hay parámetros",
)
comprobar(app.algorithm_params() == {}, "algorithm_params() vacío para FV")

cambiar_algoritmo("amp")
comprobar(campos_de_parametros() == ["agreed"], "AMP declara `agreed`: aparece su campo")
comprobar(
    DOCUMENTO.getElementById("algorithm-params").hidden is False,
    "el contenedor se muestra cuando sí hay parámetros",
)
comprobar(app.algorithm_params() == {"agreed": 0.5}, "toma el default del registro")

print("\n3. Editar un campo llega al motor")
editar_campo("agreed", "0.9")
comprobar(app.algorithm_params() == {"agreed": 0.9}, "algorithm_params() refleja la edición")
editar_campo("agreed", "no-es-un-numero")
comprobar(
    app.algorithm_params() == {"agreed": 0.9},
    "un valor inválido no pisa el anterior",
)

print("\n4. Cambiar de algoritmo conserva lo escrito")
cambiar_algoritmo("fv")
cambiar_algoritmo("amp")
comprobar(
    app.algorithm_params() == {"agreed": 0.9},
    "al volver a AMP sigue el 0.9 que se había escrito",
)
comprobar(
    campos_de_parametros() == ["agreed"],
    "los campos se regeneran al volver",
)

print("\n5. Un algoritmo nuevo aparece solo")
ALGORITMOS["zz"] = {
    "nombre": "Algoritmo de prueba",
    "ronda": lambda *a: [],
    "parametros": {"alfa": ("Alfa", 2.5), "beta": ("Beta", 7.0)},
}
cambiar_algoritmo("zz")
comprobar("zz" in [clave for clave, _ in opciones_del_selector()], "aparece en el selector")
comprobar(campos_de_parametros() == ["alfa", "beta"], "aparecen sus dos campos")
comprobar(
    app.algorithm_params() == {"alfa": 2.5, "beta": 7.0},
    "con sus defaults propios, sin tocar app.py ni index.html",
)

print("\n6. El eje Y de la gráfica usa números redondos")


def etiquetas_y() -> list[str]:
    html = DOCUMENTO.getElementById("chart-axis-labels").innerHTML
    return re.findall(r'text-anchor="end">([^<]*)<', html)


def lineas_rejilla() -> int:
    return len(re.findall(r"<line", DOCUMENTO.getElementById("chart-grid").innerHTML))


def curva(maximo: float) -> list[dict]:
    return [
        {"p": 0.0, "mean": 0.0, "ciLow": 0.0, "ciHigh": 0.0},
        {"p": 1.0, "mean": maximo, "ciLow": maximo, "ciHigh": maximo},
    ]


def graficar(puntos: list[dict]) -> None:
    """Un sweep de un solo algoritmo con estos puntos, y a dibujar."""
    app.STATE.experiments = {"fv": {"puntos": puntos, "nombre": "FV", "teorica": None}} if puntos else {}
    app.render_chart()


graficar(curva(1.0))
comprobar(
    etiquetas_y() == ["0", "0.25", "0.5", "0.75", "1"],
    f"máximo 1.0 → {etiquetas_y()} (antes terminaba en 1.08)",
)
graficar(curva(0.87))
comprobar(etiquetas_y()[-1] == "1", "máximo 0.87 → el eje llega a 1, no a 0.94")
graficar(curva(2.5))
comprobar(
    etiquetas_y() == ["0", "0.5", "1", "1.5", "2", "2.5"],
    f"máximo 2.5 → {etiquetas_y()} (divisiones variables)",
)
comprobar(
    lineas_rejilla() == len(etiquetas_y()),
    "la rejilla tiene tantas líneas como marcas el eje",
)
graficar([])
comprobar(
    etiquetas_y() == ["0", "0.25", "0.5", "0.75", "1"],
    "sin datos vuelve a la escala 0–1",
)

peores = []
for entero in range(1, 4001):
    dato = entero / 800
    tope, _div = app.nice_axis(dato)
    if tope < dato:
        peores.append(dato)
comprobar(not peores, "el tope siempre cubre el dato: la curva nunca se sale")

aire = sorted(app.nice_axis(e / 800)[0] / (e / 800) - 1 for e in range(1, 4001))
mediana = aire[len(aire) // 2]
comprobar(
    mediana < 0.20,
    f"aire desperdiciado razonable: mediana {mediana*100:.0f}% (con 4 divisiones fijas era 42%)",
)

print("\n7. La ronda va y viene por el worker")
app.start_engine_worker()
worker = FakeWorker.ultimo
app.STATE.tab.nodes = [Node("node_0", 0, 0.0, 0.0), Node("node_1", 1, 100.0, 0.0)]
app.STATE.tab.edges = [Edge("edge_0", "node_1", "node_0", "1")]
cambiar_algoritmo("fv")

app.simulate()
boton = DOCUMENTO.getElementById("simulate-btn")
comprobar(app.STATE.round_running, "al mandar, la ronda queda en vuelo")
comprobar(
    boton.disabled and boton.textContent == "Cargando motor…",
    "antes del aviso `listo`, el botón dice que está cargando, no simulando",
)
worker.anunciar_listo()
comprobar(boton.textContent == "Simulando…", "tras el aviso, el botón se bloquea simulando")
comprobar(len(worker.enviados) == 1, "salió exactamente un mensaje al worker")
comprobar(
    worker.enviados[0]["params"]["graph"]["nodes"][1]["value"] == 1,
    "el mensaje lleva el grafo, no una referencia al estado",
)

app.simulate()
comprobar(len(worker.enviados) == 1, "un segundo clic en vuelo no manda otro mensaje")

worker.responder()
valores = [nodo.value for nodo in app.STATE.tab.nodes]
comprobar(valores == [1, 1], f"al contestar, node_0 copia el 1 del vecino: {valores}")
comprobar(not app.STATE.round_running, "la ronda deja de estar en vuelo")
comprobar(not boton.disabled and boton.textContent == "Simular", "el botón se desbloquea")
comprobar(app.STATE.simulation is not None, "queda el resultado para «Ver Antes»")

# El embudo va atado a «la ronda terminó», no a «se repintó el canvas»: un error
# del worker no dibuja nada y aun así tiene que devolver el control.
app.reset_simulation()
app.simulate()
comprobar(boton.disabled, "vuelve a bloquearse en la siguiente ronda")
worker.fallar("el algoritmo tronó")
comprobar(
    not app.STATE.round_running and not boton.disabled,
    "un error del worker también desbloquea el botón",
)
comprobar(app.STATE.round_before is None, "el embudo suelta el snapshot que esperaba")

print("\n8. La ranura de código propio")
# En producción hay DOS copias de `ALGORITMOS`: la del hilo principal, catálogo del
# que la interfaz saca sus controles, y la del Worker, donde `usuario.instalar`
# compila de verdad. Aquí comparten proceso y el mismo `ALGORITMOS`, así que el
# bloque del instalador trabaja sobre una copia y se restaura: si no, la ranura
# instalada aparecería dos veces en el selector, algo que la app real no puede tener.
CATALOGO_DE_LA_INTERFAZ = dict(ALGORITMOS)

CODIGO = """
def regla(matrix, values, messages, params):
    return [(v, None) for v in values]

ALGORITMO = {"nombre": "Intento de pirata", "ronda": regla}
"""
propio = usuario.instalar(CODIGO)
comprobar(ALGORITMOS.get(usuario.CLAVE) is propio, "queda en la ranura `user_alg`, no en otra clave")
comprobar(ALGORITMOS["fv"]["nombre"] == "Flip Value (FV)", "`fv` sigue siendo el de casa")
comprobar(propio["nombre"] == "Intento de pirata", "el nombre sí es el suyo: sale en los mensajes de error")
comprobar("batch" not in propio, "sin `batch`: el sweep lo correrá con el bucle ingenuo")
ronda = run_round(
    {"directed": True, "nodes": [{"id": "a", "value": 1}, {"id": "b", "value": 0}],
     "edges": [{"id": "e", "source": "a", "target": "b", "weight": "1"}]},
    algorithm=usuario.CLAVE,
)
comprobar(
    ronda["values"] == [1.0, 0.0] and ronda["propagation"][0]["updated_value"] is False,
    "corre en una ronda real; sin `actualizo` usa «el valor del target cambió»",
)

for descripcion, fuente, esperado in [
    ("no compila", "def roto(:", SyntaxError),
    ("no define ALGORITMO", "x = 1", ValueError),
    ("revienta al ejecutarse", "1 / 0", ZeroDivisionError),
    ("declara parámetros", 'ALGORITMO = {"ronda": print, "parametros": {"p": ("P", 1)}}', ValueError),
]:
    try:
        usuario.instalar(fuente)
        comprobar(False, f"{descripcion}: debía fallar y no falló")
    except esperado:
        comprobar(True, f"{descripcion}: eleva {esperado.__name__}")
    except Exception as error:  # noqa: BLE001 - el tipo es parte de lo que se comprueba
        comprobar(False, f"{descripcion}: esperaba {esperado.__name__}, llegó {type(error).__name__}")

comprobar(
    usuario.CLAVE not in ALGORITMOS,
    "tras una instalación fallida la ranura queda VACÍA, no con el código anterior",
)

ALGORITMOS.clear()
ALGORITMOS.update(CATALOGO_DE_LA_INTERFAZ)

# --- la mitad de interfaz ---
app.STATE.algorithm = usuario.CLAVE
app.STATE.sweep_algorithms = [usuario.CLAVE]
app.STATE.user_source = ""
DOCUMENTO.getElementById("user-code").value = "# plantilla de index.html"
app.render_user_code()
comprobar(not DOCUMENTO.getElementById("user-algorithm").hidden, "al elegir la ranura aparece la caja")
comprobar(
    app.STATE.user_source == "# plantilla de index.html",
    "la plantilla inicial se toma del textarea, no se codifica en app.py",
)

app.on_user_code_change(types.SimpleNamespace(target=types.SimpleNamespace(value="# lo que escribí")))
comprobar(app.user_source() == "# lo que escribí", "user_source() devuelve lo tecleado")
app.STATE.algorithm = "fv"
app.STATE.sweep_algorithms = ["fv"]
comprobar(app.user_source() is None, "con un algoritmo de casa no viaja ningún fuente")
app.render_user_code()
comprobar(DOCUMENTO.getElementById("user-algorithm").hidden, "y la caja se esconde")

app.STATE.algorithm = usuario.CLAVE

app.STATE.sweep_algorithms = [usuario.CLAVE]
app.simulate()
comprobar(
    worker.enviados[-1]["params"].get("fuente") == "# lo que escribí",
    "el mensaje lleva el fuente cuando la ranura está activa",
)

TRAZA = (
    'Traceback (most recent call last):\n'
    '  File "/lib/python314.zip/_pyodide/_base.py", line 597, in eval_code_async\n'
    '  File "/python/simulation/usuario.py", line 146, in instalar\n'
    '    codigo = compile(fuente, "<algoritmo>", "exec")\n'
    '  File "<algoritmo>", line 2, in regla\n'
    'IndexError: fuera de rango'
)
recortada = app.trim_traceback(TRAZA, "fuera de rango")
comprobar(
    "_pyodide" not in recortada and "usuario.py" not in recortada,
    "el traceback se recorta: fuera los marcos de Pyodide y del instalador",
)
comprobar(
    '<algoritmo>' in recortada and "IndexError" in recortada,
    "y se queda lo que sirve: la línea del código pegado y el error",
)
comprobar(
    app.trim_traceback("Traceback:\n  File \"/python/app.py\", line 1\nValueError: x", "resumen") == "resumen",
    "sin marcos propios cae al mensaje corto: el traceback sería de tripas ajenas",
)

caja = DOCUMENTO.getElementById("user-code-error")
caja.hidden = True
app.report_engine_error({"type": "error", "etapa": "compilar", "message": "expected ':'", "traza": TRAZA})
comprobar(not caja.hidden, "un error con traza se muestra en la caja de código")
comprobar("no compila" in caja.textContent, "y dice en qué etapa falló")
caja.hidden = True
app.report_engine_error({"type": "error", "message": "algo del motor", "traza": None})
comprobar(caja.hidden, "un error sin traza no ensucia la caja: ese va por notify")

print("\nEl modo de paso de mensajes llega al Worker")
indice_html = (PROJECT_ROOT / "index.html").read_text()
comprobar(
    re.findall(r'<option value="(por_[a-z]+)"', indice_html) == ["por_arista", "por_emisor"],
    "el selector de index.html ofrece los dos modos del motor",
)
app.STATE.algorithm = "fv"
app.STATE.sweep_algorithms = ["fv"]
app.STATE.round_running = False
comprobar(app.STATE.message_mode == "por_arista", "por omisión, el modo es por arista")
app.on_message_mode_change(types.SimpleNamespace(target=types.SimpleNamespace(value="por_emisor")))
app.simulate()
comprobar(worker.enviados[-1]["params"].get("modo") == "por_emisor", "la ronda manda el modo elegido")
app.STATE.round_running = False
for campo, valor in {
    "p-points-input": "5", "simulations-input": "40", "iterations-input": "2", "rounds-input": "1",
}.items():
    DOCUMENTO.getElementById(campo).value = valor
app.start_sweep()
comprobar(
    FakeWorker.ultimo.enviados[-1]["params"].get("modo") == "por_emisor",
    "el sweep manda el modo elegido",
)
app.finish_sweep()


print("\nLas dos pantallas: análisis al frente, la red detrás de un botón")
clases = DOCUMENTO.getElementById("app").classList.clases
app.STATE.active_view = "analysis"
app.render_controls()
comprobar("view-analysis" in clases and "view-editor" not in clases, "arranca en el análisis")
app.change_view("editor")
comprobar("view-editor" in clases and "view-analysis" not in clases, "«Editar red» muestra el editor")
app.change_view("analysis")
comprobar("view-analysis" in clases, "«Volver al análisis» regresa")
app.STATE.tab.nodes = [Node("n1", 1.0)]
app.STATE.selected_node_id = "n1"
app.on_key_down(types.SimpleNamespace(key="Delete", code="Delete", preventDefault=lambda: None))
comprobar(len(app.STATE.tab.nodes) == 1, "en el análisis, Supr no borra el nodo seleccionado (no se ve)")
app.render_controls()
comprobar(
    DOCUMENTO.getElementById("network-summary").textContent.startswith(f"{app.STATE.tab.name}: 1 nodo ·"),
    "el resumen de la red cuenta los nodos de la pestaña activa",
)

print("\nLos parámetros del sweep se explican solos")
from simulation.barrido import iter_sweep  # noqa: E402

GRAFO_MINIMO = {"directed": True, "nodes": [{"id": "a", "value": 0}, {"id": "b", "value": 1}],
                "edges": [{"id": "e", "source": "a", "target": "b", "weight": "1"}]}
for pedidas, iteraciones in [(5000, 50), (1000, 3), (10, 50), (7, 7), (1, 1000), (50000, 1)]:
    DOCUMENTO.getElementById("simulations-input").value = str(pedidas)
    DOCUMENTO.getElementById("iterations-input").value = str(iteraciones)
    plan = app.sweep_plan()
    inicio = next(iter_sweep(GRAFO_MINIMO, total_simulations_per_p=pedidas, iterations=iteraciones))
    comprobar(
        (plan["por_pasada"], plan["pasadas"]) == (inicio["simsPerIteration"], inicio["iterations"]),
        f"{pedidas} sims en {iteraciones} pasadas: la interfaz anuncia lo mismo que corre iter_sweep",
    )
DOCUMENTO.getElementById("simulations-input").value = "1000"
DOCUMENTO.getElementById("iterations-input").value = "3"
app.render_sweep_plan()
comprobar("Pediste 1 000: en pasadas iguales caben 999" in DOCUMENTO.getElementById("sweep-plan").textContent,
          "si el reparto recorta simulaciones, el plan lo dice")
for valor, esperado in [("5000000", 50_000), ("", 1), ("abc", 1), ("-3", 1), ("inf", 1), ("12.7", 12)]:
    DOCUMENTO.getElementById("simulations-input").value = valor
    comprobar(app.sweep_field("simulations-input") == esperado, f"simulaciones = {valor!r} → {esperado}")
app.on_message_mode_change(types.SimpleNamespace(target=types.SimpleNamespace(value="por_arista")))
ayuda_arista = DOCUMENTO.getElementById("message-mode-help").textContent
app.on_message_mode_change(types.SimpleNamespace(target=types.SimpleNamespace(value="por_emisor")))
ayuda_emisor = DOCUMENTO.getElementById("message-mode-help").textContent
comprobar(ayuda_arista and ayuda_emisor and ayuda_arista != ayuda_emisor, "cada modo de mensajes tiene su explicación")
comprobar(set(app.MODOS_AYUDA) == set(re.findall(r'<option value="(por_[a-z]+)"', indice_html)),
          "hay explicación para cada modo del selector, y para nada más")
app.STATE.tab.nodes = [Node("a", 0), Node("b", 2.5)]
app.render_controls()
comprobar(DOCUMENTO.getElementById("network-values").textContent == "Valores: 0, 2.5. Discrepancia inicial: 2.5.",
          "el resumen de la red muestra los valores y la discrepancia inicial")

print("\nLa semilla hace repetible el sweep")
import numpy as np  # noqa: E402

campo_semilla = DOCUMENTO.getElementById("seed-input")
for valor, esperado in [("", None), ("  42 ", 42), ("0", 0), (str(2**32 - 1), 2**32 - 1)]:
    campo_semilla.value = valor
    comprobar(app.seed_field() == esperado, f"semilla {valor!r} → {esperado}")
for valor in ["-1", "4.5", "abc", str(2**32), "²"]:
    campo_semilla.value = valor
    try:
        app.seed_field()
        comprobar(False, f"semilla {valor!r} debía rechazarse")
    except ValueError:
        comprobar(True, f"semilla {valor!r} se rechaza")


def mandar_sweep() -> dict | None:
    app.STATE.sweep_running = False
    FakeWorker.ultimo = None
    app.start_sweep()
    enviado = FakeWorker.ultimo.enviados[-1]["params"] if FakeWorker.ultimo else None
    app.finish_sweep()
    return enviado


campo_semilla.value = "123"
comprobar(mandar_sweep()["semilla"] == 123, "la semilla escrita viaja al Worker")
campo_semilla.value = ""
sorteada = mandar_sweep()["semilla"]
comprobar(isinstance(sorteada, int) and 0 <= sorteada <= 2**32 - 1 and app.STATE.sweep_seed == sorteada,
          "sin semilla se sortea una, viaja y queda en el estado para mostrarla")
campo_semilla.value = "abc"
comprobar(mandar_sweep() is None, "con una semilla inválida no sale ningún sweep")
comprobar("semilla debe ser" in DOCUMENTO.getElementById("sweep-status").innerHTML, "y el estado dice por qué")
campo_semilla.value = ""


def curva_con(semilla: int, algoritmo: str) -> list:
    # Lo mismo que hace el Worker: pasarle la semilla a iter_sweep.
    eventos = list(iter_sweep(GRAFO_MINIMO, algorithm=algoritmo, num_p_points=5,
                              total_simulations_per_p=40, iterations=2, semilla=semilla))
    return eventos[-1]["points"]


for algoritmo in ("fv", "amp"):
    comprobar(curva_con(7, algoritmo) == curva_con(7, algoritmo), f"{algoritmo}: misma semilla, misma curva")
    comprobar(curva_con(7, algoritmo) != curva_con(8, algoritmo), f"{algoritmo}: otra semilla, otra curva")

print("\nLas descargas del experimento")
import csv  # noqa: E402
import io  # noqa: E402
import json as json_  # noqa: E402
import xml.etree.ElementTree as ET  # noqa: E402

app.STATE.experiments = {}
app.render_downloads()
comprobar(DOCUMENTO.getElementById("download-csv-btn").disabled, "sin experimento, los botones están apagados")

app.STATE.tab.name = "Red / rara"
app.STATE.tab.nodes = [Node("a", 0), Node("b", 1)]
app.STATE.tab.edges = [Edge("e", "a", "b", "0.5")]
campo_semilla.value = "99"
app.STATE.algorithm = "amp"
app.STATE.sweep_algorithms = ["amp"]
for campo, valor in {"p-points-input": "5", "simulations-input": "40", "iterations-input": "2", "rounds-input": "1"}.items():
    DOCUMENTO.getElementById(campo).value = valor
app.STATE.sweep_running = False
app.start_sweep()
enviado = FakeWorker.ultimo.enviados[-1]["params"]
experimento = app.STATE.experiment
comprobar(experimento is not None and experimento["params"] is enviado, "el experimento guarda lo mismo que se mandó al Worker")
comprobar(DOCUMENTO.getElementById("download-json-btn").disabled, "sin curva todavía, siguen apagados")
# Lo que haría el Worker: recorrer iter_sweep con la semilla y publicar cada evento.
eventos = list(iter_sweep(enviado["graph"], algorithm="amp", params=enviado["algorithmParams"],
                          num_p_points=5, total_simulations_per_p=40, iterations=2, modo=enviado["modo"],
                          semilla=enviado["semilla"]))
from simulation.teoria import curva_exacta  # noqa: E402

valores_teoricos, _ = curva_exacta(enviado["graph"], "amp", enviado["algorithmParams"], enviado["modo"], 1,
                                   np.linspace(0, 1, 5))
FakeWorker.ultimo._entregar({"type": "teorica", "valores": valores_teoricos, "motivo": ""})
FakeWorker.ultimo._entregar(eventos[1])
comprobar(not experimento["completo"] and experimento["simulaciones_por_p"] == 20,
          "a media corrida el experimento queda marcado como incompleto")
FakeWorker.ultimo._entregar(eventos[2])
app.finish_sweep()
comprobar(experimento["completo"] and experimento["puntos"] == eventos[-1]["points"],
          "al terminar guarda la última curva y la marca completa")
comprobar(not DOCUMENTO.getElementById("download-svg-btn").disabled, "con curva, se encienden")

nombre = app.experiment_basename(experimento)
comprobar(nombre == "sweep_Red-rara_amp_semilla99", f"nombre de archivo sin caracteres raros: {nombre}")

filas = list(csv.DictReader(io.StringIO(app.experiment_csv(experimento))))
comprobar(len(filas) == 5 and list(filas[0]) == ["p", "media", "ic_bajo", "ic_alto", "simulaciones", "teorica"],
          "CSV: una fila por p, con encabezado")
comprobar(all(float(f["media"]) == pt["mean"] for f, pt in zip(filas, eventos[-1]["points"])),
          "CSV: las medias salen sin perder dígitos")
comprobar([float(f["teorica"]) for f in filas] == valores_teoricos, "CSV: trae la curva teórica")
comprobar(DOCUMENTO.getElementById("results-table-body").innerHTML.count("<tr>") == 5
          and "sin-dato" not in DOCUMENTO.getElementById("results-table-body").innerHTML,
          "la tabla tiene una fila por p, con su valor teórico")
comprobar('class="chart-theory-line"' in DOCUMENTO.getElementById("chart-curves").innerHTML,
          "la curva teórica se dibuja en la gráfica")

doc = json_.loads(app.experiment_json(experimento))
comprobar(doc["experimento"]["semilla"] == 99 and doc["algoritmo"]["clave"] == "amp" and doc["modo"] == enviado["modo"],
          "JSON: lleva semilla, algoritmo y modo")
comprobar(doc["red"] == enviado["graph"] and set(doc["red"]) == {"directed", "nodes", "edges"},
          "JSON: la red va en el formato de importación")
repetido = list(iter_sweep(doc["red"], algorithm=doc["algoritmo"]["clave"], params=doc["algoritmo"]["parametros"],
                           num_p_points=doc["experimento"]["puntos_p"],
                           total_simulations_per_p=doc["experimento"]["simulaciones_por_p"],
                           iterations=doc["experimento"]["iteraciones"], rounds=doc["experimento"]["rondas"],
                           modo=doc["modo"], semilla=doc["experimento"]["semilla"]))
comprobar([pt["media"] for pt in doc["resultado"]["puntos"]] == [pt["mean"] for pt in repetido[-1]["points"]],
          "JSON: con solo lo que trae el archivo se repite la misma curva")

markup = '<svg id="sweep-chart-svg" viewBox="0 0 800 440"><!-- c --><path class="chart-mean-line" d="M 1,2"></path></svg>'
arbol = ET.fromstring(app.chart_svg(markup).split("\n", 1)[1])
comprobar(arbol.tag == "{http://www.w3.org/2000/svg}svg", "SVG: XML válido y con su namespace")
comprobar(arbol[0].tag.endswith("style") and arbol[1].tag.endswith("rect"), "SVG: trae su estilo y su fondo")
comprobar((arbol.get("width"), arbol.get("height")) == ("800", "440"), "SVG: declara su tamaño")
grafica = ('<svg id="sweep-chart-svg" viewBox="0 0 800 440"><g class="chart-grid"><line x1="56" y1="24" x2="776" y2="24"></line></g>'
           '<path class="chart-confidence-band" style="fill:#4e79a7" d="M 56,300 L 776,100 L 776,200 L 56,392 Z"></path>'
           '<path class="chart-theory-line" style="stroke:#ffffff" d="M 56,350 L 776,150"></path>'
           '<g class="chart-axis-labels"><text class="chart-axis-title" x="14" y="208" text-anchor="middle" '
           'transform="rotate(-90 14 208)">discrepancia (máx − mín)</text></g></svg>')
pdf = app.svg_a_pdf(app.chart_svg(grafica))
inicio = int(pdf.rsplit("startxref\n", 1)[1].split()[0])
offsets = [int(linea[:10]) for linea in pdf[inicio:].split("\n")[3:8]]
comprobar(pdf.startswith("%PDF-") and pdf.isascii() and pdf[inicio:].startswith("xref")
          and all(pdf[o:].startswith(f"{i} 0 obj") for i, o in enumerate(offsets, 1)),
          "PDF: ASCII, con su tabla xref apuntando a cada objeto")
comprobar("/GS0 gs" in pdf and "[6 5] 0 d" in pdf and "0.0000 -1.0000 -1.0000 0.0000" in pdf and "\\341" in pdf,
          "PDF: banda translúcida, teórica punteada, título girado y acentos en WinAnsi")
app.STATE.algorithm = "fv"
app.STATE.sweep_algorithms = ["fv"]
campo_semilla.value = ""

print("\nEl código de los algoritmos de casa se puede copiar y corre igual")
COMPLETO_5 = {
    "directed": True,
    "nodes": [{"id": f"n{i}", "value": v} for i, v in enumerate([0, 0, 1, 1, 1])],
    "edges": [{"id": f"e{i}{j}", "source": f"n{i}", "target": f"n{j}", "weight": "0.6"}
              for i in range(5) for j in range(5) if i != j],
}
try:
    app.codigo_de("zz")
    comprobar(False, "un algoritmo hecho con lambdas debía rechazarse al copiar")
except ValueError:
    comprobar(True, "un algoritmo hecho con lambdas se rechaza al copiar, en vez de dar código roto")
for clave in ("fv", "amp", "courteous", "ciclo"):
    fuente = app.codigo_de(clave)
    copia = usuario.instalar(fuente)
    comprobar("parametros" not in copia and copia.get("lider") == ALGORITMOS[clave].get("lider"),
              f"{clave}: la copia instala, sin parámetros y con el mismo líder")
    for modo in ("por_arista", "por_emisor"):
        np.random.seed(5)
        original = run_round(COMPLETO_5, algorithm=clave, modo=modo)
        np.random.seed(5)
        copiado = run_round(COMPLETO_5, algorithm=usuario.CLAVE, modo=modo)
        comprobar((original["values"], original["propagation"]) == (copiado["values"], copiado["propagation"]),
                  f"{clave} ({modo}): una ronda de la copia da lo mismo que el original")
    np.random.seed(5)
    curva_original = list(iter_sweep(COMPLETO_5, algorithm=clave, num_p_points=5,
                                     total_simulations_per_p=30, iterations=1))[-1]["points"]
    np.random.seed(5)
    curva_copia = list(iter_sweep(COMPLETO_5, algorithm=usuario.CLAVE, num_p_points=5,
                                  total_simulations_per_p=30, iterations=1))[-1]["points"]
    comprobar(curva_original == curva_copia, f"{clave}: el sweep de la copia (con su batch) da la misma curva")
ALGORITMOS.pop(usuario.CLAVE, None)

print("\nLa pestaña «Código»")
app.STATE.algorithm = "courteous"
app.STATE.sweep_algorithms = ["courteous"]
app.change_view("code")
comprobar("view-code" in clases and "view-analysis" not in clases, "«Código» es una pestaña propia")
comprobar(not DOCUMENTO.getElementById("builtin-code").hidden and DOCUMENTO.getElementById("user-code-panel").hidden,
          "con un algoritmo de casa se ve su código, no el editor")
comprobar(DOCUMENTO.getElementById("builtin-code-text").textContent == app.codigo_de("courteous"),
          "el código que se ve es el mismo que se copia")

editor = DOCUMENTO.getElementById("user-code")
app.PLANTILLA_USUARIO = "# ejemplo"
editor.value = "# ejemplo"
app.edit_copy()
comprobar(app.STATE.algorithm == usuario.CLAVE and editor.value == app.codigo_de("courteous")
          and app.STATE.user_source == editor.value,
          "con la plantilla intacta, «Editar una copia» la reemplaza y elige el algoritmo propio")
comprobar(DOCUMENTO.getElementById("builtin-code").hidden and not DOCUMENTO.getElementById("user-code-panel").hidden,
          "y la pestaña pasa al editor")

app.STATE.algorithm = "fv"

app.STATE.sweep_algorithms = ["fv"]
app.render_controls()
editor.value = "# mi trabajo"
app.STATE.user_source = "# mi trabajo"
DOCUMENTO.getElementById("app-dialog").hidden = True
app.edit_copy()
comprobar(editor.value == "# mi trabajo" and app.STATE.algorithm == "fv" and not DOCUMENTO.getElementById("app-dialog").hidden,
          "si el editor tiene cambios, pregunta antes de reemplazar y no toca nada")
app.close_dialog()
comprobar(editor.value == "# mi trabajo", "cancelar conserva lo escrito")
app.edit_copy()
app.submit_dialog()
comprobar(editor.value == app.codigo_de("fv") and app.STATE.algorithm == usuario.CLAVE, "aceptar lo reemplaza")
app.STATE.algorithm = "fv"
app.STATE.sweep_algorithms = ["fv"]
app.change_view("analysis")

print("\nLa discrepancia teórica: exacta para una ronda")


def red(valores, aristas, dirigida=True):
    return {"directed": dirigida, "nodes": [{"id": f"n{i}", "value": v} for i, v in enumerate(valores)],
            "edges": [{"id": f"e{k}", "source": f"n{a}", "target": f"n{b}", "weight": "1"}
                      for k, (a, b) in enumerate(aristas)]}


def completa(n):
    return [(i, j) for i in range(n) for j in range(n) if i != j]


P = np.linspace(0, 1, 11)
Q = 1 - P
# Las fórmulas de los papers, en los casos donde están demostradas.
for nombre, grafo_, clave, modo, formula in [
    ("FV, 2 nodos con dos arcos: p² + q²", red([0, 1], completa(2)), "fv", "por_arista", P**2 + Q**2),
    ("FV, 2 nodos en broadcast: p² + q²", red([0, 1], completa(2)), "fv", "por_emisor", P**2 + Q**2),
    ("AMP, 2 nodos con dos arcos: q", red([0, 1], completa(2)), "amp", "por_arista", Q),
    ("FV, 2 nodos no dirigido: el sorteo compartido da 1", red([0, 1], [(0, 1)], False), "fv", "por_arista", P * 0 + 1),
    ("Courteous, (0,0,1,1,1) en broadcast: C(p)", red([0, 0, 1, 1, 1], completa(5)), "courteous", "por_emisor",
     Q**5 + 6 * P**2 * Q**3 + 3 * P**4 * Q),
    ("Ciclo, (0,0,0,1,0) en broadcast: A(p)", red([0, 0, 0, 1, 0], completa(5)), "ciclo", "por_emisor",
     Q - 2 * P * Q**4),
]:
    valores, _ = curva_exacta(grafo_, clave, {}, modo, 1, P)
    comprobar(np.allclose(valores, formula, atol=1e-12), nombre)

# Fuera de esos casos, contra la simulación (semilla fija: no es azaroso).
for nombre, grafo_, clave, modo in [
    ("FV, valores 0 / 2.5 / 7, no dirigido", red([0, 2.5, 7], [(0, 1), (1, 2), (0, 2)], False), "fv", "por_arista"),
    ("FV, dirigida irregular, broadcast", red([3, 0, 1, 5], [(0, 1), (0, 2), (0, 3), (1, 0), (2, 3)]), "fv", "por_emisor"),
    ("AMP, ciclo de 4", red([0, 1, 0.2, 0.9], [(0, 1), (1, 2), (2, 3), (3, 0)]), "amp", "por_arista"),
    ("Courteous, completa no dirigida", red([0, 1, 1, 0, 1], [(i, j) for i in range(5) for j in range(i + 1, 5)], False),
     "courteous", "por_arista"),
    ("Ciclo, otra entrada en broadcast", red([1, 0, 1, 1, 0], completa(5)), "ciclo", "por_emisor"),
]:
    np.random.seed(1)
    puntos = list(iter_sweep(grafo_, algorithm=clave, num_p_points=11, total_simulations_per_p=20000,
                             iterations=1, modo=modo))[-1]["points"]
    valores, motivo = curva_exacta(grafo_, clave, {}, modo, 1, [pt["p"] for pt in puntos])
    if valores is None:
        comprobar(False, f"{nombre}: no hubo curva exacta ({motivo})")
        continue
    peor = max(abs(pt["mean"] - v) / max((pt["ciHigh"] - pt["ciLow"]) / 3.92, 1e-12) for pt, v in zip(puntos, valores))
    comprobar(peor < 4, f"{nombre}: la simulación cae a {peor:.1f} desviaciones de la exacta")

# Cuándo no hay curva, y que lo diga.
for nombre, argumentos, pista in [
    ("con más de una ronda", (red([0, 1], completa(2)), "fv", {}, "por_arista", 2), "1 ronda"),
    ("con demasiados patrones", (red([0, 1, 0, 1, 0], completa(5)), "fv", {}, "por_arista", 1), "2^20"),
    ("con lazos", (red([0, 1], [(0, 0), (0, 1)]), "fv", {}, "por_arista", 1), "lazos"),
]:
    valores, motivo = curva_exacta(*argumentos, P)
    comprobar(valores is None and pista in motivo, f"sin curva {nombre}, y lo dice: {motivo!r}")
usuario.instalar("def r(m, v, msg, p):\n    return [(x, None) for x in v]\nALGORITMO = {'nombre': 'x', 'ronda': r}")
valores, motivo = curva_exacta(red([0, 1], completa(2)), usuario.CLAVE, {}, "por_arista", 1, P)
comprobar(valores is None and "no declara" in motivo, "un algoritmo que no declara cómo calcularla no inventa curva")
usuario.instalar(app.codigo_de("fv"))
copia, _ = curva_exacta(red([0, 2.5, 7], [(0, 1), (1, 2)]), usuario.CLAVE, {}, "por_arista", 1, P)
original, _ = curva_exacta(red([0, 2.5, 7], [(0, 1), (1, 2)]), "fv", {}, "por_arista", 1, P)
comprobar(copia == original, "la copia editable de FV trae su `esperanza` y da la misma curva exacta")
ALGORITMOS.pop(usuario.CLAVE, None)

print("\nLa cota inferior por validez, en ciclos")
from math import comb  # noqa: E402

from simulation.barrido import who_gets_message_batch  # noqa: E402
from simulation.ronda import get_adj  # noqa: E402
from simulation.teoria import cota_validez  # noqa: E402


def anillo(valores, dirigida=False):
    n = len(valores)
    return red(valores, [(i, (i + 1) % n) for i in range(n)] + ([((i + 1) % n, i) for i in range(n)] if dirigida else []),
               dirigida)


def menos_de(r, p, d):  # P(Bin(r, p) < d)
    return sum(comb(r, k) * p**k * (1 - p) ** (r - k) for k in range(d))


cota, _ = cota_validez(anillo([0] * 5 + [1] * 5), 5, P)
comprobar(np.allclose(cota, menos_de(5, P, 3) ** 4), "10 nodos, mitad y mitad, 5 rondas: P(Bin(5, p) < 3)^4")
cota, _ = cota_validez(anillo([1, 1, 0, 0, 0, 0, 1, 1, 1]), 4, P)  # el bloque de 1 cruza el inicio de la lista
comprobar(np.allclose(cota, menos_de(4, P, 3) * menos_de(4, P, 2) * menos_de(4, P, 3) ** 2),
          "9 nodos, bloques de 4 y 5: cada bloque con sus dos distancias")
for nombre, grafo_, pista in [
    ("un camino", red([0, 1, 1], [(0, 1), (1, 2)], False), "ciclo"),
    ("dos triángulos", red([0, 1, 1, 0, 1, 1], [(0, 1), (1, 2), (2, 0), (3, 4), (4, 5), (5, 3)], False), "ciclo"),
    ("un anillo de un solo sentido", red([0, 1, 1], [(0, 1), (1, 2), (2, 0)]), "ciclo"),
    ("una entrada unánime", anillo([1, 1, 1, 1]), "ceros y unos"),
    ("valores que no son 0 y 1", anillo([0, 0.5, 1, 1]), "ceros y unos"),
]:
    cota, motivo = cota_validez(grafo_, 2, P)
    comprobar(cota is None and pista in motivo, f"sin cota para {nombre}, y lo dice: {motivo!r}")
# El evento que la cota cuenta (a ningún centro le llega el valor contrario), con los
# sorteos del motor: exacto con un sorteo por arista dirigida, por encima con los demás.
np.random.seed(4)
entrada = np.array([0, 0, 0, 0, 1, 1, 1, 1])
for dirigida in (True, False):
    for modo in ("por_arista", "por_emisor"):
        grafo_ = anillo(list(entrada), dirigida)
        adj = (get_adj(grafo_) > 0).astype(float)
        sabe0, sabe1 = np.tile(entrada == 0, (40000, 1)), np.tile(entrada == 1, (40000, 1))
        for _ in range(4):
            fired = who_gets_message_batch(adj * 0.5, adj, dirigida, 40000, modo)
            sabe0, sabe1 = sabe0 | (fired & sabe0[:, :, None]).any(axis=1), sabe1 | (fired & sabe1[:, :, None]).any(axis=1)
        evento = (~sabe0[:, 5] & ~sabe1[:, 1]).mean()  # centros: el nodo 5 (bloque de 1) y el 1 (bloque de 0)
        cota = cota_validez(grafo_, 4, [0.5])[0][0]
        z = (evento - cota) / np.sqrt(cota * (1 - cota) / 40000)
        exacto = dirigida and modo == "por_arista"
        comprobar(abs(z) < 4 if exacto else z > -4,
                  f"{'dirigida' if dirigida else 'no dirigida'}, {modo}: el evento ({evento:.4f}) "
                  f"{'coincide con' if exacto else 'no baja de'} la cota ({cota:.4f})")

print("\nEl desglose: las simulaciones del sweep, una por una")
from simulation.barrido import simulacion_del_sweep  # noqa: E402

grafo_k = red([1, 1, 0, 1, 0], completa(5))
usuario.instalar(
    "def r(matrix, values, messages, params):\n"
    "    out = []\n"
    "    for i, v in enumerate(messages):\n"
    "        em = np.where(v != 0)[0]\n"
    "        out.append((values[i], None) if len(em) == 0 else (values[em[np.random.randint(len(em))]], int(em[0])))\n"
    "    return out\n"
    "ALGORITMO = {'nombre': 'sin batch', 'ronda': r}\n"
)
for clave, modo in [("fv", "por_arista"), ("courteous", "por_emisor"), (usuario.CLAVE, "por_arista")]:
    config = dict(num_p_points=4, total_simulations_per_p=12, iterations=3, rounds=2, modo=modo, semilla=7)
    puntos = list(iter_sweep(grafo_k, clave, {}, **config))[-1]["points"]
    peor = max(
        abs(np.mean([simulacion_del_sweep(grafo_k, clave, {}, indice_p=i, simulacion=k, **config)["discrepancias"][-1]
                     for k in range(12)]) - pt["mean"])
        for i, pt in enumerate(puntos)
    )
    camino = "nodo por nodo" if clave == usuario.CLAVE else "con batch"
    comprobar(peor < 1e-12, f"{clave} ({modo}, {camino}): repetir las 12 simulaciones de cada p da la media del sweep")
config = dict(num_p_points=4, total_simulations_per_p=12, iterations=3, rounds=5, modo="por_arista", semilla=7)
t = simulacion_del_sweep(grafo_k, usuario.CLAVE, {}, indice_p=2, simulacion=10, **config)
comprobar(len(t["valores"]) == 6 and len(t["rondas"]) == 5 and t["iteracion"] == 2,
          "5 rondas: 6 estados (con el inicial), 5 desgloses, y dice en qué pasada corrió")
comprobar(t["discrepancias"] == [max(v) - min(v) for v in t["valores"]], "la discrepancia de cada ronda es max − min")
comprobar(all(r["eligio"][i] is None or r["eligio"][i] in r["recibio"][i] for r in t["rondas"] for i in range(5)),
          "nodo por nodo se sabe de quién copió, y siempre es alguien de quien recibió")
comprobar(simulacion_del_sweep(grafo_k, usuario.CLAVE, {}, indice_p=2, simulacion=10, **config) == t,
          "repetirla otra vez da exactamente lo mismo")
quieta = simulacion_del_sweep(grafo_k, "courteous", {}, indice_p=0, simulacion=0, **config)
comprobar(all(r["recibio"] == [[]] * 5 for r in quieta["rondas"]), "con p = 0 nadie recibe nada")
for malo in ({"simulacion": 12}, {"indice_p": 4}):
    try:
        simulacion_del_sweep(grafo_k, "fv", {}, **{**config, "indice_p": 0, "simulacion": 0, **malo})
        comprobar(False, f"{malo} debía rechazarse")
    except ValueError:
        comprobar(True, f"pedir {malo} fuera de lo que corrió se rechaza")
ALGORITMOS.pop(usuario.CLAVE, None)

# La interfaz: arranca con la primera curva, ofrece todas las simulaciones y descarta respuestas viejas.
app.ENGINE = motor = FakeWorker("motor")
app.STATE.tab.name = "k"
app.STATE.tab.nodes = [Node(f"n{i}", v) for i, v in enumerate([1, 1, 0, 1, 0])]
app.STATE.tab.edges = [Edge(f"e{i}{j}", f"n{i}", f"n{j}", "1") for i in range(5) for j in range(5) if i != j]
app.STATE.algorithm = "fv"
app.STATE.sweep_algorithms = ["fv"]
for campo, valor in {"p-points-input": "11", "simulations-input": "30", "iterations-input": "3",
                     "rounds-input": "4", "seed-input": "99"}.items():
    DOCUMENTO.getElementById(campo).value = valor
app.STATE.sweep_running = False
app.start_sweep()
comprobar(DOCUMENTO.getElementById("trace-panel").hidden, "al empezar un sweep se esconde el desglose del anterior")
enviado = FakeWorker.ultimo.enviados[-1]["params"]
pasadas = list(iter_sweep(enviado["graph"], algorithm="fv", num_p_points=11, total_simulations_per_p=30,
                          iterations=3, rounds=4, semilla=99))[1:]
FakeWorker.ultimo._entregar(pasadas[0])
pedido = motor.enviados[-1]
comprobar(not DOCUMENTO.getElementById("trace-panel").hidden and pedido["type"] == "traza",
          "con la primera curva aparece el desglose y pide una simulación")
comprobar(DOCUMENTO.getElementById("trace-sim").max == "10", "tras la primera pasada se pueden ver sus 10 simulaciones")
FakeWorker.ultimo._entregar(pasadas[1])
FakeWorker.ultimo._entregar(pasadas[2])
app.finish_sweep()
comprobar(DOCUMENTO.getElementById("trace-sim").max == "30", "al terminar, las 30 simulaciones por p")
comprobar(DOCUMENTO.getElementById("trace-sim-out").textContent == "de 30", "y la etiqueta lo dice sin tener que mover nada")
campo_sim = DOCUMENTO.getElementById("trace-sim")
for escrito, pedida in [("", None), ("abc", None), ("7", 6), ("5000", 29), ("-3", 0), ("12.9", 11)]:
    antes = len(motor.enviados)
    campo_sim.value = escrito
    app.request_trace()
    nuevo = motor.enviados[-1]["params"]["simulacion"] if len(motor.enviados) > antes else None
    comprobar(nuevo == pedida, f"escribir {escrito!r} en el número de simulación pide la {pedida} (desde 0)")
campo_sim.value = "5000"
app.normalize_trace_sim()
comprobar(campo_sim.value == "30", "al salir del campo, un número fuera de rango queda en el máximo")
campo_sim.value = ""
app.normalize_trace_sim()
comprobar(campo_sim.value == "1", "y un campo vacío vuelve a 1")
campo_sim.value = "1"
comprobar(DOCUMENTO.getElementById("trace-p").value == "5" and pedido["params"]["indiceP"] == 5
          and pedido["params"]["simulacion"] == 0 and pedido["params"]["semilla"] == 99,
          "pide la simulación 1 en el p más cercano a 0.5, con la semilla del sweep")
resultado = simulacion_del_sweep(pedido["params"]["graph"], "fv", {}, num_p_points=11, total_simulations_per_p=30,
                                 iterations=3, rounds=4, semilla=99, indice_p=5, simulacion=0)
DOCUMENTO.getElementById("trace-sim").value = "2"
app.request_trace()
app.receive_trace({"type": "traza", "id": pedido["id"], "resultado": json_.dumps(resultado)})
comprobar(app.TRACE is None, "una respuesta vieja se descarta: el deslizador ya pidió otra")
app.receive_trace({"type": "traza", "id": app.TRACE_PEDIDO, "resultado": json_.dumps(resultado)})
comprobar(DOCUMENTO.getElementById("trace-body").innerHTML.count("<tr") == 5, "la tabla tiene una fila por nodo")
cambios = sum(1 for i in range(5) if resultado["valores"][1][i] != resultado["valores"][0][i])
comprobar(DOCUMENTO.getElementById("trace-body").innerHTML.count('class="cambio"') == cambios,
          f"se marcan los {cambios} nodos que cambiaron en la ronda 1")
app.on_trace_chip(types.SimpleNamespace(target=types.SimpleNamespace(
    closest=lambda _sel: types.SimpleNamespace(getAttribute=lambda _a: "0"))))
comprobar(DOCUMENTO.getElementById("trace-status").textContent.startswith("Ronda 0: valores iniciales"),
          "un clic en la discrepancia de una ronda salta a esa ronda")
app.STATE.round_running = True
for oyente in motor.oyentes.get("message", []):
    oyente(types.SimpleNamespace(data={"type": "error", "para": "traza", "id": app.TRACE_PEDIDO, "message": "x"}))
comprobar(app.STATE.round_running, "un error del desglose no destraba la ronda del editor")
app.STATE.round_running = False

print("\nVarios algoritmos a la vez: casillas, un Worker por algoritmo y qué se dibuja")


def casilla(atributo: str, clave: str, marcada: bool):
    elemento = FakeElement("casilla")
    elemento.setAttribute(atributo, clave)
    elemento.checked = marcada
    return types.SimpleNamespace(target=elemento)


app.STATE.active_view = "analysis"
app.STATE.sweep_algorithms = ["fv"]
app.on_algorithm_check(casilla("data-algoritmo", "amp", True))
comprobar(app.STATE.sweep_algorithms == ["fv", "amp"], "palomear AMP lo suma al sweep, en el orden del registro")
marcadas = re.findall(r'data-algoritmo="([^"]+)" checked', DOCUMENTO.getElementById("algorithm-checks").innerHTML)
comprobar(marcadas == ["fv", "amp"], f"las casillas salen de ALGORITMOS y reflejan el estado: {marcadas}")
comprobar(campos_de_parametros() == ["agreed"], "en el análisis aparecen los campos de los palomeados (AMP)")
app.on_algorithm_check(casilla("data-algoritmo", "fv", False))
app.on_algorithm_check(casilla("data-algoritmo", "amp", False))
comprobar(app.STATE.sweep_algorithms == [] and DOCUMENTO.getElementById("sweep-run-btn").disabled,
          "sin ningún algoritmo palomeado no se puede ejecutar")
app.STATE.sweep_algorithms = ["fv", "amp"]

creados: list[FakeWorker] = []
nuevo_original = FakeWorker.new
FakeWorker.new = classmethod(lambda cls, url, options=None: creados.append(cls(url, options)) or creados[-1])
app.STATE.tab.nodes = [Node("a", 0), Node("b", 1)]
app.STATE.tab.edges = [Edge("e", "a", "b", "0.5"), Edge("f", "b", "a", "0.5")]
DOCUMENTO.getElementById("seed-input").value = "7"
DOCUMENTO.getElementById("rounds-input").value = "1"  # la teórica solo existe para 1 ronda
app.STATE.sweep_running = False
app.start_sweep()
FakeWorker.new = nuevo_original
enviados = [w.enviados[-1]["params"] for w in creados]
comprobar([e["algorithm"] for e in enviados] == ["fv", "amp"], "arranca un Worker por algoritmo palomeado")
comprobar(enviados[0]["semilla"] == enviados[1]["semilla"] == 7 and enviados[1]["algorithmParams"] == {"agreed": 0.9},
          "todos con la misma semilla y cada uno con sus parámetros")
curvas = {}
for worker, mandado in zip(creados, enviados):
    evento = list(iter_sweep(mandado["graph"], algorithm=mandado["algorithm"], params=mandado["algorithmParams"],
                             num_p_points=mandado["numPPoints"], total_simulations_per_p=mandado["totalSimulationsPerP"],
                             iterations=mandado["iterations"], rounds=mandado["rounds"], semilla=7))[1]
    curvas[mandado["algorithm"]] = evento["points"]
    teo, motivo = curva_exacta(mandado["graph"], mandado["algorithm"], mandado["algorithmParams"], mandado["modo"],
                               mandado["rounds"], np.linspace(0, 1, mandado["numPPoints"]))
    worker._entregar({"type": "teorica", "valores": teo, "motivo": motivo})
    worker._entregar(evento)
comprobar({c: e["puntos"] for c, e in app.STATE.experiments.items()} == curvas,
          "cada algoritmo guarda su propia curva, igual a la de correrlo solo con esa semilla")


def dibujadas(clase: str) -> int:
    return DOCUMENTO.getElementById("chart-curves").innerHTML.count(f'class="{clase}"')


comprobar((dibujadas("chart-mean-line"), dibujadas("chart-theory-line"), dibujadas("chart-confidence-band")) == (2, 2, 2),
          "la gráfica trae media, teórica y banda de los dos")
leyenda = DOCUMENTO.getElementById("chart-toggles").innerHTML
comprobar(re.findall(r'data-curva="([^"]+)" checked', leyenda) == ["fv:sim", "fv:teo", "amp:sim", "amp:teo", "ic"],
          "«Mostrar en la gráfica» tiene una casilla por curva, todas palomeadas")
app.on_curve_toggle(casilla("data-curva", "fv:sim", False))
app.on_curve_toggle(casilla("data-curva", "amp:teo", False))
comprobar((dibujadas("chart-mean-line"), dibujadas("chart-theory-line"), dibujadas("chart-confidence-band")) == (1, 1, 1),
          "despalomear una curva la quita (y la banda se va con su media)")
app.on_curve_toggle(casilla("data-curva", "ic", False))
comprobar(dibujadas("chart-confidence-band") == 0 and dibujadas("chart-mean-line") == 1, "la casilla del IC quita las bandas")
app.STATE.hidden_curves.clear()

# La cota inferior llega en el mismo mensaje que la teórica y se dibuja una sola vez.
comprobar(dibujadas("chart-theory-line chart-bound-line") == 0 and 'data-curva="cota"' not in leyenda,
          "sin cota (la red no es un ciclo) no hay ni línea ni casilla")
creados[-1]._entregar({"type": "teorica", "valores": teo, "motivo": motivo, "cota": [0.25] * len(teo)})
comprobar(dibujadas("chart-theory-line chart-bound-line") == 1
          and 'data-curva="cota" checked' in DOCUMENTO.getElementById("chart-toggles").innerHTML,
          "con cota, la gráfica trae su línea y la leyenda su casilla")
app.on_curve_toggle(casilla("data-curva", "cota", False))
comprobar(dibujadas("chart-theory-line chart-bound-line") == 0, "despalomearla la quita")
app.STATE.hidden_curves.clear()
creados[-1]._entregar({"type": "teorica", "valores": teo, "motivo": motivo})

comprobar(app.STATE.detail_algorithm == "fv" and not DOCUMENTO.getElementById("detail-picker").hidden,
          "con dos algoritmos aparece el selector de detalle, en el primero")
app.on_detail_change(types.SimpleNamespace(target=types.SimpleNamespace(value="amp")))
comprobar(app.STATE.experiment is app.STATE.experiments["amp"]
          and app.experiment_basename(app.STATE.experiment).endswith("_amp_semilla7")
          and motor.enviados[-1]["params"]["algorithm"] == "amp",
          "cambiar el detalle cambia la tabla, las descargas y el desglose a ese algoritmo")
creados[0]._entregar({"type": "error", "message": "tronó"})
comprobar(app.STATE.sweep_running and "sweep-error" in DOCUMENTO.getElementById("sweep-status").innerHTML,
          "el error de un algoritmo se muestra y no detiene a los demás")
creados[1]._entregar({"type": "end"})
comprobar(not app.STATE.sweep_running and not app.WORKERS, "el sweep termina cuando acaba el último Worker")
app.STATE.sweep_algorithms = ["fv"]

# --- Temas ---------------------------------------------------------------
raiz, boton_tema = DOCUMENTO.getElementById("root"), DOCUMENTO.getElementById("theme-btn")
app.STATE.theme = "cafe"
app.apply_theme()
comprobar(raiz.getAttribute("data-theme") == "cafe" and boton_tema.textContent == "Usar tema claro",
          "tema: el predefinido es café y el botón ofrece el claro")
teorica_cafe, svg_cafe, nodo_cafe = app.theory_color("fv"), app.chart_svg_style(), app.value_color(0, app.STATE.theme)
app.toggle_theme()
comprobar(app.STATE.theme == "claro" and raiz.getAttribute("data-theme") == "claro"
          and boton_tema.textContent == "Usar tema café", "tema: el botón cambia al claro y ofrece volver")
comprobar(teorica_cafe != app.theory_color("fv") and svg_cafe != app.chart_svg_style()
          and nodo_cafe != app.value_color(0, app.STATE.theme) and 'fill="#ffffff"' in app.chart_svg_style(),
          "tema: nodos, teórica y SVG descargado siguen al tema")
app.toggle_theme()
comprobar(app.STATE.theme == "cafe", "tema: un segundo clic vuelve al café")

# --- Topologías ----------------------------------------------------------
print("\nTopologías: botones, parámetros, imagen y carga en una pestaña nueva")
import runpy  # noqa: E402

# El catálogo (matriz idéntica a la de networkx, nodos sin encimar, límites) se comprueba solo.
TOPOLOGIAS = runpy.run_path(str(PROJECT_ROOT / "python" / "topologias.py"), run_name="__main__")["TOPOLOGIAS"]


def de_topologia(element_id: str) -> FakeElement:
    return DOCUMENTO.getElementById(f"topology-{element_id}")


def escribir_parametro(nombre: str, valor: str) -> None:
    campo = FakeElement(f"topology-param-{nombre}")
    campo.setAttribute("data-param", nombre)
    campo.value = valor
    app.on_topology_input(types.SimpleNamespace(target=campo))


app.open_topology_dialog()
comprobar(
    re.findall(r'data-topologia="([^"]+)"', de_topologia("list").innerHTML) == list(TOPOLOGIAS)
    and not de_topologia("list").hidden and de_topologia("detail").hidden and de_topologia("accept").hidden,
    "al abrir hay un botón por topología y todavía no se puede cargar",
)
app.select_topology("malla")
comprobar(
    de_topologia("list").hidden and not de_topologia("detail").hidden
    and de_topologia("title").textContent == "Malla"
    and de_topologia("description").textContent == TOPOLOGIAS["malla"]["descripcion"]
    and re.findall(r'data-param="([^"]+)"', de_topologia("params").innerHTML) == ["filas", "columnas"],
    "elegir una muestra su descripción y un campo por parámetro",
)
comprobar(
    de_topologia("preview").innerHTML.count("<circle") == 12 and de_topologia("preview").innerHTML.count("<line") == 17
    and de_topologia("count").textContent == "12 nodos · 17 aristas",
    "la imagen dibuja la malla por omisión (3 × 4)",
)
escribir_parametro("filas", "2")
comprobar(de_topologia("preview").innerHTML.count("<circle") == 8, "cambiar un parámetro redibuja la imagen")
escribir_parametro("columnas", "99")
pestanas = len(app.STATE.tabs)
app.accept_topology()
comprobar(
    "Columnas" in de_topologia("error").textContent and not de_topologia("error").hidden
    and de_topologia("accept").disabled and de_topologia("preview").innerHTML == ""
    and len(app.STATE.tabs) == pestanas and not de_topologia("dialog").hidden,
    "un valor fuera de límites se explica y no deja cargar",
)
escribir_parametro("columnas", "5")
app.accept_topology()
comprobar(
    len(app.STATE.tabs) == pestanas + 1 and app.STATE.tab.name == "Malla (2, 5)" and not app.STATE.tab.directed
    and len(app.STATE.tab.nodes) == 10 and len(app.STATE.tab.edges) == 13 and de_topologia("dialog").hidden,
    "«Cargar» abre la malla 2 × 5 en una pestaña nueva y cierra el diálogo",
)
app.select_topology("ciclo")
comprobar(de_topologia("preview").innerHTML.count("<circle") == 6, "otra topología empieza con sus valores por omisión")

print()
if FALLAS:
    print(f"FALLA — {len(FALLAS)} comprobación(es):")
    for fallo in FALLAS:
        print(f"  - {fallo}")
    raise SystemExit(1)
print("OK — todas las comprobaciones de la interfaz pasaron.")
print("Recordatorio: esto NO sustituye el recorrido visual en navegador.")
