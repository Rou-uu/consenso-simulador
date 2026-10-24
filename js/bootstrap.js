import { loadPyodide } from "https://cdn.jsdelivr.net/pyodide/v314.0.2/full/pyodide.mjs";

const PYODIDE_INDEX_URL = "https://cdn.jsdelivr.net/pyodide/v314.0.2/full/";
const PYTHON_FILES = [
  "python/app.py",
  "python/models.py",
  "python/geometry.py",
  "python/simulation/__init__.py",
  "python/simulation/algoritmos.py",
  "python/simulation/usuario.py",
];

const loader = document.getElementById("python-loader");
const detail = document.getElementById("loader-detail");

function showRuntimeState(state, text) {
  if (loader) {
    loader.hidden = false;
    loader.dataset.state = state;
    loader.setAttribute("aria-busy", state === "loading" ? "true" : "false");
  }

  if (detail) {
    detail.textContent = text;
  }
}

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

async function startApplication() {
  showRuntimeState("loading", "Cargando Python en el navegador…");

  const pythonFilesPromise = Promise.all(PYTHON_FILES.map(fetchPythonFile));
  const pyodide = await loadPyodide({ indexURL: PYODIDE_INDEX_URL });

  showRuntimeState("loading", "Cargando NumPy…");
  const [, pythonFiles] = await Promise.all([
    pyodide.loadPackage("numpy"),
    pythonFilesPromise,
  ]);

  showRuntimeState("loading", "Cargando la aplicación…");
  mountPythonFiles(pyodide, pythonFiles);

  await pyodide.runPythonAsync(`
import runpy
import sys

if "/python" not in sys.path:
    sys.path.insert(0, "/python")

runpy.run_path("/python/app.py", run_name="__main__")
`);

  showRuntimeState("ready", "Aplicación lista.");
  loader?.setAttribute("aria-hidden", "true");
  if (loader) {
    loader.hidden = true;
  }
}

startApplication().catch((error) => {
  console.error("No se pudo iniciar la aplicación Python.", error);
  const detail = error instanceof Error ? error.message : String(error);
  showRuntimeState("error", `No se pudo iniciar la aplicación: ${detail}`);
});
