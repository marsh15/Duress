(() => {
  "use strict";

  const tokenState = { value: "" };
  const byId = (id) => document.getElementById(id);

  function currentToken() {
    const field = byId("api-token");
    if (field) tokenState.value = field.value.trim();
    return tokenState.value;
  }

  function setMessage(id, message, isError = false) {
    const node = byId(id);
    if (!node) return;
    node.textContent = message || "";
    node.classList.toggle("message-error", isError);
  }

  function apiErrorMessage(response, payload) {
    if (response.status === 401) return "This API requires an access token. Enter it above and try again.";
    if (response.status === 409) return "Another operation is already running in this workspace. Wait for it to finish, then retry.";
    if (response.status === 429) return "The operation limit has been reached. Wait before starting another run.";
    if (response.status === 413) return "The submitted artifact is too large to replay.";
    const detail = payload && payload.detail;
    if (Array.isArray(detail)) return detail.map((item) => `${(item.loc || []).join(" · ")}: ${item.msg || "invalid value"}`).join("; ");
    if (response.status === 404) return "This artifact or record is unavailable. It may have expired after a restart. Reload the library and choose an available record.";
    return detail ? `The API could not complete this action (${detail}).` : `The API returned ${response.status}.`;
  }

  async function api(path, options = {}) {
    const headers = new Headers(options.headers || {});
    const token = currentToken();
    if (token) headers.set("X-Duress-Token", token);
    if (options.body !== undefined) headers.set("Content-Type", "application/json");
    const response = await fetch(path, { ...options, headers, credentials: "same-origin" });
    let payload = {};
    try { payload = await response.json(); } catch (_) { payload = {}; }
    if (!response.ok) throw new Error(apiErrorMessage(response, payload));
    return { payload, response };
  }

  function addTargetOptions(select, targets, preferred) {
    if (!select) return;
    const previous = preferred || select.value;
    select.replaceChildren();
    for (const target of targets) {
      const option = document.createElement("option");
      option.value = target.target_id;
      option.textContent = `${target.target_id}${target.variant ? ` · ${target.variant}` : ""}`;
      select.append(option);
    }
    if (previous && targets.some((item) => item.target_id === previous)) select.value = previous;
    else if (targets.length) select.selectedIndex = 0;
  }

  function renderTargets(targets) {
    const list = byId("target-registry-list");
    if (!list) return;
    list.replaceChildren();
    if (!targets.length) {
      const item = document.createElement("li");
      item.className = "list-placeholder";
      item.textContent = "No target profiles are available.";
      list.append(item);
      return;
    }
    const references = targets.filter((target) => !target.target_id.startsWith("fixture-"));
    const featured = (references.length ? references : targets).slice(0, 3);
    for (const target of featured) {
      const item = document.createElement("li");
      item.className = "registry-row";
      const name = document.createElement("strong");
      name.textContent = target.target_id;
      const variant = document.createElement("span");
      variant.textContent = `${target.profile || "profile"} · ${target.variant || "variant"}`;
      const workers = document.createElement("small");
      workers.textContent = `${target.workers ?? "—"} worker${target.workers === 1 ? "" : "s"}`;
      item.append(name, variant, workers);
      list.append(item);
    }
    const more = document.createElement("li");
    const link = document.createElement("a");
    link.href = "/targets";
    link.textContent = `View all ${targets.length} targets and controls`;
    more.append(link);
    list.append(more);
  }

  function renderHistory(payload) {
    const list = byId("investigation-history");
    if (!list) return;
    const records = Array.isArray(payload) ? payload : payload.items || payload.investigations || [];
    list.replaceChildren();
    if (!records.length) {
      const item = document.createElement("li");
      item.className = "list-placeholder";
      item.textContent = "No saved investigation records were returned.";
      list.append(item);
      return;
    }
    for (const record of records) {
      if (!record || !record.investigation_id) continue;
      const item = document.createElement("li");
      item.className = "record-list-row";
      const anchor = document.createElement("a");
      anchor.className = "record-list-main";
      anchor.href = `/investigations/${encodeURIComponent(record.investigation_id)}`;
      const name = document.createElement("strong");
      name.textContent = record.target_id || "Target not recorded";
      const detail = document.createElement("span");
      detail.textContent = `${record.mode_effective || "mode not recorded"} · ${record.status || "recorded"}`;
      const id = document.createElement("code");
      id.textContent = record.investigation_id;
      anchor.append(name, detail, id);
      const summary = record.recorded_summary || {};
      const stats = document.createElement("span");
      stats.className = "record-list-stat";
      if (summary.valid !== undefined && summary.fail !== undefined) {
        stats.textContent = `${summary.fail} fail / ${summary.valid} valid · aggregate`;
      } else {
        stats.textContent = record.verdict
          ? `${record.verdict}${typeof record.trials_used === "number" ? ` · ${record.trials_used} trials` : ""}`
          : record.showcase ? "Historical benchmark" : "Business verdict unresolved";
        if (record.verdict === "FAIL") stats.classList.add("number-fail");
      }
      const action = document.createElement("span");
      action.className = "record-list-open";
      action.textContent = "Inspect record";
      item.append(anchor, stats, action);
      list.append(item);
    }
    if (!list.childElementCount) {
      const item = document.createElement("li");
      item.className = "list-placeholder";
      item.textContent = "No saved investigation records were returned.";
      list.append(item);
    }
  }

  function renderDnaIndex(payload) {
    const list = byId("dna-index");
    if (!list) return;
    const items = Array.isArray(payload) ? payload : payload.items || [];
    list.replaceChildren();
    if (!items.length) {
      const item = document.createElement("li");
      item.className = "list-placeholder";
      item.textContent = "No Failure DNA artifacts are available.";
      list.append(item);
      return;
    }
    for (const artifact of items) {
      if (!artifact || !artifact.dna_id) continue;
      const item = document.createElement("li");
      item.className = "record-list-row dna-list-row";
      const anchor = document.createElement("a");
      anchor.className = "record-list-main";
      anchor.href = `/dna/${encodeURIComponent(artifact.dna_id)}`;
      const name = document.createElement("strong");
      name.textContent = artifact.template_id || "Failure DNA";
      const signature = artifact.signature || {};
      const detail = document.createElement("span");
      detail.textContent = `${signature.check || "signature not recorded"}${signature.kind ? ` · ${signature.kind}` : ""}`;
      const id = document.createElement("code");
      id.textContent = artifact.dna_id;
      anchor.append(name, detail, id);
      const rate = document.createElement("span");
      rate.className = "record-list-stat";
      rate.textContent = artifact.p0 !== undefined && artifact.p0 !== null ? `p₀ ${artifact.p0}` : "p₀ not recorded";
      const action = document.createElement("a");
      action.className = "record-list-open";
      action.href = `/dna/${encodeURIComponent(artifact.dna_id)}/download`;
      action.textContent = "Download JSON";
      item.append(anchor, rate, action);
      list.append(item);
    }
  }

  function listUnavailable(id, label) {
    const list = byId(id);
    if (!list) return;
    list.replaceChildren();
    const item = document.createElement("li");
    item.className = "list-placeholder";
    item.textContent = `${label} could not be loaded from the API.`;
    list.append(item);
  }

  async function loadApiContext() {
    let targets = [];
    try {
      const { payload } = await api("/api/targets");
      targets = Array.isArray(payload.targets) ? payload.targets : [];
      addTargetOptions(byId("vulnerable-target"), targets, "razorpay-vulnerable");
      addTargetOptions(byId("candidate-target"), targets, "razorpay-fixed");
      addTargetOptions(byId("import-target"), targets, "razorpay-vulnerable");
      renderTargets(targets);
    } catch (_) {
      renderTargets([]);
    }

    if (byId("investigation-history")) {
      try { renderHistory((await api("/api/investigations")).payload); }
      catch (_) { listUnavailable("investigation-history", "Investigation history"); }
    }
    if (byId("dna-index")) {
      try { renderDnaIndex((await api("/api/dna")).payload); }
      catch (_) { listUnavailable("dna-index", "Failure DNA index"); }
    }
  }

  function makeResult(title, payload, variant = "") {
    const wrapper = document.createElement("section");
    wrapper.className = `result-summary${variant ? ` result-${variant}` : ""}`;
    const heading = document.createElement("h3");
    const outcome = payload && payload.outcome;
    const outcomeName = typeof outcome === "string" ? outcome : outcome && outcome.outcome;
    heading.textContent = outcomeName ? `${title}: ${outcomeName.replaceAll("_", " ")}` : title;
    wrapper.append(heading);

    const counts = outcome && typeof outcome === "object" ? outcome : payload;
    const countKeys = ["valid", "fail", "inconclusive", "trials", "same_signature_failures", "other_failures", "required_valid"];
    const list = document.createElement("ul");
    list.className = "result-counts";
    for (const key of countKeys) {
      if (!counts || counts[key] === undefined || counts[key] === null) continue;
      const item = document.createElement("li");
      const value = document.createElement("strong");
      value.textContent = String(counts[key]);
      const label = document.createElement("span");
      label.textContent = key.replaceAll("_", " ");
      item.append(value, label);
      list.append(item);
    }
    if (list.childElementCount) wrapper.append(list);

    for (const [key, label] of [["control", "Vulnerable control"], ["candidate", "Candidate run"]]) {
      const stage = payload[key];
      if (!stage || typeof stage !== "object") continue;
      const section = document.createElement("div");
      section.className = "result-stage";
      const stageTitle = document.createElement("strong");
      stageTitle.textContent = label;
      const stageCounts = document.createElement("ul");
      stageCounts.className = "result-counts";
      for (const countKey of ["valid", "fail", "inconclusive", "trials", "same_signature_failures", "other_failures", "required_valid", "p_hat"]) {
        if (stage[countKey] === undefined || stage[countKey] === null) continue;
        const item = document.createElement("li");
        const value = document.createElement("strong");
        value.textContent = String(stage[countKey]);
        const name = document.createElement("span");
        name.textContent = countKey.replaceAll("_", " ");
        item.append(value, name);
        stageCounts.append(item);
      }
      section.append(stageTitle, stageCounts);
      wrapper.append(section);
    }

    const reason = (outcome && typeof outcome === "object" && outcome.reason) || payload.reason;
    if (reason) {
      const note = document.createElement("p");
      note.className = "result-statement";
      note.textContent = `Reason: ${reason.replaceAll("_", " ")}`;
      wrapper.append(note);
    }
    const statement = (outcome && typeof outcome === "object" && outcome.statement) || payload.statement;
    if (statement) {
      const note = document.createElement("p");
      note.className = "result-statement";
      note.textContent = statement;
      wrapper.append(note);
    }
    if (payload.preflight) {
      const preflight = document.createElement("p");
      preflight.className = "result-statement";
      const detail = payload.preflight.detail || payload.preflight.reason;
      preflight.textContent = `Preflight: ${payload.preflight.ok ? "passed" : "rejected"}${detail ? ` · ${detail}` : ""}`;
      wrapper.append(preflight);
    }
    if (payload.operation_id) {
      const link = document.createElement("a");
      link.className = "result-artifact-link";
      link.href = `/operations/${encodeURIComponent(payload.operation_id)}`;
      link.textContent = `Open saved operation ${payload.operation_id}`;
      wrapper.append(link);
    }
    return wrapper;
  }

  function showPayload(container, payload, title, variant = "") {
    if (!container) return;
    container.replaceChildren(makeResult(title, payload, variant));
    const pre = document.createElement("pre");
    pre.className = "result-json";
    pre.textContent = JSON.stringify(payload, null, 2);
    container.append(pre);
  }

  async function postAction(path, body) {
    const { payload } = await api(path, { method: "POST", body: JSON.stringify(body) });
    return payload;
  }

  document.addEventListener("input", (event) => {
    if (event.target && event.target.id === "api-token") tokenState.value = event.target.value.trim();
  });
  document.addEventListener("change", (event) => {
    if (event.target && event.target.id === "api-token") tokenState.value = event.target.value.trim();
  });
  window.addEventListener("pagehide", () => {
    tokenState.value = "";
    const field = byId("api-token");
    if (field) field.value = "";
  });

  document.addEventListener("submit", async (event) => {
    const form = event.target;
    if (form.id !== "verify-fix-form") return;
    event.preventDefault();
    const button = form.querySelector("button[type=submit]");
    const dnaId = button.dataset.dnaId;
    button.disabled = true;
    setMessage("verify-message", "Running control and candidate trials…");
    try {
      const payload = await postAction(`/api/dna/${encodeURIComponent(dnaId)}/verify-fix`, {
        vulnerable_target_id: form.elements.vulnerable_target_id.value,
        candidate_target_id: form.elements.candidate_target_id.value,
      });
      const tone = payload.outcome === "VERIFIED" ? "success" : ["NOT_VERIFIED", "REJECTED"].includes(payload.outcome) ? "failure" : "";
      showPayload(byId("verify-result"), payload, "Verification result", tone);
      setMessage("verify-message", "Verification result received.");
    } catch (error) {
      setMessage("verify-message", error.message, true);
    } finally {
      button.disabled = false;
    }
  });

  document.addEventListener("click", async (event) => {
    if (!(event.target instanceof Element)) return;
    const replayButton = event.target.closest("#replay-existing");
    if (replayButton) {
      replayButton.disabled = true;
      setMessage("replay-message", "Running replay trials against a fresh local target…");
      try {
        const dnaId = replayButton.dataset.dnaId;
        const payload = await postAction(`/api/dna/${encodeURIComponent(dnaId)}/replay`, { policy: "replay" });
        const outcome = payload.outcome && (payload.outcome.outcome || payload.outcome);
        showPayload(byId("replay-result"), payload, "Replay result", outcome === "REPRODUCED" ? "failure" : outcome === "NOT_REPRODUCED" ? "success" : "");
        setMessage("replay-message", outcome === "REJECTED" ? "Artifact rejected before execution." : `Replay returned ${outcome}.`);
      } catch (error) {
        showPayload(byId("replay-result"), { error: error.message }, "Replay could not complete", "failure");
        setMessage("replay-message", error.message, true);
      } finally {
        replayButton.disabled = false;
      }
      return;
    }

    const importButton = event.target.closest("#replay-import");
    if (!importButton) return;
    const fileInput = byId("import-file");
    const file = fileInput && fileInput.files && fileInput.files[0];
    if (!file) return setMessage("import-message", "Choose a JSON file first.", true);
    importButton.disabled = true;
    setMessage("import-message", "Reading portable artifact…");
    try {
      if (file.size > 1_000_000) throw new Error("Choose a Failure DNA JSON file smaller than 1 MB.");
      const dna = JSON.parse(await file.text());
      if (!dna || typeof dna !== "object" || Array.isArray(dna)) throw new Error("The selected file must contain a JSON object.");
      const payload = await postAction("/api/dna/replay", {
        dna,
        target_id: byId("import-target").value,
      });
      const outcome = payload.outcome && (payload.outcome.outcome || payload.outcome);
      showPayload(byId("import-result"), payload, "Imported replay result", outcome === "REPRODUCED" ? "failure" : outcome === "NOT_REPRODUCED" ? "success" : "");
      setMessage("import-message", outcome === "REJECTED"
        ? "Artifact rejected before execution. Review the preflight reason below."
        : `Replay returned ${outcome}. The artifact was not added to this workspace.`);
    } catch (error) {
      showPayload(byId("import-result"), { error: error.message }, "Import could not complete", "failure");
      setMessage("import-message", error.message, true);
    } finally {
      importButton.disabled = false;
    }
  });

  loadApiContext();
})();
