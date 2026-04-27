// popup.js — "who — OSINT" popup logic
//
// # WIRING
// API contract this popup depends on:
//
//   POST {apiBase}/api/cases
//   Headers:
//     Content-Type: application/json
//     Authorization: Bearer {apiToken}
//   Body (JSON):
//     {
//       "selectors": {
//         "username": string|null,
//         "email":    string|null,
//         "phone":    string|null,
//         "domain":   string|null
//       },
//       "legal_basis": "consent" | "contract" | "legal_obligation"
//                    | "vital_interests" | "public_task" | "legitimate_interests",
//       "notes": string|null,
//       "source": "extension"
//     }
//   Response 200/201 (JSON):
//     { "id": string, ... }   // `id` is required to construct the case URL.
//
// On success, the extension opens `{apiBase}/v2/cases/{id}` in a new tab.
// At least one selector is required client-side; legal_basis is required by GDPR.

const $ = (id) => document.getElementById(id);

function looksLikeEmail(s)  { return /^[^\s@]+@[^\s@]+\.[^\s@]+$/.test(s); }
function looksLikePhone(s)  { return /^[+()\d][\d\s().-]{5,}$/.test(s); }
function looksLikeDomain(s) { return /^([a-z0-9-]+\.)+[a-z]{2,}$/i.test(s) && !s.includes("@"); }

function classify(sel) {
  if (!sel) return null;
  const s = sel.trim();
  if (!s) return null;
  if (looksLikeEmail(s))  return { field: "email",    value: s };
  if (looksLikePhone(s))  return { field: "phone",    value: s };
  if (looksLikeDomain(s)) return { field: "domain",   value: s };
  return { field: "username", value: s };
}

async function loadConfig() {
  return new Promise((res) =>
    chrome.storage.sync.get(["apiBase", "apiToken"], (cfg) =>
      res({ apiBase: (cfg.apiBase || "").replace(/\/+$/, ""), apiToken: cfg.apiToken || "" })
    )
  );
}

async function consumePendingSelection() {
  return new Promise((res) =>
    chrome.storage.local.get(["pendingSelection"], (s) => {
      const v = s.pendingSelection || "";
      if (v) chrome.storage.local.remove("pendingSelection");
      res(v);
    })
  );
}

function setMsg(text, kind) {
  const el = $("msg");
  el.textContent = text || "";
  el.className = kind || "";
}

document.addEventListener("DOMContentLoaded", async () => {
  const sel = await consumePendingSelection();
  if (sel) {
    const c = classify(sel);
    if (c) $(c.field).value = c.value;
  }

  $("options").addEventListener("click", () => chrome.runtime.openOptionsPage());

  $("submit").addEventListener("click", async () => {
    setMsg("", "");
    const selectors = {
      username: $("username").value.trim() || null,
      email:    $("email").value.trim()    || null,
      phone:    $("phone").value.trim()    || null,
      domain:   $("domain").value.trim()   || null
    };
    const legal_basis = $("legal_basis").value;
    const notes = $("notes").value.trim() || null;

    if (!Object.values(selectors).some(Boolean)) {
      setMsg("Provide at least one selector.", "err"); return;
    }
    if (!legal_basis) {
      setMsg("Legal basis is required (GDPR).", "err"); return;
    }

    const { apiBase, apiToken } = await loadConfig();
    if (!apiBase || !apiToken) {
      setMsg("Set backend URL and token in Settings.", "err"); return;
    }

    setMsg("Creating case…", "ok");
    try {
      const resp = await fetch(`${apiBase}/api/cases`, {
        method: "POST",
        headers: {
          "Content-Type": "application/json",
          "Authorization": `Bearer ${apiToken}`
        },
        body: JSON.stringify({ selectors, legal_basis, notes, source: "extension" })
      });
      const text = await resp.text();
      if (!resp.ok) { setMsg(`HTTP ${resp.status}: ${text}`, "err"); return; }
      let data; try { data = JSON.parse(text); } catch { data = {}; }
      const id = data.id || data.case_id;
      if (!id) { setMsg("Backend did not return a case id.", "err"); return; }
      const url = `${apiBase}/v2/cases/${encodeURIComponent(id)}`;
      chrome.tabs.create({ url });
      setMsg("Case created.", "ok");
    } catch (e) {
      setMsg(`Network error: ${e.message || e}`, "err");
    }
  });
});
