"""
Monitor diario de concursos del Banco Santander Chile.

Flujo:
  1. Scrapea la pagina oficial de concursos (scraper.scrape).
  2. Compara contra el estado guardado en data/concursos.json.
  3. Si hay concursos NUEVOS (slugs no vistos antes) -> manda un mail.
  4. Regenera el dashboard estatico docs/index.html (GitHub Pages).
  5. Actualiza el estado.

El email y el commit del estado los maneja el workflow de GitHub Actions.
Las credenciales SMTP llegan por variables de entorno (GitHub Secrets):
  SMTP_USER, SMTP_PASS, MAIL_TO  (opcionales: si faltan, no manda mail).

Salida: imprime un resumen y escribe los archivos. Exit code != 0 solo si
el scraping falla (0 concursos), para que el workflow lo marque en rojo.
"""
from __future__ import annotations

import json
import os
import smtplib
import ssl
import sys
from datetime import datetime, timezone, timedelta
from email.message import EmailMessage
from pathlib import Path

from scraper import scrape
import bases_info

ROOT = Path(__file__).resolve().parent
STATE_FILE = ROOT / "data" / "concursos.json"
DASHBOARD = ROOT / "docs" / "index.html"

CHILE_TZ = timezone(timedelta(hours=-4))  # America/Santiago (referencial)


def ahora_chile() -> datetime:
    return datetime.now(CHILE_TZ)


# --------------------------------------------------------------------------- #
#  Estado                                                                      #
# --------------------------------------------------------------------------- #
def cargar_estado() -> dict:
    if STATE_FILE.exists():
        # utf-8-sig tolera un BOM si algun editor/PowerShell lo agrego.
        with STATE_FILE.open(encoding="utf-8-sig") as f:
            return json.load(f)
    return {"first_run": True, "concursos": {}}


def guardar_estado(estado: dict) -> None:
    STATE_FILE.parent.mkdir(parents=True, exist_ok=True)
    with STATE_FILE.open("w", encoding="utf-8") as f:
        json.dump(estado, f, ensure_ascii=False, indent=2)


