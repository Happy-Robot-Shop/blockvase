/**
 * Same-origin /api helper (blockvase.com uses a separate blockvase-api.js; inlined here so one script always loads).
 */
function blockvaseApiBases() {
  return [window.location.origin.replace(/\/$/, "") + "/api"];
}

async function blockvaseFetch(path) {
  const bases = blockvaseApiBases();
  let lastErr = null;
  for (const base of bases) {
    try {
      const r = await fetch(base + path);
      if (r.ok) return r;
      let hint = "";
      try {
        const t = await r.clone().text();
        if (t && t.length < 240) hint = ": " + t.replace(/\s+/g, " ").trim();
      } catch (_) {}
      lastErr = new Error("HTTP " + r.status + hint);
    } catch (e) {
      lastErr = e;
    }
  }
  throw lastErr || new Error("Failed to fetch");
}

function formatNumber(n) {
  return n.toString().replace(/\B(?=(\d{3})+(?!\d))/g, ",");
}

function formatDifficulty(d) {
  if (d >= 1e15) return (d / 1e15).toFixed(2) + " Q";
  if (d >= 1e12) return (d / 1e12).toFixed(2) + " T";
  if (d >= 1e9) return (d / 1e9).toFixed(2) + " B";
  if (d >= 1e6) return (d / 1e6).toFixed(2) + " M";
  return formatNumber(Math.round(d));
}

function formatHashRate(h) {
  if (h >= 1e18) return (h / 1e18).toFixed(2) + " EH/s";
  if (h >= 1e15) return (h / 1e15).toFixed(2) + " PH/s";
  if (h >= 1e12) return (h / 1e12).toFixed(2) + " TH/s";
  if (h >= 1e9) return (h / 1e9).toFixed(2) + " GH/s";
  if (h >= 1e6) return (h / 1e6).toFixed(2) + " MH/s";
  if (h >= 1e3) return (h / 1e3).toFixed(2) + " KH/s";
  return (h || 0).toFixed(2) + " H/s";
}

function formatFeeRate(satvb) {
  const v = Number(satvb);
  if (!Number.isFinite(v) || v <= 0) return '<span class="fee-unavailable"></span>';
  if (v >= 1000) return v.toLocaleString(undefined, { maximumFractionDigits: 0 }) + ' <span class="portal-fee-sats-suffix">sats</span>';
  if (v >= 100) return v.toFixed(0) + ' <span class="portal-fee-sats-suffix">sats</span>';
  if (v >= 10) return v.toFixed(1) + ' <span class="portal-fee-sats-suffix">sats</span>';
  return v.toFixed(2) + ' <span class="portal-fee-sats-suffix">sats</span>';
}

/** Short numeric fee for small radial circles (unit is in the section title). */
function formatFeeRateCompact(satvb) {
  const v = Number(satvb);
  if (!Number.isFinite(v) || v <= 0) return "n/a";
  if (v >= 1000) return v.toLocaleString(undefined, { maximumFractionDigits: 0 });
  if (v >= 100) return v.toFixed(0);
  if (v >= 10) return v.toFixed(1);
  return v.toFixed(2);
}

function formatHashRateParts(h) {
  const n = Number(h) || 0;
  if (n >= 1e18) return { value: (n / 1e18).toFixed(1), unit: "EH/s" };
  if (n >= 1e15) return { value: (n / 1e15).toFixed(1), unit: "PH/s" };
  if (n >= 1e12) return { value: (n / 1e12).toFixed(1), unit: "TH/s" };
  if (n >= 1e9) return { value: (n / 1e9).toFixed(1), unit: "GH/s" };
  if (n >= 1e6) return { value: (n / 1e6).toFixed(1), unit: "MH/s" };
  if (n >= 1e3) return { value: (n / 1e3).toFixed(1), unit: "KH/s" };
  return { value: (n || 0).toFixed(1), unit: "H/s" };
}

function formatBytesParts(b) {
  const n = Number(b) || 0;
  if (n >= 1e9) return { value: (n / 1e9).toFixed(1), unit: "GB" };
  if (n >= 1e6) return { value: (n / 1e6).toFixed(1), unit: "MB" };
  if (n >= 1e3) return { value: (n / 1e3).toFixed(1), unit: "KB" };
  return { value: String(n || 0), unit: "B" };
}

function formatDifficultyParts(d) {
  const n = Number(d) || 0;
  if (n >= 1e15) return { value: (n / 1e15).toFixed(1), unit: "Q" };
  if (n >= 1e12) return { value: (n / 1e12).toFixed(1), unit: "T" };
  if (n >= 1e9) return { value: (n / 1e9).toFixed(1), unit: "B" };
  if (n >= 1e6) return { value: (n / 1e6).toFixed(1), unit: "M" };
  return { value: formatNumber(Math.round(n)), unit: "" };
}

function formatBytes(b) {
  if (b >= 1e9) return (b / 1e9).toFixed(2) + " GB";
  if (b >= 1e6) return (b / 1e6).toFixed(2) + " MB";
  if (b >= 1e3) return (b / 1e3).toFixed(2) + " KB";
  return (b || 0) + " B";
}

function formatTimeAgo(timestamp) {
  const now = Math.floor(Date.now() / 1000);
  const diff = now - (timestamp || 0);
  if (diff < 60) return diff + " sec ago";
  if (diff < 3600) return Math.floor(diff / 60) + " min ago";
  if (diff < 86400) return Math.floor(diff / 3600) + " hr ago";
  return Math.floor(diff / 86400) + " days ago";
}

function formatDuration(sec) {
  const s = Math.max(0, Math.floor(Number(sec) || 0));
  const h = Math.floor(s / 3600);
  const m = Math.floor((s % 3600) / 60);
  const r = s % 60;
  if (h > 0) return h + "h " + m + "m";
  if (m > 0) return m + "m " + r + "s";
  return r + "s";
}

function formatTempC(c) {
  if (c == null || !Number.isFinite(Number(c))) return "n/a";
  return Number(c).toFixed(1) + " °C";
}

function metricEscape(s) {
  if (s == null) return "";
  return String(s)
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;");
}

function metricBoard(title, body, extraClass) {
  const cls = "metric-board" + (extraClass ? " " + extraClass : "");
  const head = title ? '<h3 class="metric-board-title">' + metricEscape(title) + "</h3>" : "";
  return '<section class="' + cls + '">' + head + body + "</section>";
}

function metricKvHtml(rows) {
  return (
    '<dl class="metric-kv-compact">' +
    (rows || [])
      .map(function (row) {
        const v = row[1];
        const vHtml =
          typeof v === "string" && v.indexOf("<") >= 0 ? v : metricEscape(String(v ?? "n/a"));
        return "<dt>" + metricEscape(row[0]) + "</dt><dd>" + vHtml + "</dd>";
      })
      .join("") +
    "</dl>"
  );
}

function portalKpiHtml(label, value, opts) {
  opts = opts || {};
  let valueClasses = "portal-kpi-value";
  if (opts.highlight) valueClasses += " highlight";
  if (opts.accent) valueClasses += " portal-kpi-accent";
  if (opts.mono) valueClasses += " portal-kpi-value--mono";
  const valueHtml =
    typeof value === "string" && value.indexOf("<") >= 0 ? value : metricEscape(String(value ?? "n/a"));
  const unitHtml = opts.unit
    ? '<span class="portal-kpi-unit">' + metricEscape(opts.unit) + "</span>"
    : "";
  return (
    '<div class="portal-kpi" role="group" aria-label="' +
    metricEscape(label) +
    '"><span class="portal-kpi-label">' +
    metricEscape(label) +
    '</span><span class="' +
    valueClasses +
    '">' +
    valueHtml +
    unitHtml +
    "</span></div>"
  );
}

