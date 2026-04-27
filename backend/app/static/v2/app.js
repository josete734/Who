/* case_v2 — vanilla JS controller. CDN-only, no build. */
(() => {
  "use strict";

  const CASE_ID = window.__CASE_ID__;
  const API = (path) => `/api/cases/${encodeURIComponent(CASE_ID)}${path}`;

  // ---------- Type palette (shared) ----------
  const TYPE_COLORS = {
    person: "#a78bfa", email: "#60a5fa", phone: "#22d3ee",
    domain: "#14b8a6", account: "#10b981", photo: "#f472b6",
    location: "#f59e0b", document: "#9ca3af", org: "#fb7185",
  };
  const TYPE_LABELS = {
    person: "Persona", email: "Email", phone: "Teléfono",
    domain: "Dominio", account: "Cuenta", photo: "Foto",
    location: "Lugar", document: "Documento", org: "Organización",
  };
  const KIND_ICONS = { home: "🏠", work: "💼", gym: "🏋", endpoint: "📍", school: "🏫", travel: "✈" };
  const KIND_COLORS = {
    home: "#10b981", work: "#60a5fa", gym: "#f472b6",
    endpoint: "#f59e0b", school: "#a78bfa", travel: "#22d3ee",
    photo_gps: "#f472b6", ip: "#60a5fa", address: "#10b981", social_place: "#f59e0b",
  };

  // ---------- Tab switcher ----------
  const tabs = document.querySelectorAll(".v2-tab");
  const panels = document.querySelectorAll(".v2-panel");
  const loaded = new Set();
  const loaders = {};
  let currentTab = "graph";

  function activate(tab) {
    currentTab = tab;
    tabs.forEach((t) => {
      const isActive = t.dataset.tab === tab;
      t.classList.toggle("v2-tab-active", isActive);
      t.setAttribute("aria-selected", isActive ? "true" : "false");
    });
    panels.forEach((p) => p.classList.toggle("hidden", p.dataset.panel !== tab));
    if (!loaded.has(tab) && typeof loaders[tab] === "function") {
      loaded.add(tab);
      try { loaders[tab](); } catch (e) { console.error("loader", tab, e); }
    } else if (typeof loaders[tab + ":resize"] === "function") {
      loaders[tab + ":resize"]();
    }
  }
  tabs.forEach((t) => t.addEventListener("click", () => activate(t.dataset.tab)));

  // ---------- Helpers ----------
  async function getJSON(url) {
    const r = await fetch(url, { headers: { Accept: "application/json" } });
    if (!r.ok) throw new Error(`${r.status} ${r.statusText}`);
    return r.json();
  }
  function escapeHTML(s) {
    return String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
  }
  function fmtNum(n) {
    if (n == null || isNaN(n)) return "—";
    return new Intl.NumberFormat().format(n);
  }
  function nodeColor(t) { return TYPE_COLORS[t] || "#737373"; }

  // ---------- Sidebar (case overview) ----------
  async function loadSidebar() {
    try {
      const data = await getJSON(API("")).catch(() => null);
      if (data) {
        const inputs = data.inputs || data.targets || data.original_inputs || {};
        const inputsEl = document.getElementById("sb-inputs");
        if (inputs && Object.keys(inputs).length) {
          inputsEl.innerHTML = Object.entries(inputs).map(([k, v]) =>
            `<div><span class="text-neutral-500">${escapeHTML(k)}:</span> <span class="font-mono text-neutral-200">${escapeHTML(typeof v === "object" ? JSON.stringify(v) : v)}</span></div>`
          ).join("");
        } else if (Array.isArray(data.targets)) {
          inputsEl.textContent = data.targets.join(", ");
        }
        const status = data.status || data.state || "—";
        document.getElementById("sb-status").textContent = status;
        const pill = document.getElementById("case-status-pill");
        pill.textContent = status;
        pill.className = "pill " + (status === "completed" || status === "done" ? "pill-ok" :
                                     status === "running" ? "pill-warn" :
                                     status === "error" || status === "failed" ? "pill-err" : "pill-muted");
        const dur = data.durations || data.timings || {};
        if (Object.keys(dur).length) {
          document.getElementById("sb-durations").innerHTML = Object.entries(dur).map(([k, v]) =>
            `<div class="flex justify-between"><span class="text-neutral-500">${escapeHTML(k)}</span><span class="font-mono">${escapeHTML(v)}</span></div>`
          ).join("");
        }
      }
    } catch (e) { /* silent */ }
  }

  // ---------- GRAPH ----------
  let cy = null;
  let graphRaw = null;
  const enabledTypes = new Set();
  let selectedNodeId = null;

  function buildTypeChips(types) {
    const wrap = document.getElementById("graph-type-chips");
    const labelEl = wrap.querySelector(".toolbar-label");
    wrap.innerHTML = "";
    if (labelEl) wrap.appendChild(labelEl);
    else { const l = document.createElement("span"); l.className = "toolbar-label"; l.textContent = "Tipos:"; wrap.appendChild(l); }
    types.forEach((t) => {
      const chip = document.createElement("span");
      chip.className = "type-chip active";
      chip.dataset.type = t;
      chip.innerHTML = `<span class="dot" style="background:${nodeColor(t)}"></span>${escapeHTML(TYPE_LABELS[t] || t)}`;
      enabledTypes.add(t);
      chip.addEventListener("click", () => {
        if (enabledTypes.has(t)) { enabledTypes.delete(t); chip.classList.remove("active"); }
        else { enabledTypes.add(t); chip.classList.add("active"); }
        renderGraph();
      });
      wrap.appendChild(chip);
    });

    // legend
    const legend = document.getElementById("graph-legend");
    legend.innerHTML = types.map((t) =>
      `<span class="lg-item"><span class="dot" style="background:${nodeColor(t)}"></span>${escapeHTML(TYPE_LABELS[t] || t)}</span>`
    ).join("");
  }

  function nodeSize(n) {
    const s = Number(n.score ?? n.confidence ?? 50);
    const norm = Math.max(0, Math.min(100, s)) / 100;
    return 24 + norm * 36;
  }
  function edgeWidth(e) {
    const w = Number(e.weight ?? e.confidence ?? 1);
    return 0.8 + Math.min(6, w * 1.4);
  }

  function renderGraph() {
    if (!graphRaw) return;
    const minScore = parseInt(document.getElementById("graph-score-filter").value, 10) || 0;
    const nodes = (graphRaw.nodes || []).filter((n) => {
      if (enabledTypes.size && !enabledTypes.has(n.type)) return false;
      const s = Number(n.score ?? n.confidence ?? 100);
      return s >= minScore;
    });
    const nodeIds = new Set(nodes.map((n) => n.id));
    const edges = (graphRaw.edges || []).filter((e) => nodeIds.has(e.source) && nodeIds.has(e.target));

    const elements = [
      ...nodes.map((n) => ({
        data: {
          id: n.id, label: n.label || n.key || n.id,
          type: n.type || "node", raw: n,
          size: nodeSize(n), color: nodeColor(n.type),
        },
      })),
      ...edges.map((e, i) => ({
        data: {
          id: e.id || `e${i}`, source: e.source, target: e.target,
          label: e.rel || e.label || e.type || "",
          width: edgeWidth(e), raw: e,
        },
      })),
    ];

    if (cy) { cy.destroy(); cy = null; }
    cy = window.cytoscape({
      container: document.getElementById("cy"),
      elements,
      style: [
        { selector: "node", style: {
          "background-color": "data(color)",
          "label": "data(label)",
          "color": "#fafafa",
          "font-size": 10,
          "font-weight": 500,
          "text-outline-color": "#0a0a0a",
          "text-outline-width": 2,
          "text-valign": "bottom",
          "text-margin-y": 4,
          "width": "data(size)", "height": "data(size)",
          "border-width": 1.5, "border-color": "#0a0a0a",
          "transition-property": "opacity, border-color, border-width",
          "transition-duration": "0.2s",
        }},
        { selector: "edge", style: {
          "width": "data(width)",
          "line-color": "#3a3a3a",
          "target-arrow-color": "#3a3a3a",
          "target-arrow-shape": "triangle",
          "arrow-scale": 0.8,
          "curve-style": "bezier",
          "label": "data(label)",
          "font-size": 8,
          "color": "#737373",
          "text-rotation": "autorotate",
          "text-background-color": "#0a0a0a",
          "text-background-opacity": 0.7,
          "text-background-padding": 2,
          "transition-property": "opacity, line-color, width",
          "transition-duration": "0.2s",
        }},
        { selector: ".dim", style: { "opacity": 0.25 } },
        { selector: ".highlight", style: {
          "border-color": "#fafafa", "border-width": 3,
        }},
        { selector: "edge.highlight", style: {
          "line-color": "#10b981", "target-arrow-color": "#10b981", "width": 3,
        }},
        { selector: ":selected", style: { "border-color": "#10b981", "border-width": 3 } },
      ],
      layout: {
        name: "cose-bilkent", animate: "end", randomize: true,
        nodeRepulsion: 8000, idealEdgeLength: 120,
        edgeElasticity: 0.45, gravity: 0.25, numIter: 2500,
        tile: true, animationDuration: 600,
      },
      wheelSensitivity: 0.2,
      minZoom: 0.15, maxZoom: 3,
    });

    cy.on("tap", "node", (evt) => selectNode(evt.target));
    cy.on("tap", (evt) => { if (evt.target === cy) clearSelection(); });

    document.getElementById("graph-status").textContent = `${nodes.length} nodos · ${edges.length} aristas`;
  }

  function clearSelection() {
    if (!cy) return;
    cy.elements().removeClass("dim highlight");
    closeSidePanel();
    selectedNodeId = null;
  }

  function selectNode(node) {
    if (!cy) return;
    selectedNodeId = node.id();
    const neigh = node.closedNeighborhood();
    cy.elements().addClass("dim");
    neigh.removeClass("dim").addClass("highlight");
    openSidePanelForNode(node);
  }

  function openSidePanelForNode(node) {
    const raw = node.data("raw") || {};
    const type = raw.type || "node";
    const attrs = raw.attrs || raw.attributes || raw.properties || {};
    const sources = raw.sources || raw.findings || raw.evidence || [];
    const score = Number(raw.score ?? raw.confidence ?? 0);
    const neighbors = node.neighborhood("node").map((n) => n);

    const side = document.getElementById("graph-side");
    side.innerHTML = `
      <button class="side-close" id="sp-close" title="Cerrar">×</button>
      <div class="sp-header">
        <span class="sp-type-badge" style="color:${nodeColor(type)};border:1px solid ${nodeColor(type)}33">
          <span class="dot" style="display:inline-block;width:7px;height:7px;border-radius:50%;background:${nodeColor(type)};"></span>
          ${escapeHTML(TYPE_LABELS[type] || type)}
        </span>
        <div class="sp-key">${escapeHTML(raw.label || raw.key || raw.id || "")}</div>
        <div class="text-xs text-neutral-500 mt-2">score: <span class="font-mono text-emerald-400">${score.toFixed(1)}</span></div>
        <div class="sp-score-bar"><div class="sp-score-fill" style="width:${Math.max(0, Math.min(100, score))}%"></div></div>
      </div>
      <div class="sp-section">
        <div class="sp-section-title">Atributos (${Object.keys(attrs).length})</div>
        ${Object.keys(attrs).length === 0 ? '<div class="text-xs text-neutral-600">(ninguno)</div>' :
          Object.entries(attrs).slice(0, 40).map(([k, v]) =>
            `<div class="attr-row"><div class="k">${escapeHTML(k)}</div><div class="v">${escapeHTML(typeof v === "object" ? JSON.stringify(v) : v)}</div></div>`
          ).join("")
        }
      </div>
      <div class="sp-section">
        <div class="sp-section-title">Vecinos (${neighbors.length})</div>
        <div>
          ${neighbors.slice(0, 60).map((n) => {
            const t = n.data("type"); const lbl = n.data("label");
            return `<span class="neighbor-pill" data-nid="${escapeHTML(n.id())}"><span class="dot" style="background:${nodeColor(t)}"></span>${escapeHTML(lbl)}</span>`;
          }).join("") || '<div class="text-xs text-neutral-600">(ninguno)</div>'}
        </div>
      </div>
      <div class="sp-section">
        <div class="sp-section-title">Fuentes / Findings (${sources.length})</div>
        ${sources.length === 0 ? '<div class="text-xs text-neutral-600">(ninguna)</div>' :
          sources.slice(0, 40).map((s) => {
            const txt = typeof s === "string" ? s :
              (s.type ? `[${s.type}] ` : "") +
              (s.value || s.label || s.name || s.url || JSON.stringify(s));
            return `<div class="source-line">${escapeHTML(txt)}</div>`;
          }).join("")
        }
      </div>
    `;
    side.classList.add("open");
    document.getElementById("sp-close").addEventListener("click", clearSelection);
    side.querySelectorAll(".neighbor-pill").forEach((pill) => {
      pill.addEventListener("click", () => {
        const id = pill.dataset.nid;
        const n = cy.getElementById(id);
        if (n && n.length) { cy.animate({ center: { eles: n }, zoom: Math.max(cy.zoom(), 1.2) }, { duration: 350 }); selectNode(n); }
      });
    });
  }
  function closeSidePanel() {
    const side = document.getElementById("graph-side");
    if (side) side.classList.remove("open");
  }

  loaders.graph = async () => {
    const status = document.getElementById("graph-status");
    status.textContent = "cargando…";
    try {
      graphRaw = await getJSON(API("/graph"));
      const types = Array.from(new Set((graphRaw.nodes || []).map((n) => n.type).filter(Boolean)));
      buildTypeChips(types);
      renderGraph();
    } catch (e) {
      status.textContent = `error: ${e.message}`;
    }
  };
  const scoreEl = document.getElementById("graph-score-filter");
  scoreEl.addEventListener("input", () => {
    document.getElementById("graph-score-val").textContent = scoreEl.value;
    renderGraph();
  });
  document.getElementById("graph-reload").addEventListener("click", () => { loaded.delete("graph"); loaders.graph(); });
  document.getElementById("graph-fit").addEventListener("click", () => { if (cy) cy.fit(null, 40); });
  document.getElementById("graph-relayout").addEventListener("click", () => {
    if (cy) cy.layout({ name: "cose-bilkent", animate: "end", randomize: true, nodeRepulsion: 8000, idealEdgeLength: 120 }).run();
  });
  document.getElementById("graph-export").addEventListener("click", () => {
    if (!cy) return;
    const png = cy.png({ output: "blob", bg: "#0a0a0a", scale: 2, full: true });
    const a = document.createElement("a");
    a.href = URL.createObjectURL(png);
    a.download = `case-${CASE_ID}-graph.png`;
    a.click();
    URL.revokeObjectURL(a.href);
  });

  // ---------- TIMELINE ----------
  let tlInstance = null;
  let tlItems = null;
  let tlGroups = null;

  loaders.timeline = async () => {
    const status = document.getElementById("timeline-status");
    status.textContent = "cargando…";
    try {
      const data = await getJSON(API("/timeline"));
      const evs = (data.events || data.items || []).filter((e) => e.timestamp || e.start || e.date);
      const kinds = Array.from(new Set(evs.map((e) => e.kind || e.group || e.type || "evento")));
      const groups = new window.vis.DataSet(kinds.map((k) => ({
        id: k,
        content: `<span style="color:${nodeColor(k) === "#737373" ? "#10b981" : nodeColor(k)}">${KIND_ICONS[k] || "•"}</span> ${escapeHTML(k)}`,
      })));
      const items = evs.map((ev, i) => {
        const kind = ev.kind || ev.group || ev.type || "evento";
        const color = nodeColor(kind) === "#737373" ? "#10b981" : nodeColor(kind);
        const conf = ev.confidence ?? "";
        const tipParts = [];
        if (ev.evidence) tipParts.push(`<b>evidencia:</b> ${escapeHTML(typeof ev.evidence === "object" ? JSON.stringify(ev.evidence) : ev.evidence)}`);
        if (conf !== "") tipParts.push(`<b>conf:</b> ${escapeHTML(conf)}`);
        if (ev.source_collector) tipParts.push(`<b>fuente:</b> ${escapeHTML(ev.source_collector)}`);
        return {
          id: ev.id || i,
          group: kind,
          content: `${KIND_ICONS[kind] || ""} ${escapeHTML(ev.title || ev.label || kind)}`,
          start: ev.timestamp || ev.start || ev.date,
          end: ev.end,
          title: tipParts.join("<br>") || escapeHTML(ev.description || ""),
          style: `border-color:${color};background:linear-gradient(180deg, #1f1f1f, #161616);`,
        };
      });
      tlItems = new window.vis.DataSet(items);
      tlGroups = groups;
      const container = document.getElementById("timeline");
      container.innerHTML = "";
      if (tlInstance) { try { tlInstance.destroy(); } catch (_) {} tlInstance = null; }
      tlInstance = new window.vis.Timeline(container, tlItems, tlGroups, {
        zoomable: true, stack: true, height: "100%",
        tooltip: { followMouse: true, overflowMethod: "flip" },
        orientation: "top",
      });
      document.getElementById("sb-n-events").textContent = fmtNum(items.length);
      status.textContent = `${items.length} eventos · ${kinds.length} categorías`;
    } catch (e) {
      status.textContent = `error: ${e.message}`;
    }
  };
  loaders["timeline:resize"] = () => { if (tlInstance) tlInstance.redraw(); };
  document.getElementById("tl-fit").addEventListener("click", () => { if (tlInstance) tlInstance.fit(); });
  document.getElementById("tl-search").addEventListener("input", (e) => {
    if (!tlItems) return;
    const q = e.target.value.toLowerCase();
    tlItems.forEach((it) => {
      const visible = !q || (it.content || "").toLowerCase().includes(q) || (it.title || "").toLowerCase().includes(q);
      tlItems.update({ id: it.id, className: visible ? "" : "vis-item-hidden", style: visible ? it.style : it.style + ";opacity:.15;" });
    });
  });

  // ---------- PHOTOS ----------
  let photosData = null;

  function photoBadges(p) {
    const badges = [];
    const exif = p.exif || p.metadata || {};
    const hasGPS = (exif.gps || exif.GPSLatitude || exif.lat || p.lat || (p.location && p.location.lat)) != null;
    const hasAI = !!(p.vision || p.ai || p.analysis || p.ocr);
    if (hasGPS) badges.push(`<span class="photo-badge photo-badge-gps">GPS</span>`);
    if (hasAI) badges.push(`<span class="photo-badge photo-badge-ai">IA</span>`);
    return badges.join("");
  }

  function renderPhotos() {
    if (!photosData) return;
    const wrap = document.getElementById("photos-clusters");
    const onlyGPS = document.getElementById("photos-only-gps").checked;
    const onlyAI = document.getElementById("photos-only-ai").checked;
    const clusters = photosData.clusters || [];
    let total = 0;
    wrap.innerHTML = clusters.map((c, i) => {
      let photos = c.photos || c.items || [];
      if (onlyGPS) photos = photos.filter((p) => {
        const exif = p.exif || p.metadata || {};
        return (exif.gps || exif.GPSLatitude || exif.lat || p.lat || (p.location && p.location.lat)) != null;
      });
      if (onlyAI) photos = photos.filter((p) => !!(p.vision || p.ai || p.analysis || p.ocr));
      if (!photos.length) return "";
      total += photos.length;
      const avatar = photos[0]?.url || photos[0]?.thumb || photos[0]?.src || "";
      const label = c.label || c.cluster_label || `Cluster ${c.id ?? (i + 1)}`;
      return `
        <section class="photo-cluster">
          <div class="photo-cluster-header">
            <div class="photo-cluster-avatar">${avatar ? `<img src="${escapeHTML(avatar)}" alt="">` : ""}</div>
            <div>
              <div class="photo-cluster-title">${escapeHTML(label)}</div>
              <div class="photo-cluster-sub">${photos.length} fotos${c.tag ? ` · ${escapeHTML(c.tag)}` : ""}</div>
            </div>
          </div>
          <div class="photo-cluster-grid">
            ${photos.map((p, idx) => {
              const url = p.url || p.thumb || p.src || "";
              return `<figure data-cidx="${i}" data-pidx="${idx}">
                <img loading="lazy" src="${escapeHTML(url)}" alt="${escapeHTML(p.caption || "")}" />
                <div class="photo-badges">${photoBadges(p)}</div>
              </figure>`;
            }).join("")}
          </div>
        </section>
      `;
    }).join("");
    document.getElementById("photos-status").textContent = `${clusters.length} clusters · ${total} fotos`;
    document.getElementById("sb-n-photos").textContent = fmtNum(total);

    wrap.querySelectorAll("figure").forEach((fig) => {
      fig.addEventListener("click", () => {
        const ci = +fig.dataset.cidx, pi = +fig.dataset.pidx;
        const photo = (clusters[ci].photos || clusters[ci].items || []).filter((p) => {
          if (onlyGPS) {
            const exif = p.exif || p.metadata || {};
            if (!(exif.gps || exif.GPSLatitude || exif.lat || p.lat || (p.location && p.location.lat))) return false;
          }
          if (onlyAI && !(p.vision || p.ai || p.analysis || p.ocr)) return false;
          return true;
        })[pi];
        if (photo) openLightbox(photo);
      });
    });
  }

  loaders.photos = async () => {
    const status = document.getElementById("photos-status");
    status.textContent = "cargando…";
    try {
      photosData = await getJSON(API("/photos/clusters"));
      renderPhotos();
    } catch (e) {
      status.textContent = `error: ${e.message}`;
    }
  };
  document.getElementById("photos-only-gps").addEventListener("change", renderPhotos);
  document.getElementById("photos-only-ai").addEventListener("change", renderPhotos);

  // ---------- LIGHTBOX ----------
  function openLightbox(photo) {
    const lb = document.getElementById("lightbox");
    const img = document.getElementById("lb-img");
    const exifWrap = document.getElementById("lb-exif");
    const aiWrap = document.getElementById("lb-ai");
    const badges = document.getElementById("lb-badges");
    const title = document.getElementById("lb-title");
    const mapBtn = document.getElementById("lb-map-btn");

    img.src = photo.url || photo.thumb || photo.src || "";
    title.textContent = photo.filename || photo.caption || "Foto";
    badges.innerHTML = photoBadges(photo);

    const exif = photo.exif || photo.metadata || {};
    const exifEntries = [];
    const gpsLat = exif.GPSLatitude ?? exif.lat ?? photo.lat ?? (photo.location && photo.location.lat);
    const gpsLng = exif.GPSLongitude ?? exif.lng ?? exif.lon ?? photo.lng ?? photo.lon ?? (photo.location && (photo.location.lng || photo.location.lon));
    if (gpsLat != null && gpsLng != null) exifEntries.push(["GPS", `${gpsLat}, ${gpsLng}`]);
    if (exif.DateTime || exif.date || photo.date) exifEntries.push(["Fecha", exif.DateTime || exif.date || photo.date]);
    if (exif.Make || exif.camera) exifEntries.push(["Cámara", `${exif.Make || ""} ${exif.Model || ""}`.trim() || exif.camera]);
    if (exif.LensModel) exifEntries.push(["Lente", exif.LensModel]);
    Object.entries(exif).slice(0, 15).forEach(([k, v]) => {
      if (["GPSLatitude", "GPSLongitude", "DateTime", "Make", "Model", "LensModel"].includes(k)) return;
      if (typeof v === "object") return;
      exifEntries.push([k, v]);
    });
    exifWrap.innerHTML = exifEntries.length ? exifEntries.map(([k, v]) =>
      `<div class="row"><div class="k">${escapeHTML(k)}</div><div class="v">${escapeHTML(v)}</div></div>`
    ).join("") : '<div class="text-xs text-neutral-600">(sin EXIF)</div>';

    const ai = photo.vision || photo.ai || photo.analysis || null;
    if (ai) {
      const ocr = ai.ocr_text || ai.ocr || (ai.text_detections || []).map((t) => t.text).join(" ");
      const landmarks = ai.landmarks || ai.places || [];
      const vehicles = ai.vehicles || [];
      let html = "";
      if (ocr) html += `<div class="lb-section-title">OCR</div><pre>${escapeHTML(ocr).slice(0, 800)}</pre>`;
      if (landmarks.length) html += `<div class="lb-section-title">Landmarks</div><pre>${escapeHTML(JSON.stringify(landmarks, null, 2))}</pre>`;
      if (vehicles.length) html += `<div class="lb-section-title">Vehículos</div><pre>${escapeHTML(JSON.stringify(vehicles, null, 2))}</pre>`;
      if (!html) html = `<pre>${escapeHTML(JSON.stringify(ai, null, 2)).slice(0, 1500)}</pre>`;
      aiWrap.innerHTML = html;
    } else {
      aiWrap.innerHTML = '<div class="text-xs text-neutral-600">(sin análisis IA)</div>';
    }

    if (gpsLat != null && gpsLng != null) {
      mapBtn.style.display = "";
      mapBtn.onclick = () => {
        closeLightbox();
        activate("geo");
        setTimeout(() => { if (geoMap) geoMap.flyTo([Number(gpsLat), Number(gpsLng)], 15); }, 350);
      };
    } else mapBtn.style.display = "none";

    lb.classList.remove("hidden");
  }
  function closeLightbox() { document.getElementById("lightbox").classList.add("hidden"); }
  document.getElementById("lb-close").addEventListener("click", closeLightbox);
  document.getElementById("lightbox").addEventListener("click", (e) => {
    if (e.target.id === "lightbox") closeLightbox();
  });
  document.addEventListener("keydown", (e) => { if (e.key === "Escape") closeLightbox(); });

  // ---------- GEO ----------
  let geoMap = null;
  let geoLayers = { inferred: null, signals: null, heat: null };
  const reverseCache = new Map();

  async function reverseGeocode(lat, lng) {
    const key = `${lat.toFixed(4)},${lng.toFixed(4)}`;
    if (reverseCache.has(key)) return reverseCache.get(key);
    try {
      const r = await fetch(`https://nominatim.openstreetmap.org/reverse?format=json&lat=${lat}&lon=${lng}&zoom=16&addressdetails=1`, {
        headers: { Accept: "application/json" },
      });
      if (!r.ok) throw new Error("nominatim");
      const j = await r.json();
      const addr = j.display_name || "";
      reverseCache.set(key, addr);
      return addr;
    } catch { reverseCache.set(key, ""); return ""; }
  }

  function makeKindIcon(kind) {
    const icon = KIND_ICONS[kind] || "📍";
    const color = KIND_COLORS[kind] || "#10b981";
    return window.L.divIcon({
      className: "",
      html: `<div class="kind-marker" style="background:${color}"><span>${icon}</span></div>`,
      iconSize: [38, 38], iconAnchor: [19, 38], popupAnchor: [0, -36],
    });
  }

  loaders.geo = async () => {
    const status = document.getElementById("geo-status");
    status.textContent = "cargando…";
    try {
      if (!geoMap) {
        geoMap = window.L.map("geo-map", { zoomControl: true, preferCanvas: true }).setView([20, 0], 2);
        window.L.tileLayer("https://{s}.basemaps.cartocdn.com/dark_all/{z}/{x}/{y}{r}.png", {
          maxZoom: 19, subdomains: "abcd",
          attribution: '© <a href="https://www.openstreetmap.org/copyright">OSM</a> · © <a href="https://carto.com/attributions">CARTO</a>',
        }).addTo(geoMap);
      }
      Object.values(geoLayers).forEach((l) => { if (l) geoMap.removeLayer(l); });
      geoLayers = { inferred: window.L.layerGroup(), signals: window.L.layerGroup(), heat: window.L.layerGroup() };

      // Inferred locations
      const infData = await getJSON(API("/geo/inferred")).catch(() => ({}));
      const inferred = infData.locations || infData.inferred_locations || infData.items || [];
      const allLatLngs = [];
      inferred.forEach((loc) => {
        const lat = Number(loc.lat ?? loc.latitude ?? (loc.center && loc.center[0]));
        const lng = Number(loc.lng ?? loc.lon ?? loc.longitude ?? (loc.center && loc.center[1]));
        if (isNaN(lat) || isNaN(lng)) return;
        const kind = (loc.kind || loc.label || "endpoint").toLowerCase();
        const color = KIND_COLORS[kind] || "#10b981";
        const radius = Number(loc.radius_m ?? loc.radius ?? 200);
        window.L.circle([lat, lng], {
          radius, color, weight: 2, fillColor: color, fillOpacity: 0.15,
        }).addTo(geoLayers.inferred);
        const m = window.L.marker([lat, lng], { icon: makeKindIcon(kind) });
        const popupId = `pp-${Math.random().toString(36).slice(2, 8)}`;
        m.bindPopup(`<div id="${popupId}">
          <div class="pp-title"><span style="color:${color}">${KIND_ICONS[kind] || "📍"}</span> ${escapeHTML(loc.label || kind)}</div>
          <div class="pp-row"><b>kind:</b> ${escapeHTML(kind)}</div>
          <div class="pp-row"><b>conf:</b> ${escapeHTML(loc.confidence ?? "—")}</div>
          <div class="pp-row"><b>actividades:</b> ${escapeHTML(loc.n_activities ?? "—")}</div>
          <div class="pp-row"><b>diversidad temporal:</b> ${escapeHTML(loc.temporal_diversity_days ?? "—")} días</div>
          <div class="pp-row"><b>radio:</b> ${escapeHTML(radius)} m</div>
          <div class="pp-row pp-addr" style="margin-top:4px;color:#a3a3a3;font-style:italic">resolviendo dirección…</div>
        </div>`);
        m.on("popupopen", async () => {
          const addr = await reverseGeocode(lat, lng);
          const el = document.querySelector(`#${popupId} .pp-addr`);
          if (el) el.textContent = addr || "(dirección no disponible)";
        });
        m.addTo(geoLayers.inferred);
        allLatLngs.push([lat, lng]);
      });

      // Signals
      const sigData = await getJSON(API("/geo/signals")).catch(() => ({}));
      const signals = sigData.signals || sigData.items || [];
      signals.forEach((s) => {
        const lat = Number(s.lat ?? s.latitude); const lng = Number(s.lng ?? s.lon ?? s.longitude);
        if (isNaN(lat) || isNaN(lng)) return;
        const kind = (s.kind || s.type || "ip").toLowerCase();
        const color = KIND_COLORS[kind] || "#737373";
        window.L.circleMarker([lat, lng], {
          radius: 4, color, weight: 1, fillColor: color, fillOpacity: 0.7,
        }).bindPopup(`<div class="pp-title">${escapeHTML(kind)}</div>
          <div class="pp-row"><b>fuente:</b> ${escapeHTML(s.source || "—")}</div>
          <div class="pp-row"><b>ts:</b> ${escapeHTML(s.timestamp || "—")}</div>`).addTo(geoLayers.signals);
        allLatLngs.push([lat, lng]);
      });

      // Heatmap H3
      const heatData = await getJSON(API("/geo/heatmap")).catch(() => ({}));
      const cells = heatData.cells || heatData.h3 || [];
      let max = 1;
      cells.forEach((c) => { const w = c.count ?? c.weight ?? 1; if (w > max) max = w; });
      cells.forEach((c) => {
        const lat = c.lat ?? (c.latlng && c.latlng[0]); const lng = c.lng ?? c.lon ?? (c.latlng && c.latlng[1]);
        if (lat == null || lng == null) return;
        const w = (c.count ?? c.weight ?? 1) / max;
        const poly = c.boundary || c.polygon;
        if (Array.isArray(poly) && poly.length >= 3) {
          window.L.polygon(poly, {
            color: "#10b981", weight: 1, fillColor: "#10b981",
            fillOpacity: 0.15 + w * 0.45,
          }).bindPopup(`h3: ${escapeHTML(c.h3 || "")}<br>weight: ${c.count ?? c.weight ?? 1}`).addTo(geoLayers.heat);
        } else {
          window.L.circleMarker([lat, lng], {
            radius: 5 + w * 18, color: "#10b981", weight: 1, fillColor: "#10b981", fillOpacity: 0.2 + w * 0.5,
          }).bindPopup(`h3: ${escapeHTML(c.h3 || "")}<br>weight: ${c.count ?? c.weight ?? 1}`).addTo(geoLayers.heat);
        }
        allLatLngs.push([lat, lng]);
      });

      if (document.getElementById("geo-layer-inferred").checked) geoLayers.inferred.addTo(geoMap);
      if (document.getElementById("geo-layer-signals").checked) geoLayers.signals.addTo(geoMap);
      if (document.getElementById("geo-layer-heat").checked) geoLayers.heat.addTo(geoMap);

      window.L.control.layers(null, {
        "Lugares inferidos": geoLayers.inferred,
        "Señales": geoLayers.signals,
        "Heatmap H3": geoLayers.heat,
      }, { position: "bottomright", collapsed: false }).addTo(geoMap);

      if (allLatLngs.length) geoMap.fitBounds(allLatLngs, { padding: [40, 40] });
      setTimeout(() => geoMap.invalidateSize(), 50);
      document.getElementById("sb-n-locs").textContent = fmtNum(inferred.length);
      status.textContent = `${inferred.length} lugares · ${signals.length} señales · ${cells.length} celdas`;
    } catch (e) {
      status.textContent = `error: ${e.message}`;
    }
  };
  loaders["geo:resize"] = () => { if (geoMap) setTimeout(() => geoMap.invalidateSize(), 50); };
  document.getElementById("geo-fit").addEventListener("click", () => {
    if (!geoMap) return;
    const bounds = [];
    Object.values(geoLayers).forEach((g) => g && g.eachLayer((l) => {
      if (l.getLatLng) bounds.push(l.getLatLng());
      else if (l.getBounds) bounds.push(l.getBounds().getNorthEast(), l.getBounds().getSouthWest());
    }));
    if (bounds.length) geoMap.fitBounds(bounds, { padding: [40, 40] });
  });
  ["inferred", "signals", "heat"].forEach((k) => {
    document.getElementById(`geo-layer-${k}`).addEventListener("change", (e) => {
      if (!geoMap || !geoLayers[k]) return;
      if (e.target.checked) geoLayers[k].addTo(geoMap);
      else geoMap.removeLayer(geoLayers[k]);
    });
  });

  // ---------- FINDINGS (virtualized) ----------
  const fState = { rows: [], filtered: [], sortKey: "score", sortDir: -1, query: "", typeFilter: "", rowHeight: 36 };

  function filterAndSortFindings() {
    const q = fState.query.toLowerCase();
    fState.filtered = fState.rows.filter((f) => {
      if (fState.typeFilter && (f.type || "") !== fState.typeFilter) return false;
      if (!q) return true;
      const hay = [f.type, f.value, f.label, f.source, f.collector, f.score].join(" ").toLowerCase();
      return hay.includes(q);
    });
    const k = fState.sortKey, d = fState.sortDir;
    fState.filtered.sort((a, b) => {
      let av = a[k], bv = b[k];
      if (k === "score") { av = Number(a.score ?? a.confidence ?? 0); bv = Number(b.score ?? b.confidence ?? 0); }
      if (k === "value") { av = a.value || a.label || ""; bv = b.value || b.label || ""; }
      if (k === "source") { av = a.source || a.collector || ""; bv = b.source || b.collector || ""; }
      if (k === "seen") { av = a.seen_at || a.timestamp || ""; bv = b.seen_at || b.timestamp || ""; }
      if (av < bv) return -1 * d;
      if (av > bv) return 1 * d;
      return 0;
    });
    document.getElementById("findings-spacer").style.height = (fState.filtered.length * fState.rowHeight) + "px";
    document.getElementById("findings-status").textContent = `${fState.filtered.length} / ${fState.rows.length}`;
    document.getElementById("sb-n-findings").textContent = fmtNum(fState.rows.length);
    renderFindingsViewport();
  }

  function highlight(text, q) {
    const s = String(text ?? "");
    if (!q) return escapeHTML(s);
    const idx = s.toLowerCase().indexOf(q.toLowerCase());
    if (idx === -1) return escapeHTML(s);
    return escapeHTML(s.slice(0, idx)) + "<mark>" + escapeHTML(s.slice(idx, idx + q.length)) + "</mark>" + escapeHTML(s.slice(idx + q.length));
  }

  function renderFindingsViewport() {
    const vp = document.getElementById("findings-viewport");
    const rows = document.getElementById("findings-rows");
    const scrollTop = vp.scrollTop;
    const viewportH = vp.clientHeight;
    const rh = fState.rowHeight;
    const start = Math.max(0, Math.floor(scrollTop / rh) - 5);
    const end = Math.min(fState.filtered.length, Math.ceil((scrollTop + viewportH) / rh) + 5);
    const q = fState.query;
    let html = "";
    for (let i = start; i < end; i++) {
      const f = fState.filtered[i];
      html += `<div class="f-row" style="position:absolute;top:${i * rh}px;left:0;right:0;">
        <div class="f-cell"><span class="f-type-badge" style="color:${nodeColor(f.type)}">${escapeHTML(f.type || "")}</span></div>
        <div class="f-cell flex-1 font-mono" title="${escapeHTML(f.value || f.label || "")}">${highlight(f.value || f.label || "", q)}</div>
        <div class="f-cell text-neutral-400">${highlight(f.source || f.collector || "", q)}</div>
        <div class="f-cell text-right font-mono text-emerald-400">${escapeHTML(String(f.score ?? f.confidence ?? ""))}</div>
        <div class="f-cell text-neutral-500 text-xs">${escapeHTML(f.seen_at || f.timestamp || "")}</div>
      </div>`;
    }
    rows.innerHTML = html;
  }

  loaders.findings = async () => {
    const status = document.getElementById("findings-status");
    status.textContent = "cargando…";
    try {
      const data = await getJSON(API("/findings"));
      fState.rows = data.findings || data.items || (Array.isArray(data) ? data : []);
      const types = Array.from(new Set(fState.rows.map((f) => f.type).filter(Boolean))).sort();
      const sel = document.getElementById("findings-type-filter");
      sel.innerHTML = `<option value="">todos los tipos (${fState.rows.length})</option>` +
        types.map((t) => `<option value="${escapeHTML(t)}">${escapeHTML(t)}</option>`).join("");
      filterAndSortFindings();
    } catch (e) {
      status.textContent = `error: ${e.message}`;
    }
  };
  document.getElementById("findings-viewport").addEventListener("scroll", renderFindingsViewport);
  document.getElementById("findings-search").addEventListener("input", (e) => { fState.query = e.target.value; filterAndSortFindings(); });
  document.getElementById("findings-type-filter").addEventListener("change", (e) => { fState.typeFilter = e.target.value; filterAndSortFindings(); });
  document.querySelectorAll(".fh-cell").forEach((th) => {
    th.addEventListener("click", () => {
      const k = th.dataset.sort;
      if (fState.sortKey === k) fState.sortDir *= -1;
      else { fState.sortKey = k; fState.sortDir = -1; }
      filterAndSortFindings();
    });
  });

  // ---------- AI ----------
  let aiAbort = null;
  function aiMsg(kind, label, body) {
    const el = document.getElementById("ai-log");
    const empty = el.querySelector(".ai-chat-empty");
    if (empty) empty.remove();
    const wrap = document.createElement("div");
    wrap.className = `ai-msg ${kind}`;
    const colorMap = { thought: "#a78bfa", tool: "#60a5fa", result: "#10b981", error: "#f87171", start: "#fbbf24", final: "#34d399" };
    wrap.innerHTML = `
      <div class="ai-msg-head"><span class="dot" style="background:${colorMap[kind] || "#737373"}"></span>${escapeHTML(label)}</div>
      <div class="ai-msg-body">${body}</div>`;
    el.appendChild(wrap);
    el.scrollTop = el.scrollHeight;
  }

  async function runAI() {
    const runBtn = document.getElementById("ai-run");
    const stopBtn = document.getElementById("ai-stop");
    const status = document.getElementById("ai-status");
    const report = document.getElementById("ai-report");
    document.getElementById("ai-log").innerHTML = "";
    report.innerHTML = '<div class="ai-report-empty text-neutral-500 text-xs">Esperando respuesta…</div>';
    runBtn.disabled = true; stopBtn.disabled = false;
    status.textContent = "ejecutando…";

    aiAbort = new AbortController();
    try {
      const r = await fetch(API("/investigate"), {
        method: "POST",
        headers: { Accept: "text/event-stream", "Content-Type": "application/json" },
        body: JSON.stringify({}),
        signal: aiAbort.signal,
      });
      if (!r.ok || !r.body) throw new Error(`${r.status} ${r.statusText}`);
      const reader = r.body.getReader();
      const decoder = new TextDecoder();
      let buf = "";
      while (true) {
        const { value, done } = await reader.read();
        if (done) break;
        buf += decoder.decode(value, { stream: true });
        let idx;
        while ((idx = buf.indexOf("\n\n")) !== -1) {
          const chunk = buf.slice(0, idx); buf = buf.slice(idx + 2);
          let event = "message", data = "";
          chunk.split("\n").forEach((ln) => {
            if (ln.startsWith("event:")) event = ln.slice(6).trim();
            else if (ln.startsWith("data:")) data += ln.slice(5).trim();
          });
          if (!data) continue;
          let payload; try { payload = JSON.parse(data); } catch { payload = data; }

          if (event === "final" || event === "report") {
            report.innerHTML = renderReport(payload);
            aiMsg("final", "Informe final", "Informe completo en el panel derecho.");
          } else if (event === "error") {
            aiMsg("error", "Error", `<pre>${escapeHTML(payload.message || JSON.stringify(payload))}</pre>`);
          } else if (event === "tool" || event === "tool_use") {
            const args = payload.args || payload.input || {};
            aiMsg("tool", `Herramienta: ${payload.name || payload.tool || "?"}`,
              `<pre>${escapeHTML(JSON.stringify(args, null, 2))}</pre>`);
          } else if (event === "tool_result" || event === "result") {
            const res = payload.result ?? payload.output ?? payload;
            const txt = typeof res === "string" ? res : JSON.stringify(res, null, 2);
            aiMsg("result", "Resultado", `<pre>${escapeHTML(txt.slice(0, 2000))}${txt.length > 2000 ? "\n…" : ""}</pre>`);
          } else if (event === "thought" || event === "thinking") {
            aiMsg("thought", "Razonamiento", escapeHTML(payload.text || payload).replace(/\n/g, "<br>"));
          } else if (event === "start") {
            aiMsg("start", "Inicio", escapeHTML(payload.message || "Investigador iniciado"));
          } else {
            aiMsg("thought", event, `<pre>${escapeHTML(typeof payload === "string" ? payload : JSON.stringify(payload, null, 2))}</pre>`);
          }
        }
      }
      status.textContent = "completado";
    } catch (e) {
      if (e.name === "AbortError") status.textContent = "detenido";
      else { status.textContent = `error: ${e.message}`; aiMsg("error", "Error", escapeHTML(e.message)); }
    } finally {
      runBtn.disabled = false; stopBtn.disabled = true; aiAbort = null;
    }
  }

  function renderReport(rep) {
    if (!rep || typeof rep !== "object") return `<pre class="text-xs whitespace-pre-wrap">${escapeHTML(String(rep))}</pre>`;
    const summary = rep.summary || rep.executive_summary || "";
    const findings = rep.findings || rep.key_findings || [];
    const recs = rep.recommendations || rep.next_steps || [];
    return `
      <h2>Informe del investigador</h2>
      ${summary ? `<p class="text-sm text-neutral-200 mb-3 leading-relaxed">${escapeHTML(summary)}</p>` : ""}
      ${findings.length ? `<h3>Hallazgos clave</h3>
        <ul class="text-neutral-200">
          ${findings.map((f) => `<li>${escapeHTML(typeof f === "string" ? f : (f.text || f.summary || JSON.stringify(f)))}</li>`).join("")}
        </ul>` : ""}
      ${recs.length ? `<h3>Recomendaciones</h3>
        <ul class="text-neutral-200">
          ${recs.map((r) => `<li>${escapeHTML(typeof r === "string" ? r : (r.text || JSON.stringify(r)))}</li>`).join("")}
        </ul>` : ""}
    `;
  }

  document.getElementById("ai-run").addEventListener("click", runAI);
  document.getElementById("ai-stop").addEventListener("click", () => { if (aiAbort) aiAbort.abort(); });
  loaders.ai = () => { /* lazy */ };

  // ---------- Refresh all ----------
  document.getElementById("action-refresh").addEventListener("click", () => {
    loaded.clear();
    loadSidebar();
    activate(currentTab);
  });

  // ---------- Bootstrap ----------
  loadSidebar();
  activate("graph");
})();
