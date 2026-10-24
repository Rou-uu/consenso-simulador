# 08 — Despliegue

El sitio vive en **https://simulador.ruee.dev/** (GitHub Pages, DNS en
Cloudflare). Se publica el repositorio tal cual: no hay nada que compilar.

| Pieza | Dónde | Si falta |
|---|---|---|
| Workflow | `.github/workflows/pages.yml` | no se publica |
| `.nojekyll` | raíz, 0 bytes | el sitio se publica y **muere al arrancar** |
| Origen de Pages | Settings → Pages → *GitHub Actions* | `deploy-pages` falla |
| Dominio | Settings → Pages → *Custom domain*, y el archivo `CNAME` (`simulador.ruee.dev`) | el sitio sigue en `rou-uu.github.io/consenso-simulador/` |
| DNS | Cloudflare, zona `ruee.dev`: `CNAME` a `rou-uu.github.io` **sin proxear** | sin certificado |

## El workflow

En cada push a `main` (o a mano) corren dos jobs **a la vez**:

- `verificar`: Python 3.12, `numpy==2.4.5`, `equivalencia.py` y `smoke_ui.py`.
- `publicar`: `upload-pages-artifact` con `path: .` y `deploy-pages`.

`publicar` **no** depende de `verificar`: un arnés en rojo no detiene la
publicación. Es a propósito mientras no se confirme que la línea base da idéntica
en Linux; después, `needs: verificar` lo convierte en candado.

`path: .` publica todo el repositorio, incluidos `scripts/` y la línea base. Las
carpetas que empiezan con punto (`.github/`) no se sirven.

## `.nojekyll`

Sin él, Pages pasa el sitio por Jekyll, que descarta los archivos que empiezan
con `_`. `python/simulation/__init__.py` da 404 y el arranque falla. Localmente
todo funciona, así que el fallo solo se ve en producción.

## El dominio

- **La nube de Cloudflare va gris** (*DNS only*). Con la naranja, el desafío ACME
  de GitHub no llega, el certificado nunca se emite y, como `.dev` está en la
  lista HSTS precargada, el sitio queda inaccesible, sin botón para continuar.
- Si algún día se proxea: SSL **Full** (nunca *Flexible*, que hace un bucle de
  redirecciones), y Rocket Loader y Auto Minify de JS **apagados** (rompen los
  módulos ES y el Worker).
- El archivo `CNAME` se agregó desde la web de GitHub (commit `84b6632`). Con
  despliegue por Actions manda lo que diga Settings; mantén los dos con el mismo
  dominio.

## Otro hosting

Cualquier servidor estático sirve si:
- manda `.js` y `.mjs` como `text/javascript` y `.wasm` como `application/wasm`;
- devuelve 404 de verdad (un fallback tipo SPA entrega `index.html` en lugar de un
  `.py` y Pyodide lo intenta ejecutar);
- sirve los archivos que empiezan con `_`.

Todas las rutas son relativas, así que el sitio funciona en cualquier subruta.

## Comprobar un despliegue

```bash
SITIO=https://simulador.ruee.dev
curl -s -o /dev/null -w '%{http_code}\n' $SITIO/python/simulation/__init__.py   # 200; 404 = falta .nojekyll
curl -sI $SITIO/js/bootstrap.js | grep -i content-type                         # text/javascript
dig +short simulador.ruee.dev                                                  # rou-uu.github.io. + IPs 185.199.10x.153
```

IPs `104.21.x.x` o `172.67.x.x` en el `dig` significan que alguien proxeó el
registro.

Luego, en una ventana privada: el overlay desaparece, una ronda funciona y un
sweep corto llega al final. El sweep es lo único que prueba el segundo Worker.
Pages cachea HTML, CSS y JS unos 10 minutos (`max-age=600`); los `.py` se piden
con `no-store`.