function portalKpiStrip(items, extraClass) {
  const cls = "portal-kpi-strip" + (extraClass ? " " + extraClass : "");
  return '<div class="' + cls + '">' + items.join("") + "</div>";
}

function metricClusterHtml(title, body) {
  const head = title
    ? '<h4 class="metric-cluster-title">' + metricEscape(title) + "</h4>"
    : "";
  return '<div class="metric-cluster">' + head + body + "</div>";
}

function feeStripHtml(low, med, high) {
  const items = [
    ["Low ~60m", low],
    ["Med ~30m", med],
    ["High ~10m", high],
  ];
  return (
    '<div class="metric-fee-strip" aria-label="Fee rates in sat/vB">' +
    items
      .map(function (item) {
        return (
          '<span class="metric-fee-chip"><span class="metric-fee-chip-label">' +
          escapeHtml(item[0]) +
          '</span><span class="metric-fee-chip-value">' +
          escapeHtml(formatFeeRateCompact(item[1])) +
          "</span></span>"
        );
      })
      .join("") +
    "</div>"
  );
}

function retargetBarHtml(blocksUntilRetarget, progressPct) {
  const pct = Number(progressPct);
  const etaHours = (blocksUntilRetarget * 10 / 60).toFixed(1);
  return (
    '<div class="metric-board metric-board--retarget metric-board--dense">' +
    '<div class="portal-retarget-bar" role="group" aria-label="Difficulty retarget">' +
    '<div class="portal-retarget-head">' +
    '<span class="portal-retarget-label">Retarget</span>' +
    '<span class="portal-retarget-meta">' +
    escapeHtml(progressPct + "%") +
    " · " +
    escapeHtml(blocksUntilRetarget + " blocks") +
    " · ~" +
    escapeHtml(etaHours + "h") +
    "</span></div>" +
    '<div class="portal-retarget-track" role="progressbar" aria-valuenow="' +
    pct +
    '" aria-valuemin="0" aria-valuemax="100" aria-label="Progress to next difficulty retarget">' +
    '<div class="portal-retarget-fill" style="width:' +
    pct +
    '%"></div></div></div></div>'
  );
}

function portalMetricPendingHtml() {
  return '<span class="portal-metric-pending">After sync</span>';
}

function feeStripPendingHtml() {
  const items = ["Low ~60m", "Med ~30m", "High ~10m"];
  return (
    '<div class="metric-fee-strip" aria-label="Fee rates in sat/vB">' +
    items
      .map(function (label) {
        return (
          '<span class="metric-fee-chip"><span class="metric-fee-chip-label">' +
          escapeHtml(label) +
          '</span><span class="metric-fee-chip-value portal-metric-pending">' +
          escapeHtml("After sync") +
          "</span></span>"
        );
      })
      .join("") +
    "</div>"
  );
}

function retargetBarPendingHtml() {
  return (
    '<div class="metric-board metric-board--retarget metric-board--dense">' +
    '<div class="portal-retarget-bar" role="group" aria-label="Difficulty retarget">' +
    '<div class="portal-retarget-head">' +
    '<span class="portal-retarget-label">Retarget</span>' +
    '<span class="portal-retarget-meta portal-metric-pending">After sync</span></div>' +
    '<div class="portal-retarget-track" role="progressbar" aria-valuenow="0" aria-valuemin="0" aria-valuemax="100" aria-label="Progress to next difficulty retarget">' +
    '<div class="portal-retarget-fill" style="width:0"></div></div></div></div>'
  );
}

function renderPortalSyncStatus(el, syncState) {
  if (!el) return;
  const vp = syncState.verificationprogress != null ? Number(syncState.verificationprogress) : null;
  const pct = vp != null && Number.isFinite(vp) ? (vp * 100).toFixed(1) : null;
  const blocks = syncState.blocks != null ? Number(syncState.blocks) : null;
  let text = "Chain syncing";
  if (syncState.initialblockdownload === true) text = "Initial block download";
  if (pct != null) text += ": " + pct + "% verified";
  if (blocks != null && Number.isFinite(blocks)) text += " · block " + formatNumber(blocks);
  setPortalStatusBadge(el, "syncing", text);
}

function syncPendingKpiValue(syncState, field) {
  if (field === "height") {
    const blocks = syncState.blocks != null ? Number(syncState.blocks) : null;
    if (blocks != null && Number.isFinite(blocks)) return formatNumber(blocks);
  }
  return portalMetricPendingHtml();
}

function renderPortalSyncPendingMetrics(syncState, statusEl, beforeEl, upperDetailEl) {
  if (patchPortalSyncPendingMetrics(syncState, statusEl, beforeEl, upperDetailEl)) {
    return;
  }

  const pending = portalMetricPendingHtml();
  renderPortalSyncStatus(statusEl, syncState);

  const vp = syncState.verificationprogress != null ? Number(syncState.verificationprogress) : null;
  const verified =
    vp != null && Number.isFinite(vp) ? (vp * 100).toFixed(1) + "%" : pending;

  const gridHtml = metricBoard(
    "Chain overview",
    portalKpiStrip(
      [
        portalKpiHtml("Height", syncPendingKpiValue(syncState, "height"), { highlight: true }),
        portalKpiHtml("Difficulty", pending),
        portalKpiHtml("Network hash", pending),
        portalKpiHtml("Chain size", pending),
        portalKpiHtml("Mempool tx", pending),
        portalKpiHtml("Peers", pending),
      ],
      "portal-kpi-strip--in-board"
    ),
    "metric-board--chain metric-board--dense"
  );

  const compactSecondaryHtml = metricBoard(
    "",
    '<div class="metric-cluster-grid">' +
      metricClusterHtml(
        "Node",
        metricKvHtml([
          ["Verified", verified],
          ["Chain", pending],
          ["Pruned", pending],
          ["Version", pending],
        ])
      ) +
      metricClusterHtml(
        "Mempool",
        metricKvHtml([
          ["Size", pending],
          ["Min fee", pending],
        ])
      ) +
      metricClusterHtml("Fees · sat/vB", feeStripPendingHtml()) +
      "</div>" +
      '<div class="metric-cluster-divider" aria-hidden="true"></div>' +
      metricClusterHtml(
        "Mining",
        metricKvHtml([
          ["Hash rate", pending],
          ["Accepted", pending],
          ["Temperature", pending],
          ["Uptime", pending],
          ["Difficulty", pending],
          ["Errors", pending],
        ])
      ) +
      '<div class="metric-cluster-divider" aria-hidden="true"></div>' +
      metricClusterHtml(
        "Recent blocks",
        '<div class="portal-compact-note portal-metric-pending">Recent blocks appear after sync</div>'
      ),
    "metric-board--dense metric-board--details"
  );

  const grid = document.getElementById("metricsGrid");
  if (grid) {
    grid.innerHTML = gridHtml;
    grid.removeAttribute("aria-hidden");
  }
  if (beforeEl) beforeEl.innerHTML = retargetBarPendingHtml();
  if (upperDetailEl) upperDetailEl.innerHTML = compactSecondaryHtml;
  portalMetricsRenderMode = "sync";
}

let lastBlockHeight = 0;
/** Keep last rendered portal dashboard when polls fail or RPC is briefly unavailable. */
let portalMetricsEverRendered = false;
let portalMiningEverRendered = false;
/** Stay in sync-only layout if RPC drops briefly during IBD / chain verification. */
let portalChainSyncSticky = false;
let portalMempoolIframeInitialized = false;
let portalLayoutRevealed = false;
/** 'sync' | 'live' — avoid full innerHTML rebuilds on every poll (layout flash). */
let portalMetricsRenderMode = null;

function setPortalStatusBadge(el, kind, text) {
  if (!el) return;
  const existing = el.querySelector(".status-badge");
  const cls = kind === "syncing" ? "status-badge status-syncing" : "status-badge status-connected";
  if (existing && existing.className === cls) {
    if (existing.textContent !== text) existing.textContent = text;
    return;
  }
  el.innerHTML = '<span class="' + cls + '">' + escapeHtml(text) + "</span>";
}

