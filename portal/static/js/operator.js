/* agent-orchestrator console — operator terminal (issue #774)
 *
 * The browser IT-terminal behind the SSO session. It invents nothing: the read
 * half is the fleet single-pane-of-glass projection already served by the
 * server (GET /api/fleet/snapshot, issue #331/#332), and the steer half is the
 * remote control family already served (POST /api/control/<family>/<action>,
 * issue #554, ADR-0025). This module only *renders* both into one document and
 * adds the one thing a terminal pane cannot: a click-to-steer panel over the
 * CLOSED control-verb vocabulary — never a raw command, never a shell.
 *
 * Honesty is load-bearing. Both halves ship feature-flag-gated OFF:
 *   * GET /api/fleet/snapshot  -> 404 feature_disabled while
 *     surfaces.fleet_projection is off;
 *   * POST /api/control/...    -> 404 feature_disabled while
 *     surfaces.remote_control is off.
 * This module renders that answer as a visible "feature disabled" state naming
 * the flag, never as fake data and never as an empty table that reads as "no
 * fleet". A read projection that answers feature_disabled must not look like an
 * idle fleet.
 *
 * Dependency-free and DOM-framework-agnostic (the console ships offline, no npm,
 * no CDN): the model functions are pure and the render functions only touch the
 * DOM they are handed. The pure functions are exposed on `window.OT` so the
 * offline gate (scripts/check-operator-terminal.sh) and the test suite can drive
 * them from fixtures with no browser and no network.
 */
