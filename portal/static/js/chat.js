/* agent-orchestrator console — conversation view (issue #508, ADR-0023)
 * ---knowledge---
 * module_id: portal.static.js.chat
 * system: portal
 * app: static
 * solution_class: pattern
 * patterns: [client-half, honest-absence, tier-only-picker]
 * derives_from: null
 * owner_sme: frontend-sme
 * tier: L1
 * interfaces: [portal/static/views/chat.html]
 * invariants: "the picker offers tiers only - the resolved model is displayed, never chosen; a fragment the envelope does not back renders data-supported=false"
 * gotchas: "absence is a state, not an empty success - NO_DATA for grounding, no_data for an absent figure"
 * related: ["#508", "#514"]
 * do_not_duplicate: null
 * ---knowledge---
 *
 * The client half of the conversational surface. It owns no authority: it
 * renders what the serving surface streamed and it says so when the surface
 * said nothing. Three rules are load-bearing and visible in the code below:
 *
 *   - the picker offers *tiers only* (the resolved model is displayed, never
 *     chosen — the select is filled from /api/chat/tiers and nothing else);
 *   - a fragment the citations envelope does not back is rendered
 *     data-supported="false" ("unsupported"), never as fact;
 *   - absence is a state, not an empty success: NO_DATA for grounding, and
 *     no_data for a per-turn figure the FinOps read model did not return,
 *     while a hard_stop budget is visibly distinct from a soft warning.
 */