function findPortalKpi(root, label) {
  if (!root) return null;
  const kpis = root.querySelectorAll(".portal-kpi");
  for (let i = 0; i < kpis.length; i++) {
    const lbl = kpis[i].querySelector(".portal-kpi-label");
    if (lbl && lbl.textContent === label) return kpis[i];
  }
  return null;
}

function patchPortalKpi(root, label, value, unit) {
  const kpi = findPortalKpi(root, label);
  if (!kpi) return false;
  const valueEl = kpi.querySelector(".portal-kpi-value");
  if (!valueEl) return false;
  const valueText = String(value ?? "n/a");
  const unitText = unit ? String(unit) : "";
  let unitEl = valueEl.querySelector(".portal-kpi-unit");
  // Preserve value text node(s); only rewrite when changed.
  const currentUnit = unitEl ? unitEl.textContent : "";
  let currentValue = "";
  valueEl.childNodes.forEach(function (node) {
    if (node.nodeType === Node.TEXT_NODE) currentValue += node.textContent;
  });
  currentValue = currentValue.trim();
  if (currentValue !== valueText || currentUnit !== unitText) {
    if (unitText) {
      valueEl.textContent = "";
      valueEl.appendChild(document.createTextNode(valueText));
      unitEl = document.createElement("span");
      unitEl.className = "portal-kpi-unit";
      unitEl.textContent = unitText;
      valueEl.appendChild(unitEl);
    } else {
      valueEl.textContent = valueText;
    }
  }
  return true;
}

function patchPortalKpiPending(root, label) {
  const kpi = findPortalKpi(root, label);
  if (!kpi) return false;
  const valueEl = kpi.querySelector(".portal-kpi-value");
  if (!valueEl) return false;
  const pending = valueEl.querySelector(".portal-metric-pending");
  if (pending && pending.textContent === "After sync") return true;
  valueEl.innerHTML = portalMetricPendingHtml();
  return true;
}

function findMetricDd(root, label) {
  if (!root) return null;
  const dts = root.querySelectorAll("dt");
  for (let i = 0; i < dts.length; i++) {
    if (dts[i].textContent === label) {
      const dd = dts[i].nextElementSibling;
      if (dd && dd.tagName === "DD") return dd;
    }
  }
  return null;
}

function patchMetricKv(root, label, valueHtmlOrText) {
  const dd = findMetricDd(root, label);
  if (!dd) return false;
  const html =
    typeof valueHtmlOrText === "string" && valueHtmlOrText.indexOf("<") >= 0
      ? valueHtmlOrText
      : metricEscape(String(valueHtmlOrText ?? "n/a"));
  if (dd.innerHTML !== html) dd.innerHTML = html;
  return true;
}

function patchRetargetBar(beforeEl, blocksUntilRetarget, progressPct) {
  if (!beforeEl) return false;
  const bar = beforeEl.querySelector(".portal-retarget-bar");
  if (!bar) return false;
  const pct = Number(progressPct);
  const etaHours = ((blocksUntilRetarget * 10) / 60).toFixed(1);
  const meta = bar.querySelector(".portal-retarget-meta");
  const track = bar.querySelector(".portal-retarget-track");
  const fill = bar.querySelector(".portal-retarget-fill");
  const metaText = progressPct + "% · " + blocksUntilRetarget + " blocks · ~" + etaHours + "h";
  if (meta && meta.textContent !== metaText) meta.textContent = metaText;
  if (track) track.setAttribute("aria-valuenow", String(pct));
  if (fill) {
    const width = pct + "%";
    if (fill.style.width !== width) fill.style.width = width;
  }
  return true;
}

function patchFeeStrip(root, low, med, high) {
  if (!root) return false;
  const chips = root.querySelectorAll(".metric-fee-chip-value");
  if (chips.length < 3) return false;
  const vals = [formatFeeRateCompact(low), formatFeeRateCompact(med), formatFeeRateCompact(high)];
  for (let i = 0; i < 3; i++) {
    if (chips[i].textContent !== vals[i]) chips[i].textContent = vals[i];
  }
  return true;
}

function recentBlocksHtml(blocks) {
  const rows = (blocks || [])
    .slice()
    .sort((a, b) => (b.height || 0) - (a.height || 0))
    .slice(0, 4)
    .map(
      (b) => `
      <div class="portal-mini-block">
        <div class="portal-mini-block-head">
          <span class="portal-mini-block-height">#${formatNumber(b.height || 0)}</span>
          <span class="portal-mini-block-time">${formatTimeAgo(b.timestamp || 0)}</span>
        </div>
        <div class="portal-mini-block-stats">
          <span>${formatNumber(b.tx_count || 0)} tx</span>
          <span>${formatBytes(b.size || 0)}</span>
        </div>
      </div>
    `
    )
    .join("");
  return rows || '<div class="portal-compact-note">No recent block data.</div>';
}

function patchRecentBlocks(root, blocks) {
  if (!root) return false;
  const host = root.querySelector(".portal-mini-blocks");
  if (!host) return false;
  const sorted = (blocks || [])
    .slice()
    .sort((a, b) => (b.height || 0) - (a.height || 0))
    .slice(0, 4);
  const existing = host.querySelectorAll(".portal-mini-block");
  const heightsMatch =
    existing.length === sorted.length &&
    sorted.every(function (b, i) {
      const h = existing[i].querySelector(".portal-mini-block-height");
      return h && h.textContent === "#" + formatNumber(b.height || 0);
    });
  if (heightsMatch) {
    sorted.forEach(function (b, i) {
      const timeEl = existing[i].querySelector(".portal-mini-block-time");
      const stats = existing[i].querySelectorAll(".portal-mini-block-stats span");
      const nextTime = formatTimeAgo(b.timestamp || 0);
      if (timeEl && timeEl.textContent !== nextTime) timeEl.textContent = nextTime;
      if (stats[0]) {
        const tx = formatNumber(b.tx_count || 0) + " tx";
        if (stats[0].textContent !== tx) stats[0].textContent = tx;
      }
      if (stats[1]) {
        const size = formatBytes(b.size || 0);
        if (stats[1].textContent !== size) stats[1].textContent = size;
      }
    });
    return true;
  }
  const next = recentBlocksHtml(blocks);
  if (host.innerHTML !== next) host.innerHTML = next;
  return true;
}

function portalLiveStructureReady() {
  const grid = document.getElementById("metricsGrid");
  const beforeEl = document.getElementById("metricsBeforeMempool");
  const upperDetailEl = document.getElementById("metricsUpperDetail");
  return !!(
    grid &&
    grid.querySelector(".portal-kpi-strip") &&
    beforeEl &&
    beforeEl.querySelector(".portal-retarget-bar") &&
    upperDetailEl &&
    upperDetailEl.querySelector("#compactMiningSummary") &&
    upperDetailEl.querySelector(".portal-mini-blocks")
  );
}

function portalSyncStructureReady() {
  const grid = document.getElementById("metricsGrid");
  const beforeEl = document.getElementById("metricsBeforeMempool");
  const upperDetailEl = document.getElementById("metricsUpperDetail");
  return !!(
    grid &&
    grid.querySelector(".portal-kpi-strip") &&
    beforeEl &&
    beforeEl.querySelector(".portal-retarget-bar") &&
    upperDetailEl &&
    upperDetailEl.querySelector(".portal-metric-pending")
  );
}

