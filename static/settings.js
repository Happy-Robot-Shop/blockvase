const setupToken = new URLSearchParams(window.location.search).get("token") || "";

function withToken(path) {
  if (!setupToken) return path;
  const sep = path.includes("?") ? "&" : "?";
  return `${path}${sep}token=${encodeURIComponent(setupToken)}`;
}

function showLoading(msg) {
  const overlay = document.getElementById("loadingOverlay");
  const text = document.getElementById("loadingText");
  const container = document.getElementById("mainContainer");
  if (text) text.textContent = msg || "Loading...";
  if (overlay) overlay.classList.add("active");
  if (container) container.classList.add("faded");
}

function hideLoading() {
  const overlay = document.getElementById("loadingOverlay");
  const container = document.getElementById("mainContainer");
  if (overlay) overlay.classList.remove("active");
  if (container) container.classList.remove("faded");
}

function showStatus(el, type, msg) {
  if (!el) return;
  el.style.display = "block";
  el.className = "status " + type;
  el.textContent = msg;
  if (el._hideTimeout) clearTimeout(el._hideTimeout);
  el._hideTimeout = setTimeout(() => { el.style.display = "none"; }, 5000);
}

/** Parse JSON from fetch; if the server returned HTML/text (e.g. 500 page), surface a clear message. */
async function parseJsonResponse(response) {
  const text = await response.text();
  const trimmed = (text || "").trim();
  if (!trimmed) {
    throw new Error("Empty response (HTTP " + response.status + ")");
  }
  try {
    return JSON.parse(trimmed);
  } catch (_) {
    const snippet = trimmed.replace(/\s+/g, " ").slice(0, 160);
    throw new Error(
      "Server did not return JSON (HTTP " + response.status + "): " + snippet
    );
  }
}

function formatDeviceName(name) {
  if (!name) return "Blockvase";
  return name.trim().split(/[- ]+/).filter(Boolean)
    .map(w => w.charAt(0).toUpperCase() + w.slice(1).toLowerCase())
    .join(" ");
}

function loadDeviceName() {
  const h1 = document.getElementById("deviceNameHeader");
  const cached = localStorage.getItem("deviceName");
  if (cached && h1) h1.textContent = formatDeviceName(cached) + " Settings";

  fetch(withToken("/api/device-name"))
    .then(r => r.json())
    .then(n => {
      const name = n.name || "blockvase";
      localStorage.setItem("deviceName", name);
      if (h1) h1.textContent = formatDeviceName(name) + " Settings";
    })
    .catch(() => {});
}

async function validateQrToken() {
  if (!setupToken) return false;
  try {
    const r = await fetch(withToken("/api/validate-qr-token"));
    const d = await r.json();
    return !!d.valid;
  } catch {
    return false;
  }
}

function setSettingsVisible(visible) {
  const auth = document.getElementById("adminAuthSection");
  const settings = document.getElementById("settingsSections");
  if (auth) auth.style.display = visible ? "none" : "block";
  if (settings) settings.style.display = visible ? "" : "none";
}

async function requireSettingsAccess() {
  if (setupToken) {
    setSettingsVisible(true);
    return true;
  }
  try {
    const r = await fetch("/api/admin-auth/status");
    const d = await r.json();
    if (d.authenticated) {
      setSettingsVisible(true);
      return true;
    }
    setSettingsVisible(false);
    const form = document.getElementById("adminLoginForm");
    const help = document.getElementById("adminAuthHelp");
    if (form) form.style.display = d.credentials_configured ? "block" : "none";
    if (help && !d.credentials_configured) {
      help.textContent = "Admin login has not been configured yet. Open the original setup QR link on this device to set credentials.";
    }
    return false;
  } catch {
    setSettingsVisible(false);
    return false;
  }
}

function loginAdmin(e) {
  e.preventDefault();
  const statusDiv = document.getElementById("adminAuthStatus");
  const username = document.getElementById("adminLoginUsername").value.trim();
  const passwordInput = document.getElementById("adminLoginPassword");
  const password = passwordInput.value;
  showStatus(statusDiv, "info", "Logging in...");
  fetch("/api/admin-auth/login", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ username, password }),
  })
    .then(r => parseJsonResponse(r).then(d => ({ ok: r.ok, d })))
    .then(({ ok, d }) => {
      passwordInput.value = "";
      if (!ok || !d.success) {
        showStatus(statusDiv, "error", d.error || "Login failed");
        return;
      }
      setSettingsVisible(true);
      startSettingsPage();
    })
    .catch(err => {
      passwordInput.value = "";
      showStatus(statusDiv, "error", "Error: " + err.message);
    });
}

