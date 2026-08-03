const setupToken = new URLSearchParams(window.location.search).get("token") || "";
let totpEnabled = false;
let receiveMintedThisOpen = false;
let receiveMintPromise = null;
let sendInFlight = false;
let backupInFlight = false;

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
  el._hideTimeout = setTimeout(() => {
    el.style.display = "none";
  }, 6000);
}

async function parseJsonResponse(response) {
  const text = await response.text();
  const trimmed = (text || "").trim();
  if (!trimmed) throw new Error("Empty response (HTTP " + response.status + ")");
  try {
    return JSON.parse(trimmed);
  } catch (_) {
    throw new Error(
      "Server did not return JSON (HTTP " +
        response.status +
        "): " +
        trimmed.replace(/\s+/g, " ").slice(0, 160)
    );
  }
}

function formatDeviceName(name) {
  if (!name) return "Blockvase";
  return name
    .trim()
    .split(/[- ]+/)
    .filter(Boolean)
    .map((w) => w.charAt(0).toUpperCase() + w.slice(1).toLowerCase())
    .join(" ");
}

function escapeHtml(value) {
  return String(value ?? "")
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;")
    .replace(/'/g, "&#39;");
}

/** Format BTC from a decimal string (or number) without relying on float math for display. */
function formatBtc(raw) {
  const s = String(raw ?? "").trim();
  if (!s || !/^-?\d+(\.\d+)?$/.test(s)) return "—";
  const neg = s.startsWith("-");
  const body = neg ? s.slice(1) : s;
  const parts = body.split(".");
  const whole = parts[0] || "0";
  let frac = (parts[1] || "").slice(0, 8);
  while (frac.length < 8) frac += "0";
  return (neg ? "-" : "") + whole + "." + frac + " BTC";
}

function isValidBtcAmountString(raw) {
  const s = String(raw ?? "").trim();
  return /^\d+(\.\d{1,8})?$/.test(s) && s !== "0" && !/^0+$/.test(s) && s !== "0.0" && !/^0\.0+$/.test(s);
}

function setTotpUi(enabled) {
  totpEnabled = !!enabled;
  const sendWrap = document.getElementById("walletSendTotpWrap");
  const backupWrap = document.getElementById("walletBackupTotpWrap");
  const hint = document.getElementById("walletSendTotpHint");
  if (sendWrap) sendWrap.style.display = totpEnabled ? "block" : "none";
  if (backupWrap) backupWrap.style.display = totpEnabled ? "block" : "none";
  if (hint) hint.style.display = totpEnabled ? "inline" : "none";
  const sendTotp = document.getElementById("walletSendTotp");
  const backupTotp = document.getElementById("walletBackupTotp");
  if (sendTotp) sendTotp.required = totpEnabled;
  if (backupTotp) backupTotp.required = totpEnabled;
}

function setWalletVisible(visible) {
  const auth = document.getElementById("adminAuthSection");
  const sections = document.getElementById("walletSections");
  if (auth) auth.style.display = visible ? "none" : "block";
  if (sections) sections.style.display = visible ? "" : "none";
}

async function requireWalletAccess() {
  if (setupToken) {
    setWalletVisible(true);
    return true;
  }
  try {
    const r = await fetch("/api/admin-auth/status");
    const d = await r.json();
    setTotpUi(!!d.totp_enabled);
    if (d.authenticated) {
      const user = d.username || "";
      const sendUser = document.getElementById("walletSendUsername");
      const backupUser = document.getElementById("walletBackupUsername");
      if (sendUser && user) sendUser.value = user;
      if (backupUser && user) backupUser.value = user;
      setWalletVisible(true);
      return true;
    }
    const form = document.getElementById("adminLoginForm");
    const help = document.getElementById("adminAuthHelp");
    if (form) form.style.display = d.credentials_configured ? "block" : "none";
    if (help && !d.credentials_configured) {
      help.textContent =
        "Admin login has not been configured yet. Open the original setup QR link on this device to set credentials.";
    }
  } catch (_) {}
  setWalletVisible(false);
  return false;
}

function loginAdmin(e) {
  e.preventDefault();
  const status = document.getElementById("adminAuthStatus");
  const totpStep = document.getElementById("adminLoginTotpStep");
  const passwordStep = document.getElementById("adminLoginPasswordStep");
  const pendingEl = document.getElementById("adminLoginPendingToken");

  function fillStepUpUsername(name) {
    const sendUser = document.getElementById("walletSendUsername");
    const backupUser = document.getElementById("walletBackupUsername");
    if (sendUser && name) sendUser.value = name;
    if (backupUser && name) backupUser.value = name;
  }

  if (totpStep && totpStep.style.display !== "none") {
    const code = document.getElementById("adminLoginTotpCode")?.value || "";
    const pending = pendingEl?.value || "";
    const username =
      document.getElementById("adminLoginUsername")?.value ||
      pendingEl?.dataset?.username ||
      "";
    fetch("/api/admin-auth/login/2fa", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ pending_token: pending, code }),
    })
      .then((r) => parseJsonResponse(r))
      .then((d) => {
        if (d.success) {
          fillStepUpUsername(username);
          setWalletVisible(true);
          loadWallet();
        } else {
          showStatus(status, "error", d.error || "Invalid code");
        }
      })
      .catch((err) => showStatus(status, "error", err.message));
    return;
  }

  const username = document.getElementById("adminLoginUsername")?.value || "";
  const password = document.getElementById("adminLoginPassword")?.value || "";
  fetch("/api/admin-auth/login", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ username, password }),
  })
    .then((r) => parseJsonResponse(r))
    .then((d) => {
      if (d.success && d.needs_2fa) {
        if (pendingEl) {
          pendingEl.value = d.pending_token || "";
          pendingEl.dataset.username = username;
        }
        if (passwordStep) passwordStep.style.display = "none";
        if (totpStep) totpStep.style.display = "block";
        showStatus(status, "info", d.message || "Enter authenticator code");
        return;
      }
      if (d.success) {
        fillStepUpUsername(username);
        setWalletVisible(true);
        loadWallet();
      } else {
        showStatus(status, "error", d.error || "Login failed");
      }
    })
    .catch((err) => showStatus(status, "error", err.message));
}

