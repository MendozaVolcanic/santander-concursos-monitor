"""
Enriquecimiento de concursos: descarga el PDF de bases y extrae, best-effort,
la VIGENCIA (fecha de cierre) y a QUIÉN se dirige (requisitos).

El PDF está en el CDN de Santander, también detrás de Akamai: un GET normal
(o `ctx.request.get`) recibe 403. La única forma que funciona es dejar que el
visor embebido de la página `/archivos/SLUG` pida el PDF y capturar esa
respuesta de red desde el navegador headed.

Todo es best-effort: si algo falla, se devuelve lo que se pudo y NUNCA se lanza
una excepción que tumbe el monitor. El email siempre incluye el link al PDF
oficial para leer las bases completas.
"""
from __future__ import annotations

import io
import re

from pypdf import PdfReader
from playwright.sync_api import sync_playwright

UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"
)

MESES_RE = (
    "enero|febrero|marzo|abril|mayo|junio|julio|agosto|"
    "septiembre|octubre|noviembre|diciembre"
)


def _limpiar(s: str) -> str:
    return re.sub(r"\s+", " ", s).strip()


def _partir_oraciones(txt: str) -> list[str]:
    # corta por punto seguido de espacio; suficiente para texto legal.
    return [o.strip() for o in re.split(r"(?<=\.)\s+", txt) if o.strip()]


# Una fecha en texto legal: "8 de junio de 2026", "8 de junio del 2026" o "8/6/2026".
_FECHA = rf"(?:\d{{1,2}}\s+de\s+(?:{MESES_RE})\s+(?:de[l]?\s+(?:año\s+)?)?\d{{4}}|\d{{1,2}}/\d{{1,2}}/\d{{4}})"
_FECHA_RE = re.compile(_FECHA, re.IGNORECASE)


def extraer_vigencia(txt: str) -> str | None:
    """Devuelve la cláusula que describe el período/cierre del concurso.

    Estrategia general: busca una oración que (a) hable de vigencia/período/
    plazo/desde/entre/hasta y (b) contenga al menos una fecha. Prioriza las que
    mencionan 'vigencia'. Cubre las distintas redacciones de las bases:
      - "entre el 22 de junio y 22 de julio de 2026"
      - "desde las 00:00 horas del día 8 de junio de 2026 hasta ..."
    """
    txt = _limpiar(txt)
    candidatas: list[tuple[int, str]] = []
    for o in _partir_oraciones(txt):
        bajo = o.lower()
        tiene_kw = any(k in bajo for k in (
            "vigencia", "vigente", "período", "periodo", "plazo",
            "entre el", "hasta el", "desde el", "desde las",
        ))
        if tiene_kw and _FECHA_RE.search(o):
            # prioridad: 'vigencia' > 'periodo/plazo' > resto
            prio = 0 if ("vigencia" in bajo or "vigente" in bajo) else (
                1 if ("periodo" in bajo or "período" in bajo or "plazo" in bajo) else 2)
            candidatas.append((prio, o[:300]))
    if candidatas:
        candidatas.sort(key=lambda c: c[0])
        return candidatas[0][1]
    return None


def extraer_dirigido(txt: str) -> str | None:
    """Devuelve la cláusula de 'a quiénes se dirige' / 'podrán participar'."""
    txt = _limpiar(txt)
    # corta el encabezado tipo "PRIMERO: A quienes se dirige ..."
    m = re.search(
        r"(?:a\s+qui[eé]nes?\s+se\s+dirige|podr[áa]n?\s+participar|"
        r"participantes?\b|qui[eé]nes?\s+pueden?\s+participar)",
        txt, re.IGNORECASE,
    )
    if not m:
        return None
    frag = txt[m.start():m.start() + 500]
    # cortar en el siguiente encabezado en mayúsculas tipo "SEGUNDO:"
    corte = re.search(r"\b(SEGUNDO|TERCERO|CUARTO)\b\s*:", frag)
    if corte:
        frag = frag[:corte.start()]
    return _limpiar(frag)[:400]


def _pdf_a_texto(data: bytes) -> str:
    try:
        r = PdfReader(io.BytesIO(data))
        return "\n".join(p.extract_text() or "" for p in r.pages)
    except Exception:
        return ""


def enriquecer(urls: list[str], timeout_ms: int = 25_000) -> dict[str, dict]:
    """
    Para cada URL de página de bases (/archivos/SLUG) devuelve:
        { url: {"pdf_url": str|None, "vigencia": str|None, "requisitos": str|None} }
    Reutiliza un solo navegador headed. Best-effort, no lanza.
    """
    out: dict[str, dict] = {}
    if not urls:
        return out
    with sync_playwright() as p:
        browser = p.chromium.launch(
            headless=False,
            args=["--disable-blink-features=AutomationControlled", "--no-sandbox"],
        )
        ctx = browser.new_context(user_agent=UA, locale="es-CL")
        ctx.add_init_script(
            "Object.defineProperty(navigator,'webdriver',{get:()=>undefined})"
        )
        for url in urls:
            info = {"pdf_url": None, "vigencia": None, "requisitos": None}
            try:
                page = ctx.new_page()
                cap: dict = {}

                def on_resp(r, cap=cap):
                    if r.url.lower().endswith(".pdf") and "data" not in cap:
                        try:
                            body = r.body()
                            if body[:4] == b"%PDF":
                                cap["data"] = body
                                cap["url"] = r.url
                        except Exception:
                            pass

                page.on("response", on_resp)
                page.goto(url, wait_until="domcontentloaded", timeout=timeout_ms)
                # esperar a que el visor embebido descargue el PDF
                for _ in range(int(timeout_ms / 1000)):
                    if "data" in cap:
                        break
                    page.wait_for_timeout(1000)

                if "data" in cap:
                    info["pdf_url"] = cap.get("url")
                    txt = _pdf_a_texto(cap["data"])
                    if txt:
                        info["vigencia"] = extraer_vigencia(txt)
                        info["requisitos"] = extraer_dirigido(txt)
                page.close()
            except Exception:
                pass
            out[url] = info
    browser.close() if False else None  # browser cerrado al salir del with
    return out


if __name__ == "__main__":
    import json, sys
    pruebas = sys.argv[1:] or [
        "https://banco.santander.cl/informacion/resultados-de-concursos/archivos/bases-sorteo-experiencia-formula-1-select",
        "https://banco.santander.cl/informacion/resultados-de-concursos/archivos/sorteo-estadia-hotel-awa",
        "https://banco.santander.cl/informacion/resultados-de-concursos/archivos/bases-concurso-avant-premiere-la-odisea",
    ]
    res = enriquecer(pruebas)
    for u, info in res.items():
        print("\n===", u.rsplit("/", 1)[-1], "===")
        print("PDF:", (info["pdf_url"] or "—")[:90])
        print("VIGENCIA:", info["vigencia"] or "(no detectada)")
        print("REQUISITOS:", (info["requisitos"] or "(no detectado)")[:300])
