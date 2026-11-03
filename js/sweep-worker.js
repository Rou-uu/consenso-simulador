import { loadPyodide } from "https://cdn.jsdelivr.net/pyodide/v314.0.2/full/pyodide.mjs";

const PYODIDE_INDEX_URL = "https://cdn.jsdelivr.net/pyodide/v314.0.2/full/";
const SWEEP_FILES = [
  "python/simulation/__init__.py",
  "python/simulation/algoritmos.py",
  "python/simulation/ronda.py",
  "python/simulation/barrido.py",
  "python/simulation/usuario.py",
];

async function fetchPythonFile(sourcePath) {
  const sourceUrl = new URL(`../${sourcePath}`, import.meta.url);
  const response = await fetch(sourceUrl, { cache: "no-store" });
  if (!response.ok) {
    throw new Error(`No se pudo descargar ${sourcePath} (${response.status}).`);
  }

  return {
    destination: `/${sourcePath}`,
    source: await response.text(),
  };
}

function mountPythonFiles(pyodide, files) {
  for (const { destination, source } of files) {
    const parentDirectory = destination.slice(0, destination.lastIndexOf("/"));
    pyodide.FS.mkdirTree(parentDirectory);
    pyodide.FS.writeFile(destination, source, { encoding: "utf8" });
  }
}

async function createRuntime() {
  const pythonFilesPromise = Promise.all(SWEEP_FILES.map(fetchPythonFile));
  const pyodide = await loadPyodide({ indexURL: PYODIDE_INDEX_URL });
  const [, pythonFiles] = await Promise.all([
    pyodide.loadPackage("numpy"),
    pythonFilesPromise,
  ]);

  mountPythonFiles(pyodide, pythonFiles);
  await pyodide.runPythonAsync(`
import sys

if "/python" not in sys.path:
    sys.path.insert(0, "/python")
`);

  return pyodide;
}

const runtimePromise = createRuntime();
// Avisa en cuanto el runtime esta listo. Sin esto, el primer clic en Simular se
// queda quieto ~15 s mientras baja Pyodide y NumPy, sin nada que lo explique.
runtimePromise.then(() => self.postMessage({ type: "listo" }));

function mensajeLegible(error) {
  const texto = error instanceof Error ? error.message : String(error);
  // Pyodide entrega el traceback completo de Python. La ultima linea con
  // contenido es el mensaje real; el resto es ruido para quien usa la app.
  const lineas = texto.trimEnd().split("\n").filter((linea) => linea.trim());
  const ultima = lineas[lineas.length - 1];
  if (!ultima) {
    return texto;
  }
  // "ValueError: no hay version vectorizada" -> "no hay version vectorizada"
  const conClase = ultima.match(/^\s*[A-Za-z_][\w.]*(?:Error|Exception):\s*(.+)$/);
  return conClase ? conClase[1] : ultima.trim();
}

// El ultimo fuente instalado. Compilar cuesta, y el codigo viaja en CADA
// mensaje para que el worker no tenga estado que sincronizar: comparar la cadena
// evita recompilar en cada clic sin reintroducir ese estado.
let fuenteInstalada = null;

async function instalarFuente(fuente) {
  const pyodide = await runtimePromise;
  if (fuente === fuenteInstalada) {
    return;
  }
  pyodide.globals.set("_fuente_usuario", fuente);
  await pyodide.runPythonAsync(`
from simulation import usuario

usuario.instalar(_fuente_usuario)
`);
  // Solo despues del await: si la instalacion falla, el proximo mensaje
  // reintenta en vez de dar por buena una compilacion que nunca ocurrio.
  fuenteInstalada = fuente;
}

// Una sola ronda para el editor. Devuelve el resultado ya serializado: el
// contrato de `run_round` promete floats nativos y `None`, asi que el viaje por
// JSON no pierde nada y evita convertir proxies de Python en el otro lado.
async function correrRonda(datos) {
  const pyodide = await runtimePromise;
  pyodide.globals.set("_ronda_params_json", JSON.stringify(datos.params ?? {}));

  return await pyodide.runPythonAsync(`
import json

from simulation.ronda import run_round

_params = json.loads(_ronda_params_json)
json.dumps(
    run_round(
        _params["graph"],
        algorithm=_params.get("algorithm", "fv"),
        params=_params.get("algorithmParams") or {},
        modo=_params.get("modo", "por_arista"),
    )
)
`);
}

async function correrBarrido(datos) {
  const pyodide = await runtimePromise;
  pyodide.globals.set("_sweep_params_json", JSON.stringify(datos.params ?? {}));

  await pyodide.runPythonAsync(`
import json

from js import Object, postMessage
from pyodide.ffi import to_js
from simulation.barrido import iter_sweep

_params = json.loads(_sweep_params_json)
for _message in iter_sweep(
    _params["graph"],
    algorithm=_params.get("algorithm", "fv"),
    params=_params.get("algorithmParams") or {},
    num_p_points=_params.get("numPPoints", 201),
    total_simulations_per_p=_params.get("totalSimulationsPerP", 5000),
    iterations=_params.get("iterations", 50),
    rounds=_params.get("rounds", 1),
    modo=_params.get("modo", "por_arista"),
):
    postMessage(to_js(_message, dict_converter=Object.fromEntries))
`);
}

self.onmessage = async (event) => {
  const tipo = event.data?.type;
  if (tipo !== "run" && tipo !== "ronda") {
    return;
  }

  const fuente = event.data?.params?.fuente;
  // Que el fallo diga en que etapa ocurrio: "no compila" y "revento a medio
  // correr" son dos problemas distintos y se arreglan distinto.
  let etapa = "compilar";
  try {
    if (fuente) {
      await instalarFuente(fuente);
    }
    etapa = "correr";
    if (tipo === "ronda") {
      self.postMessage({ type: "ronda", resultado: await correrRonda(event.data) });
      return;
    }
    await correrBarrido(event.data);
    self.postMessage({ type: "end" });
  } catch (error) {
    const texto = error instanceof Error ? error.message : String(error);
    self.postMessage({
      type: "error",
      etapa,
      message: mensajeLegible(error),
      // El traceback completo solo cuando el codigo es de quien usa la app: en
      // los algoritmos de casa es ruido, pero ahi es el numero de linea que
      // hace falta para depurar.
      traza: fuente ? texto : null,
    });
  }
};