function patchPortalSyncPendingMetrics(syncState, statusEl, beforeEl, upperDetailEl) {
  if (portalMetricsRenderMode !== "sync" || !portalSyncStructureReady()) return false;
  const grid = document.getElementById("metricsGrid");
  const vp = syncState.verificationprogress != null ? Number(syncState.verificationprogress) : null;
  const pct = vp != null && Number.isFinite(vp) ? (vp * 100).toFixed(1) : null;
  const blocks = syncState.blocks != null ? Number(syncState.blocks) : null;
  let text = "Chain syncing";
  if (syncState.initialblockdownload === true) text = "Initial block download";
  if (pct != null) text += ": " + pct + "% verified";
  if (blocks != null && Number.isFinite(blocks)) text += " · block " + formatNumber(blocks);
  setPortalStatusBadge(statusEl, "syncing", text);

  if (blocks != null && Number.isFinite(blocks)) {
    patchPortalKpi(grid, "Height", formatNumber(blocks), "");
  } else {
    patchPortalKpiPending(grid, "Height");
  }
  ["Difficulty", "Network hash", "Chain size", "Mempool tx", "Peers"].forEach(function (label) {
    patchPortalKpiPending(grid, label);
  });

  const verified =
    vp != null && Number.isFinite(vp) ? (vp * 100).toFixed(1) + "%" : portalMetricPendingHtml();
  patchMetricKv(upperDetailEl, "Verified", verified);
  return true;
}

function patchPortalLiveMetrics(d, statusEl, beforeEl, upperDetailEl) {
  if (portalMetricsRenderMode !== "live" || !portalLiveStructureReady()) return false;
  const grid = document.getElementById("metricsGrid");
  const nodeVer = (d.node_version || d.nodeVersion || "").trim();
  const statusText = nodeVer
    ? "Connected to BIP-110 compliant node " + (nodeVer.startsWith("v") ? nodeVer : "v" + nodeVer)
    : "Connected to BIP-110 compliant node";
  setPortalStatusBadge(statusEl, "connected", statusText);

  const blocksUntilRetarget = d.blocks_until_retarget ?? 2016;
  const progressPct = (((2016 - blocksUntilRetarget) / 2016) * 100).toFixed(1);
  const verifyPct = (d.verificationprogress || 0) * 100;
  const diffParts = formatDifficultyParts(d.difficulty || 0);
  const hashParts = formatHashRateParts(d.networkhashps || 0);
  const chainParts = formatBytesParts(d.size_on_disk || 0);
  const mempoolParts = formatBytesParts(d.mempool_size || d.mempool_bytes || 0);
  const nodeVerStr = nodeVer ? (nodeVer.startsWith("v") ? nodeVer : "v" + nodeVer) : "n/a";
  const mempoolSize = mempoolParts.value + (mempoolParts.unit ? " " + mempoolParts.unit : "");

  if (!patchPortalKpi(grid, "Height", formatNumber(d.blocks || 0), "")) return false;
  if (!patchPortalKpi(grid, "Difficulty", diffParts.value, diffParts.unit)) return false;
  if (!patchPortalKpi(grid, "Network hash", hashParts.value, hashParts.unit)) return false;
  if (!patchPortalKpi(grid, "Chain size", chainParts.value, chainParts.unit)) return false;
  if (!patchPortalKpi(grid, "Mempool tx", formatNumber(d.mempool_tx || 0), "")) return false;
  if (!patchPortalKpi(grid, "Peers", formatNumber(d.connections || 0), "")) return false;
  if (!patchRetargetBar(beforeEl, blocksUntilRetarget, progressPct)) return false;

  patchMetricKv(upperDetailEl, "Verified", verifyPct.toFixed(1) + "%");
  patchMetricKv(upperDetailEl, "Chain", String(d.chain || "unknown"));
  patchMetricKv(upperDetailEl, "Pruned", d.pruned ? "Yes" : "No");
  patchMetricKv(upperDetailEl, "Version", nodeVerStr);
  patchMetricKv(upperDetailEl, "Size", mempoolSize);
  patchMetricKv(
    upperDetailEl,
    "Min fee",
    (d.mempool_minfee || 0).toFixed(8) + ' <span class="portal-kv-unit">BTC/kB</span>'
  );
  patchFeeStrip(upperDetailEl, d.fee_low, d.fee_medium, d.fee_high);
  patchRecentBlocks(upperDetailEl, d.recent_blocks);

  if (d.blocks > lastBlockHeight && lastBlockHeight > 0) {
    const firstBlock = upperDetailEl.querySelector(".portal-mini-block");
    if (firstBlock) {
      const themeClass = "new-block-orange";
      firstBlock.classList.add(themeClass);
      setTimeout(() => firstBlock.classList.remove(themeClass), 1200);
    }
  }
  lastBlockHeight = d.blocks;
  return true;
}

function chainSyncSignalsPending(d) {
  if (!d || typeof d !== "object" || d.connected !== true) return false;
  if (d.initialblockdownload === true) return true;
  const vp = d.verificationprogress != null ? Number(d.verificationprogress) : 1;
  if (!Number.isFinite(vp) || vp < 0.99999) return true;
  const blocks = d.blocks != null ? Number(d.blocks) : null;
  const headers = d.headers != null ? Number(d.headers) : blocks;
  if (blocks != null && headers != null && headers - blocks > 1) return true;
  return false;
}

function isPortalChainSyncPending(d) {
  if (!d || typeof d !== "object") return portalChainSyncSticky;
  const pending = chainSyncSignalsPending(d);
  if (pending) {
    portalChainSyncSticky = true;
    return true;
  }
  /* Require live display-sync before first reveal; stale cached metrics can look "synced" during reindex. */
  if (d.connected === true && !d.displaySyncResolved && !portalLayoutRevealed) {
    portalChainSyncSticky = true;
    return true;
  }
  if (d.connected === true) portalChainSyncSticky = false;
  if (d.connected !== true && portalChainSyncSticky) return true;
  return false;
}

function setPortalMempoolVisible(show) {
  document.body.classList.toggle("portal-mempool-visible", !!show);
  const card = document.getElementById("portalMempoolCard");
  if (card) card.hidden = !show;
}

function applyPortalChainSyncLayout(d) {
  const pending = isPortalChainSyncPending(d);
  document.body.classList.toggle("portal-chain-syncing", pending);
  if (portalLayoutRevealed) {
    setPortalMempoolVisible(!pending);
    if (!pending) maybeInitDeferredMempoolIframe();
  }
}

async function fetchPortalSyncState(blockchainData) {
  let syncState = { connected: false, displaySyncResolved: false };

  try {
    const sr = await blockvaseFetch("/display-sync");
    if (sr.ok) {
      const live = await sr.json();
      if (live && typeof live === "object") {
        syncState = {
          ...syncState,
          ...live,
          displaySyncResolved: live.connected === true,
        };
      }
    }
  } catch (_) {}

  if (blockchainData && typeof blockchainData === "object") {
    if (!syncState.displaySyncResolved) {
      Object.assign(syncState, blockchainData);
    } else {
      const fillKeys = [
        "node_version",
        "nodeVersion",
        "blocks_until_retarget",
        "difficulty",
        "networkhashps",
        "size_on_disk",
        "mempool_size",
        "mempool_bytes",
        "mempool_tx",
        "connections",
        "recent_blocks",
        "chain",
        "pruned",
        "mempool_minfee",
        "fee_low",
        "fee_medium",
        "fee_high",
        "metrics_stale",
      ];
      fillKeys.forEach(function (key) {
        if (syncState[key] == null && blockchainData[key] != null) {
          syncState[key] = blockchainData[key];
        }
      });
    }
  }

  return syncState;
}

/** Match backend state poller / settings stats refresh */
const METRICS_POLL_MS = 5000;
let metricsPollTimer = null;

function loadDeviceName() {
  const h1 = document.getElementById("deviceNameHeader");
  if (h1) h1.textContent = "Blockvase";
}