(function () {
  "use strict";

  var SNAPSHOT_PATH = "/api/fleet/snapshot";
  var VERBS_PATH = "/api/control/fleet/verbs";
  var CONTROL_ROOT = "/api/control/";

  /* ---------------------------------------------------------------- helpers */

  function asArray(value) {
    return Array.isArray(value) ? value : [];
  }

  function asObject(value) {
    return value && typeof value === "object" && !Array.isArray(value) ? value : {};
  }

  function el(tag, attrs, children) {
    var node = document.createElement(tag);
    attrs = attrs || {};
    Object.keys(attrs).forEach(function (key) {
      var value = attrs[key];
      if (value === null || value === undefined) return;
      if (key === "class") node.className = value;
      else if (key === "text") node.textContent = String(value);
      else if (key === "style") node.setAttribute("style", value);
      else if (key === "on") node.addEventListener("click", value);
      else if (value === true) node.setAttribute(key, key);
      else node.setAttribute(key, String(value));
    });
    asArray(children).forEach(function (child) {
      if (child === null || child === undefined || child === false) return;
      node.appendChild(child.nodeType ? child : document.createTextNode(String(child)));
    });
    return node;
  }

  function clear(node) {
    while (node.firstChild) node.removeChild(node.firstChild);
    return node;
  }

  function toneFor(rungState) {
    var value = String(rungState || "").toLowerCase();
    if (value === "working" || value === "active" || value === "idle") return "ok";
    if (value === "down" || value === "failed" || value === "error") return "err";
    if (value === "no-heartbeat" || value === "unknown" || value === "stale") return "warn";
    return "info";
  }

  /* The issue token a lane row is keyed by ("#232" from "#232"/"issue #232 …"). */
  function issueToken(value) {
    var match = String(value === null || value === undefined ? "" : value)
      .match(/#?\s*(\d{1,6})\b/);
    return match ? "#" + match[1] : "";
  }

  /* ------------------------------------------------------------ pure model */

  function normalizeSnapshot(raw) {
    var src = asObject(raw);
    return {
      repo: String(src.repo || ""),
      head: String(src.head || ""),
      now: String(src.now || ""),
      uptime: String(src.uptime || ""),
      rungs: asObject(src.rungs),
      orders: asArray(src.orders),
      dispatches: asArray(src.dispatches),
      claims: asArray(src.claims),
      waves: asArray(src.waves),
      closed: asArray(src.closed),
      events: asArray(src.events),
      watchdog: asArray(src.watchdog)
    };
  }

  /* One row per rung, health-colored from its heartbeat state. */
  function rungRows(frame) {
    var names = Object.keys(asObject(frame.rungs)).sort();
    return names.map(function (name) {
      var info = asObject(asObject(frame.rungs)[name]);
      var state = String(info.state || "unknown");
      return {
        kind: "rung",
        name: name,
        state: state,
        tone: toneFor(state),
        detail: info.commit ? "commit " + info.commit
          : (info.beat_age === null || info.beat_age === undefined
            ? "no heartbeat" : "beat " + info.beat_age + "s")
      };
    });
  }

  /* Board / focus / pool: waves (plans) and claims, one row each. */
  function boardRows(frame) {
    var rows = [];
    var seen = {};
    function push(kind, key, state) {
      var token = issueToken(key);
      if (!token || seen[kind + token]) return;
      seen[kind + token] = true;
      rows.push({ kind: kind, key: token, state: state });
    }
    asArray(frame.waves).forEach(function (plan) {
      var wave = asObject(plan);
      asArray(wave.children).forEach(function (issue) {
        push("wave", issue, "pending");
      });
      asArray(wave.dispatched).forEach(function (issue) {
        push("wave", issue, "dispatched");
      });
      if (wave.parent !== undefined && wave.parent !== null &&
          !asArray(wave.children).length && !asArray(wave.dispatched).length) {
        push("wave", wave.parent, "planned");
      }
    });
    asArray(frame.claims).forEach(function (claim) {
      push("claim", claim, "claimed");
    });
    return rows;
  }

  /* Directives in flight: the dispatch messages. */
  function dispatchRows(frame) {
    return asArray(frame.dispatches).map(function (message) {
      var msg = asObject(message);
      var task = asObject(msg.task);
      var lane = (task.issue !== undefined && task.issue !== null)
        ? task.issue : msg.correlation_id;
      return {
        from: String(msg.from || ""),
        to: String(msg.to || ""),
        type: String(msg.type || "dispatched"),
        lane: issueToken(lane)
      };
    });
  }

  /* The closed vocabulary, normalised to the shape the steer panel renders. */
  function steerRows(raw) {
    var doc = asObject(raw);
    var verbs = asArray(doc.verbs);
    var classes = asObject(doc.effectClasses);
    return verbs.map(function (entry) {
      var verb = asObject(entry);
      return {
        id: String(verb.id || ""),
        family: String(verb.family || ""),
        action: String(verb.action || ""),
        effectClass: String(verb.effectClass || "read"),
        effectLabel: String(classes[verb.effectClass] || verb.effectClass || ""),
        capability: String(verb.capability || ""),
        auditAction: verb.auditAction === null || verb.auditAction === undefined
          ? "" : String(verb.auditAction),
        idempotent: Boolean(verb.idempotent),
        exposed: Boolean(verb.exposed),
        whyNotExposed: verb.whyNotExposed ? String(verb.whyNotExposed) : ""
      };
    });
  }

  function effectTone(effectClass) {
    if (effectClass === "irreversible") return "err";
    if (effectClass === "stop") return "warn";
    if (effectClass === "hold") return "info";
    return "ok";
  }

  /* Split an args string into the lever's argv tokens (whitespace, shell-free). */
  function parseArgs(text) {
    return String(text || "").trim().split(/\s+/).filter(function (token) {
      return token.length > 0;
    });
  }

  var model = {
    normalizeSnapshot: normalizeSnapshot,
    rungRows: rungRows,
    boardRows: boardRows,
    dispatchRows: dispatchRows,
    steerRows: steerRows,
    effectTone: effectTone,
    parseArgs: parseArgs
  };

  /* -------------------------------------------------------------- rendering */

  var state = { snapshot: null, verbs: null, error: "" };

  function renderFleetStatus(frame) {
    var set = function (id, value) {
      var node = document.getElementById(id);
      if (node) node.textContent = value;
    };
    set("statusRepo", frame.repo || "-");
    set("statusHead", frame.head || "-");
    set("statusUptime", frame.uptime || "up ?");
    set("statusNow", frame.now || "-");
  }

  function renderRows(tableId, rows, columns) {
    var body = document.getElementById(tableId);
    if (!body) return;
    clear(body);
    rows.forEach(function (row) {
      var tr = document.createElement("tr");
      columns.forEach(function (column) {
        var td = document.createElement("td");
        if (column === "state") {
          td.appendChild(el("span", { class: "badge " + row.tone, text: row.state }));
        } else if (column === "kind") {
          td.appendChild(el("span", { class: "badge info", text: row.kind }));
        } else {
          td.textContent = row[column] == null ? "" : String(row[column]);
        }
        tr.appendChild(td);
      });
      body.appendChild(tr);
    });
  }

  function renderVerbs(rows) {
    var body = document.getElementById("verbsBody");
    var stateNode = document.getElementById("steerState");
    if (!body) return;
    clear(body);
    var families = {};
    rows.forEach(function (row) {
      (families[row.family] = families[row.family] || []).push(row);
    });
    Object.keys(families).sort().forEach(function (family) {
      body.appendChild(el("div", { class: "hint", text: family }));
      var table = el("table", { class: "tbl" });
      var thead = el("thead");
      var headRow = el("tr");
      ["verb", "effect class", "capability", "audit", "action"].forEach(function (label) {
        headRow.appendChild(el("th", { text: label }));
      });
      thead.appendChild(headRow);
      table.appendChild(thead);
      var tbody = el("tbody");
      families[family].forEach(function (row) {
        tbody.appendChild(verbRow(row));
      });
      table.appendChild(tbody);
      body.appendChild(table);
    });
    if (stateNode) stateNode.textContent = rows.length + " declared verb(s)";
  }

  function verbRow(row) {
    var tr = el("tr");
    tr.appendChild(el("td", { class: "mono", text: row.id }));
    tr.appendChild(el("td", {}, el("span", {
      class: "badge " + effectTone(row.effectClass),
      text: row.effectClass
    })));
    tr.appendChild(el("td", { class: "mono", text: row.capability }));
    tr.appendChild(el("td", { class: "mono", text: row.auditAction || "—" }));
    var actionTd = el("td");
    if (!row.exposed) {
      actionTd.appendChild(el("span", {
        class: "hint",
        text: "withheld: " + (row.whyNotExposed || "not exposed")
      }));
    } else {
      var input = el("input", {
        class: "mono",
        style: "width:140px;margin-right:var(--os-space-2)",
        placeholder: "args (space-separated)"
      });
      var button = el("button", {
        class: "btn ghost small",
        text: "steer",
        on: function () { steer(row, input); }
      });
      actionTd.appendChild(input);
      actionTd.appendChild(button);
    }
    tr.appendChild(actionTd);
    return tr;
  }

  function steer(row, input) {
    var args = parseArgs(input.value);
    CP.post(CONTROL_ROOT + row.family + "/" + row.action, {
      commandId: "ot_" + Date.now().toString(36) + Math.random().toString(36).slice(2, 8),
      args: args
    }).then(function (payload) {
      var data = asObject(payload.data);
      showReceipt(row.id, data.exitCode === 0 ? "applied" : "refused", data.output || "");
    }).catch(function (err) {
      showReceipt(row.id, "refused", err.message);
    });
  }

  function showReceipt(verb, verdict, detail) {
    var body = document.getElementById("verbsBody");
    if (!body) return;
    var tone = verdict === "applied" ? "ok" : "err";
    body.insertBefore(
      el("div", { class: "card", style: "margin-top:var(--os-space-2)" },
        el("div", { class: "spread" },
          el("span", { class: "mono", text: verb }),
          el("span", { class: "badge " + tone, text: verdict })
        ),
        el("div", { class: "hint", text: detail || "(no output)" })
      ),
      body.firstChild
    );
  }

  function disabled(section, flag) {
    return el("div", { class: "card" },
      el("div", { class: "spread" },
        el("span", { class: "badge warn", text: "feature disabled" }),
        el("span", { class: "hint mono", text: flag })
      ),
      el("div", { class: "hint", text: section + " is feature-flag-gated OFF. " +
        "Promote it before this panel can render live data." })
    );
  }

  function setProjectionState(stateName) {
    var node = document.getElementById("projectionState");
    if (!node) return;
    node.setAttribute("data-state", stateName);
    node.className = "badge " + (stateName === "ok" ? "ok" : (stateName === "disabled" ? "warn" : "info"));
    node.textContent = stateName;
  }

  async function load() {
    /* read half: the fleet projection */
    try {
      var snapshot = await CP.get(SNAPSHOT_PATH);
      var frame = normalizeSnapshot(snapshot.data);
      state.snapshot = frame;
      renderFleetStatus(frame);
      renderRows("rungsBody", rungRows(frame), ["name", "state", "detail"]);
      renderRows("boardBody", boardRows(frame), ["kind", "key", "state"]);
      renderRows("dispatchesBody", dispatchRows(frame), ["from", "to", "type", "lane"]);
      setProjectionState("ok");
    } catch (err) {
      if (err.code === "feature_disabled") {
        var app = document.getElementById("app");
        if (app) {
          clear(app);
          app.appendChild(disabled("The fleet projection", "surfaces.fleet_projection"));
        }
        setProjectionState("disabled");
      } else {
        state.error = err.message;
        setProjectionState("error");
      }
    }
    /* steer half: the closed vocabulary */
    try {
      var verbs = await CP.post(VERBS_PATH, {});
      var rows = steerRows(asObject(asObject(verbs.data).content));
      state.verbs = rows;
      renderVerbs(rows);
    } catch (err) {
      var steer = document.getElementById("steerState");
      if (err.code === "feature_disabled") {
        if (steer) steer.textContent = "";
        var body = document.getElementById("verbsBody");
        if (body) {
          clear(body);
          body.appendChild(disabled("The remote control family", "surfaces.remote_control"));
        }
      } else if (steer) {
        steer.textContent = "cannot load the vocabulary: " + err.message;
      }
    }
  }

  document.addEventListener("DOMContentLoaded", load);

  /* The pure model, exposed for the offline gate and the test suite. */
  window.OT = model;
})();