(function () {
  "use strict";

  var el = window.CP.el;
  var clear = window.CP.clear;

  var messages = document.getElementById("messages");
  var liveRegion = document.getElementById("liveRegion");
  var tierPicker = document.getElementById("tierPicker");
  var tierResolved = document.getElementById("tierResolved");
  var budgetState = document.getElementById("budgetState");
  var conversationList = document.getElementById("conversationList");
  var conversationTitle = document.getElementById("conversationTitle");
  var historyState = document.getElementById("historyState");
  var turnState = document.getElementById("turnState");
  var input = document.getElementById("input");
  var sendButton = document.getElementById("send");
  var stopButton = document.getElementById("stop");
  var retryButton = document.getElementById("retry");
  var newButton = document.getElementById("newConversation");
  var turnTokens = document.getElementById("turnTokens");
  var turnLatency = document.getElementById("turnLatency");
  var turnCost = document.getElementById("turnCost");
  var turnGrounding = document.getElementById("turnGrounding");
  var turnDegraded = document.getElementById("turnDegraded");
  var turnFooter = document.getElementById("turnFooter");

  var params = window.CP.params;
  var tenant = params.get("tenant") || "";
  var state = {
    conversationId: "",
    turnId: "",
    streaming: false,
    vocabulary: [],
    resolvedModels: {},
    selectedTier: "",
    budget: null,
    current: null
  };

  function withTenant(path) {
    if (!tenant) { return path; }
    return path + (path.indexOf("?") === -1 ? "?" : "&") +
      "tenant=" + encodeURIComponent(tenant);
  }

  function announce(text, tag) {
    liveRegion.textContent = text;
    liveRegion.setAttribute("data-announce", tag || "info");
  }

  function setTurnState(name) {
    turnState.textContent = name;
    turnState.setAttribute("data-state", name);
  }

  /* ---- budget: soft warning vs hard stop are never the same badge -------- */
  function severityClass(severity) {
    return {
      ok: "ok",
      warning: "warn",
      hard_stop: "err",
      no_data: "info"
    }[severity] || "info";
  }

  function severityLabel(severity, budget) {
    if (severity === "ok") { return "budget ok"; }
    if (severity === "warning") {
      return "budget warning" + (budget && budget.action === "fallback"
        ? " (tier falls back)" : "");
    }
    if (severity === "hard_stop") { return "budget STOPPED — send refused"; }
    return "budget no data for this";
  }

  function renderBudget(budget) {
    if (!budget) { return; }
    state.budget = budget;
    var severity = budget.severity || "no_data";
    budgetState.className = "badge " + severityClass(severity);
    budgetState.setAttribute("data-severity", severity);
    budgetState.setAttribute("data-can-send", budget.canSend ? "true" : "false");
    budgetState.textContent = severityLabel(severity, budget);
    budgetState.title = budget.note || "";
    sendButton.disabled = state.streaming || budget.canSend === false;
  }

  function loadBudget() {
    return window.CP.get(withTenant("/api/chat/budget")).then(function (payload) {
      renderBudget(payload.data);
    }).catch(function (error) {
      renderBudget({
        severity: "no_data", canSend: false, action: "",
        note: "the budget state could not be read: " + error.message
      });
    });
  }

  /* ---- tiers: tiers only; the resolved model is displayed --------------- */
  function renderTiers(data) {
    state.vocabulary = data.vocabulary || [];
    state.resolvedModels = data.resolvedModels || {};
    clear(tierPicker);
    (data.tiers || []).forEach(function (tier) {
      tierPicker.appendChild(el("option", { value: tier.id, text: tier.label }));
    });
    state.selectedTier = data.defaultTier || (state.vocabulary[0] || "");
    tierPicker.value = state.selectedTier;
    tierPicker.setAttribute("data-selects", data.selects || "tier");
    renderResolved();
  }

  function renderResolved() {
    var model = state.resolvedModels[state.selectedTier];
    tierResolved.textContent = model
      ? "resolved: " + model
      : "resolved at send";
    tierResolved.setAttribute("data-resolved", model || "");
  }

  function loadTiers() {
    return window.CP.get(withTenant("/api/chat/tiers")).then(function (payload) {
      renderTiers(payload.data);
    });
  }

  /* ---- transcript ------------------------------------------------------- */
  function messageBubble(role) {
    var bubble = el("div", {
      class: "feed-item",
      "data-role": role,
      "data-state": "streaming",
      style: "border:1px solid var(--os-color-border);border-radius:var(--os-radius-md);padding:var(--os-space-2)"
    });
    bubble.appendChild(el("div", {
      class: "dim mono",
      style: "font-size:var(--os-font-size-xs)",
      text: role
    }));
    bubble.appendChild(el("div", { class: "text", text: "" }));
    return bubble;
  }

  function citationChip(fragment) {
    var supported = fragment.supported === true;
    var chip = el("span", {
      class: "chip",
      // The visible provenance status is the machine-readable one too.
      "data-supported": supported ? "true" : "false",
      "data-source-id": fragment.sourceId || "",
      style: supported
        ? "color:var(--os-color-info);margin-right:var(--os-space-2)"
        : "color:var(--os-color-warning);margin-right:var(--os-space-2)",
      title: supported
        ? "grounded in the citations envelope"
        : "this claim is not backed by the citations envelope",
      text: supported ? fragment.label : "unsupported — no source in the envelope"
    });
    return chip;
  }

  function renderFragments(bubble, turn) {
    var fragments = turn.fragments || [];
    if (!fragments.length) { return; }
    var block = el("div", {
      class: "dim",
      style: "margin-top:var(--os-space-2);font-size:var(--os-font-size-xs)"
    });
    fragments.forEach(function (fragment) {
      block.appendChild(citationChip(fragment));
      block.appendChild(el("div", { class: "hint",
        text: fragment.text || "" }));
    });
    bubble.appendChild(block);
  }

  function renderDegraded(bubble, turn) {
    var degraded = turn.degraded || {};
    if (!degraded.degraded) { return; }
    bubble.appendChild(el("div", {
      class: "badge warn",
      "data-degraded": "true",
      style: "margin-top:var(--os-space-2)",
      text: "degraded: " + (degraded.reason || "a lower tier answered") +
        (degraded.toTier ? " (" + (degraded.fromTier || "?") + " → " +
          degraded.toTier + ")" : "")
    }));
  }

  function renderGrounding(bubble, turn) {
    var grounding = turn.grounding || {};
    var state_ = grounding.state === "OK" ? "OK" : "NO_DATA";
    bubble.appendChild(el("div", {
      class: "hint",
      "data-state": state_,
      style: "margin-top:var(--os-space-2)",
      text: state_ === "OK"
        ? "grounded in " + ((turn.sources || []).length) + " source(s)"
        : "no data for this: " + (grounding.note || "no grounding source")
    }));
  }

  function renderTurnMeta(turn) {
    var usage = turn.usage || {};
    var known = usage.state === "OK";
    turnFooter.setAttribute("data-usage", known ? "OK" : "no_data");
    turnTokens.setAttribute("data-tokens", known ? "OK" : "no_data");
    turnTokens.textContent = known && usage.totalTokens != null
      ? "tokens: " + window.CP.num(usage.totalTokens)
      : "tokens: no data";
    turnLatency.setAttribute("data-latency", known && usage.latencyMs != null
      ? "OK" : "no_data");
    turnLatency.textContent = known && usage.latencyMs != null
      ? "latency: " + usage.latencyMs + " ms"
      : "latency: no data";
    turnCost.setAttribute("data-cost", known && usage.estimatedCostUsd != null
      ? "OK" : "no_data");
    turnCost.textContent = known && usage.estimatedCostUsd != null
      ? "cost: " + window.CP.money(usage.estimatedCostUsd)
      : "cost: no data";
    turnGrounding.setAttribute("data-state",
      (turn.grounding && turn.grounding.state) === "OK" ? "OK" : "NO_DATA");
    turnGrounding.textContent =
      (turn.grounding && turn.grounding.state) === "OK"
        ? "grounding: " + ((turn.sources || []).length) + " source(s)"
        : "grounding: no data for this";
    var degraded = turn.degraded || {};
    turnDegraded.hidden = !degraded.degraded;
    turnDegraded.setAttribute("data-degraded", degraded.degraded ? "true" : "false");
    turnDegraded.textContent = degraded.degraded
      ? "degraded: " + (degraded.reason || "fallback tier") : "not degraded";
    if (turn.resolvedModel) {
      state.resolvedModels[turn.tier] = turn.resolvedModel;
      renderResolved();
    }
  }

  function renderTurn(bubble, turn) {
    bubble.querySelector(".text").textContent = turn.text || "";
    bubble.setAttribute("data-state", turn.state || "complete");
    if (turn.resolvedModel) {
      bubble.appendChild(el("div", { class: "dim mono",
        style: "font-size:var(--os-font-size-xs)",
        text: "answered by " + turn.resolvedModel + " (tier " + turn.tier + ")" }));
    }
    renderFragments(bubble, turn);
    renderDegraded(bubble, turn);
    renderGrounding(bubble, turn);
    renderTurnMeta(turn);
  }

  function pushTurn(turn) {
    var bubble = messageBubble(turn.role || "assistant");
    renderTurn(bubble, turn);
    messages.appendChild(bubble);
    messages.scrollTop = messages.scrollHeight;
    return bubble;
  }

  /* ---- conversations ---------------------------------------------------- */
  function renderConversations(rows) {
    clear(conversationList);
    historyState.setAttribute("data-count", String(rows.length));
    historyState.textContent = rows.length + " conversation(s)";
    rows.forEach(function (row) {
      var button = el("button", {
        class: "nav-btn" + (row.id === state.conversationId ? " active" : ""),
        type: "button",
        "data-conversation": row.id,
        text: row.title + " · " + row.turnCount + " turn(s)"
      });
      button.addEventListener("click", function () { openConversation(row.id); });
      conversationList.appendChild(el("li", {}, button));
    });
  }

  function loadConversations() {
    return window.CP.get(withTenant("/api/chat/conversations"))
      .then(function (payload) { renderConversations(payload.data.conversations || []); });
  }

  function openConversation(conversationId) {
    state.conversationId = conversationId;
    return window.CP.get(withTenant("/api/chat/conversations/" + conversationId))
      .then(function (payload) {
        var conversation = payload.data.conversation;
        conversationTitle.textContent = conversation.title;
        clear(messages);
        (conversation.turns || []).forEach(function (turn) { pushTurn(turn); });
        setTurnState("idle");
        announce("opened " + conversation.title, "idle");
        return loadConversations();
      });
  }

  function newConversation() {
    return window.CP.post(withTenant("/api/chat/conversations"), {})
      .then(function (payload) {
        var conversation = payload.data.conversation;
        state.conversationId = conversation.id;
        conversationTitle.textContent = conversation.title;
        clear(messages);
        setTurnState("idle");
        return loadConversations();
      });
  }

  /* ---- streaming -------------------------------------------------------- */
  function dispatchFrame(bubble, event, data) {
    if (event === "turn.started") {
      state.turnId = data.turnId;
      setTurnState("streaming");
      announce("streaming", "streaming");
      return;
    }
    if (event === "turn.budget") { renderBudget(data); return; }
    if (event === "turn.delta") {
      bubble.querySelector(".text").textContent = data.text || "";
      messages.scrollTop = messages.scrollHeight;
      var tail = (data.text || "").slice(-60);
      announce("streamed: " + tail, "streaming");
      return;
    }
    if (event === "turn.degraded") {
      renderDegraded(bubble, { degraded: data });
      announce("degraded: " + (data.reason || ""), "degraded");
      return;
    }
    if (event === "turn.citations") {
      renderFragments(bubble, { fragments: data.fragments || [] });
      announce(((data.sources || []).length) + " source(s)", "citations");
      return;
    }
    if (event === "turn.completed") {
      bubble.setAttribute("data-state", "complete");
      setTurnState("complete");
      state.current = data.turn;
      return;
    }
    if (event === "turn.cancelled") {
      bubble.setAttribute("data-state", "cancelled");
      setTurnState("cancelled");
      bubble.appendChild(el("div", { class: "hint", "data-state": "cancelled",
        text: "generation stopped" }));
      announce("stopped", "cancelled");
      return;
    }
    if (event === "turn.error") {
      bubble.setAttribute("data-state", "failed");
      setTurnState("failed");
      bubble.appendChild(el("div", { class: "error-text", "data-state": "failed",
        text: ((data.failure || {}).message) || "the turn failed" }));
      announce("the turn failed", "failed");
    }
  }

  function stream(path, body, bubble) {
    return fetch(path, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body)
    }).then(function (response) {
      if (!response.ok) {
        return response.json().catch(function () { return {}; })
          .then(function (payload) {
            var error = payload.error || {};
            bubble.setAttribute("data-state", "refused");
            bubble.setAttribute("data-refusal", error.code || String(response.status));
            bubble.appendChild(el("div", { class: "error-text",
              "data-state": "refused",
              text: error.message || ("HTTP " + response.status) }));
            setTurnState("refused");
            announce("refused: " + (error.code || response.status), "refused");
          });
      }
      var reader = response.body.getReader();
      var decoder = new TextDecoder("utf-8");
      var buffer = "";
      return new Promise(function (resolve, reject) {
        function pump() {
          reader.read().then(function (result) {
            if (result.done) { resolve(); return; }
            buffer += decoder.decode(result.value, { stream: true });
            var blocks = buffer.split("\n\n");
            buffer = blocks.pop();
            var finished = false;
            blocks.forEach(function (block) {
              var event = "message";
              var data = "";
              block.split("\n").forEach(function (line) {
                if (line.indexOf("event: ") === 0) { event = line.slice(7).trim(); }
                if (line.indexOf("data: ") === 0) { data += line.slice(6); }
              });
              if (!data) { return; }
              if (data === "[DONE]") { finished = true; return; }
              try {
                dispatchFrame(bubble, event, JSON.parse(data));
              } catch (error) { /* a frame we cannot read is not a render */ }
            });
            if (finished) { resolve(); return; }
            pump();
          }, reject);
        }
        pump();
      });
    });
  }

  function sendTurn(body) {
    if (state.streaming) { return Promise.resolve(); }
    state.streaming = true;
    sendButton.disabled = true;
    stopButton.disabled = false;
    var bubble = messageBubble("assistant");
    messages.appendChild(bubble);
    var path = withTenant("/api/chat/conversations/" + state.conversationId + "/turns");
    return stream(path, body, bubble).catch(function (error) {
      bubble.setAttribute("data-state", "failed");
      bubble.appendChild(el("div", { class: "error-text", "data-state": "failed",
        text: "stream failed: " + error.message }));
      setTurnState("failed");
      announce("stream failed", "failed");
    }).then(function () {
      state.streaming = false;
      stopButton.disabled = true;
      sendButton.disabled = state.budget && state.budget.canSend === false;
      return Promise.all([loadBudget(), loadConversations()]);
    });
  }

  function submit() {
    var text = (input.value || "").trim();
    if (!text || !state.conversationId) { return; }
    input.value = "";
    pushTurn({ role: "user", text: text, state: "complete" });
    setTurnState("sending");
    sendTurn({ text: text, tier: tierPicker.value });
  }

  function stop() {
    if (!state.streaming || !state.conversationId) { return; }
    window.CP.post(withTenant(
      "/api/chat/conversations/" + state.conversationId + "/cancel"), {});
  }

  function retry() {
    if (state.streaming || !state.conversationId) { return; }
    var body = { tier: tierPicker.value };
    state.streaming = true;
    sendButton.disabled = true;
    stopButton.disabled = false;
    var bubble = messageBubble("assistant");
    messages.appendChild(bubble);
    stream(withTenant("/api/chat/conversations/" + state.conversationId + "/retry"),
      body, bubble).then(function () {
      state.streaming = false;
      stopButton.disabled = true;
      sendButton.disabled = false;
      return Promise.all([loadBudget(), loadConversations()]);
    }).catch(function (error) {
      state.streaming = false;
      stopButton.disabled = true;
      if (error && error.code === "chat_nothing_to_retry") {
        announce("nothing to retry", "idle");
      } else {
        announce("retry failed", "failed");
      }
    });
  }

  /* ---- wiring ----------------------------------------------------------- */
  document.getElementById("composer").addEventListener("submit", function (event) {
    event.preventDefault();
    submit();
  });
  stopButton.addEventListener("click", stop);
  retryButton.addEventListener("click", retry);
  newButton.addEventListener("click", function () { newConversation(); });
  tierPicker.addEventListener("change", function () {
    state.selectedTier = tierPicker.value;
    renderResolved();
  });
  input.addEventListener("keydown", function (event) {
    if (event.key === "Enter" && !event.shiftKey) {
      event.preventDefault();
      submit();
    }
    if (event.key === "Escape") { stop(); }
  });

  Promise.all([loadTiers(), loadBudget(), loadConversations()]).then(function () {
    if (!state.conversationId && conversationList.firstChild === null) {
      return newConversation();
    }
    return null;
  }).catch(function (error) {
    announce("chat surface unavailable: " + error.message, "failed");
  });
})();