async function loadMiningSection() {
  const host = document.getElementById("compactMiningSummary");
  if (!host) return;
  if (document.body.classList.contains("portal-chain-syncing")) return;

  function renderMiningSummary(d, available) {
    const hrHs = available ? Number(d.hashrate_hs) || 0 : 0;
    const accepted = available ? Number(d.accepted) || 0 : 0;
    const temp = available ? formatTempC(d.temperature_c) : "n/a";
    const uptime = available ? formatDuration(d.uptime_sec) : "0s";
    const difficulty = Number(d.difficulty);
    const diffStr =
      available && Number.isFinite(difficulty) && difficulty > 0 ? formatDifficulty(difficulty) : "n/a";
    const errors = available ? formatNumber(d.pool_errors || 0) : "0";
    const hashParts = formatHashRateParts(hrHs);
    const rows = [
      ["Hash rate", hashParts.value + (hashParts.unit ? " " + hashParts.unit : "")],
      ["Accepted", formatNumber(accepted)],
      ["Temperature", temp],
      ["Uptime", uptime],
      ["Difficulty", diffStr],
      ["Errors", errors],
    ];

    // Patch in place when the mining KV list already exists (avoids flash each poll).
    if (host.querySelector("dl.metric-kv-compact")) {
      let ok = true;
      rows.forEach(function (row) {
        if (!patchMetricKv(host, row[0], row[1])) ok = false;
      });
      if (ok) return;
    }
    host.innerHTML = metricKvHtml(rows);
  }

  try {
    const r = await blockvaseFetch("/mining");
    const d = await r.json();
    renderMiningSummary(d, Boolean(d.available));
    portalMiningEverRendered = true;
  } catch (e) {
    if (!portalMiningEverRendered) return;
  }
}

async function loadMetrics() {
  const beforeEl = document.getElementById("metricsBeforeMempool");
  const upperDetailEl = document.getElementById("metricsUpperDetail");
  const status = document.getElementById("status");
  let d = { connected: false };
  try {
    try {
      const r = await blockvaseFetch("/blockchain-info");
      d = await r.json();
    } catch (_) {
      if (portalMetricsEverRendered) return;
    }

    const syncState = await fetchPortalSyncState(d);
    const syncPending = isPortalChainSyncPending(syncState);
    applyPortalChainSyncLayout(syncState);
    if (syncPending) {
      renderPortalSyncPendingMetrics(syncState, status, beforeEl, upperDetailEl);
      portalMetricsEverRendered = true;
      return;
    }

    if (!d.connected) {
      if (portalMetricsEverRendered) return;
      return;
    }

    if (patchPortalLiveMetrics(d, status, beforeEl, upperDetailEl)) {
      portalMetricsEverRendered = true;
      return;
    }

    const nodeVer = (d.node_version || d.nodeVersion || "").trim();
    const statusText = nodeVer
      ? "Connected to BIP-110 compliant node " + (nodeVer.startsWith("v") ? nodeVer : "v" + nodeVer)
      : "Connected to BIP-110 compliant node";
    setPortalStatusBadge(status, "connected", statusText);

    const blocksUntilRetarget = d.blocks_until_retarget ?? 2016;
    const progressPct = ((2016 - blocksUntilRetarget) / 2016 * 100).toFixed(1);

    const verifyPct = (d.verificationprogress || 0) * 100;
    const diffParts = formatDifficultyParts(d.difficulty || 0);
    const hashParts = formatHashRateParts(d.networkhashps || 0);
    const chainParts = formatBytesParts(d.size_on_disk || 0);
    const mempoolParts = formatBytesParts(d.mempool_size || d.mempool_bytes || 0);

    const gridHtml = metricBoard(
      "Chain overview",
      portalKpiStrip(
        [
          portalKpiHtml("Height", formatNumber(d.blocks || 0), { highlight: true }),
          portalKpiHtml("Difficulty", diffParts.value, { unit: diffParts.unit }),
          portalKpiHtml("Network hash", hashParts.value, { unit: hashParts.unit, accent: true }),
          portalKpiHtml("Chain size", chainParts.value, { unit: chainParts.unit }),
          portalKpiHtml("Mempool tx", formatNumber(d.mempool_tx || 0)),
          portalKpiHtml("Peers", formatNumber(d.connections || 0)),
        ],
        "portal-kpi-strip--in-board"
      ),
      "metric-board--chain metric-board--dense"
    );

    const beforeMempoolHtml = retargetBarHtml(blocksUntilRetarget, progressPct);

    const nodeVerStr = nodeVer ? (nodeVer.startsWith("v") ? nodeVer : "v" + nodeVer) : "n/a";
    const mempoolSize =
      mempoolParts.value + (mempoolParts.unit ? " " + mempoolParts.unit : "");

    const recentBlockRows = recentBlocksHtml(d.recent_blocks);

    const compactSecondaryHtml = metricBoard(
      "",
      '<div class="metric-cluster-grid">' +
        metricClusterHtml(
          "Node",
          metricKvHtml([
            ["Verified", verifyPct.toFixed(1) + "%"],
            ["Chain", escapeHtml(String(d.chain || "unknown"))],
            ["Pruned", d.pruned ? "Yes" : "No"],
            ["Version", escapeHtml(nodeVerStr)],
          ])
        ) +
        metricClusterHtml(
          "Mempool",
          metricKvHtml([
            ["Size", escapeHtml(mempoolSize)],
            [
              "Min fee",
              (d.mempool_minfee || 0).toFixed(8) + ' <span class="portal-kv-unit">BTC/kB</span>',
            ],
          ])
        ) +
        metricClusterHtml(
          "Fees · sat/vB",
          feeStripHtml(d.fee_low, d.fee_medium, d.fee_high)
        ) +
        "</div>" +
        '<div class="metric-cluster-divider" aria-hidden="true"></div>' +
        metricClusterHtml(
          "Mining",
          '<div id="compactMiningSummary" aria-label="Solo mining summary"><div class="portal-compact-note">Loading…</div></div>'
        ) +
        '<div class="metric-cluster-divider" aria-hidden="true"></div>' +
        metricClusterHtml(
          "Recent blocks",
          '<div class="portal-mini-blocks portal-mini-blocks--row">' +
            recentBlockRows +
            "</div>"
        ),
      "metric-board--dense metric-board--details"
    );

    const upperDetailHtml = compactSecondaryHtml;

    const grid = document.getElementById("metricsGrid");
    if (grid) {
      grid.innerHTML = gridHtml;
      grid.removeAttribute("aria-hidden");
    }
    if (beforeEl) beforeEl.innerHTML = beforeMempoolHtml;
    if (upperDetailEl) upperDetailEl.innerHTML = upperDetailHtml;

    portalMetricsEverRendered = true;
    portalMetricsRenderMode = "live";

    if (d.blocks > lastBlockHeight && lastBlockHeight > 0) {
      const firstBlock = document.querySelector(".portal-mini-block");
      if (firstBlock) {
        const themeClass = "new-block-orange";
        firstBlock.classList.add(themeClass);
        setTimeout(() => firstBlock.classList.remove(themeClass), 1200);
      }
    }
    lastBlockHeight = d.blocks;
  } catch (e) {
    if (portalMetricsEverRendered) return;
  } finally {
    if (!portalLayoutRevealed) revealPortalLayout();
  }
}

function initPortalFullscreen() {
  const btn = document.getElementById("portalFullscreen");
  if (!btn) return;
  const el = document.documentElement;
  const fsEl = () => document.fullscreenElement ?? document.webkitFullscreenElement;
  btn.addEventListener("click", () => {
    if (!fsEl()) {
      (el.requestFullscreen ?? el.webkitRequestFullscreen)?.call(el);
    } else {
      (document.exitFullscreen ?? document.webkitExitFullscreen)?.call(document);
    }
  });
  function updateLabel() {
    btn.setAttribute("aria-label", fsEl() ? "Exit fullscreen" : "Fullscreen");
  }
  document.addEventListener("fullscreenchange", updateLabel);
  document.addEventListener("webkitfullscreenchange", updateLabel);
}