function renderTransactions(rows) {
  const el = document.getElementById("walletTxList");
  if (!el) return;
  if (!rows || !rows.length) {
    el.textContent = "No transactions yet.";
    return;
  }
  el.innerHTML = rows
    .map((tx) => {
      const amountStr = String(tx.amount ?? "");
      const sign = amountStr.startsWith("-") ? "" : "+";
      const conf =
        tx.confirmations > 0 ? tx.confirmations + " conf" : "unconfirmed";
      const when = tx.time
        ? new Date(tx.time * 1000).toLocaleString()
        : "";
      const txidShort = tx.txid ? String(tx.txid).slice(0, 12) + "…" : "";
      return (
        '<div class="wallet-tx-row">' +
        '<div class="wallet-tx-main">' +
        '<span class="wallet-tx-cat">' +
        escapeHtml(tx.category || "tx") +
        "</span> " +
        '<span class="portal-kpi-value--mono">' +
        escapeHtml(sign + formatBtc(amountStr)) +
        "</span>" +
        "</div>" +
        '<div class="wallet-tx-meta muted-note muted-note--compact">' +
        escapeHtml(conf) +
        (when ? " · " + escapeHtml(when) : "") +
        (txidShort ? " · " + escapeHtml(txidShort) : "") +
        "</div></div>"
      );
    })
    .join("");
}

function applyWalletPayload(d) {
  if (typeof d.totp_enabled === "boolean") setTotpUi(d.totp_enabled);

  const setText = (id, v) => {
    const el = document.getElementById(id);
    if (el) el.textContent = v;
  };
  setText("walletBalTrusted", formatBtc(d.trusted));
  setText("walletBalPending", formatBtc(d.untrusted_pending));
  setText("walletBalImmature", formatBtc(d.immature));

  const addrInput = document.getElementById("walletReceiveAddress");
  const qr = document.getElementById("walletReceiveQr");
  if (addrInput && d.receive_address) addrInput.value = d.receive_address;
  if (qr && d.receive_address) {
    qr.src = withToken(
      "/api/wallet/receive-qr.svg?address=" + encodeURIComponent(d.receive_address)
    );
    qr.style.display = "block";
  }

  const syncNote = document.getElementById("walletSyncNote");
  if (syncNote) {
    const notes = [];
    if (d.initialblockdownload) {
      notes.push(
        "Node is still syncing (IBD). You can receive anytime; sending may fail until the tip is caught up."
      );
    }
    if (d.legacy_mining_balance) {
      notes.push(
        "A previous mining wallet on this device still holds " +
          formatBtc(d.legacy_mining_balance) +
          ". New rewards use this portal wallet; move legacy funds with bitcoin-cli if needed."
      );
    }
    if (notes.length) {
      syncNote.style.display = "block";
      syncNote.textContent = notes.join(" ");
    } else {
      syncNote.style.display = "none";
      syncNote.textContent = "";
    }
  }
  renderTransactions(d.transactions || []);
}

