# Collectors

The platform ships with **56 collectors**, auto-discovered via the registry decorator in `backend/app/collectors/base.py`.

Each row is generated from the source files. Edit the collector and re-run the docs build to refresh.

| Name | Category | Needs (any of) | Source | Description |
|---|---|---|---|---|
| `orcid` | academic | `full_name`, `birth_name`, `aliases`, `email` | `orcid.py` :: `OrcidCollector` | ORCID collector: search academic researchers by name or email. |
| `gemini_websearch` | ai_research | `full_name`, `birth_name`, `aliases`, `email`, `username`, `phone`, `linkedin_url`, `domain` | `gemini_websearch.py` :: `GeminiWebResearchCollector` | Gemini-powered web research collector. |
| `archive_advanced` | archive | `domain` | `archive_advanced.py` :: `ArchiveAdvancedCollector` | Advanced archive enumeration: Wayback CDX wildcard + archive.today + Common Crawl. |
| `wayback` | archive | `domain`, `linkedin_url`, `username`, `email` | `wayback.py` :: `WaybackCollector` | Wayback Machine CDX API lookup. |
| `combo_lists_local` | breach | `email`, `username` | `combo_lists.py` :: `ComboListLocal` | Search the local combo-list FTS/index for known exposures. |
| `dehashed` | breach | `email`, `username`, `phone`, `domain` | `dehashed.py` :: `DehashedCollector` | Dehashed v2 breach search collector (paid API, optional). |
| `dockerhub` | code | `username` | `dockerhub.py` :: `DockerHubCollector` | Docker Hub public user lookup. |
| `github` | code | `username`, `email`, `full_name`, `birth_name`, `aliases` | `github.py` :: `GitHubCollector` | GitHub collector: use the public REST API (5000 req/h with personal token). |
| `gitlab` | code | `username`, `email` | `gitlab.py` :: `GitLabCollector` | GitLab public user search (no token required for limited queries). |
| `npm` | code | `username` | `npm_pypi.py` :: `NpmAuthorCollector` | npm and PyPI author/maintainer lookups. |
| `pypi` | code | `username`, `email` | `npm_pypi.py` :: `PypiAuthorCollector` | npm and PyPI author/maintainer lookups. |
| `stackexchange` | code | `full_name`, `birth_name`, `aliases`, `username` | `stackexchange.py` :: `StackExchangeCollector` | Stack Exchange (Stack Overflow) user search by username or display name. |
| `crypto_ens` | crypto | `username`, `full_name` | `crypto_ens.py` :: `CryptoENSCollector` | Crypto / ENS collector. |
| `ahmia` | dark_web | `full_name`, `birth_name`, `aliases`, `email`, `username`, `phone` | `ahmia.py` :: `AhmiaCollector` | Ahmia.fi — search the Tor (.onion) public index from the clearnet. |
| `crtsh_domain` | domain | `domain` | `crtsh.py` :: `CrtShDomainCollector` | crt.sh — Certificate Transparency lookup. |
| `ct_monitor` | domain | `domain` | `ct_monitor.py` :: `CTMonitorCollector` | Certificate Transparency monitor — collector mode. |
| `domain_photon` | domain | `domain` | `domain_photon.py` :: `DomainPhotonCollector` | Lightweight site crawler inspired by Photon (s0md3v). |
| `urlscan` | domain | `domain`, `email` | `urlscan.py` :: `URLScanCollector` | urlscan.io public search — free tier without key; higher limits with key. |
| `searxng_dorks` | dork | `full_name`, `birth_name`, `aliases`, `email`, `username`, `phone`, `linkedin_url` | `searxng_dorks.py` :: `SearXNGDorksCollector` | SearXNG-backed dorks. |
| `rapidapi_guarddoxx` | doxx | `email`, `phone`, `full_name`, `birth_name`, `aliases`, `username` | `rapidapi_generic.py` :: `RapidAPIGuardDoxx` | RapidAPI collectors. |
| `crtsh_email` | email | `email` | `crtsh.py` :: `CrtShEmailCollector` | crt.sh — Certificate Transparency lookup. |
| `dns_mx` | email | `email` | `dns_mx.py` :: `DnsMxCollector` | MX / DNS lookup for email domain — disposable check, catch-all hint. |
| `gravatar` | email | `email` | `gravatar.py` :: `GravatarCollector` | Gravatar collector. |
| `hibp_hint` | email | `email` | `hibp.py` :: `HIBPBreachHint` | HaveIBeenPwned collector using the free Pwned Passwords k-anonymity endpoint |
| `holehe` | email | `email` | `holehe.py` :: `HoleheCollector` | Holehe collector: runs the `holehe` CLI against an email address. |
| `rapidapi_mailcheck` | email | `email` | `rapidapi_generic.py` :: `RapidAPIEmailMailCheck` | RapidAPI collectors. |
| `boe` | es_official | `full_name`, `birth_name`, `aliases` | `boe.py` :: `BOECollector` | BOE (Boletín Oficial del Estado) full-text search by name. |
| `borme` | es_official | `full_name`, `birth_name`, `aliases` | `borme.py` :: `BORMECollector` | BORME (Boletín Oficial del Registro Mercantil) search. |
| `eu_registries` | eu_official | `full_name`, `birth_name`, `aliases`, `company_name`, `domain` | `eu_registries.py` :: `EURegistriesCollector` | EU corporate registries via OpenCorporates. |
| `rapidapi_reverse_image` | image | `extra_context` | `rapidapi_generic.py` :: `RapidAPIReverseImage` | RapidAPI collectors. |
| `reverse_image` | image | `extra_context` | `reverse_image.py` :: `ReverseImageCollector` | Reverse image lookup collector (Wave 3 / C8). |
| `ipinfo` | infra | `extra_context` | `ipinfo.py` :: `IpInfoCollector` | Resolve any IP found in extra_context via ipinfo.io (no key, limited). |
| `leakix` | infra | `domain`, `email` | `leakix.py` :: `LeakIXCollector` | LeakIX — services / leaks indexer. Free tier accepts anonymous limited queries |
| `shodan_internetdb` | infra | `domain`, `extra_context` | `shodan_internetdb.py` :: `ShodanInternetDBCollector` | Shodan InternetDB: free, keyless, read-only lookup by IP. |
| `wikidata` | knowledge | `full_name`, `birth_name`, `aliases` | `wikidata.py` :: `WikidataCollector` | Wikidata SPARQL collector: find humans matching the full name. |
| `wikipedia` | knowledge | `full_name`, `birth_name`, `aliases` | `wikipedia_es.py` :: `WikipediaCollector` | Wikipedia ES/EN opensearch by full name. |
| `pastebin_search` | leak | `email`, `username`, `full_name`, `domain` | `pastebin_search.py` :: `PastebinSearchCollector` | Pastebin / IDE-paste search collector. |
| `hibp_passwords` | password | — | `hibp.py` :: `HIBPPasswordKAnon` | Not applicable by default (no password in SearchInput). Placeholder for future use. |
| `phoneinfoga` | phone | `phone` | `phoneinfoga.py` :: `PhoneInfogaCollector` | PhoneInfoga-lite collector. |
| `rapidapi_phone_validate` | phone | `phone` | `rapidapi_generic.py` :: `RapidAPIPhoneValidate` | RapidAPI collectors. |
| `rapidapi_whatsapp` | phone | `phone` | `rapidapi_generic.py` :: `RapidAPIWhatsApp` | RapidAPI collectors. |
| `wa_me` | phone | `phone` | `wa_me.py` :: `WaMeCollector` | wa.me WhatsApp existence check. |
| `bluesky` | social | `username` | `bluesky.py` :: `BlueskyCollector` | Bluesky public profile lookup via AT Protocol app-view (no auth needed). |
| `discord_public` | social | `username`, `extra_context` | `discord_public.py` :: `DiscordPublicCollector` | Discord public profile lookup (no auth, public/community endpoints only). |
| `instagram_public` | social | `username` | `instagram_public.py` :: `InstagramPublicCollector` | Instagram public profile via the web_profile_info endpoint. |
| `keybase` | social | `username` | `keybase.py` :: `KeybaseCollector` | Keybase user lookup (free public API). |
| `linkedin_public` | social | `username`, `full_name`, `email` | `linkedin_public.py` :: `LinkedInPublicCollector` | LinkedIn public profile collector. |
| `mastodon` | social | `username` | `mastodon.py` :: `MastodonWebFingerCollector` | Mastodon WebFinger probe on popular instances. |
| `messengers_extra` | social | `username`, `phone` | `messengers_extra.py` :: `MessengersExtraCollector` | Extra messenger presence collectors (Wave 3 / C12). |
| `rapidapi_socialscan` | social | `email`, `phone`, `username` | `rapidapi_generic.py` :: `RapidAPISocialScanner` | RapidAPI collectors. |
| `reddit` | social | `username` | `reddit.py` :: `RedditUserCollector` | Reddit collector — best-effort public fetch. |
| `telegram_public` | social | `username` | `telegram_public.py` :: `TelegramPublicCollector` | Telegram public profile / channel existence via t.me OG tags. |
| `tiktok_public` | social | `username` | `tiktok_public.py` :: `TikTokPublicCollector` | TikTok public profile existence. |
| `twitter_nitter` | social | `username` | `twitter_nitter.py` :: `TwitterNitterCollector` | Twitter/X collector via public Nitter mirrors. |
| `maigret` | username | `username` | `maigret.py` :: `MaigretCollector` | Maigret collector: Sherlock on steroids, 3000+ sites with metadata extraction. |
| `sherlock` | username | `username` | `sherlock.py` :: `SherlockCollector` | Sherlock collector: hunts a username across 400+ platforms. |