# --------------------------------------------------------------------------- #
#  Email                                                                       #
# --------------------------------------------------------------------------- #
def enviar_mail(nuevos: list[dict]) -> bool:
    user = os.environ.get("SMTP_USER")
    pwd = os.environ.get("SMTP_PASS")
    to = os.environ.get("MAIL_TO") or user
    if not (user and pwd and to):
        print("[mail] SMTP_USER/SMTP_PASS/MAIL_TO no configurados -> no se envia mail")
        return False

    destinatarios = [d.strip() for d in to.split(",") if d.strip()]
    n = len(nuevos)
    asunto = f"🎉 {n} concurso{'s' if n != 1 else ''} nuevo{'s' if n != 1 else ''} en Santander"

    filas = ""
    for c in nuevos:
        badge = "🏆 Bases / Participar" if c["tipo"] == "bases" else "📋 Resultados"
        fecha = f"{c.get('mes') or ''} {c.get('anio') or ''}".strip()
        vig = c.get("vigencia")
        req = c.get("requisitos")
        extra = ""
        if vig:
            extra += (f'<div style="margin-top:6px;font-size:13px;">'
                      f'<strong>📅 Vigencia:</strong> {vig}</div>')
        if req:
            extra += (f'<div style="margin-top:4px;font-size:13px;color:#444;">'
                      f'<strong>👤 Quiénes participan:</strong> {req}</div>')
        filas += f"""
        <tr>
          <td style="padding:12px 10px;border-bottom:1px solid #eee;vertical-align:top;">
            <strong style="font-size:15px;">{c['titulo']}</strong><br>
            <span style="color:#888;font-size:12px;">{fecha} · {badge}</span>
            {extra}
            <div style="margin-top:6px;">
              <a href="{c['url']}" style="color:#ec0000;font-weight:bold;">Ver bases completas / participar →</a>
            </div>
          </td>
        </tr>"""

    html = f"""\
    <div style="font-family:Arial,Helvetica,sans-serif;max-width:600px;margin:auto;">
      <div style="background:#ec0000;color:#fff;padding:18px 20px;border-radius:8px 8px 0 0;">
        <h2 style="margin:0;">Nuevos concursos Santander</h2>
        <p style="margin:4px 0 0;font-size:13px;">Detectados el {ahora_chile():%d-%m-%Y %H:%M} (Chile)</p>
      </div>
      <table style="width:100%;border-collapse:collapse;background:#fff;">{filas}</table>
      <p style="font-size:12px;color:#888;margin-top:16px;">
        Monitor automatico. Fuente oficial:
        <a href="https://banco.santander.cl/informacion/resultados-de-concursos">banco.santander.cl/informacion/resultados-de-concursos</a><br>
        Recorda leer las bases de cada concurso: requisitos y fechas de cierre varian.
      </p>
    </div>"""

    def _txt(c: dict) -> str:
        s = f"- {c['titulo']} ({c.get('mes')} {c.get('anio')})"
        if c.get("vigencia"):
            s += f"\n  Vigencia: {c['vigencia']}"
        if c.get("requisitos"):
            s += f"\n  Quienes participan: {c['requisitos']}"
        s += f"\n  Bases: {c['url']}"
        return s
    texto = "Nuevos concursos Santander:\n\n" + "\n\n".join(_txt(c) for c in nuevos)

    msg = EmailMessage()
    msg["Subject"] = asunto
    msg["From"] = user
    msg["To"] = ", ".join(destinatarios)
    msg.set_content(texto)
    msg.add_alternative(html, subtype="html")

    ctx = ssl.create_default_context()
    with smtplib.SMTP_SSL("smtp.gmail.com", 465, context=ctx) as s:
        s.login(user, pwd)
        s.send_message(msg)
    print(f"[mail] enviado a {', '.join(destinatarios)} ({n} nuevos)")
    return True


# --------------------------------------------------------------------------- #
#  Dashboard                                                                   #
# --------------------------------------------------------------------------- #
def card_html(c: dict, nuevo: bool, vigencia: str | None = None) -> str:
    tipo = c["tipo"]
    badge = ('<span class="badge bases">Participar</span>' if tipo == "bases"
             else '<span class="badge gan">Resultados</span>')
    nuevo_badge = '<span class="badge nuevo">NUEVO</span>' if nuevo else ""
    fecha = f"{c.get('mes') or ''} {c.get('anio') or ''}".strip()
    img = c.get("imagen") or ""
    # referrerpolicy=no-referrer: el CDN de Santander bloquea el hotlink si llega
    # un referrer de otro dominio (github.io). Sin referrer, sirve la imagen.
    img_html = (f'<img src="{img}" alt="" loading="lazy" referrerpolicy="no-referrer">'
                if img else '<div class="noimg"></div>')
    vig_html = (f'<p class="vig" title="{vigencia}">📅 {vigencia[:90]}</p>'
                if vigencia else "")
    return f"""
      <a class="card" href="{c['url']}" target="_blank" rel="noopener">
        {img_html}
        <div class="body">
          <div class="badges">{badge}{nuevo_badge}</div>
          <h3>{c['titulo']}</h3>
          <p class="fecha">{fecha}</p>
          {vig_html}
        </div>
      </a>"""