function syncSettingsLayoutMode() {
  const setup = document.getElementById("setupSection");
  document.body.classList.toggle(
    "settings-setup-visible",
    !!(setup && setup.style.display === "block")
  );
}

function checkApMode() {
  // Fast path: /setup?token=... is already server-validated in the route handler.
  // Render immediately to avoid perceived hangs while async checks complete.
  if (setupToken) {
    window.isApMode = true;
    document.getElementById("setupSection").style.display = "block";
    document.getElementById("wifiRpcSection").style.display = "none";
    syncSettingsLayoutMode();
    loadRpc();
    validateQrToken().then(valid => {
      if (!valid) {
        document.body.innerHTML = '<div style="padding:40px;text-align:center;color:#fff;"><h1>Access Denied</h1><p>Invalid or missing QR code token. Please scan the QR code displayed on the device.</p></div>';
      }
    });
    return;
  }

  fetch(withToken("/api/ap-mode"))
    .then(r => r.json())
    .then(d => {
      window.isApMode = !!d.ap_mode;
      if (d.ap_mode) {
        validateQrToken().then(valid => {
          if (!valid) {
            document.body.innerHTML = '<div style="padding:40px;text-align:center;color:#fff;"><h1>Access Denied</h1><p>Invalid or missing QR code token. Please scan the QR code displayed on the device.</p></div>';
            return;
          }
          document.getElementById("setupSection").style.display = "block";
          document.getElementById("wifiRpcSection").style.display = "none";
          syncSettingsLayoutMode();
          loadRpc();
        });
      } else {
        document.getElementById("setupSection").style.display = "none";
        document.getElementById("wifiRpcSection").style.display = "block";
        syncSettingsLayoutMode();
        loadRpc();
      }
    })
    .catch(() => {
      document.getElementById("setupSection").style.display = "none";
      document.getElementById("wifiRpcSection").style.display = "block";
      syncSettingsLayoutMode();
      loadRpc();
    });
}

function loadAdminCredentials() {
  fetch(withToken("/api/admin-auth/status"))
    .then(r => r.json())
    .then(d => {
      const username = document.getElementById("adminUsername");
      if (username) username.value = d.username || "admin";
    })
    .catch(() => {});
}

function loadTheme() {
  fetch(withToken("/api/theme"))
    .then(r => r.json())
    .then(d => {
      const theme = d.theme || "default";
      document.body.dataset.theme = theme;
      const sel = document.getElementById("theme");
      if (sel) sel.value = theme;
    })
    .catch(() => {});
}

function loadMiningPayout() {
  fetch(withToken("/api/mining-payout"))
    .then(r => r.json())
    .then(d => {
      const input = document.getElementById("miningPayoutAddress");
      if (input) input.value = d.address || "";
    })
    .catch(() => {});
}

function saveMiningPayout(e) {
  e.preventDefault();
  const input = document.getElementById("miningPayoutAddress");
  const statusDiv = document.getElementById("miningPayoutStatus");
  if (!input) return;
  const address = input.value.trim();
  if (!address) {
    showStatus(statusDiv, "error", "Bitcoin payout address is required");
    return;
  }
  showLoading("Saving mining payout address...");
  showStatus(statusDiv, "info", "Saving mining payout address...");
  const payload = { address };
  if (setupToken) payload.token = setupToken;
  fetch(withToken("/api/mining-payout"), {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(payload),
  })
    .then(r => parseJsonResponse(r))
    .then(d => {
      hideLoading();
      if (d.success) {
        input.value = d.address || address;
        showStatus(statusDiv, d.applied === false ? "info" : "success", d.message || "Mining payout address saved");
      } else {
        showStatus(statusDiv, "error", d.error || "Failed to save mining payout address");
      }
    })
    .catch(err => {
      hideLoading();
      showStatus(statusDiv, "error", "Error: " + err.message);
    });
}

