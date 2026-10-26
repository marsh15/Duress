(() => {
  "use strict";

  const form = document.getElementById("investigation-create-form");
  const button = form ? form.querySelector('button[type="submit"]') : null;
  const message = document.getElementById("investigation-create-message");
  const tokenField = document.getElementById("api-token");

  function announce(text, isError = false) {
    if (!message) return;
    message.textContent = text;
    message.classList.toggle("message-error", isError);
  }

  function errorText(response, payload) {
    if (response.status === 401) {
      return "This server requires an access token for that mode. Enter a token above and try again.";
    }
    if (response.status === 409) {
      return "Another operation is already running in this workspace. Wait for it to finish, then retry.";
    }
    if (response.status === 429) {
      return "The operation limit has been reached. Wait before starting another investigation.";
    }
    const detail = payload && payload.detail;
    if (detail === "unknown_target") return "That target is no longer available. Reload the page and choose an available target.";
    if (response.status === 422 && Array.isArray(detail)) {
      const issues = detail.map((item) => {
        const location = Array.isArray(item && item.loc)
          ? item.loc.filter((part) => part !== "body").join(" → ")
          : "";
        const reason = item && (item.msg || item.message);
        return [location, reason || "Invalid value"].filter(Boolean).join(": ");
      });
      if (issues.length) return `Please correct the request: ${issues.join("; ")}.`;
    }
    if (detail && typeof detail === "object") {
      const readable = detail.message || detail.error || detail.reason;
      return readable ? `The server rejected this request (${readable}).` : "The server rejected this request with validation details that could not be displayed.";
    }
    if (detail) return `The server rejected this request (${String(detail).replaceAll("_", " ")}).`;
    return `The server returned ${response.status} while starting the investigation.`;
  }

  function pollRoot(event) {
    const detail = event.detail || {};
    const candidates = [detail.elt, detail.target, detail.requestConfig && detail.requestConfig.elt];
    for (const candidate of candidates) {
      if (!(candidate instanceof Element)) continue;
      if (candidate.id === "investigation-live") return candidate;
      const root = candidate.closest("#investigation-live");
      if (root) return root;
    }
    return document.getElementById("investigation-live");
  }

  function showPollMessage(root, text, stop = false) {
    if (!root) return;
    if (stop) root.dataset.pollStopped = "true";
    const status = root.querySelector("#investigation-poll-message");
    if (!status) return;
    status.hidden = false;
    status.textContent = text;
  }

  document.body.addEventListener("htmx:beforeRequest", (event) => {
    const root = pollRoot(event);
    if (root && root.dataset.pollStopped === "true") event.preventDefault();
  });

  document.body.addEventListener("htmx:responseError", (event) => {
    const root = pollRoot(event);
    if (!root) return;
    const status = event.detail && event.detail.xhr && event.detail.xhr.status;
    if (status === 404) {
      showPollMessage(root, "This investigation record is missing or has expired (HTTP 404). Progress polling has stopped.", true);
    } else if (status >= 400 && status < 500) {
      showPollMessage(root, `Progress could not be refreshed (HTTP ${status}). Automatic polling has stopped.` , true);
    } else {
      showPollMessage(root, `Progress refresh returned HTTP ${status || "error"}. Retrying automatically.`, false);
    }
  });

  document.body.addEventListener("htmx:sendError", (event) => {
    const root = pollRoot(event);
    showPollMessage(root, "Progress could not be reached right now. The page will retry automatically.", false);
  });

  if (!form) return;

  form.addEventListener("submit", async (event) => {
    event.preventDefault();
    if (!form.reportValidity()) return;

    const targetId = form.elements.target_id.value;
    const mode = form.elements.mode.value;
    if (!targetId) {
      announce("Choose an available target before starting.", true);
      return;
    }

    if (button) {
      button.disabled = true;
      button.textContent = "Starting…";
    }
    announce("Submitting the investigation request…");

    const headers = new Headers({ "Content-Type": "application/json" });
    const token = tokenField ? tokenField.value.trim() : "";
    if (token) headers.set("X-Duress-Token", token);

    try {
      const response = await fetch("/api/investigations", {
        method: "POST",
        credentials: "same-origin",
        headers,
        body: JSON.stringify({ target_id: targetId, mode, reduce: false }),
      });
      let payload = {};
      try { payload = await response.json(); } catch (_) { payload = {}; }
      if (!response.ok) throw new Error(errorText(response, payload));
      if (response.status !== 202 || !payload.investigation_id) {
        throw new Error("The server response did not include a queued investigation record.");
      }

      announce("Accepted · queued. Opening the persisted progress record…");
      const destination = `/investigations/${encodeURIComponent(payload.investigation_id)}`;
      window.setTimeout(() => window.location.assign(destination), 160);
    } catch (error) {
      announce(error instanceof Error ? error.message : "The investigation could not be started.", true);
      if (button) {
        button.disabled = false;
        button.textContent = "Start investigation";
      }
    }
  });
})();