def generar_dashboard(concursos: list[dict], estado: dict) -> None:
    DASHBOARD.parent.mkdir(parents=True, exist_ok=True)
    hace_7d = (ahora_chile() - timedelta(days=7)).isoformat()

    def es_nuevo(c: dict) -> bool:
        fs = estado["concursos"].get(c["url"], {}).get("first_seen", "")
        return fs >= hace_7d

    bases = [c for c in concursos if c["tipo"] == "bases"]
    ganadores = [c for c in concursos if c["tipo"] == "ganadores"]

    def vig_de(c: dict) -> str | None:
        return estado["concursos"].get(c["url"], {}).get("vigencia")

    cards_bases = "".join(card_html(c, es_nuevo(c), vig_de(c)) for c in bases)
    cards_gan = "".join(card_html(c, es_nuevo(c)) for c in ganadores)
    n_nuevos = sum(1 for c in concursos if es_nuevo(c))

    html = f"""<!DOCTYPE html>
<html lang="es">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Concursos Santander · Monitor</title>
<style>
  :root {{ --rojo:#ec0000; --bg:#f4f5f7; --txt:#222; }}
  * {{ box-sizing:border-box; }}
  body {{ margin:0; font-family:'Segoe UI',Arial,sans-serif; background:var(--bg); color:var(--txt); }}
  header {{ background:var(--rojo); color:#fff; padding:28px 20px; }}
  header h1 {{ margin:0; font-size:24px; }}
  header p {{ margin:6px 0 0; font-size:14px; opacity:.9; }}
  .wrap {{ max-width:1100px; margin:0 auto; padding:24px 20px 60px; }}
  .stats {{ display:flex; gap:14px; flex-wrap:wrap; margin:0 0 24px; }}
  .stat {{ background:#fff; border-radius:10px; padding:14px 20px; box-shadow:0 1px 4px rgba(0,0,0,.08); }}
  .stat b {{ font-size:26px; color:var(--rojo); display:block; }}
  .stat span {{ font-size:12px; color:#777; }}
  h2 {{ font-size:18px; margin:28px 0 14px; border-left:4px solid var(--rojo); padding-left:10px; }}
  .grid {{ display:grid; grid-template-columns:repeat(auto-fill,minmax(230px,1fr)); gap:16px; }}
  .card {{ background:#fff; border-radius:12px; overflow:hidden; text-decoration:none; color:inherit;
           box-shadow:0 1px 4px rgba(0,0,0,.08); transition:transform .12s, box-shadow .12s; display:flex; flex-direction:column; }}
  .card:hover {{ transform:translateY(-3px); box-shadow:0 6px 18px rgba(0,0,0,.14); }}
  .card img, .card .noimg {{ width:100%; aspect-ratio:1/1; object-fit:cover; background:#eee; }}
  .card .body {{ padding:12px 14px 16px; }}
  .card h3 {{ font-size:14px; margin:6px 0 4px; line-height:1.3; }}
  .fecha {{ font-size:12px; color:#888; margin:0; }}
  .vig {{ font-size:11px; color:#16a34a; margin:6px 0 0; line-height:1.3;
          display:-webkit-box; -webkit-line-clamp:2; -webkit-box-orient:vertical; overflow:hidden; }}
  .badges {{ display:flex; gap:6px; flex-wrap:wrap; }}
  .badge {{ font-size:10px; font-weight:700; padding:3px 8px; border-radius:20px; text-transform:uppercase; letter-spacing:.3px; }}
  .badge.bases {{ background:#ffe5e5; color:var(--rojo); }}
  .badge.gan {{ background:#e8e8e8; color:#666; }}
  .badge.nuevo {{ background:#16a34a; color:#fff; }}
  footer {{ text-align:center; font-size:12px; color:#999; padding:30px 20px; }}
  footer a {{ color:var(--rojo); }}
</style>
</head>
<body>
<header>
  <div class="wrap" style="padding-bottom:0;">
    <h1>🎯 Monitor de Concursos · Santander Chile</h1>
    <p>Actualizado: {ahora_chile():%d-%m-%Y %H:%M} (hora Chile) · Fuente oficial actualizada cada dia</p>
  </div>
</header>
<div class="wrap">
  <div class="stats">
    <div class="stat"><b>{len(bases)}</b><span>con bases (participables)</span></div>
    <div class="stat"><b>{len(ganadores)}</b><span>resultados/ganadores</span></div>
    <div class="stat"><b>{n_nuevos}</b><span>nuevos (ultimos 7 dias)</span></div>
  </div>

  <h2>🏆 Concursos con bases — para participar</h2>
  <div class="grid">{cards_bases}</div>

  <h2>📋 Resultados y ganadores</h2>
  <div class="grid">{cards_gan}</div>
</div>
<footer>
  Monitor automatico no oficial · Datos desde
  <a href="https://banco.santander.cl/informacion/resultados-de-concursos" target="_blank">banco.santander.cl</a><br>
  Verifica siempre las bases oficiales (requisitos y fechas de cierre) antes de participar.
</footer>
</body>
</html>"""
    DASHBOARD.write_text(html, encoding="utf-8")
    print(f"[dashboard] generado: {DASHBOARD} ({len(concursos)} concursos)")