function initDisplayOpenFull() {
  const btn = document.getElementById("displayOpenFull");
  if (!btn) return;
  btn.addEventListener("click", () => {
    window.open(window.location.origin + "/mempool", "_blank");
  });
}

function initDeferredMempoolIframe() {
  const iframe = document.getElementById("displayIframe");
  const wrapper = document.getElementById("displayIframeWrapper");
  if (!iframe || !wrapper || !iframe.dataset.src) return;
  let assigned = false;
  function assignIframeSrc() {
    if (assigned) return;
    assigned = true;
    iframe.src = iframe.dataset.src;
    setTimeout(() => wrapper.classList.add("display-iframe-wrapper--cover-hidden"), 100);
  }
  if (document.readyState === "complete") {
    setTimeout(assignIframeSrc, 0);
  } else {
    window.addEventListener("load", () => setTimeout(assignIframeSrc, 0), { once: true });
  }
}

function dismissPortalSplash() {
  const el = document.getElementById("portalSplashOverlay");
  if (!el || el.classList.contains("portal-splash-overlay--gone")) return;
  function finish() {
    el.classList.add("portal-splash-overlay--gone");
  }
  function fadeOut() {
    if (el.classList.contains("portal-splash-overlay--hide")) return;
    el.classList.add("portal-splash-overlay--hide");
    el.addEventListener("transitionend", finish, { once: true });
    setTimeout(finish, 600);
  }
  fadeOut();
}

function revealPortalLayout() {
  if (portalLayoutRevealed) return;
  portalLayoutRevealed = true;
  setPortalMempoolVisible(!document.body.classList.contains("portal-chain-syncing"));
  document.body.classList.remove("portal-layout-pending");
  const loader = document.getElementById("portalLayoutLoader");
  if (loader) loader.setAttribute("aria-busy", "false");
  dismissPortalSplash();
  if (!document.body.classList.contains("portal-chain-syncing")) {
    maybeInitDeferredMempoolIframe();
  }
}

function maybeInitDeferredMempoolIframe() {
  if (portalMempoolIframeInitialized) return;
  if (document.body.classList.contains("portal-chain-syncing")) return;
  portalMempoolIframeInitialized = true;
  initDeferredMempoolIframe();
}

function fmtSats(btc) {
  return Math.round((btc || 0) * 1e8).toLocaleString();
}

function shortAddr(addr, len = 8) {
  if (!addr || addr.length <= len * 2) return addr || "n/a";
  return addr.slice(0, len) + "…" + addr.slice(-len);
}

const TX_NODE_GROUP_THRESHOLD = 10;

function txTableCell(display, copyValue, row, col, opts) {
  opts = opts || {};
  let cls = "tx-table-cell";
  if (opts.mono) cls += " tx-table-cell--mono";
  if (opts.num) cls += " tx-table-cell--num";
  if (opts.action) cls += " tx-table-cell--action";
  const copy = copyValue != null && copyValue !== "" ? String(copyValue) : String(display ?? "n/a");
  const action = opts.action ? ' data-action="' + escapeHtml(opts.action) + '"' : "";
  return (
    "<td class=\"" +
    cls +
    '" tabindex="0" data-row="' +
    row +
    '" data-col="' +
    col +
    '" data-copy="' +
    escapeHtml(copy) +
    '"' +
    action +
    ">" +
    escapeHtml(String(display ?? "n/a")) +
    "</td>"
  );
}

function sumIoSats(nodes) {
  return nodes.reduce(function (sum, n) {
    if (n.value == null) return sum;
    const v = parseInt(String(n.value).replace(/,/g, ""), 10);
    return sum + (Number.isFinite(v) ? v : 0);
  }, 0);
}

function renderTxTableHtml(tx) {
  const vin = tx.vin || [];
  const vout = tx.vout || [];

  const inpNodes = vin.map((inp, i) => {
    if (inp.coinbase) return { label: "Coinbase", full: "coinbase", value: null };
    const prev = inp.prevout || {};
    const spk = prev.scriptPubKey || {};
    const fullAddr = spk.address || spk.desc || (inp.txid ? inp.txid + ":" + (inp.vout ?? "") : null);
    const label = fullAddr ? (fullAddr.length > 20 ? shortAddr(fullAddr, 10) : fullAddr) : "Input " + (i + 1);
    const val = prev.value != null ? fmtSats(prev.value) : null;
    return { label, full: fullAddr || label, value: val };
  });

  const outNodes = vout.map((out, i) => {
    const spk = out.scriptPubKey || {};
    const fullAddr = spk.address || spk.desc || null;
    const label = fullAddr || (out.scriptPubKey?.type || "Output") + " " + (i + 1);
    const displayLabel = fullAddr && fullAddr.length > 20 ? shortAddr(fullAddr, 10) : label;
    const val = out.value != null ? fmtSats(out.value) : null;
    return { label: displayLabel, full: fullAddr || label, value: val };
  });

  let navRow = 0;

  function ioDataRow(node, index) {
    const r = navRow++;
    const valDisplay = node.value != null ? node.value + " sats" : "n/a";
    const valCopy = node.value != null ? String(node.value).replace(/,/g, "") : "";
    return (
      "<tr class=\"tx-table-row\">" +
      '<td class="tx-table-cell tx-table-cell--idx">' +
      (index + 1) +
      "</td>" +
      txTableCell(node.label, node.full, r, 0, { mono: true }) +
      txTableCell(valDisplay, valCopy, r, 1, { num: true }) +
      "</tr>"
    );
  }

  function renderIoTableBody(nodes, kindLabel) {
    if (nodes.length <= TX_NODE_GROUP_THRESHOLD) {
      return nodes.map(ioDataRow).join("");
    }
    const count = nodes.length;
    const total = sumIoSats(nodes);
    const summary =
      count +
      " " +
      kindLabel +
      (total > 0 ? " · " + total.toLocaleString() + " sats total" : "") +
      " · Enter to expand";
    const r = navRow++;
    let html =
      '<tbody class="tx-table-group" data-collapsed="1">' +
      "<tr class=\"tx-table-group-summary\"><td colspan=\"3\" class=\"tx-table-cell tx-table-cell--action tx-table-cell--mono\" tabindex=\"0\" data-row=\"" +
      r +
      '" data-col="0" data-copy="' +
      escapeHtml(summary) +
      '" data-action="toggle-group">' +
      escapeHtml(summary) +
      "</td></tr>";
    html += nodes
      .map(function (node, i) {
        return ioDataRow(node, i).replace("<tr", '<tr hidden class="tx-table-group-row"');
      })
      .join("");
    const rCollapse = navRow++;
    html +=
      '<tr hidden class="tx-table-group-collapse"><td colspan="3" class="tx-table-cell tx-table-cell--action" tabindex="0" data-row="' +
      rCollapse +
      '" data-col="0" data-copy="collapse" data-action="toggle-group">' +
      escapeHtml("Enter to collapse") +
      "</td></tr></tbody>";
    return html;
  }

  function renderIoSection(title, nodes, kindLabel) {
    const head =
      "<thead><tr><th scope=\"col\">#</th><th scope=\"col\">Source</th><th scope=\"col\">Amount</th></tr></thead>";
    const body =
      nodes.length <= TX_NODE_GROUP_THRESHOLD
        ? "<tbody>" + nodes.map(ioDataRow).join("") + "</tbody>"
        : renderIoTableBody(nodes, kindLabel);
    return (
      '<section class="tx-table-section"><h4 class="tx-table-section-title">' +
      escapeHtml(title) +
      '</h4><div class="tx-table-wrap"><div class="tx-table-scroll"><table class="tx-table">' +
      head +
      body +
      "</table></div></div></section>"
    );
  }

  const txFull = tx.txid || tx.hash || "n/a";
  const txRow = navRow++;
  const txDisplay = txFull.length > 24 ? shortAddr(txFull, 12) : txFull;
  const txSection =
    '<section class="tx-table-section"><h4 class="tx-table-section-title">Transaction</h4>' +
    '<div class="tx-table-wrap"><div class="tx-table-scroll"><table class="tx-table tx-table--kv">' +
    "<tbody><tr>" +
    '<th scope="row" class="tx-table-row-label">TXID</th>' +
    txTableCell(txDisplay, txFull, txRow, 0, { mono: true }) +
    "</tr></tbody></table></div></div></section>";

  return (
    '<p class="tx-table-hint muted-note">Arrow keys to move · drag to select text · Enter or Ctrl+C copies cell · Enter on grouped rows to expand</p>' +
    renderIoSection("Inputs", inpNodes, "inputs") +
    txSection +
    renderIoSection("Outputs", outNodes, "outputs")
  );
}

