# 🎯 Monitor de Concursos · Banco Santander Chile

Revisa **todos los días** la página oficial de concursos de Santander
([resultados-de-concursos](https://banco.santander.cl/informacion/resultados-de-concursos)),
detecta **concursos nuevos**, te manda un **mail** y publica un **dashboard**
en GitHub Pages.

## ¿Por qué es así de complicado el scraping?

La página no trae los concursos en el HTML: los carga por JavaScript y el
sitio está protegido con **Akamai Bot Manager + reCAPTCHA**. Chromium en modo
*headless* recibe un **403 "Internet Connection Error"**. La solución es correr
Chromium en modo **headed** bajo un **display virtual (`xvfb`)** dentro del
runner de GitHub Actions. Eso es exactamente lo que hace el workflow.

## Cómo funciona

| Pieza | Archivo | Qué hace |
|---|---|---|
| Scraper | `scraper.py` | Abre la página con Playwright (headed) y extrae los 99 concursos (título, mes/año, tipo, link a bases, imagen). |
| Monitor | `monitor.py` | Compara contra `data/concursos.json`, detecta nuevos, manda mail y regenera el dashboard. |
| Estado | `data/concursos.json` | Memoria: qué concursos ya se vieron (clave = slug de la URL). Se commitea solo. |
| Dashboard | `docs/index.html` | Página estática (GitHub Pages) con todos los concursos y badge **NUEVO**. |
| Automatización | `.github/workflows/monitor.yml` | Cron diario 13:00 UTC (~09:00 Chile) + botón manual. |

La **primera corrida** guarda la línea base (99 concursos) **sin** mandar mail.
A partir de ahí, solo avisa cuando aparece algo nuevo.

## Puesta en marcha (3 pasos)

### 1. Secrets para el email (Gmail)
En GitHub: **Settings → Secrets and variables → Actions → New repository secret**.
Crea estos 3:

| Secret | Valor |
|---|---|
| `SMTP_USER` | tu Gmail, ej. `tucorreo@gmail.com` |
| `SMTP_PASS` | una **contraseña de aplicación** de Gmail (16 caracteres, **no** tu clave normal) |
| `MAIL_TO` | a dónde llegan los avisos (puede ser el mismo Gmail; varios separados por coma) |

> **Contraseña de aplicación de Gmail:** requiere verificación en 2 pasos activada.
> Se genera en <https://myaccount.google.com/apppasswords>. Es la única forma
> segura de que un script mande mail por vos sin exponer tu clave real.

Si no configurás los secrets, el monitor igual corre y actualiza el dashboard,
solo que no manda mail.

### 2. Activar GitHub Pages
**Settings → Pages → Source: Deploy from a branch → Branch: `main` / carpeta `/docs`**.
El dashboard queda en `https://<usuario>.github.io/<repo>/`.

### 3. Listo
El workflow ya corre solo cada día. Para probarlo ahora:
**Actions → Monitor Concursos Santander → Run workflow**.

## Correr localmente

```bash
pip install -r requirements.txt
python -m playwright install chromium
python monitor.py          # en Windows/Mac abre una ventana de Chromium
```

(En Linux sin pantalla: `xvfb-run -a python monitor.py`.)

## Notas

- Cada concurso tiene **bases distintas** (algunos solo clientes Select, otros
  exigen usar la tarjeta, otros son por redes sociales). El monitor te avisa que
  existe; **leé siempre las bases oficiales** antes de participar.
- Si algún día el scraping devuelve 0 concursos, el job **falla a propósito** y
  **no** pisa el estado, para no perder la memoria por un bloqueo temporal.
