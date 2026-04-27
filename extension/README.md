# who — OSINT Browser Extension

Manifest v3 extension that lets you create OSINT cases from any page selection
and submit them to your `who` backend.

## Features

- Right-click any selected text → "Investigate selection with who" → opens the
  popup pre-filled (auto-classified as email / phone / domain / username).
- Popup form with selector fields and a **GDPR legal basis** selector (required).
- On submit: `POST {apiBase}/api/cases` with a Bearer token, then opens
  `{apiBase}/v2/cases/{id}` in a new tab.
- Settings page to configure backend base URL and bearer token. Host
  permissions are requested for the configured origin only.

## Files

```
extension/
  manifest.json
  background.js
  popup.html
  popup.js
  options.html
  icons/{icon16,icon48,icon128}.png
```

## Load unpacked — Chrome / Edge / Brave

1. Open `chrome://extensions`.
2. Toggle **Developer mode** (top right).
3. Click **Load unpacked** and select the `extension/` directory.
4. Open the extension's **Details → Extension options** (or click *Settings*
   in the popup) and set:
   - **Backend base URL**, e.g. `https://who.example.com`
   - **Bearer token**
   When prompted, grant the host permission for that origin.
5. Pin the extension to the toolbar for easy access.

## Load temporary — Firefox

Firefox supports MV3 with `background.service_worker`.

1. Open `about:debugging#/runtime/this-firefox`.
2. Click **Load Temporary Add-on…**
3. Select `extension/manifest.json`.
4. Open the add-on's **Preferences** to set backend URL and token.

Note: temporary add-ons are removed on browser restart. For persistent install,
package and sign through AMO.

## Configuration storage

- `chrome.storage.sync` — `apiBase`, `apiToken`
- `chrome.storage.local` — `pendingSelection` (transient, cleared on read)

## API contract

See the `# WIRING` block at the top of `popup.js` for the exact request /
response schema the extension depends on.

## Icons

`icons/icon16.png`, `icons/icon48.png`, `icons/icon128.png` are 1×1 transparent
PNG placeholders. Replace with real artwork before publishing.
