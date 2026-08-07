/* Portal TLS card + platform-specific CA install guide (auto-detect + dropdown). */
(function () {
  function placePortalTlsCard(el) {
    if (!el) return;
    const header = document.querySelector(".container > header") || document.querySelector("header");
    const nav = document.getElementById("portalSectionNav");
    // Keep certificate card above the tab switcher: header → card → nav → …
    if (nav && nav.parentNode) {
      if (el.nextElementSibling !== nav) nav.parentNode.insertBefore(el, nav);
      return;
    }
    if (header && header.parentNode) {
      if (el.previousElementSibling !== header) header.insertAdjacentElement("afterend", el);
      return;
    }
    if (!el.parentNode) document.body.insertBefore(el, document.body.firstChild);
  }

  function ensurePortalSectionNav() {
    if (!document.body || !document.body.classList.contains("portal-page")) return;
    let nav = document.getElementById("portalSectionNav");
    if (nav) {
      const card = document.getElementById("portalTlsCard");
      if (card) placePortalTlsCard(card);
      else {
        const header = document.querySelector(".container > header");
        if (header && nav.previousElementSibling !== header) {
          header.insertAdjacentElement("afterend", nav);
        }
      }
      document.body.classList.add("portal-has-section-nav");
      return;
    }
    const header = document.querySelector(".container > header");
    if (!header || !header.parentNode) return;

    const path = (location.pathname || "/").replace(/\/+$/, "") || "/";
    const tabs = [
      { href: "/", label: "Metrics", active: path === "/" },
      { href: "/wallet", label: "Wallet", active: path === "/wallet" || path.startsWith("/wallet/") },
      {
        href: "/settings",
        label: "Settings",
        active: path === "/settings" || path === "/setup" || path.startsWith("/settings/"),
      },
    ];

    nav = document.createElement("nav");
    nav.id = "portalSectionNav";
    nav.className = "portal-tab-nav";
    nav.setAttribute("aria-label", "Blockvase portal sections");
    nav.setAttribute("role", "tablist");
    nav.innerHTML = tabs
      .map((tab) => {
        const cls = "portal-tab-button" + (tab.active ? " is-active" : "");
        return (
          '<a href="' +
          tab.href +
          '" class="' +
          cls +
          '" role="tab" aria-selected="' +
          (tab.active ? "true" : "false") +
          '">' +
          tab.label +
          "</a>"
        );
      })
      .join("");

    const card = document.getElementById("portalTlsCard");
    if (card && card.parentNode) {
      card.insertAdjacentElement("afterend", nav);
    } else {
      header.insertAdjacentElement("afterend", nav);
    }
    document.body.classList.add("portal-has-section-nav");
  }

  if (!window.BlockvaseTlsGuide) {
    const PLATFORMS = [
      { id: "ios", label: "iPhone / iPad" },
      { id: "android", label: "Android" },
      { id: "macos", label: "macOS" },
      { id: "windows", label: "Windows" },
      { id: "linux", label: "Linux" },
      { id: "other", label: "Other / unsure" },
    ];

    const STEPS = {
      ios: [
        "Tap <strong>Download CA certificate</strong> above and allow the download.",
        "Open the file / profile prompt and tap <strong>Install</strong> (enter your passcode if asked). Tap Install again to confirm.",
        "Confirm the profile under <strong>Settings → General → VPN &amp; Device Management</strong> (name like “Blockvase … Portal CA”). Remove any older Blockvase profiles first.",
        "Critical: go to <strong>Settings → General → About → Certificate Trust Settings</strong> and enable <strong>Full Trust</strong> for the Blockvase CA. Installing alone is not enough.",
        "Force-quit Safari, then open the secure portal with the <strong>.local</strong> address (button above)—not the numeric Wi‑Fi IP, or Safari will still warn.",
      ],
      android: [
        "Tap <strong>Download CA certificate</strong> above.",
        "Open the downloaded file. Choose <strong>CA certificate</strong> (wording may be “VPN and apps” or “Wi‑Fi”).",
        "Name it something like “Blockvase” and confirm. You may need a lock screen PIN.",
        "Open Chrome (or your browser) to the secure portal using the <strong>.local</strong> address—not the numeric Wi‑Fi IP.",
        "If Chrome still warns, check that the cert was installed as a <em>CA</em> (not “User” / Wi‑Fi-only on some OEMs), then retry.",
      ],
      macos: [
        "Click <strong>Download CA certificate</strong> above.",
        "Open the .crt file so it lands in <strong>Keychain Access</strong> (Login or System keychain).",
        "Find the Blockvase Portal CA → double-click → expand <strong>Trust</strong> → set <strong>When using this certificate</strong> to <strong>Always Trust</strong> (at least for SSL). Close and enter your password.",
        "Quit and reopen Safari/Chrome, then open the secure portal with the <strong>.local</strong> address—not the numeric Wi‑Fi IP.",
      ],
      windows: [
        "Click <strong>Download CA certificate</strong> above.",
        "Open the .crt → <strong>Install Certificate…</strong>",
        "Choose <strong>Local Machine</strong> (recommended) → Place all certificates in <strong>Trusted Root Certification Authorities</strong> → Finish.",
        "Approve the security warning, then open Edge or Chrome to the secure portal using the <strong>.local</strong> address—not the numeric Wi‑Fi IP.",
      ],
      linux: [
        "Click <strong>Download CA certificate</strong> above.",
        "For Chromium/Chrome (system store): copy the CA into your distro’s trusted CA path and run <code>update-ca-certificates</code> (or your distro’s equivalent) as root.",
        "For Firefox: Settings → Privacy &amp; Security → Certificates → View Certificates → Authorities → Import the CA and trust it for websites.",
        "Restart the browser, then open the secure portal with the <strong>.local</strong> address—not the numeric Wi‑Fi IP.",
      ],
      other: [
        "Download the <strong>CA</strong> certificate (not a public internet CA—this one is only for this Blockvase).",
        "Install it into your OS or browser trust store as a <em>trusted root / CA</em>.",
        "Open the secure portal using the device <strong>.local</strong> hostname. Using the numeric LAN IP will still show a warning because that IP is not on the certificate.",
        "If your phone is iPhone/iPad, you must also enable <strong>Full Trust</strong> under Certificate Trust Settings after installing the profile.",
      ],
    };

    const STORAGE_KEY = "blockvase.tlsInstallPlatform";

    function detectPlatform() {
      const ua = navigator.userAgent || "";
      const platform = navigator.platform || "";
      if (/iPhone|iPad|iPod/i.test(ua) || (platform === "MacIntel" && navigator.maxTouchPoints > 1)) {
        return "ios";
      }
      if (/Android/i.test(ua)) return "android";
      if (/Win/i.test(platform) || /Windows/i.test(ua)) return "windows";
      if (/Mac/i.test(platform) || /Macintosh/i.test(ua)) return "macos";
      if (/Linux/i.test(platform) || /Linux/i.test(ua) || /CrOS/i.test(ua)) return "linux";
      return "other";
    }

    function escapeHtml(s) {
      return String(s)
        .replace(/&/g, "&amp;")
        .replace(/</g, "&lt;")
        .replace(/>/g, "&gt;")
        .replace(/"/g, "&quot;");
    }

    function renderSteps(platformId, httpsUrl) {
      const steps = STEPS[platformId] || STEPS.other;
      const host = (httpsUrl || "https://your-device.local/").replace(/\/$/, "");
      return (
        '<ol class="portal-tls-steps">' +
        steps
          .map(
            (step, i) =>
              '<li><span class="portal-tls-step-num">' +
              (i + 1) +
              '</span><span class="portal-tls-step-body">' +
              step +
              "</span></li>"
          )
          .join("") +
        "</ol>" +
        '<p class="muted-note muted-note--compact portal-tls-tip">' +
        "<strong>Use this URL after trust is enabled:</strong> <code>" +
        escapeHtml(host) +
        "</code>. Do not use the Wi‑Fi IP address.</p>"
      );
    }

    function readStoredPlatform() {
      try {
        const v = sessionStorage.getItem(STORAGE_KEY);
        if (v && STEPS[v]) return v;
      } catch (_) {
        /* ignore */
      }
      return null;
    }

    function storePlatform(id) {
      try {
        if (STEPS[id]) sessionStorage.setItem(STORAGE_KEY, id);
      } catch (_) {
        /* ignore */
      }
    }

    function mountGuide(root, options) {
      if (!root) return null;
      const opts = options || {};
      const selectId = opts.selectId || (root.id || "tlsGuide") + "Platform";
      const stepsId = opts.stepsId || (root.id || "tlsGuide") + "Steps";
      const detected = detectPlatform();
      const initial = opts.platform || readStoredPlatform() || detected;

      root.classList.add("portal-tls-guide");
      root.innerHTML =
        '<div class="portal-tls-guide-toolbar">' +
        '<label for="' +
        escapeHtml(selectId) +
        '">Your device</label>' +
        '<select id="' +
        escapeHtml(selectId) +
        '" class="portal-tls-platform-select" aria-label="Certificate install platform">' +
        PLATFORMS.map(
          (p) =>
            '<option value="' +
            p.id +
            '"' +
            (p.id === initial ? " selected" : "") +
            ">" +
            escapeHtml(p.label) +
            (p.id === detected ? " (detected)" : "") +
            "</option>"
        ).join("") +
        "</select></div>" +
        '<div id="' +
        escapeHtml(stepsId) +
        '" class="portal-tls-guide-steps"></div>';

      const select = document.getElementById(selectId);
      const stepsEl = document.getElementById(stepsId);
      if (!select || !stepsEl) return null;

      function paint() {
        const id = select.value || detectPlatform();
        storePlatform(id);
        stepsEl.innerHTML = renderSteps(id, opts.httpsUrl || root.dataset.httpsUrl || "");
      }

      select.addEventListener("change", () => {
        storePlatform(select.value || detectPlatform());
        refreshAllGuides(opts.httpsUrl || root.dataset.httpsUrl || "");
      });
      if (opts.httpsUrl) root.dataset.httpsUrl = opts.httpsUrl;
      paint();
      return { select, paint, detected };
    }

    function refreshAllGuides(httpsUrl) {
      document.querySelectorAll("[data-tls-install-guide]").forEach((el) => {
        mountGuide(el, { httpsUrl: httpsUrl || el.dataset.httpsUrl || "" });
      });
    }

    window.BlockvaseTlsGuide = {
      detectPlatform,
      mountGuide,
      refreshAllGuides,
      PLATFORMS,
    };
  }

  function ensureCard() {
    let el = document.getElementById("portalTlsCard");
    if (!el) {
      el = document.createElement("div");
      el.id = "portalTlsCard";
      el.className = "metric-board metric-board--dense portal-tls-card";
      el.style.display = "none";
      el.setAttribute("hidden", "");
      el.setAttribute("role", "region");
      el.setAttribute("aria-label", "Portal certificate");
    }
    placePortalTlsCard(el);
    return el;
  }

  function renderCard(d) {
    const el = ensureCard();
    if (!d || !d.show_http_banner) {
      el.style.display = "none";
      el.setAttribute("hidden", "");
      el.innerHTML = "";
      return;
    }
    const httpsUrl = d.https_url || "#";
    const host = String(httpsUrl).replace(/\/$/, "") || "https://your-device.local";
    el.style.display = "";
    el.removeAttribute("hidden");
    el.classList.add("metric-board", "metric-board--dense", "portal-tls-card");
    el.innerHTML =
      '<h2 class="metric-board-title">Portal certificate</h2>' +
      '<p class="muted-note muted-note--compact">This device has a private CA. Download and install it on this phone or computer, follow the steps for your platform, then open the HTTPS portal. HTTP stays available until you turn on “Prefer HTTPS” in Settings.</p>' +
      '<div class="portal-tls-banner-actions">' +
      '<a class="btn secondary" href="/api/tls/cert.crt">Download CA certificate</a> ' +
      '<a class="btn secondary" href="' +
      httpsUrl +
      '">Open secure portal</a> ' +
      '<a class="btn secondary" href="/settings">Certificate settings</a>' +
      "</div>" +
      '<h3 class="metric-cluster-title portal-tls-guide-heading">Install steps</h3>' +
      '<div id="portalTlsCardGuide" data-tls-install-guide></div>';

    const guideRoot = document.getElementById("portalTlsCardGuide");
    if (guideRoot && window.BlockvaseTlsGuide) {
      window.BlockvaseTlsGuide.mountGuide(guideRoot, { httpsUrl: httpsUrl });
    }
  }

  window.refreshPortalTlsBanner = function () {
    return fetch("/api/tls/status", { cache: "no-store" })
      .then((r) => (r.ok ? r.json() : null))
      .then((d) => {
        renderCard(d);
        return d;
      })
      .catch(() => null);
  };

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", () => {
      ensurePortalSectionNav();
      window.refreshPortalTlsBanner();
    });
  } else {
    ensurePortalSectionNav();
    window.refreshPortalTlsBanner();
  }
})();
