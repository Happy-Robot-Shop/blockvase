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

function formatBtc(n) {
  const v = Number(n);
  if (!Number.isFinite(v)) return "—";
  return v.toFixed(8) + " BTC";
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
    if (d.authenticated) {
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

  if (totpStep && totpStep.style.display !== "none") {
    const code = document.getElementById("adminLoginTotpCode")?.value || "";
    const pending = pendingEl?.value || "";
    fetch("/api/admin-auth/login/2fa", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ pending_token: pending, code }),
    })
      .then((r) => parseJsonResponse(r))
      .then((d) => {
        if (d.success) {
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
        if (pendingEl) pendingEl.value = d.pending_token || "";
        if (passwordStep) passwordStep.style.display = "none";
        if (totpStep) totpStep.style.display = "block";
        showStatus(status, "info", d.message || "Enter authenticator code");
        return;
      }
      if (d.success) {
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
      const sign = Number(tx.amount) >= 0 ? "+" : "";
      const conf =
        tx.confirmations > 0 ? tx.confirmations + " conf" : "unconfirmed";
      const when = tx.time
        ? new Date(tx.time * 1000).toLocaleString()
        : "";
      return (
        '<div class="wallet-tx-row">' +
        '<div class="wallet-tx-main">' +
        '<span class="wallet-tx-cat">' +
        (tx.category || "tx") +
        "</span> " +
        '<span class="portal-kpi-value--mono">' +
        sign +
        formatBtc(tx.amount) +
        "</span>" +
        "</div>" +
        '<div class="wallet-tx-meta muted-note muted-note--compact">' +
        conf +
        (when ? " · " + when : "") +
        (tx.txid ? " · " + String(tx.txid).slice(0, 12) + "…" : "") +
        "</div></div>"
      );
    })
    .join("");
}

function applyWalletPayload(d) {
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
    if (d.initialblockdownload) {
      syncNote.style.display = "block";
      syncNote.textContent =
        "Node is still syncing (IBD). You can receive anytime; sending may fail until the tip is caught up.";
    } else {
      syncNote.style.display = "none";
      syncNote.textContent = "";
    }
  }
  renderTransactions(d.transactions || []);
}

function loadWallet() {
  showLoading("Loading wallet...");
  fetch(withToken("/api/wallet"))
    .then((r) => parseJsonResponse(r))
    .then((d) => {
      hideLoading();
      if (d.error && !d.receive_address) {
        showStatus(document.getElementById("walletStatus"), "error", d.error);
        return;
      }
      applyWalletPayload(d);
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
  fetch(withToken("/api/wallet/receive"), { method: "POST" })
    .then((r) => parseJsonResponse(r))
    .then((d) => {
      hideLoading();
      if (!d.success) {
        showStatus(status, "error", d.error || "Could not generate address");
        return;
      }
      applyWalletPayload(d);
      showStatus(status, "success", "New receive address ready");
    })
    .catch((err) => {
      hideLoading();
      showStatus(status, "error", err.message);
    });
}

function sendBitcoin(e) {
  e.preventDefault();
  const status = document.getElementById("walletSendStatus");
  const address = document.getElementById("walletSendAddress")?.value.trim() || "";
  const amountRaw = document.getElementById("walletSendAmount")?.value.trim() || "";
  const subtract = !!document.getElementById("walletSubtractFee")?.checked;
  const amount = Number(amountRaw);
  if (!address) {
    showStatus(status, "error", "Destination address is required");
    return;
  }
  if (!Number.isFinite(amount) || amount <= 0) {
    showStatus(status, "error", "Enter a valid BTC amount");
    return;
  }
  if (
    !window.confirm(
      "Send " + amount.toFixed(8) + " BTC to\n" + address + "\n\nThis cannot be undone."
    )
  ) {
    return;
  }
  showLoading("Broadcasting transaction...");
  fetch(withToken("/api/wallet/send"), {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({
      address,
      amount,
      subtract_fee_from_amount: subtract,
    }),
  })
    .then((r) => parseJsonResponse(r))
    .then((d) => {
      hideLoading();
      if (!d.success) {
        showStatus(status, "error", d.error || "Send failed");
        return;
      }
      showStatus(status, "success", "Sent. Txid: " + (d.txid || "").slice(0, 18) + "…");
      document.getElementById("walletSendAddress").value = "";
      document.getElementById("walletSendAmount").value = "";
      loadWallet();
    })
    .catch((err) => {
      hideLoading();
      showStatus(status, "error", err.message);
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

loadDeviceName();
requireWalletAccess().then((ok) => {
  if (ok) loadWallet();
});
