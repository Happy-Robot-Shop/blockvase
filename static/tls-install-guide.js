/* Platform-specific Blockvase CA install guide (auto-detect + dropdown override). */
(function () {
  if (window.BlockvaseTlsGuide) return;

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

  const STORAGE_KEY = "blockvase.tlsInstallPlatform";

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
})();
