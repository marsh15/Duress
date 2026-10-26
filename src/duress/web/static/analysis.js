(() => {
  "use strict";

  const byId = (id) => document.getElementById(id);
  const terminalStatuses = new Set(["completed", "failed"]);
  const pollDelayMs = 1200;
  const maxPolls = 300;

  function setMessage(id, message, isError = false) {
    const node = byId(id);
    if (!node) return;
    node.textContent = message || "";
    node.classList.toggle("message-error", isError);
  }

  function apiError(response, payload) {
    if (response.status === 401) return "This API requires an access token. Enter it above and try again.";
    if (response.status === 409) return "Another operation is already running in this workspace. Wait for it to finish, then retry.";
    if (response.status === 429) return "The operation limit has been reached. Wait before starting another run.";
    const detail = payload && payload.detail;
    if (typeof detail === "string") return `The API could not complete this action (${detail}).`;
    if (Array.isArray(detail) && detail.length) {
      const issues = detail.map((item) => {
        const field = Array.isArray(item.loc) ? item.loc.filter((part) => part !== "body").join(".") : "request";
        return `${field || "request"}: ${item.msg || "invalid value"}`;
      });
      return `Check the request fields: ${issues.join("; ")}`;
    }
    return detail ? `The API returned ${response.status}.` : `The API returned ${response.status}.`;
  }

  async function api(path, options = {}) {
    const headers = new Headers(options.headers || {});
    const tokenField = byId("api-token");
    const token = tokenField ? tokenField.value.trim() : "";
    if (token) headers.set("X-Duress-Token", token);
    if (options.body !== undefined) headers.set("Content-Type", "application/json");
    const response = await fetch(path, {
      ...options,
      headers,
      credentials: "same-origin",
    });
    let payload = {};
    try { payload = await response.json(); } catch (_) { payload = {}; }
    if (!response.ok) throw new Error(apiError(response, payload));
    return payload;
  }

  function savedRecordLink(container, href, label) {
    if (!container) return;
    const link = document.createElement("a");
    link.href = href;
    link.textContent = label;
    container.replaceChildren(link);
    container.hidden = false;
  }

  async function pollAccepted(statusUrl, recordUrl, messageId, label) {
    let failures = 0;
    for (let attempt = 0; attempt < maxPolls; attempt += 1) {
      await new Promise((resolve) => window.setTimeout(resolve, pollDelayMs));
      try {
        const record = await api(statusUrl);
        failures = 0;
        if (terminalStatuses.has(record.status)) {
          setMessage(messageId, `${label} ${record.status === "completed" ? "finished" : "stopped"}. Opening the saved record.`);
          window.location.assign(recordUrl);
          return;
        }
        setMessage(messageId, `${label} is ${record.status || "running"}. Its saved record is available below.`);
      } catch (error) {
        failures += 1;
        if (failures >= 5) {
          setMessage(messageId, `${label} was accepted, but status checks are unavailable. Use the saved record link to check again. ${error.message}`, true);
          return;
        }
      }
    }
    setMessage(messageId, `${label} is still running. Open its saved record to continue checking the result.`);
  }

  async function pollCurrentRecord() {
    const panel = document.querySelector("[data-analysis-poll='true']");
    if (!panel || !panel.dataset.statusUrl) return;
    let failures = 0;
    for (let attempt = 0; attempt < maxPolls; attempt += 1) {
      await new Promise((resolve) => window.setTimeout(resolve, pollDelayMs));
      try {
        const record = await api(panel.dataset.statusUrl);
        failures = 0;
        if (terminalStatuses.has(record.status)) {
          window.location.reload();
          return;
        }
        setMessage("analysis-poll-message", `Saved record status: ${record.status || "running"}. Checking again…`);
      } catch (error) {
        failures += 1;
        if (failures >= 5) {
          setMessage("analysis-poll-message", `Status checks are unavailable. Refresh this page to retry. ${error.message}`, true);
          return;
        }
      }
    }
    const recordUrl = panel.dataset.recordUrl;
    setMessage("analysis-poll-message", `Polling paused after five minutes. ${recordUrl ? "Refresh this saved record to check again." : "Refresh this page to check again."}`);
  }

  document.addEventListener("submit", async (event) => {
    const form = event.target;
    if (!(form instanceof HTMLFormElement)) return;

    if (form.id === "reduce-dna-form") {
      event.preventDefault();
      const button = form.querySelector("button[type='submit']");
      const dnaId = form.dataset.dnaId;
      if (!dnaId) return setMessage("reduce-message", "This DNA page does not include an artifact ID.", true);
      if (button) button.disabled = true;
      setMessage("reduce-message", "Submitting bounded reduction…");
      try {
        const record = await api(`/api/dna/${encodeURIComponent(dnaId)}/reduce`, {
          method: "POST",
          body: JSON.stringify({ target_id: form.dataset.targetId || undefined, black_box: true }),
        });
        const recordUrl = `/reductions/${encodeURIComponent(record.reduction_id)}`;
        savedRecordLink(byId("reduce-record-link"), recordUrl, `Open saved reduction ${record.reduction_id}`);
        setMessage("reduce-message", `Reduction accepted (${record.status || "queued"}). Checking the persisted record…`);
        pollAccepted(record.status_url || `/api/reductions/${encodeURIComponent(record.reduction_id)}`, recordUrl, "reduce-message", "Reduction");
      } catch (error) {
        setMessage("reduce-message", error.message || "The reduction could not be started.", true);
        if (button) button.disabled = false;
      }
      return;
    }

    if (form.id !== "differential-form") return;
    event.preventDefault();
    const button = form.querySelector("button[type='submit']");
    const selected = Array.from(form.querySelectorAll("input[name='target_ids']:checked"), (input) => input.value);
    if (selected.length < 1 || selected.length > 5) {
      setMessage("differential-message", "Select between one and five targets.", true);
      return;
    }
    if (button) button.disabled = true;
    setMessage("differential-message", "Submitting the S3, S5 and S9 comparison…");
    try {
      const record = await api("/api/differential", {
        method: "POST",
        body: JSON.stringify({ target_ids: selected, policy: byId("differential-policy").value }),
      });
      const recordUrl = `/differential/${encodeURIComponent(record.differential_id)}`;
      setMessage("differential-message", `Differential accepted (${record.status || "queued"}). Opening its saved record…`);
      window.location.assign(recordUrl);
    } catch (error) {
      setMessage("differential-message", error.message || "The differential could not be started.", true);
      if (button) button.disabled = false;
    }
  });

  document.addEventListener("change", (event) => {
    if (!(event.target instanceof HTMLInputElement) || event.target.name !== "target_ids") return;
    const selected = document.querySelectorAll("input[name='target_ids']:checked");
    const submit = byId("differential-form")?.querySelector("button[type='submit']");
    if (!submit) return;
    submit.disabled = selected.length < 1 || selected.length > 5;
  });

  window.addEventListener("pagehide", () => {
    const tokenField = byId("api-token");
    if (tokenField) tokenField.value = "";
  });

  pollCurrentRecord();
})();
