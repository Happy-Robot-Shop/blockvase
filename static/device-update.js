/**
 * Full-screen device-update overlay for portal pages (uses .loading-overlay).
 */
(function () {
  const POLL_MS = 2000;

  function ensureOverlay() {
    let el = document.getElementById("deviceUpdateOverlay");
    if (el) return el;
    el = document.createElement("div");
    el.id = "deviceUpdateOverlay";
    el.className = "loading-overlay device-update-overlay";
    el.setAttribute("aria-live", "polite");
    el.setAttribute("aria-label", "Device update");
    el.setAttribute("aria-hidden", "true");
    el.innerHTML =
      '<div class="loading-spinner" id="deviceUpdateSpinner" aria-hidden="true"></div>' +
      '<div class="loading-text" id="deviceUpdateTitle">Updating Blockvase</div>' +
      '<p class="muted-note muted-note--compact" id="deviceUpdateMessage">' +
      "Pulling the latest software. This can take several minutes." +
      "</p>";
    document.body.appendChild(el);
    return el;
  }

  function applyUpdateUi(update) {
    const overlay = ensureOverlay();
    const titleEl = document.getElementById("deviceUpdateTitle");
    const msgEl = document.getElementById("deviceUpdateMessage");
    const spinner =
      document.getElementById("deviceUpdateSpinner") ||
      overlay.querySelector(".loading-spinner");
    const status = (update && update.status) || "idle";
    const message = (update && update.message) || "";
    const show = !!(update && update.show_overlay);

    if (titleEl) {
      if (status === "success") titleEl.textContent = "Update complete";
      else if (status === "failed") titleEl.textContent = "Update failed";
      else titleEl.textContent = "Updating Blockvase";
      titleEl.classList.toggle("loading-text--failed", status === "failed");
    }
    if (msgEl) {
      if (message) msgEl.textContent = message;
      else if (status === "success") {
        msgEl.textContent = "Restarting services. The portal will return shortly.";
      } else if (status === "failed") {
        msgEl.textContent = "See /var/lib/blockvase/device-update.log on the device.";
      } else {
        msgEl.textContent = "Pulling the latest software. This can take several minutes.";
      }
    }
    if (spinner) {
      spinner.classList.toggle("loading-spinner--done", status === "success");
      spinner.classList.toggle("loading-spinner--failed", status === "failed");
    }
    overlay.classList.toggle("active", show);
    overlay.setAttribute("aria-hidden", show ? "false" : "true");
    return { show, status };
  }

  async function pollOnce() {
    try {
      const resp = await fetch("/api/device-update", { cache: "no-store" });
      if (!resp.ok) return;
      const data = await resp.json();
      const { show, status } = applyUpdateUi(data);
      if (status === "success" && !show) {
        window.location.reload();
      }
    } catch (_) {
      /* ignore transient network blips during service restart */
    }
  }

  let timer = null;
  function startPolling() {
    if (timer) return;
    pollOnce();
    timer = setInterval(pollOnce, POLL_MS);
  }

  window.blockvaseShowDeviceUpdateOverlay = function (update) {
    applyUpdateUi(
      update || {
        status: "running",
        message: "Starting device update...",
        show_overlay: true,
      }
    );
    startPolling();
  };

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", startPolling);
  } else {
    startPolling();
  }
})();
