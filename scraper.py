"""
Scraper de concursos del Banco Santander Chile.

La pagina https://banco.santander.cl/informacion/resultados-de-concursos
carga los concursos por JavaScript (no estan en el HTML crudo) y el sitio
usa proteccion Akamai + reCAPTCHA, por eso se usa un navegador headless
(Playwright/Chromium) en vez de un simple requests.get().

Cada concurso es una tarjeta <a href=".../archivos/SLUG"> (bases descargables)
o <a href=".../detalles/SLUG"> (pagina de resultados/ganadores).
El SLUG final de la URL es un identificador unico y estable: lo usamos como
clave para detectar concursos nuevos.
"""
from __future__ import annotations

import re
from playwright.sync_api import sync_playwright

URL = "https://banco.santander.cl/informacion/resultados-de-concursos"

MESES = (
    "Enero|Febrero|Marzo|Abril|Mayo|Junio|Julio|Agosto|"
    "Septiembre|Octubre|Noviembre|Diciembre"
)
MES_NUM = {
    "enero": 1, "febrero": 2, "marzo": 3, "abril": 4, "mayo": 5, "junio": 6,
    "julio": 7, "agosto": 8, "septiembre": 9, "octubre": 10,
    "noviembre": 11, "diciembre": 12,
}

# "Titulo larguisimo Junio 2026 Ver bases" -> (titulo, mes, anio, accion)
_RE = re.compile(
    rf"^(.*?)\s+({MESES})\s+(\d{{4}})\s+(Ver bases|Ver ganadores|Ver anexo)\s*$",
    re.IGNORECASE | re.DOTALL,
)

USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"
)


def _parse_text(text: str) -> dict:
    """Separa 'titulo / mes / anio / tipo' del texto de la tarjeta."""
    text = re.sub(r"\s+", " ", text).strip()
    m = _RE.match(text)
    if not m:
        return {"titulo": text, "mes": None, "anio": None,
                "tipo": "bases", "orden": 0}
    titulo, mes, anio, accion = m.groups()
    anio = int(anio)
    mes_n = MES_NUM.get(mes.lower(), 0)
    tipo = "ganadores" if "ganadores" in accion.lower() else "bases"
    return {
        "titulo": titulo.strip(),
        "mes": mes.capitalize(),
        "anio": anio,
        "tipo": tipo,
        # clave de orden cronologico descendente
        "orden": anio * 100 + mes_n,
    }


def scrape(headless: bool = False) -> list[dict]:
    """Devuelve la lista de concursos publicados. Lanza si no encuentra ninguno.

    IMPORTANTE: el sitio de Santander bloquea Chromium en modo headless
    (Akamai devuelve 403 'Internet Connection Error'). Por eso se corre en
    modo *headed* (headless=False). En GitHub Actions/Linux sin pantalla,
    se ejecuta bajo un display virtual con `xvfb-run` (ver workflow).
    """
    concursos: dict[str, dict] = {}
    with sync_playwright() as p:
        browser = p.chromium.launch(
            headless=headless,
            args=[
                "--disable-blink-features=AutomationControlled",
                "--no-sandbox",
            ],
        )
        ctx = browser.new_context(
            user_agent=USER_AGENT,
            locale="es-CL",
            viewport={"width": 1440, "height": 900},
        )
        # Oculta navigator.webdriver (reduce deteccion de bot)
        ctx.add_init_script(
            "Object.defineProperty(navigator,'webdriver',{get:()=>undefined})"
        )
        page = ctx.new_page()
        page.goto(URL, wait_until="domcontentloaded", timeout=60_000)
        # Esperamos a que las tarjetas (cargadas por JS) aparezcan en el DOM.
        # state="attached": basta con que existan (estan en un carrusel y no
        # todas estan "visibles" a la vez).
        page.wait_for_selector(
            'a[href*="resultados-de-concursos/archivos"], '
            'a[href*="resultados-de-concursos/detalles"]',
            state="attached",
            timeout=45_000,
        )
        page.wait_for_timeout(2_500)

        anchors = page.query_selector_all(
            'a[href*="resultados-de-concursos/archivos"], '
            'a[href*="resultados-de-concursos/detalles"]'
        )
        for a in anchors:
            href = a.get_attribute("href") or ""
            if not href:
                continue
            href = href.split("#")[0].split("?")[0].rstrip("/")
            slug = href.rsplit("/", 1)[-1]
            text = a.inner_text() or ""
            img_el = a.query_selector("img")
            img = img_el.get_attribute("src") if img_el else None
            parsed = _parse_text(text)
            # de-dup por href; conservamos el primero
            concursos.setdefault(href, {
                "slug": slug,
                "url": href,
                "imagen": img,
                **parsed,
            })
        browser.close()

    items = list(concursos.values())
    if not items:
        raise RuntimeError(
            "0 concursos extraidos: posible bloqueo de bot (Akamai) o "
            "cambio en la estructura de la pagina."
        )
    items.sort(key=lambda c: c["orden"], reverse=True)
    return items


if __name__ == "__main__":
    import json
    data = scrape()
    print(f"{len(data)} concursos extraidos\n")
    for c in data[:15]:
        print(f"  [{c['tipo']:9}] {c['mes'] or '?'} {c['anio'] or '?'} - {c['titulo'][:60]}")
    print("\nJSON de muestra:")
    print(json.dumps(data[0], ensure_ascii=False, indent=2))