function saveTheme() {
  const sel = document.getElementById("theme");
  if (!sel) return;
  const theme = sel.value || "default";
  const statusDiv = document.getElementById("themeStatus") || document.createElement("div");
  statusDiv.id = "themeStatus";
  statusDiv.className = "status info";
  statusDiv.style.display = "block";
  statusDiv.style.marginTop = "12px";
  if (!statusDiv.parentElement) {
    const btn = document.getElementById("saveThemeBtn");
    if (btn && btn.parentElement) btn.parentElement.appendChild(statusDiv);
  }
  showStatus(statusDiv, "info", "Saving theme...");
  const themePayload = { theme };
  if (setupToken) themePayload.token = setupToken;
  fetch(withToken("/api/theme"), {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(themePayload),
  })
    .then(r => parseJsonResponse(r))
    .then(d => {
      if (d.success) {
        document.body.dataset.theme = d.theme || theme;
        try {
          const bc = new BroadcastChannel("blockvase");
          bc.postMessage({ type: "theme-change", theme: d.theme || theme });
          bc.close();
        } catch (_) {}
        showStatus(statusDiv, "success", "Theme saved");
      } else {
        showStatus(statusDiv, "error", d.error || "Failed to save theme");
      }
    })
    .catch(err => showStatus(statusDiv, "error", "Error: " + err));
}

function loadRpc(retryCount = 0) {
  const maxRetries = 3;
  fetch(withToken("/api/rpc"))
    .then(r => r.json())
    .then(d => {
      if (window.isApMode) {
        const dn = document.getElementById("setupDeviceName");
        if (dn) dn.value = "";
      } else {
        const rpcNode = document.getElementById("rpcNode");
        const rpcNodeInfo = document.getElementById("rpcNodeInfo");
        const rpcStatus = document.getElementById("rpcStatus");
        const host = d.host || "127.0.0.1";
        const port = d.port || 8332;
        const ep = host + ":" + port;
        if (rpcNode) rpcNode.textContent = ep;
        if (rpcNodeInfo) rpcNodeInfo.textContent = ep;
        if (rpcStatus) rpcStatus.textContent = d.connected ? "Connected" : "Disconnected";
      }
      return fetch(withToken("/api/wifi"));
    })
    .then(r => r.json())
    .then(w => {
      if (w.ssid) {
        const ssidEl = document.getElementById(window.isApMode ? "setupSsid" : "ssid");
        if (ssidEl && !ssidEl.value) ssidEl.value = w.ssid;
      }
      return fetch(withToken("/api/device-name"));
    })
    .then(r => r.json())
    .then(n => {
      const name = n.name || "";
      if (!window.isApMode) {
        document.getElementById("deviceName").value = name;
        document.getElementById("deviceAddress").textContent = name ? name + ".local" : "-";
      }
    })
    .catch(err => {
      if (retryCount < 3) setTimeout(() => loadRpc(retryCount + 1), 1000 * (retryCount + 1));
      else console.error("Failed to load RPC config:", err);
    });
}

function saveDeviceName(e) {
  e.preventDefault();
  const nameInput = window.isApMode ? document.getElementById("setupDeviceName") : document.getElementById("deviceName");
  const displayName = nameInput.value.trim().toLowerCase();
  const deviceName = displayName.replace(/\s+/g, "-").replace(/[^a-z0-9-]/g, "-");
  const statusDiv = document.getElementById("deviceNameStatus");
  if (!deviceName) {
    showStatus(statusDiv, "error", "Device name is required");
    return;
  }
  if (deviceName.length > 19) {
    showStatus(statusDiv, "error", "Device name must be 19 characters or less");
    return;
  }
  showLoading("Saving device name...");
  showStatus(statusDiv, "info", "Saving device name...");
  const namePayload = { name: deviceName };
  if (setupToken) namePayload.token = setupToken;
  fetch(withToken("/api/device-name"), {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(namePayload),
  })
    .then(r => parseJsonResponse(r))
    .then(d => {
      if (d.success) {
        const savedName = d.name || deviceName;
        localStorage.setItem("deviceName", displayName);
        if (!window.isApMode) {
          showStatus(statusDiv, "success", d.message || "Device name saved. Redirecting...");
          document.getElementById("deviceAddress").textContent = savedName + ".local";
          nameInput.value = displayName;
          showLoading("Device name saved. Redirecting in 30 seconds...");
          setTimeout(() => { window.location.href = "http://" + savedName + ".local"; }, 30000);
        } else {
          hideLoading();
          showStatus(statusDiv, "success", "Device name saved. Will take effect after reboot.");
          nameInput.value = displayName;
        }
      } else {
        hideLoading();
        showStatus(statusDiv, "error", "Error: " + (d.error || "Unknown"));
      }
    })
    .catch(err => {
      hideLoading();
      showStatus(statusDiv, "error", "Network error: " + err);
    });
}