## Categories

- **academic** — `orcid`
- **ai_research** — `gemini_websearch`
- **archive** — `archive_advanced`, `wayback`
- **breach** — `combo_lists_local`, `dehashed`
- **code** — `dockerhub`, `github`, `gitlab`, `npm`, `pypi`, `stackexchange`
- **crypto** — `crypto_ens`
- **dark_web** — `ahmia`
- **domain** — `crtsh_domain`, `ct_monitor`, `domain_photon`, `urlscan`
- **dork** — `searxng_dorks`
- **doxx** — `rapidapi_guarddoxx`
- **email** — `crtsh_email`, `dns_mx`, `gravatar`, `hibp_hint`, `holehe`, `rapidapi_mailcheck`
- **es_official** — `boe`, `borme`
- **eu_official** — `eu_registries`
- **image** — `rapidapi_reverse_image`, `reverse_image`
- **infra** — `ipinfo`, `leakix`, `shodan_internetdb`
- **knowledge** — `wikidata`, `wikipedia`
- **leak** — `pastebin_search`
- **password** — `hibp_passwords`
- **phone** — `phoneinfoga`, `rapidapi_phone_validate`, `rapidapi_whatsapp`, `wa_me`
- **social** — `bluesky`, `discord_public`, `instagram_public`, `keybase`, `linkedin_public`, `mastodon`, `messengers_extra`, `rapidapi_socialscan`, `reddit`, `telegram_public`, `tiktok_public`, `twitter_nitter`
- **username** — `maigret`, `sherlock`