function showTxTableCopyToast(root, message) {
  const toast = root?.closest(".tx-detail-body")?.querySelector("#txTableCopyToast");
  if (!toast) return;
  toast.textContent = message;
  toast.hidden = false;
  clearTimeout(showTxTableCopyToast._timer);
  showTxTableCopyToast._timer = setTimeout(function () {
    toast.hidden = true;
  }, 1400);
}

function txTableNavCells(root) {
  if (!root) return [];
  return Array.from(root.querySelectorAll(".tx-table-cell:not(.tx-table-cell--idx)")).filter(function (el) {
    if (el.hidden) return false;
    const row = el.closest("tr");
    if (row?.hidden) return false;
    const group = el.closest(".tx-table-group");
    if (group && group.dataset.collapsed !== "0") {
      return el.closest(".tx-table-group-summary") != null;
    }
    return true;
  });
}

function txTableClearFocus(root) {
  if (!root) return;
  root.querySelectorAll(".tx-table-cell--focused").forEach(function (el) {
    el.classList.remove("tx-table-cell--focused");
  });
}

function txTableCellHasTextSelection(cell) {
  const sel = window.getSelection();
  if (!sel || sel.isCollapsed || !cell) return false;
  const node = sel.anchorNode;
  return !!(node && cell.contains(node));
}

function txTableScrollContainer(el) {
  if (!el) return false;
  const s = window.getComputedStyle(el);
  return /(auto|scroll|overlay)/.test(s.overflowX + " " + s.overflowY);
}

function txTableScrollElementIntoContainer(container, target, pad) {
  const cRect = container.getBoundingClientRect();
  const tRect = target.getBoundingClientRect();
  if (tRect.left < cRect.left + pad) {
    container.scrollLeft += tRect.left - (cRect.left + pad);
  } else if (tRect.right > cRect.right - pad) {
    container.scrollLeft += tRect.right - (cRect.right - pad);
  }
  if (tRect.top < cRect.top + pad) {
    container.scrollTop += tRect.top - (cRect.top + pad);
  } else if (tRect.bottom > cRect.bottom - pad) {
    container.scrollTop += tRect.bottom - (cRect.bottom - pad);
  }
}

function txTableScrollCellIntoView(cell) {
  if (!cell) return;
  const pad = 8;
  let node = cell.parentElement;
  while (node && node !== document.documentElement) {
    if (txTableScrollContainer(node)) {
      txTableScrollElementIntoContainer(node, cell, pad);
    }
    node = node.parentElement;
  }
}

function txTableMarkFocused(root, cell, opts) {
  opts = opts || {};
  if (!root || !cell) return;
  txTableClearFocus(root);
  cell.classList.add("tx-table-cell--focused");
  if (opts.focus !== false) {
    try {
      cell.focus({ preventScroll: true });
    } catch (_) {}
  }
  if (opts.clearSelection) {
    const sel = window.getSelection();
    if (sel) sel.removeAllRanges();
  }
  if (opts.scroll) {
    requestAnimationFrame(function () {
      txTableScrollCellIntoView(cell);
    });
  }
}

function txTableFocusCell(root, cell, opts) {
  opts = opts || {};
  txTableMarkFocused(root, cell, { clearSelection: true, scroll: !!opts.scroll });
}

function txTableFocusedCell(root) {
  return root?.querySelector(".tx-table-cell--focused") || null;
}

function txTableFocusRelative(root, delta) {
  const cells = txTableNavCells(root);
  if (!cells.length) return;
  let idx = cells.indexOf(txTableFocusedCell(root));
  if (idx < 0) idx = 0;
  else idx = Math.max(0, Math.min(cells.length - 1, idx + delta));
  txTableFocusCell(root, cells[idx], { scroll: true });
}

function txTableFocusGrid(root, rowDelta, colDelta) {
  const cells = txTableNavCells(root);
  if (!cells.length) return;
  const cell = txTableFocusedCell(root);
  if (!cell) {
    txTableFocusCell(root, cells[0], { scroll: true });
    return;
  }
  const row = Number(cell.dataset.row);
  const col = Number(cell.dataset.col);
  if (!Number.isFinite(row) || !Number.isFinite(col)) {
    txTableFocusRelative(root, rowDelta !== 0 ? rowDelta : colDelta);
    return;
  }
  const target = cells.find(function (el) {
    return Number(el.dataset.row) === row + rowDelta && Number(el.dataset.col) === col + colDelta;
  });
  if (target) txTableFocusCell(root, target, { scroll: true });
  else txTableFocusRelative(root, rowDelta !== 0 ? rowDelta : colDelta);
}

function txTableToggleGroup(group, root) {
  if (!group) return;
  const collapsed = group.dataset.collapsed !== "0";
  group.dataset.collapsed = collapsed ? "0" : "1";
  const isExpanded = group.dataset.collapsed === "0";
  const summary = group.querySelector(".tx-table-group-summary");
  const collapseRow = group.querySelector(".tx-table-group-collapse");
  const detailRows = group.querySelectorAll(".tx-table-group-row");
  if (summary) summary.hidden = isExpanded;
  if (collapseRow) collapseRow.hidden = !isExpanded;
  detailRows.forEach(function (row) {
    row.hidden = !isExpanded;
  });
  const next = isExpanded
    ? group.querySelector(".tx-table-group-collapse .tx-table-cell") ||
      group.querySelector(".tx-table-group-row .tx-table-cell")
    : group.querySelector(".tx-table-group-summary .tx-table-cell");
  if (root && next) txTableFocusCell(root, next, { scroll: true });
}

async function copyTextToClipboard(text, restoreFocusEl) {
  if (text == null || text === "") return false;
  const value = String(text);
  const restore =
    restoreFocusEl && typeof restoreFocusEl.focus === "function" ? restoreFocusEl : null;
  try {
    if (navigator.clipboard && window.isSecureContext) {
      try {
        await navigator.clipboard.writeText(value);
        return true;
      } catch (_) {}
    }
    const ta = document.createElement("textarea");
    ta.value = value;
    ta.setAttribute("readonly", "");
    ta.style.cssText = "position:fixed;left:-9999px;top:0;opacity:0";
    document.body.appendChild(ta);
    ta.focus();
    ta.select();
    ta.setSelectionRange(0, value.length);
    const ok = document.execCommand("copy");
    document.body.removeChild(ta);
    return ok;
  } catch (_) {
    return false;
  } finally {
    if (restore) {
      try {
        restore.focus({ preventScroll: true });
      } catch (_) {}
    }
  }
}

async function txTableCopyCell(root, cell) {
  if (!cell) return;
  const sel = window.getSelection();
  let text = "";
  if (sel && !sel.isCollapsed && cell.contains(sel.anchorNode)) {
    text = sel.toString();
  }
  if (!text) {
    text = cell.getAttribute("data-copy") || cell.dataset.copy || "";
  }
  if (!text || text === "collapse") return;
  const ok = await copyTextToClipboard(text, cell);
  showTxTableCopyToast(root, ok ? "Copied" : "Copy failed");
  if (root && root.contains(cell)) {
    txTableMarkFocused(root, cell, { clearSelection: true });
  }
}