function saveAll(e) {
  e.preventDefault();
  let deviceName, ssid, password, statusDiv;
  const adminCredStatus = document.getElementById("adminCredentialsStatus");
  if (adminCredStatus) adminCredStatus.style.display = "none";

  if (window.isApMode) {
    deviceName = document.getElementById("setupDeviceName").value.trim().toLowerCase().replace(/\s+/g, "-").replace(/[^a-z0-9-]/g, "-");
    ssid = document.getElementById("setupSsid").value;
    password = document.getElementById("setupPassword").value;
    statusDiv = document.getElementById("setupStatus");
  } else {
    deviceName = document.getElementById("deviceName").value.trim().toLowerCase().replace(/\s+/g, "-").replace(/[^a-z0-9-]/g, "-");
    ssid = document.getElementById("ssid").value;
    password = document.getElementById("password").value;
    statusDiv = document.getElementById("wifiRpcStatus");
  }

  if (!deviceName) {
    showStatus(statusDiv, "error", "Device name is required");
    return;
  }
  if (deviceName.length > 19) {
    showStatus(statusDiv, "error", "Device name must be 19 characters or less");
    return;
  }
  if (!ssid) {
    showStatus(statusDiv, "error", "WiFi SSID is required");
    return;
  }

  const savePayload = { deviceName, ssid, password };

  if (window.isApMode) {
    const adminUsername = document.getElementById("setupAdminUsername").value.trim();
    const adminPasswordInput = document.getElementById("setupAdminPassword");
    const adminPasswordConfirmInput = document.getElementById("setupAdminPasswordConfirm");
    const adminPassword = adminPasswordInput.value;
    const adminPasswordConfirm = adminPasswordConfirmInput.value;
    if (!adminUsername) {
      showStatus(statusDiv, "error", "Admin username is required");
      return;
    }
    if (!adminPassword) {
      showStatus(statusDiv, "error", "Admin password is required");
      return;
    }
    if (adminPassword !== adminPasswordConfirm) {
      showStatus(statusDiv, "error", "Admin passwords do not match");
      return;
    }
    savePayload.adminUsername = adminUsername;
    savePayload.adminPassword = adminPassword;
    adminPasswordInput.value = "";
    adminPasswordConfirmInput.value = "";
    document.getElementById("setupPassword").value = "";
  } else {
    const adminUsername = (document.getElementById("adminUsername")?.value || "").trim();
    const adminPasswordEl = document.getElementById("adminPassword");
    const adminConfirmEl = document.getElementById("adminPasswordConfirm");
    const adminPassword = adminPasswordEl?.value || "";
    const adminPasswordConfirm = adminConfirmEl?.value || "";

    if (adminPassword || adminPasswordConfirm) {
      if (!adminUsername) {
        showStatus(adminCredStatus || statusDiv, "error", "Admin username is required when changing the admin password");
        return;
      }
      if (adminPassword !== adminPasswordConfirm) {
        showStatus(adminCredStatus || statusDiv, "error", "Admin passwords do not match");
        return;
      }
      if (adminPassword.length < 8) {
        showStatus(adminCredStatus || statusDiv, "error", "Admin password must be at least 8 characters");
        return;
      }
      savePayload.adminUsername = adminUsername;
      savePayload.adminPassword = adminPassword;
    }

    document.getElementById("password").value = "";
    if (adminPasswordEl) adminPasswordEl.value = "";
    if (adminConfirmEl) adminConfirmEl.value = "";
  }

  showLoading("Saving settings...");
  showStatus(statusDiv, "info", "Saving settings...");
  if (setupToken) savePayload.token = setupToken;
  fetch(withToken("/api/save-all"), {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(savePayload),
  })
    .then(r => parseJsonResponse(r))
    .then(d => {
      if (d.success) {
        const finalDeviceName = (d.deviceName && d.deviceName.trim()) ? d.deviceName.trim() : deviceName;
        if (d.rebootScheduled === false) {
          hideLoading();
          showStatus(
            statusDiv,
            "error",
            "Settings saved, but automatic reboot is disabled (ENABLE_SYSTEM_ACTIONS). " +
              "Use the Reboot button below or run: sudo reboot"
          );
          return;
        }
        showStatus(statusDiv, "success", "Settings saved. Rebooting shortly...");
        showLoading(
          "Settings saved. The device will reboot in a few seconds, then you can open http://" +
            finalDeviceName +
            ".local (redirect in 30s)..."
        );
        setTimeout(() => {
          window.location.href = "http://" + finalDeviceName + ".local";
        }, 30000);
      } else {
        hideLoading();
        showStatus(statusDiv, "error", "Error: " + (d.error || "Unknown"));
      }
    })
    .catch(err => {
      hideLoading();
      showStatus(statusDiv, "error", "Network error: " + err);
    });
}

