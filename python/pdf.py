"""La gráfica del sweep en PDF vectorial, sin dependencias.

Traduce el SVG autónomo que arma `app.chart_svg` (líneas, trazos M/L/Z, textos y
el fondo) a operadores PDF. No es un conversor de SVG general: cubre lo que esa
gráfica usa y nada más. El estilo sale del mismo `<style>` que lleva el SVG, así
que el PDF no repite colores ni grosores.

El texto va en Times-Roman, una de las 14 fuentes que todo lector de PDF trae,
así que no se incrusta ninguna. El archivo sale en ASCII puro: se puede entregar
como `str` a la misma función de descarga que el CSV y el SVG.
"""

from __future__ import annotations

import math
import re
import xml.etree.ElementTree as ET

SVG = "{http://www.w3.org/2000/svg}"

#: Anchos de Times-Roman en milésimas del cuerpo (AFM de Adobe), de " " a "~".
#: Hacen falta para centrar o alinear a la derecha (text-anchor).
ANCHOS_TIMES = [
    250, 333, 408, 500, 500, 833, 778, 333, 333, 333, 500, 564, 250, 333, 250, 278,
    500, 500, 500, 500, 500, 500, 500, 500, 500, 500, 278, 278, 564, 564, 564, 444,
    921, 722, 667, 667, 722, 611, 556, 722, 722, 333, 389, 722, 611, 889, 722, 722,
    556, 722, 667, 556, 611, 722, 722, 944, 722, 722, 611, 333, 278, 333, 469, 500,
    333, 444, 500, 444, 500, 444, 333, 500, 500, 278, 278, 500, 278, 778, 500, 500,
    500, 500, 333, 389, 278, 500, 500, 722, 500, 500, 444, 480, 200, 480, 541,
]


def ancho_texto(texto: str, cuerpo: float) -> float:
    # Fuera de ASCII (acentos) vale 500: ninguno de esos glifos se aleja mucho.
    return sum(ANCHOS_TIMES[ord(c) - 32] if 32 <= ord(c) < 127 else 500 for c in texto) * cuerpo / 1000


def reglas_css(css: str) -> list[tuple[list[str], dict[str, str]]]:
    """``.a b{x:1;y:2}`` → ``[(["a", "b"], {"x": "1", "y": "2"})]``, en orden."""
    reglas = []
    for selector, cuerpo in re.findall(r"([^{}]+)\{([^}]*)\}", css):
        props = dict(
            (k.strip(), v.strip()) for k, v in (d.split(":", 1) for d in cuerpo.split(";") if ":" in d)
        )
        reglas.append((selector.split(), props))
    return reglas


def coincide(paso: str, elemento: ET.Element) -> bool:
    if paso.startswith("."):
        return paso[1:] in (elemento.get("class") or "").split()
    return elemento.tag.removeprefix(SVG) == paso


def estilo_de(elemento: ET.Element, ancestros: list[ET.Element], reglas) -> dict[str, str]:
    """Las reglas que le tocan, en orden de documento, y encima su `style` y sus
    atributos de presentación. Solo selectores de uno o dos pasos (`.a` o `.a b`)."""
    estilo: dict[str, str] = {}
    for atributo in ("fill", "stroke", "stroke-width", "fill-opacity", "text-anchor"):
        if elemento.get(atributo) is not None:
            estilo[atributo] = elemento.get(atributo)
    for pasos, props in reglas:
        if coincide(pasos[-1], elemento) and all(any(coincide(p, a) for a in ancestros) for p in pasos[:-1]):
            estilo.update(props)
    estilo.update(reglas_css("x{" + (elemento.get("style") or "") + "}")[0][1])
    if "font" in estilo:
        estilo.setdefault("font-size", estilo["font"].split()[0])
    return estilo


def color(valor: str | None) -> tuple[float, float, float] | None:
    if not valor or valor == "none":
        return None
    if valor.startswith("#"):
        h = valor[1:] if len(valor) == 7 else "".join(c * 2 for c in valor[1:4])
        return tuple(int(h[i:i + 2], 16) / 255 for i in (0, 2, 4))
    canales = re.findall(r"[\d.]+", valor)
    return tuple(float(c) / 255 for c in canales[:3]) if len(canales) >= 3 else (0.0, 0.0, 0.0)


def numero(valor: str | None, por_omision: float) -> float:
    m = re.match(r"-?[\d.]+(?:e-?\d+)?", valor or "")
    return float(m.group()) if m else por_omision


def cadena_pdf(texto: str) -> str:
    """Un literal de cadena PDF en ASCII: lo de fuera va en octal de WinAnsi."""
    salida = []
    for c in texto.replace("\u2212", "-"):  # el signo menos no está en WinAnsi
        if c in "()\\":
            salida.append("\\" + c)
        elif 32 <= ord(c) < 127:
            salida.append(c)
        else:
            salida.append("\\%03o" % c.encode("cp1252", "replace")[0])
    return "(" + "".join(salida) + ")"


