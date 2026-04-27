// background.js — service worker for "who — OSINT" MV3 extension
// Registers a context menu and stashes the selected text so popup.js can prefill.

const MENU_ID = "who-investigate-selection";

chrome.runtime.onInstalled.addListener(() => {
  chrome.contextMenus.create({
    id: MENU_ID,
    title: "Investigate selection with who",
    contexts: ["selection"]
  });

  // Seed defaults if missing
  chrome.storage.sync.get(["apiBase", "apiToken"], (cfg) => {
    const patch = {};
    if (!cfg.apiBase) patch.apiBase = "http://localhost:8000";
    if (!cfg.apiToken) patch.apiToken = "";
    if (Object.keys(patch).length) chrome.storage.sync.set(patch);
  });
});

chrome.contextMenus.onClicked.addListener(async (info, tab) => {
  if (info.menuItemId !== MENU_ID) return;
  const selection = (info.selectionText || "").trim();
  await chrome.storage.local.set({ pendingSelection: selection });
  // MV3 cannot programmatically open the action popup on all platforms.
  // Fallback: open popup.html in a new tab where popup.js will read pendingSelection.
  if (chrome.action && chrome.action.openPopup) {
    try { await chrome.action.openPopup(); return; } catch (_) { /* fall through */ }
  }
  chrome.tabs.create({ url: chrome.runtime.getURL("popup.html?from=context") });
});