function refreshStats() {
  fetch(withToken("/api/stats"))
    .then(r => r.json())
    .then(d => {
      document.getElementById("uptime").textContent = d.uptime || "-";
      document.getElementById("freeHeap").textContent = d.freeHeap ?? "-";
      document.getElementById("largestBlock").textContent = d.largestBlock ?? "-";
      document.getElementById("wifiStatusText").textContent = d.wifiStatus || "-";
      document.getElementById("ipAddress").textContent = d.ipAddress || "-";
      document.getElementById("bitcoinNode").textContent = d.bitcoinNode || "-";
      const nvEl = document.getElementById("bitcoinNodeVersion");
      if (nvEl) nvEl.textContent = (d.nodeVersion || d.node_version || "").trim() || "-";
      document.getElementById("rpcNodeInfo").textContent = d.rpcNode || "-";
      let statusText = d.rpcConnected ? "Connected" : "Disconnected";
      if (!d.rpcConnected && d.rpcStatusCode) statusText += " (HTTP " + d.rpcStatusCode + ")";
      if (!d.rpcConnected && d.rpcErrorBody) statusText += " - " + d.rpcErrorBody;
      document.getElementById("rpcStatus").textContent = statusText;
      document.getElementById("blockHeight").textContent = d.blockHeight ?? "-";
      document.getElementById("blocksFound").textContent = d.blocksFound ?? "-";
    })
    .catch(() => {});
}

function simulateBlock() {
  const a = document.getElementById("actionStatus");
  showStatus(a, "info", "Triggering block animation...");
  fetch(withToken("/api/simulate-block"), { method: "POST" })
    .then(r => r.json())
    .then(d => {
      if (d.success) showStatus(a, "success", "Block animation triggered");
      else showStatus(a, "error", "Error: " + (d.error || "Unknown"));
    })
    .catch(err => showStatus(a, "error", "Error: " + err));
}

function simulateMinerBlock() {
  const a = document.getElementById("actionStatus");
  showStatus(a, "info", "Triggering miner block animation...");
  fetch(withToken("/api/simulate-miner-block"), { method: "POST" })
    .then(r => r.json())
    .then(d => {
      if (d.success) showStatus(a, "success", "Miner block animation triggered");
      else showStatus(a, "error", "Error: " + (d.error || "Unknown"));
    })
    .catch(err => showStatus(a, "error", "Error: " + err));
}

function setUpdateAvailableIndicator(available, detail) {
  const btn = document.getElementById("updateDeviceBtn");
  if (!btn) return;
  const on = !!available;
  btn.classList.toggle("btn-update-available", on);
  if (on) {
    const behind = detail && detail.commits_behind ? Number(detail.commits_behind) : 0;
    btn.title =
      behind > 0
        ? `Update available (${behind} commit${behind === 1 ? "" : "s"} behind)`
        : "Update available";
    btn.setAttribute("aria-label", btn.title);
  } else {
    btn.title = "Update device";
    btn.setAttribute("aria-label", "Update device");
  }
}

function refreshUpdateAvailability(forceRefresh) {
  const q = forceRefresh ? "?refresh=1" : "";
  return fetch("/api/device-update" + q, { cache: "no-store" })
    .then((r) => (r.ok ? r.json() : null))
    .then((d) => {
      if (!d) return;
      setUpdateAvailableIndicator(!!d.update_available && !d.updating, d);
    })
    .catch(() => {});
}