function mintReceiveAddress(forceNew) {
  const status = document.getElementById("walletReceiveStatus");
  // Coalesce concurrent auto-mints (e.g. loadWallet after send while first mint runs).
  // Forced "New address" waits for any in-flight mint, then requests another.
  if (receiveMintPromise) {
    if (!forceNew) return receiveMintPromise;
    return receiveMintPromise.catch(() => null).then(() => mintReceiveAddress(true));
  }
  receiveMintPromise = fetch(withToken("/api/wallet/receive"), { method: "POST" })
    .then((r) => parseJsonResponse(r))
    .then((d) => {
      if (!d.success && d.error) {
        throw new Error(d.error);
      }
      receiveMintedThisOpen = true;
      applyWalletPayload(d);
      return d;
    })
    .catch((err) => {
      showStatus(status, "error", err.message);
      throw err;
    })
    .finally(() => {
      receiveMintPromise = null;
    });
  return receiveMintPromise;
}

function loadWallet() {
  showLoading("Loading wallet...");
  fetch(withToken("/api/wallet"))
    .then((r) => parseJsonResponse(r))
    .then((d) => {
      if (d.error && d.success === false) {
        hideLoading();
        showStatus(document.getElementById("walletStatus"), "error", d.error);
        return null;
      }
      applyWalletPayload(d);
      if (!receiveMintedThisOpen) {
        return mintReceiveAddress(false).finally(hideLoading);
      }
      hideLoading();
      return d;
    })
    .catch((err) => {
      hideLoading();
      showStatus(document.getElementById("walletStatus"), "error", err.message);
    });
}

function copyReceiveAddress() {
  const input = document.getElementById("walletReceiveAddress");
  const status = document.getElementById("walletReceiveStatus");
  const addr = input?.value || "";
  if (!addr) return;
  navigator.clipboard
    .writeText(addr)
    .then(() => showStatus(status, "success", "Address copied"))
    .catch(() => {
      input.select();
      showStatus(status, "info", "Select and copy the address manually");
    });
}

function newReceiveAddress() {
  const status = document.getElementById("walletReceiveStatus");
  showLoading("Generating address...");
  mintReceiveAddress(true)
    .then(() => {
      hideLoading();
      showStatus(status, "success", "New receive address ready");
    })
    .catch(() => hideLoading());
}

function clearStepUpFields(prefix) {
  const pw = document.getElementById(prefix + "Password");
  const totp = document.getElementById(prefix + "Totp");
  if (pw) pw.value = "";
  if (totp) totp.value = "";
}

function sendBitcoin(e) {
  e.preventDefault();
  if (sendInFlight) return;
  const status = document.getElementById("walletSendStatus");
  const form = document.getElementById("walletSendForm");
  const address = document.getElementById("walletSendAddress")?.value.trim() || "";
  const amountRaw = document.getElementById("walletSendAmount")?.value.trim() || "";
  const subtract = !!document.getElementById("walletSubtractFee")?.checked;
  const username = document.getElementById("walletSendUsername")?.value.trim() || "";
  const password = document.getElementById("walletSendPassword")?.value || "";
  const totp_code = document.getElementById("walletSendTotp")?.value.trim() || "";
  if (!address) {
    showStatus(status, "error", "Destination address is required");
    return;
  }
  if (!isValidBtcAmountString(amountRaw)) {
    showStatus(status, "error", "Enter a valid BTC amount (up to 8 decimals)");
    return;
  }
  if (!username || !password) {
    showStatus(status, "error", "Re-enter admin username and password to send");
    return;
  }
  if (totpEnabled && !totp_code) {
    showStatus(status, "error", "Authenticator code is required");
    return;
  }
  if (
    !window.confirm(
      "Send " + formatBtc(amountRaw) + " to\n" + address + "\n\nThis cannot be undone."
    )
  ) {
    return;
  }
  sendInFlight = true;
  if (form) form.querySelectorAll("button,input").forEach((el) => { el.disabled = true; });
  showLoading("Broadcasting transaction...");
  fetch(withToken("/api/wallet/send"), {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({
      address,
      amount: amountRaw,
      subtract_fee_from_amount: subtract,
      username,
      password,
      totp_code,
    }),
  })
    .then((r) => parseJsonResponse(r))
    .then((d) => {
      hideLoading();
      clearStepUpFields("walletSend");
      if (!d.success) {
        showStatus(status, "error", d.error || "Send failed");
        return;
      }
      showStatus(status, "success", "Sent. Txid: " + String(d.txid || "").slice(0, 18) + "…");
      document.getElementById("walletSendAddress").value = "";
      document.getElementById("walletSendAmount").value = "";
      loadWallet();
    })
    .catch((err) => {
      hideLoading();
      clearStepUpFields("walletSend");
      showStatus(status, "error", err.message);
    })
    .finally(() => {
      sendInFlight = false;
      if (form) {
        form.querySelectorAll("button,input").forEach((el) => {
          // Keep receive-style readonly fields alone; restore editable controls.
          if (el.id === "walletSendTotp" && !totpEnabled) return;
          el.disabled = false;
        });
        const totp = document.getElementById("walletSendTotp");
        if (totp) totp.required = totpEnabled;
      }
    });
}

