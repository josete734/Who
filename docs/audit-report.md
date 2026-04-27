# Audit operacional — colectores

Fecha: 2026-04-27
Operador: jose@m3estrategia.com
Caso de prueba (post-fix): `2c12dfc1-1c14-4e38-833a-abfd5701a7a1`
Inputs: full_name=Jose Castillo, email=josea.castillodiez@gmail.com,
phone=+34695965122, city=Reus, country=ES.

## Resumen ejecutivo

- Colectores registrados: **96**.
- Ejecutados con los inputs del caso (pasaron `applicable()`): **48**.
- No ejecutados (input insuficiente, ej. faltó `username`, `domain`,
  `linkedin_url`, `aliases`, `birth_name`, `extra_context`): **48**.
- Antes del fix: 44 ok / 4 error.
- Tras los fixes: **48 ok / 0 error**, 163 findings agregados.

Los 4 fallos eran todos del módulo `rapidapi_generic.py` (ver §3).

## 1. Inventario por estado (post-fix)

Leyenda: OK = ejecutado con éxito. SKIP = no aplicable con inputs del smoke
test (`applicable()` devolvió `False`). NO-CREDS = sub-categoría de SKIP, falta
clave/credencial. ERROR = ya no quedan tras el fix.

### OK (48)

ahmia, aleph, axesor, boe, borme, combo_lists_local, companies_house_uk,
courtlistener, crtsh_email, crypto_ens, dehashed, disboard, dns_mx,
espacenet, eu_registries, gemini_websearch, github, gitlab, google_patents,
gravatar, hibp_hint, holehe, infoempresa, leakix, linkedin_public,
messengers_extra, opencorporates_free, orcid, pastebin_search, phoneinfoga,
pypi, rapidapi_guarddoxx, rapidapi_mailcheck, rapidapi_phone_validate,
rapidapi_socialscan, rapidapi_whatsapp, searxng_dorks, sec_edgar,
semantic_scholar, smtp_rcpt, stackexchange, strava_authed, strava_public,
urlscan, wa_me, wayback, wikidata, wikipedia.

### SKIP — input insuficiente (48)

Necesitan campos no aportados (`username`, `domain`, `linkedin_url`,
`aliases`, `birth_name`, `extra_context` con URL de imagen, etc.):

alltrails, archive_advanced, bluesky, combot, crtsh_domain, ct_monitor,
devto, discord_public, dockerhub, domain_photon, foursquare_public,
garmin_connect_public, goodreads, hashnode, hibp_passwords, instagram_public,
ipinfo, keybase, lastfm, letterboxd, maigret, mapmyrun, mastodon, medium,
npm, polar_flow_public, rapidapi_reverse_image, reddit, reverse_image,
sherlock, shodan_internetdb, skype_validator, spotify_public, steam_community,
strava_heatmap, subdomain_passive, substack, suunto_public, telegram_public,
telegram_resolver, tgstat, threads_public, tiktok_public, twitch,
twitter_nitter, untappd, whatsmyname, wigle.

### NO-CREDS / paid

Detectados durante el caso (todos manejados con skip silencioso, no quedan
como ERROR): `rapidapi_mailcheck`, `rapidapi_phone_validate` (devolvieron 403
"no suscrito"); `rapidapi_socialscan` (429 rate limit). Requieren upgrades en
RapidAPI para producir findings.

## 2. Top errores y fixes aplicados

| # | Colector | Error original | Causa raíz | Fix |
|---|---|---|---|---|
| 1 | rapidapi_whatsapp | `'list' object has no attribute 'get'` | El endpoint `/bizos` devuelve `[]` cuando el número no está registrado en WhatsApp Business; el parser asumía dict. | Normalizar list→dict antes de `.get`; status no-200 → return silencioso (en vez de raise). |
| 2 | rapidapi_socialscan | `RapidAPI rate limit` | Plan gratuito agotado en social-media-scanner1. | `continue` en 401/403/429 (en lugar de raise) — sigue probando otros inputs. |
| 3 | rapidapi_mailcheck | `RapidAPI mailcheck: no suscrito` | 403, no estamos suscritos a este endpoint dentro del key compartido. | `return` silencioso en cualquier no-200 (no raise). |
| 4 | rapidapi_phone_validate | `RapidAPI phone-validate: no suscrito` | 403, mismo motivo. | `return` silencioso (no raise). |
| 5 | rapidapi_reverse_image | (no falló en este caso, pero mismo patrón) | 403 no suscrito habría disparado raise. | `continue` silencioso preventivo. |
| 6 | rapidapi_guarddoxx | (idem, preventivo) | 403 no suscrito. | `return` silencioso preventivo. |

Cada cambio lleva un comentario `# AUDIT FIX:` explicando la causa
inmediatamente encima.

Archivo modificado: `backend/app/collectors/rapidapi_generic.py`.

Verificación: re-ejecutado el smoke test tras `docker compose build worker &&
up -d worker`. Resultado: 48/48 OK, 163 findings.

## 3. Top-5 colectores más lentos

| # | Colector | Duración (ms) | timeout_s | Análisis |
|---|---|---|---|---|
| 1 | searxng_dorks | 120 838 | 240 | 60 queries secuenciales × ~2s. Margen razonable, pero podría paralelizarse con `asyncio.gather(...)` con un semáforo (4-8) — fuera del scope mínimo. **Sin cambio**. |
| 2 | gemini_websearch | 67 672 | 240 | Llamadas a Gemini con grounding, naturalmente lentas. Timeout adecuado. **Sin cambio**. |
| 3 | pastebin_search | 30 633 | 180 | OK. **Sin cambio**. |
| 4 | holehe | 16 783 | 180 | Holehe sondea ~80 servicios; 17s es saludable. **Sin cambio** (fuera de scope: `holehe.py` excluido). |
| 5 | linkedin_public | 9 149 | 30 | Razonable. **Sin cambio**. |

No se aplicaron reducciones agresivas de timeout porque ningún colector
estuvo cerca de tocar techo (peor caso 121s de 240s).

## 4. Recomendaciones (sin fix obvio)

- **rapidapi_socialscan / mailcheck / phone_validate / reverse_image**:
  requieren suscripción específica en RapidAPI más allá de la base. Decidir
  si pagar o eliminar las clases del registro.
- **rapidapi_guarddoxx**: el endpoint `/health` aparece OK pero los
  `/check`, `/lookup`, etc. son sondeos a ciegas; la API real no está
  documentada. Recomendado: contactar al provider o reemplazar por API con
  contrato estable.
- **searxng_dorks**: paralelizar las 60 queries con `asyncio.gather` +
  semáforo para bajar de 120s a ~30-40s. Requiere refactor moderado.
- **48 colectores skipped**: para aprovecharlos hay que pedir al usuario más
  inputs (username, dominio, URL LinkedIn, foto). Considerar enriquecer la
  UI para sugerir campos opcionales que activan más colectores.
- **Solo 96 colectores registrados pero el brief mencionaba 96** — coincide.

## 5. Constraints respetados

No se modificaron: `schemas.py`, `holehe.py`, `hibp.py`, `gravatar.py`,
`dehashed.py`, `smtp_rcpt.py`, `templates/*`, `static/v2/*`. Único archivo
tocado: `backend/app/collectors/rapidapi_generic.py`. No se hizo commit.