function updateDevice() {
  const a = document.getElementById("actionStatus");
  if (
    !confirm(
      "Update this device from the source repository? The display and portal will show an updating screen while git pull and bootstrap run. This can take several minutes."
    )
  ) {
    return;
  }
  setUpdateAvailableIndicator(false);
  showStatus(a, "info", "Starting device update...");
  if (typeof window.blockvaseShowDeviceUpdateOverlay === "function") {
    window.blockvaseShowDeviceUpdateOverlay({
      status: "running",
      message: "Starting device update...",
      show_overlay: true,
    });
  }
  fetch(withToken("/api/device-update"), { method: "POST" })
    .then((r) => r.json().then((d) => ({ ok: r.ok, data: d })))
    .then(({ ok, data }) => {
      if (ok && data.success) {
        showStatus(a, "success", data.message || "Device update started");
        if (typeof window.blockvaseShowDeviceUpdateOverlay === "function") {
          window.blockvaseShowDeviceUpdateOverlay({
            status: "running",
            message: data.message || "Updating...",
            show_overlay: true,
          });
        }
      } else {
        showStatus(a, "error", data?.error || "Update failed to start");
        if (typeof window.blockvaseShowDeviceUpdateOverlay === "function") {
          window.blockvaseShowDeviceUpdateOverlay({
            status: "failed",
            message: data?.error || "Update failed to start",
            show_overlay: true,
          });
        }
      }
    })
    .catch((err) => {
      showStatus(a, "error", "Error: " + err);
      if (typeof window.blockvaseShowDeviceUpdateOverlay === "function") {
        window.blockvaseShowDeviceUpdateOverlay({
          status: "failed",
          message: String(err),
          show_overlay: true,
        });
      }
    });
}

function reboot() {
  const a = document.getElementById("actionStatus");
  if (!confirm("Reboot device?")) return;
  showStatus(a, "info", "Rebooting...");
  fetch(withToken("/api/reboot"), { method: "POST" })
    .then(() => showStatus(a, "success", "Device rebooting..."))
    .catch(err => showStatus(a, "error", "Error: " + err));
}

function factoryReset() {
  const a = document.getElementById("actionStatus");
  if (
    !confirm(
      "Factory reset clears app settings (Wi-Fi, display, mining address, setup state). Bitcoin Knots blockchain data and /etc/bitcoin/bitcoin.conf are not modified; RPC credentials saved for this UI are preserved. Continue?"
    )
  )
    return;
  showStatus(a, "info", "Resetting to factory defaults...");
  fetch(withToken("/api/factory-reset"), { method: "POST" })
    .then(r => r.json().then(d => ({ ok: r.ok, data: d })))
    .then(({ ok, data }) => {
      if (ok && data.success) showStatus(a, "success", data.message || "Factory reset complete. Rebooting...");
      else showStatus(a, "error", data?.error || "Factory reset failed");
    })
    .catch(err => showStatus(a, "error", "Error: " + err));
}

function updateDeviceAddress() {
  const nameInput = document.getElementById("deviceName");
  if (!nameInput) return;
  const name = nameInput.value.trim().toLowerCase().replace(/\s+/g, "-").replace(/[^a-z0-9-]/g, "-");
  const addrEl = document.getElementById("deviceAddress");
  if (addrEl) addrEl.textContent = name ? name + ".local" : "-";
}

// Init
document.getElementById("setupForm")?.addEventListener("submit", saveAll);
document.getElementById("wifiRpcForm")?.addEventListener("submit", saveAll);
document.getElementById("deviceNameForm")?.addEventListener("submit", saveDeviceName);
document.getElementById("adminLoginForm")?.addEventListener("submit", loginAdmin);
document.getElementById("miningPayoutForm")?.addEventListener("submit", saveMiningPayout);
document.getElementById("saveThemeBtn")?.addEventListener("click", saveTheme);

let statsInterval = null;
let settingsStarted = false;

let updateCheckInterval = null;

async function startSettingsPage() {
  if (settingsStarted) return;
  settingsStarted = true;
  checkApMode();
  refreshStats();
  loadDeviceName();
  loadTheme();
  loadAdminCredentials();
  loadMiningPayout();
  refreshUpdateAvailability(true);
  if (!statsInterval) statsInterval = setInterval(refreshStats, 5000);
  if (!updateCheckInterval) {
    // Server also checks ~every 30m; poll cache here so the badge updates while Settings is open.
    updateCheckInterval = setInterval(() => refreshUpdateAvailability(false), 60 * 1000);
  }
  setTimeout(() => {
    const nameInput = document.getElementById("deviceName");
    if (nameInput) {
      nameInput.addEventListener("input", updateDeviceAddress);
      updateDeviceAddress();
    }
  }, 100);
  // After a forced refresh starts, re-read once the fetch likely finished.
  setTimeout(() => refreshUpdateAvailability(false), 8000);
}

requireSettingsAccess().then((ok) => {
  if (ok) startSettingsPage();
});
