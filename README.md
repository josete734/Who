<div align="center">

# 🕵️ `who` — OSINT People Profiler

**Perfil digital exhaustivo de una persona a partir de fuentes abiertas.**

Agregador modular de 40+ colectores OSINT con síntesis final por IA
(Gemini / OpenAI / Ollama Cloud). Docker, streaming en vivo, panel web propio.

![license](https://img.shields.io/badge/license-MIT-green)
![docker](https://img.shields.io/badge/docker-compose-blue)
![status](https://img.shields.io/badge/status-self%20hosted-purple)
![python](https://img.shields.io/badge/python-3.12-yellow)

</div>

---

## ✨ Qué hace

Dado el mínimo input sobre una persona (nombre, email, teléfono, username, URL de
LinkedIn o dominio propio), `who` lanza **más de 40 colectores en paralelo** sobre
fuentes abiertas y te entrega:

- Un **dossier Markdown** estructurado (Síntesis IA con Gemini, ChatGPT u Ollama)
- La **lista cruda de hallazgos** con filtros por fuente/categoría/confianza
- Una **galería de perfiles sociales** con avatares y bios
- Una **galería de fotos** con todas las imágenes descubiertas
- Progreso en **streaming SSE en vivo**
- Export a **JSON, Markdown, PDF**

Todo corre en tu máquina. Sin subscripciones obligatorias (los LLMs son opcionales),
aunque algunos colectores mejoran con claves gratuitas opcionales.

---

## 🔭 Fuentes cubiertas

<details><summary><b>Username (6 colectores)</b></summary>

- `sherlock` — 400+ plataformas
- `maigret` — 3 000+ plataformas + extracción de metadatos
- `bluesky` — AT Protocol
- `mastodon` — WebFinger en 12 instancias populares
- `keybase` — perfil y proofs verificadas
- `reddit` — perfil público

</details>

<details><summary><b>Email (7 colectores)</b></summary>

- `holehe` — 120+ servicios (¿tienen cuenta con este email?)
- `gravatar` — perfil vinculado al MD5 del email
- `hibp_hint` — pista en agregadores de brechas
- `crtsh_email` — certificados TLS que mencionan el email
- `dns_mx` — MX records, SPF, DMARC, detección disposable
- `rapidapi_mailcheck` — validación por dominio (opcional)
- GitHub commit-email search

</details>

<details><summary><b>Teléfono (5 colectores)</b></summary>

- `phoneinfoga` — parsing libphonenumber + dorks multi-motor
- `wa_me` — ¿existe en WhatsApp? (pasivo)
- `rapidapi_whatsapp` — perfil WhatsApp (Business Info, foto, about)
- `rapidapi_phone_validate` — validación teléfono con carrier
- `rapidapi_socialscan` — ¿qué redes tienen cuenta con este número?

</details>

<details><summary><b>Nombre y registros oficiales (España/UE)</b></summary>

- `borme` — Boletín Oficial del Registro Mercantil
- `boe` — Boletín Oficial del Estado (oposiciones, sanciones, edictos)
- `searxng_dorks` — 60+ dorks estructurados en Google/Bing/DDG/Yandex/Brave/Mojeek
- `wikidata` + `wikipedia` — personajes notables
- `orcid` — investigadores académicos
- `stackexchange` — usuarios de Stack Overflow
- `ahmia` — índice de servicios .onion

</details>

<details><summary><b>Huella técnica</b></summary>

- `github` — perfil, eventos, commits, emails reales, lenguajes dominantes, zona horaria inferida
- `gitlab` — perfil público
- `npm` / `pypi` — paquetes publicados
- `dockerhub` — perfil Docker Hub
- `crtsh_domain` — Certificate Transparency (subdominios)
- `wayback` — snapshots archive.org
- `urlscan` — escaneos previos de urlscan.io
- `leakix` — servicios expuestos e indicadores de leak
- `shodan_internetdb` — InternetDB (gratis, sin key) + Shodan premium opcional
- `domain_photon` — crawler ligero del dominio propio (emails, teléfonos, socials)

</details>

<details><summary><b>Redes sociales y media</b></summary>

- `instagram_public` — perfil público sin cookie
- `tiktok_public` — validación estricta por `uniqueId`
- `telegram_public` — presencia en t.me con validación `og:url`
- `rapidapi_reverse_image` — búsqueda inversa de imágenes (opcional)
- `rapidapi_guarddoxx` — enumeración en GuardDoxx (opcional)

</details>

<details><summary><b>IA-guided (el más diferencial)</b></summary>

- `gemini_websearch` — Gemini 2.5 Pro con **Google Search grounding**: navega activamente
  la web, lee contexto, distingue homónimos y devuelve candidatos estructurados con
  explicación del match.

</details>

---

## 🖼 Capturas

| Formulario | Caso (Síntesis) | Perfiles | Fotos |
|---|---|---|---|
| _(pega screenshots después del primer arranque)_ | | | |

---

## ⚡ Despliegue

Prerequisitos: un host con Docker + Docker Compose.

```bash
git clone https://github.com/TU_USUARIO/osint-tool who && cd who
cp .env.example .env        # edita las API keys (todas opcionales salvo una LLM si quieres síntesis)
docker compose up -d --build
```

Abre `http://localhost:8000` (o el dominio con tu reverse proxy).

### Detrás de un dominio con HTTPS

El proyecto incluye un ejemplo de Caddyfile. Para un servidor con nginx existente:

```nginx
server {
    listen 443 ssl http2;
    server_name who.ejemplo.com;
    ssl_certificate     /path/to/fullchain.pem;
    ssl_certificate_key /path/to/privkey.pem;
    client_max_body_size 20M;

    location / {
        proxy_pass http://127.0.0.1:8765;
        proxy_http_version 1.1;
        proxy_set_header Host $host;
        proxy_set_header X-Forwarded-Proto https;
    }
    location ~ ^/api/cases/[^/]+/stream$ {
        proxy_pass http://127.0.0.1:8765;
        proxy_buffering off;
        proxy_read_timeout 3600s;
        add_header X-Accel-Buffering no;
    }
}
```

Expón el backend solo en `127.0.0.1:8765` (ya configurado) y deja que nginx haga de gateway.

---

## ⚙️ Panel de Ajustes

Navega a `/settings` para editar todas las API keys desde el navegador. Se guardan en
Postgres y tienen prioridad sobre las del `.env`. Claves soportadas:

| Grupo | Keys |
|---|---|
| **LLM** | `GEMINI_API_KEY`, `OPENAI_API_KEY`, `OLLAMA_API_KEY`, `ANTHROPIC_API_KEY`, `DEFAULT_LLM`, y modelo por proveedor |
| **Colectores** | `GITHUB_TOKEN`, `RAPIDAPI_KEY`, `SHODAN_API_KEY`, `URLSCAN_API_KEY`, `LEAKIX_API_KEY`, `HUNTER_API_KEY`, `NUMVERIFY_API_KEY`, credenciales Reddit, Companies House |

Los secretos se muestran enmascarados (`abcd…wxyz`). Para rotarlos, escribe el valor nuevo completo.

---

## 🧠 LLMs soportados

| Proveedor | Modelo por defecto | Notas |
|---|---|---|
| **Gemini** | `gemini-2.5-pro` | Incluye web grounding en `gemini_websearch` |
| **OpenAI** | `gpt-4o-mini` | `ChatGPT` clásico vía Chat Completions |
| **Ollama Cloud** | `gpt-oss:120b` | Modelos alojados en ollama.com |
| **Anthropic** | `claude-sonnet-4-5` | Opcional, fuera del dropdown por defecto |

---

## 🏗 Arquitectura

```
Navegador ──► nginx / Caddy ──► FastAPI (uvicorn) ──► Postgres  (findings, cases, settings)
                                       │          ──► Redis     (cola Arq, event bus SSE)
                                       │          ──► SearXNG   (meta-buscador self-hosted)
                                       └──────────► Arq worker  ──► 40+ colectores async
```

- **Backend**: FastAPI + `asyncio` + `httpx` + Pydantic v2
- **DB**: PostgreSQL 16 (JSONB + pg_trgm)
- **Cola**: Arq (Redis async-native)
- **Streaming**: Server-Sent Events con Redis Pub/Sub
- **UI**: HTML + Tailwind CDN + JS vanilla (sin build step)
- **Síntesis**: prompt con jerarquía Verificado/Declarado/Inferido para evitar falsos positivos

---

## 🧩 Inspirado / aprovechado de

- [jivoi/awesome-osint](https://github.com/jivoi/awesome-osint) — catálogo de fuentes
- [laramies/theHarvester](https://github.com/laramies/theHarvester) — enumeración de emails
- [s0md3v/Photon](https://github.com/s0md3v/Photon) — crawler ligero (`domain_photon`)
- [Datalux/Osintgram](https://github.com/Datalux/Osintgram) — Instagram OSINT
- [sherlock-project/sherlock](https://github.com/sherlock-project/sherlock) — username enumeration
- [soxoj/maigret](https://github.com/soxoj/maigret) — sucesor mejorado de Sherlock
- [megadose/holehe](https://github.com/megadose/holehe) — email → servicios
- [sundowndev/phoneinfoga](https://github.com/sundowndev/phoneinfoga) — teléfono + dorks
- [DedSecInside/TorBot](https://github.com/DedSecInside/TorBot) — inspiración para `ahmia`
- [lockfale/OSINT-Framework](https://github.com/lockfale/OSINT-Framework) — árbol de categorías
- [sinwindie/OSINT](https://github.com/sinwindie/OSINT) — recursos por categoría
- [cipher387/osint_stuff_tool_collection](https://github.com/cipher387/osint_stuff_tool_collection) — APIs freemium

---

## 🧱 Escribir un colector nuevo

Cualquier archivo en `backend/app/collectors/` con esta pinta queda auto-registrado:

```python
from collections.abc import AsyncIterator
from app.collectors.base import Collector, Finding, register
from app.schemas import SearchInput

@register
class MiColector(Collector):
    name = "mi_fuente"
    category = "email"          # username/email/phone/name/social/...
    needs = ("email",)           # campos que activan este colector
    timeout_seconds = 30

    async def run(self, input: SearchInput) -> AsyncIterator[Finding]:
        # hacer la petición
        yield Finding(
            collector=self.name,
            category="email",
            entity_type="ServiceAccount",
            title="Hello",
            url="https://...",
            confidence=0.8,
            payload={"foo": "bar"},
        )
```

Importarlo en `backend/app/collectors/__init__.py` y ya aparece en `/api/health`.

---

## 🔌 API REST

Todas las rutas devuelven JSON (excepto `/settings` y `/case/...` que son HTML).

```bash
# Listar colectores registrados
curl http://localhost:8000/api/health

# Crear caso
curl -X POST http://localhost:8000/api/cases -H 'Content-Type: application/json' -d '{
  "title": "test", "llm": "gemini",
  "input": {"full_name": "Linus Torvalds", "email": "torvalds@linux-foundation.org"}
}'

# Stream de eventos
curl -N http://localhost:8000/api/cases/<id>/stream

# Hallazgos
curl http://localhost:8000/api/cases/<id>/findings

# Perfiles sociales consolidados
curl http://localhost:8000/api/cases/<id>/profiles

# Fotos extraídas
curl http://localhost:8000/api/cases/<id>/photos

# Ajustes
curl http://localhost:8000/api/settings
curl -X POST http://localhost:8000/api/settings -H 'Content-Type: application/json' \
    -d '{"values": {"GEMINI_API_KEY": "...", "DEFAULT_LLM": "gemini"}}'
```

---

## ⚖️ Uso responsable

Esta herramienta **sólo consulta fuentes abiertas y públicas**. Aun así:

- **Europa / España**: el RGPD aplica aunque los datos sean públicos. Documenta
  tu base legal (interés legítimo, investigación, periodismo…). Esta app guarda
  un audit log con el campo `legal_basis` de cada caso.
- **No la uses** para acoso, stalking, ingeniería social maliciosa, o discriminación.
  Los terceros tienen derecho a saber si los investigas (Art. 14 RGPD) salvo
  excepciones estrictas.
- **Tus claves son tuyas**. No commitees `.env` al repositorio público.

Es responsabilidad del usuario final cumplir la legislación aplicable en su
jurisdicción. El autor no se hace responsable del uso que se dé a la herramienta.

---

## 🛣 Roadmap

- [ ] Neo4j + grafo Sigma.js de entidades
- [ ] Timeline cronológica (`vis-timeline`)
- [ ] Mapa de hallazgos geolocalizados (MapLibre GL)
- [ ] Reverse image search con embeddings locales (CLIP)
- [ ] Tor container para dark-web deep scraping
- [ ] Plugins de cuenta Telethon / Instaloader con cuentas burner

---

## 📝 Licencia

MIT. Haz con esto lo que quieras, pero no me hagas responsable.

---

<div align="center">

_Hecho con rabia análoga y docker._

</div>