def svg_a_pdf(svg: str) -> str:
    raiz = ET.fromstring(svg.split("?>", 1)[-1].strip())
    _, _, ancho, alto = (float(v) for v in raiz.get("viewBox", "0 0 800 440").split())
    reglas = []
    for nodo in raiz.iter(f"{SVG}style"):
        reglas += reglas_css(nodo.text or "")

    # El SVG mide y hacia abajo y el PDF hacia arriba: se voltea una vez aquí.
    ops = [f"1 0 0 -1 0 {alto:g} cm"]
    opacidades: dict[float, str] = {}

    def pintar(estilo: dict[str, str], trazo: str) -> None:
        relleno, linea = color(estilo.get("fill", "#000")), color(estilo.get("stroke"))
        if not relleno and not linea:
            return
        ops.append("q")
        if relleno:
            ops.append("%.4f %.4f %.4f rg" % relleno)
            opacidad = numero(estilo.get("fill-opacity"), 1.0)
            if opacidad < 1:
                nombre = opacidades.setdefault(opacidad, f"GS{len(opacidades)}")
                ops.append(f"/{nombre} gs")
        if linea:
            ops.append("%.4f %.4f %.4f RG" % linea)
            ops.append(f"{numero(estilo.get('stroke-width'), 1.0):g} w")
            guiones = estilo.get("stroke-dasharray")
            if guiones and guiones != "none":
                ops.append("[" + " ".join(re.findall(r"[\d.]+", guiones)) + "] 0 d")
        ops.append(trazo)
        ops.append("B" if relleno and linea else "f" if relleno else "S")
        ops.append("Q")

    def recorrer(elemento: ET.Element, ancestros: list[ET.Element]) -> None:
        etiqueta = elemento.tag.removeprefix(SVG)
        estilo = estilo_de(elemento, ancestros, reglas)
        if etiqueta == "rect":
            def medida(nombre: str, total: float) -> float:
                v = elemento.get(nombre, "0")
                return total * float(v[:-1]) / 100 if v.endswith("%") else float(v)
            pintar(estilo, f"{medida('x', ancho):g} {medida('y', alto):g} "
                           f"{medida('width', ancho):g} {medida('height', alto):g} re")
        elif etiqueta == "line":
            estilo.setdefault("fill", "none")
            x1, y1, x2, y2 = (float(elemento.get(k, 0)) for k in ("x1", "y1", "x2", "y2"))
            pintar(estilo, f"{x1:g} {y1:g} m {x2:g} {y2:g} l")
        elif etiqueta == "path":
            partes, fichas = [], re.findall(r"[MLZ]|-?[\d.]+(?:e-?\d+)?", elemento.get("d", ""))
            orden, numeros = "M", []
            for ficha in fichas:
                if ficha in "MLZ":
                    orden = ficha
                    if ficha == "Z":
                        partes.append("h")
                    continue
                numeros.append(ficha)
                if len(numeros) == 2:
                    partes.append(f"{numeros[0]} {numeros[1]} {'m' if orden == 'M' else 'l'}")
                    orden, numeros = "L", []  # tras M, los pares siguientes son L
            if partes:
                pintar(estilo, " ".join(partes))
        elif etiqueta == "text" and (elemento.text or "").strip():
            texto = elemento.text.strip()
            cuerpo = numero(estilo.get("font-size"), 16.0)
            x, y = float(elemento.get("x", 0)), float(elemento.get("y", 0))
            angulo = 0.0
            giro = re.match(r"rotate\(([-\d.]+)\s+([-\d.]+)\s+([-\d.]+)\)", elemento.get("transform", ""))
            if giro:
                angulo, cx, cy = (float(v) for v in giro.groups())
                t = math.radians(angulo)
                x, y = cx + (x - cx) * math.cos(t) - (y - cy) * math.sin(t), cy + (x - cx) * math.sin(t) + (y - cy) * math.cos(t)
            t = math.radians(angulo)
            a, b = round(math.cos(t), 6) + 0.0, round(math.sin(t), 6) + 0.0  # sin -0.0 en el PDF
            corrida = {"middle": 0.5, "end": 1.0}.get(estilo.get("text-anchor", ""), 0.0) * ancho_texto(texto, cuerpo)
            relleno = color(estilo.get("fill", "#000")) or (0.0, 0.0, 0.0)
            # [a b b -a]: el giro del SVG compuesto con el volteo vertical de la página.
            ops.append("BT %.4f %.4f %.4f rg /F1 %g Tf %.4f %.4f %.4f %.4f %.2f %.2f Tm %s Tj ET" % (
                *relleno, cuerpo, a, b, b, 0.0 - a, x - a * corrida, y - b * corrida, cadena_pdf(texto)))
        for hijo in elemento:
            recorrer(hijo, ancestros + [elemento])

    recorrer(raiz, [])

    contenido = "\n".join(ops)
    estados = "".join(f"/{n} <</Type /ExtGState /ca {o:g}>> " for o, n in opacidades.items())
    objetos = [
        "<</Type /Catalog /Pages 2 0 R>>",
        "<</Type /Pages /Kids [3 0 R] /Count 1>>",
        f"<</Type /Page /Parent 2 0 R /MediaBox [0 0 {ancho:g} {alto:g}] /Contents 4 0 R "
        f"/Resources <</Font <</F1 5 0 R>> /ExtGState <<{estados}>>>>>>",
        f"<</Length {len(contenido)}>>\nstream\n{contenido}\nendstream",
        "<</Type /Font /Subtype /Type1 /BaseFont /Times-Roman /Encoding /WinAnsiEncoding>>",
    ]
    salida, posiciones = "%PDF-1.4\n", []
    for i, objeto in enumerate(objetos, 1):
        posiciones.append(len(salida))
        salida += f"{i} 0 obj\n{objeto}\nendobj\n"
    inicio_xref = len(salida)
    salida += f"xref\n0 {len(objetos) + 1}\n0000000000 65535 f \n"
    salida += "".join(f"{p:010d} 00000 n \n" for p in posiciones)
    salida += f"trailer\n<</Size {len(objetos) + 1} /Root 1 0 R>>\nstartxref\n{inicio_xref}\n%%EOF\n"
    return salida