# --------------------------------------------------------------------------- #
#  Main                                                                        #
# --------------------------------------------------------------------------- #
def main() -> int:
    try:
        concursos = scrape()
    except Exception as e:  # scraping fallido -> no tocar estado, marcar error
        print(f"[ERROR] scraping fallido: {e}", file=sys.stderr)
        return 1

    estado = cargar_estado()
    previos = estado.get("concursos", {})
    first_run = estado.get("first_run", True) or not previos

    ahora_iso = ahora_chile().isoformat()
    # En la primera corrida (baseline) backdatamos first_seen para que los 99
    # concursos existentes NO aparezcan como "NUEVO" en el dashboard.
    first_seen_val = "2000-01-01T00:00:00-04:00" if first_run else ahora_iso
    nuevos = []
    for c in concursos:
        if c["url"] not in previos:
            nuevos.append(c)
            previos[c["url"]] = {
                "titulo": c["titulo"], "tipo": c["tipo"],
                "mes": c.get("mes"), "anio": c.get("anio"),
                "first_seen": first_seen_val,
            }

    # Enriquecer los concursos NUEVOS con bases (vigencia + requisitos desde el
    # PDF). Solo si no es la primera corrida (en baseline no mailamos) y solo
    # los de tipo "bases" (los de ganadores no tienen requisitos que avisar).
    if nuevos and not first_run:
        a_enriquecer = [c for c in nuevos if c["tipo"] == "bases"]
        if a_enriquecer:
            print(f"[bases] enriqueciendo {len(a_enriquecer)} concurso(s) con sus PDFs...")
            try:
                info = bases_info.enriquecer([c["url"] for c in a_enriquecer])
            except Exception as e:
                print(f"[bases] error al enriquecer: {e}", file=sys.stderr)
                info = {}
            for c in a_enriquecer:
                datos = info.get(c["url"], {})
                c["vigencia"] = datos.get("vigencia")
                c["requisitos"] = datos.get("requisitos")
                c["pdf_url"] = datos.get("pdf_url")
                # persistir en el estado para el dashboard
                previos[c["url"]].update({
                    "vigencia": c["vigencia"],
                    "requisitos": c["requisitos"],
                })

    estado["concursos"] = previos
    estado["first_run"] = False
    estado["last_check"] = ahora_iso
    estado["total"] = len(concursos)

    generar_dashboard(concursos, estado)
    guardar_estado(estado)

    print(f"[resumen] total={len(concursos)} nuevos={len(nuevos)} first_run={first_run}")
    for c in nuevos:
        print(f"   + {c['titulo']} ({c.get('mes')} {c.get('anio')})")

    if nuevos and not first_run:
        try:
            enviar_mail(nuevos)
        except Exception as e:
            print(f"[mail] error al enviar: {e}", file=sys.stderr)
    elif first_run:
        print("[mail] primera corrida: se guarda la linea base, no se notifica.")
    else:
        print("[mail] sin concursos nuevos.")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