function focusTxTable(root) {
  if (!root || !root.classList.contains("tx-table-view")) return;
  requestAnimationFrame(function () {
    const first = txTableNavCells(root)[0];
    if (first) txTableFocusCell(root, first);
  });
}

function initTxTableView(root) {
  focusTxTable(root);
}

function renderTxGraphHtml(tx) {
  return renderTxTableHtml(tx);
}

function escapeHtml(s) {
  if (!s) return "";
  const d = document.createElement("div");
  d.textContent = s;
  return d.innerHTML;
}

function postToMempoolIframe(data) {
  const iframe = document.getElementById("displayIframe");
  if (!iframe?.contentWindow) return;
  try {
    iframe.contentWindow.postMessage(data, window.location.origin);
  } catch (_) {}
}

function initTxDetailPanel() {
  const panel = document.getElementById("tx-detail-panel");
  const closeBtn = document.querySelector(".tx-detail-close");
  const txidEl = document.getElementById("tx-detail-txid");
  const statusEl = document.getElementById("tx-detail-status");
  const graph = document.getElementById("tx-graph");

  if (!panel || !closeBtn) return;

  function closePanel() {
    if (document.fullscreenElement === panel || document.webkitFullscreenElement === panel) {
      (document.exitFullscreen ?? document.webkitExitFullscreen)?.call(document);
    }
    panel.classList.remove("expanded");
    panel.setAttribute("aria-hidden", "true");
    postToMempoolIframe({ type: "blockvase-tx-deselect" });
  }

  closeBtn.addEventListener("click", closePanel);

  if (!panel.dataset.txTableBound) {
    panel.dataset.txTableBound = "1";

    panel.addEventListener("click", function (e) {
      if (!panel.classList.contains("expanded")) return;
      const cell = e.target.closest(".tx-table-cell");
      if (!cell || cell.classList.contains("tx-table-cell--idx") || !panel.contains(cell)) return;
      const root = document.getElementById("tx-graph");
      if (txTableCellHasTextSelection(cell)) {
        txTableMarkFocused(root, cell, { clearSelection: false });
      } else {
        txTableFocusCell(root, cell);
      }
      if (cell.dataset.action === "toggle-group") {
        txTableToggleGroup(cell.closest(".tx-table-group"), root);
      }
    });

    document.addEventListener("keydown", function (e) {
      if (!panel.classList.contains("expanded")) return;
      const root = document.getElementById("tx-graph");
      if (!root) return;

      let cell = txTableFocusedCell(root);
      const active = document.activeElement;
      if (active && panel.contains(active) && !active.closest(".btn-icon")) {
        const activeCell = active.closest(".tx-table-cell");
        if (activeCell && !activeCell.classList.contains("tx-table-cell--idx")) {
          cell = activeCell;
        }
      }
      if (!cell || cell.classList.contains("tx-table-cell--idx") || !panel.contains(cell)) return;

      if (e.key === "ArrowRight") {
        e.preventDefault();
        txTableFocusGrid(root, 0, 1);
        return;
      }
      if (e.key === "ArrowLeft") {
        e.preventDefault();
        txTableFocusGrid(root, 0, -1);
        return;
      }
      if (e.key === "ArrowDown") {
        e.preventDefault();
        txTableFocusGrid(root, 1, 0);
        return;
      }
      if (e.key === "ArrowUp") {
        e.preventDefault();
        txTableFocusGrid(root, -1, 0);
        return;
      }
      if (e.key === "Enter" || e.key === " ") {
        if (cell.dataset.action === "toggle-group") {
          e.preventDefault();
          txTableToggleGroup(cell.closest(".tx-table-group"), root);
          return;
        }
        if (e.key === "Enter" && cell.dataset.copy && cell.dataset.copy !== "collapse") {
          e.preventDefault();
          txTableCopyCell(root, cell);
        }
      }
      if (e.key === "Copy" || ((e.ctrlKey || e.metaKey) && e.key.toLowerCase() === "c")) {
        e.preventDefault();
        txTableCopyCell(root, cell);
        return;
      }
    });
  }

  function swapContent(html) {
    if (!graph) return;
    graph.innerHTML = html;
    graph.classList.remove("tx-table-swapping");
    initTxTableView(graph);
  }

  const fullscreenBtn = document.getElementById("txDetailFullscreen");
  if (fullscreenBtn) {
    const fsEl = () => document.fullscreenElement ?? document.webkitFullscreenElement;
    fullscreenBtn.addEventListener("click", () => {
      if (!fsEl()) {
        (panel.requestFullscreen ?? panel.webkitRequestFullscreen)?.call(panel);
      } else if (fsEl() === panel) {
        (document.exitFullscreen ?? document.webkitExitFullscreen)?.call(document);
      }
    });
    document.addEventListener("fullscreenchange", () => {
      fullscreenBtn.setAttribute("aria-label", fsEl() === panel ? "Exit fullscreen" : "Fullscreen");
    });
    document.addEventListener("webkitfullscreenchange", () => {
      fullscreenBtn.setAttribute("aria-label", fsEl() === panel ? "Exit fullscreen" : "Fullscreen");
    });
  }

  window.addEventListener("message", (ev) => {
    if (ev.origin !== window.location.origin) return;
    if (ev.data?.type !== "blockvase-tx-select" || !ev.data.txid) return;
    const txid = ev.data.txid;

    panel.classList.add("expanded");
    panel.setAttribute("aria-hidden", "false");
    if (txidEl) txidEl.textContent = txid;
    if (statusEl) {
      statusEl.innerHTML =
        '<span class="portal-status-with-spinner">' +
        '<span class="portal-inline-spinner" aria-hidden="true"></span>' +
        "<span>Loading…</span></span>";
    }
    if (graph) {
      graph.innerHTML =
        '<div class="tx-detail-loading" role="status" aria-live="polite" aria-label="Loading transaction">' +
        '<span class="tx-detail-loading-spinner" aria-hidden="true"></span>' +
        '<span class="tx-detail-loading-label muted-note">Loading transaction…</span>' +
        "</div>";
    }
    try {
      panel.scrollIntoView({ behavior: "smooth", block: "nearest" });
    } catch (_) {
      panel.scrollIntoView(false);
    }

    blockvaseFetch("/tx/" + encodeURIComponent(txid))
      .then((r) => r.json())
      .then((tx) => {
        if (statusEl) {
          statusEl.textContent = tx.confirmations > 0 ? "Confirmed" : "Unconfirmed (mempool)";
        }
        swapContent(renderTxTableHtml(tx));
      })
      .catch((e) => {
        if (statusEl) statusEl.textContent = "";
        swapContent('<div class="tx-detail-error">' + (e.message || "Failed to load transaction") + "</div>");
      });
  });
}

try {
  const bc = new BroadcastChannel("blockvase");
  bc.onmessage = (e) => {
    if (e.data?.type === "theme-change" && e.data?.theme) {
      document.body.dataset.theme = e.data.theme;
    }
  };
} catch (_) {}

async function refreshDashboardMetrics() {
  await loadMetrics();
  await loadMiningSection();
}

function startMetricsPolling() {
  if (metricsPollTimer) clearInterval(metricsPollTimer);
  metricsPollTimer = setInterval(() => {
    refreshDashboardMetrics().catch(() => {});
  }, METRICS_POLL_MS);
}

async function init() {
  loadDeviceName();
  initPortalFullscreen();
  initDisplayOpenFull();
  initTxDetailPanel();
  await refreshDashboardMetrics();
  startMetricsPolling();
}

init();