function exportBackup(e) {
  e.preventDefault();
  if (backupInFlight) return;
  const status = document.getElementById("walletBackupStatus");
  const out = document.getElementById("walletBackupOutput");
  const form = document.getElementById("walletBackupForm");
  const username = document.getElementById("walletBackupUsername")?.value.trim() || "";
  const password = document.getElementById("walletBackupPassword")?.value || "";
  const totp_code = document.getElementById("walletBackupTotp")?.value.trim() || "";
  if (!username || !password) {
    showStatus(status, "error", "Re-enter admin username and password to export");
    return;
  }
  if (totpEnabled && !totp_code) {
    showStatus(status, "error", "Authenticator code is required");
    return;
  }
  // Never leave private descriptors sitting in the DOM.
  if (out) {
    out.hidden = true;
    out.style.display = "none";
    out.textContent = "";
  }
  backupInFlight = true;
  if (form) form.querySelectorAll("button,input").forEach((el) => { el.disabled = true; });
  showLoading("Exporting backup...");
  fetch(withToken("/api/wallet/backup"), {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ username, password, totp_code }),
  })
    .then((r) => parseJsonResponse(r))
    .then((d) => {
      hideLoading();
      clearStepUpFields("walletBackup");
      if (!d.success) {
        showStatus(status, "error", d.error || "Backup failed");
        return;
      }
      const text = d.backup || "";
      const blob = new Blob([text], { type: "text/plain;charset=utf-8" });
      const url = URL.createObjectURL(blob);
      const a = document.createElement("a");
      a.href = url;
      a.download = "blockvase-spend-wallet-backup.txt";
      document.body.appendChild(a);
      a.click();
      a.remove();
      URL.revokeObjectURL(url);
      showStatus(
        status,
        "success",
        d.warning || "Backup downloaded. Store it offline and keep it secret."
      );
    })
    .catch((err) => {
      hideLoading();
      clearStepUpFields("walletBackup");
      showStatus(status, "error", err.message);
    })
    .finally(() => {
      backupInFlight = false;
      if (form) {
        form.querySelectorAll("button,input").forEach((el) => {
          el.disabled = false;
        });
        const totp = document.getElementById("walletBackupTotp");
        if (totp) totp.required = totpEnabled;
      }
    });
}

function loadDeviceName() {
  const h1 = document.getElementById("deviceNameHeader");
  fetch(withToken("/api/device-name"))
    .then((r) => r.json())
    .then((n) => {
      const name = n.name || "blockvase";
      if (h1) h1.textContent = formatDeviceName(name) + " Wallet";
    })
    .catch(() => {});
}

document.getElementById("adminLoginForm")?.addEventListener("submit", loginAdmin);
document.getElementById("adminLoginTotpCancel")?.addEventListener("click", () => {
  const totpStep = document.getElementById("adminLoginTotpStep");
  const passwordStep = document.getElementById("adminLoginPasswordStep");
  if (totpStep) totpStep.style.display = "none";
  if (passwordStep) passwordStep.style.display = "block";
});
document.getElementById("walletCopyAddressBtn")?.addEventListener("click", copyReceiveAddress);
document.getElementById("walletNewAddressBtn")?.addEventListener("click", newReceiveAddress);
document.getElementById("walletSendForm")?.addEventListener("submit", sendBitcoin);
document.getElementById("walletBackupForm")?.addEventListener("submit", exportBackup);

loadDeviceName();
requireWalletAccess().then((ok) => {
  if (ok) loadWallet();
});
