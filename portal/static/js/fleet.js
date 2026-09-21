/*
 * ---knowledge---
 * module_id: portal.static.js.fleet
 * system: portal
 * app: static
 * solution_class: pattern
 * patterns: [client-adapter, no-own-authority]
 * derives_from: null
 * owner_sme: frontend-sme
 * tier: L1
 * interfaces: []
 * invariants: ""
 * gotchas: ""
 * related: ["#332"]
 * do_not_duplicate: null
 * ---knowledge---
 */
/* agent-orchestrator console — fleet single-pane-of-glass (issue #332)
 *
 * The browser half of the web single-pane-of-glass. The server half (issue
 * #331) already projects `fleet/console.py snapshot()` over HTTP:
 *   GET /api/fleet/snapshot          one projection
 *   GET /api/fleet/stream            SSE, one `snapshot` frame per push
 *   GET /api/fleet/events?limit=N    the parsed slog.jsonl history
 * This module re-implements none of it. It *renders* that projection into a
 * real DOM and adds the three things a terminal pane cannot do:
 *
 *   1. a live frame per push — each SSE frame mutates the DOM in place; the
 *      document is never re-navigated (no reload, no iframe swap);
 *   2. filters — tenant / lane / rung / severity narrow the visible rows;
 *   3. a scrollable timeline of the event history, and the per-org roll-up
 *      keyed to the #151 tenant hierarchy (assets/fleet-hierarchy.json).
 *
 * Faithful to the TUI: the same sections (rungs + health coloring, waves and
 * lanes, dispatches, claims, events, watchdog) and the same source projection,
 * so the two surfaces can never disagree about fleet state.
 *
 * Deliberately dependency-free and DOM-framework-agnostic (the console ships
 * offline, with no npm and no CDN): the model functions are pure and the
 * render functions only ever touch the DOM they are handed, which is what the
 * browser test in portal/tests/test_fleet_dashboard_dom.py drives.
 */
(function () {
  "use strict";

  /* The TUI renders `console.events_snapshot()`'s default window: 8 records.
   * The timeline exists because a terminal cannot scroll history, so it keeps
   * a bounded buffer of the last 200 records from /api/fleet/events. */
  var TUI_EVENT_WINDOW = 8;
  var TIMELINE_LIMIT = 200;

  var HIERARCHY_PATH = "/assets/fleet-hierarchy.json";
  var SNAPSHOT_PATH = "/api/fleet/snapshot";
  var EVENTS_PATH = "/api/fleet/events?limit=" + TIMELINE_LIMIT;
  var STREAM_PATH = "/api/fleet/stream";
  var TENANTS_PATH = "/api/tenants";
  var LOADS_KEY = "fleet-page-loads";

  /* The rung -> lane ownership rule: a projection section belongs to exactly
   * one rung of the fleet hierarchy (brain dispatches and plans waves, the
   * sister holds and works the claims, the monitor watches). A lane row's
   * `rung` facet is read from that mapping, never invented per row. */
  var SECTION_RUNG = {
    waves: "brain",
    dispatches: "brain",
    claims: "sister",
    watchdog: "monitor"
  };

  var state = {
    frame: null,
    hierarchy: null,
    tenants: [],
    history: [],
    filters: { tenant: "", lane: "", rung: "", severity: "" },
    frames: 0,   /* live frames applied from the push channel */
    pushes: 0,   /* stream events received */
    renders: 0,
    loads: 0,    /* page loads, counted in sessionStorage (the no-reload proof) */
    stream: "connecting",
    error: ""
  };

  /* ---------------------------------------------------------------- helpers */

  function asArray(value) {
    return Array.isArray(value) ? value : [];
  }

  function asObject(value) {
    return value && typeof value === "object" && !Array.isArray(value) ? value : {};
  }

  function num(value) {
    var parsed = Number(value);
    return isFinite(parsed) ? parsed : 0;
  }

  function money(value) {
    return "$" + num(value).toLocaleString("en-US", {
      minimumFractionDigits: 2, maximumFractionDigits: 2
    });
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

  /* The issue token a lane row is keyed by: "#232" from "#232"/"232"/
   * "issue #232 held by …". Empty when the text carries no issue number. */
  function issueToken(value) {
    var match = String(value === null || value === undefined ? "" : value)
      .match(/#?\s*(\d{1,6})\b/);
    return match ? "#" + match[1] : "";
  }

  /* Health tone -> the console's own badge vocabulary (ok/warn/err). */
  function toneFor(rungState) {
    var value = String(rungState || "").toLowerCase();
    if (value === "working" || value === "active" || value === "idle") return "ok";
    if (value === "down" || value === "failed" || value === "error") return "err";
    if (value === "no-heartbeat" || value === "unknown" || value === "stale") return "warn";
    return "info";
  }

  function severityFor(rungState) {
    var tone = toneFor(rungState);
    if (tone === "err") return "err";
    if (tone === "warn") return "warn";
    return "info";
  }

  /* ------------------------------------------------------------ pure model */

  function normalizeFrame(raw) {
    var src = asObject(raw);
    return {
      present: Object.keys(src).length > 0,
      repo: String(src.repo || ""),
      head: String(src.head || ""),
      now: String(src.now || ""),
      uptime: String(src.uptime || ""),
      rungs: asObject(src.rungs),
      orders: asObject(src.orders),
      dispatches: asArray(src.dispatches),
      claims: asArray(src.claims),
      waves: asArray(src.waves),
      closed: asArray(src.closed),
      events: asArray(src.events),
      watchdog: asArray(src.watchdog)
    };
  }

  /* The tenant that owns a repo, per the #151 hierarchy. */
  function findTenantForRepo(hierarchy, repo) {
    var doc = asObject(hierarchy);
    var orgs = asArray(doc.orgs);
    for (var i = 0; i < orgs.length; i += 1) {
      var tenants = asArray(asObject(orgs[i]).tenants);
      for (var j = 0; j < tenants.length; j += 1) {
        if (asArray(asObject(tenants[j]).repos).indexOf(repo) !== -1) {
          return {
            orgId: String(asObject(orgs[i]).orgId || ""),
            tenantId: String(asObject(tenants[j]).tenantId || "")
          };
        }
      }
    }
    return { orgId: "", tenantId: "" };
  }

  function rowId(kind, key) {
    return kind + ":" + key;
  }

  /* Wave plans key lanes: the parent issue plus every child and every
   * dispatched issue, lowest parent first (the console's own ordering). */
  function laneRows(frame, ctx) {
    var rows = [];
    var seen = {};

    function push(lane, rung, state_, detail, severity) {
      var token = issueToken(lane);
      if (!token || seen[rung + token]) return;
      seen[rung + token] = true;
      rows.push({
        id: rowId("lane", rung + token),
        kind: "lane",
        tenant: ctx.tenant,
        org: ctx.org,
        repo: ctx.repo,
        lane: token,
        rung: rung,
        state: state_,
        severity: severity || severityFor(state_),
        detail: detail
      });
    }

    frame.waves.forEach(function (plan) {
      var wave = asObject(plan);
      asArray(wave.children).forEach(function (issue) {
        push(issue, SECTION_RUNG.waves, "pending", "wave " + issueToken(wave.parent));
      });
      asArray(wave.dispatched).forEach(function (issue) {
        push(issue, SECTION_RUNG.waves, "dispatched", "wave " + issueToken(wave.parent));
      });
      if (wave.parent !== undefined && wave.parent !== null && !asArray(wave.children).length
          && !asArray(wave.dispatched).length) {
        push(wave.parent, SECTION_RUNG.waves, "planned", "wave plan");
      }
    });

    frame.claims.forEach(function (claim) {
      push(claim, SECTION_RUNG.claims, "claimed", String(claim));
    });

    frame.dispatches.forEach(function (message) {
      var msg = asObject(message);
      var task = asObject(msg.task);
      var lane = (task.issue !== undefined && task.issue !== null) ? task.issue : msg.correlation_id;
      push(lane, SECTION_RUNG.dispatches, String(msg.type || "dispatched"),
        String(msg.from || "") + " -> " + String(msg.to || ""));
    });

    frame.watchdog.forEach(function (line) {
      push(line, SECTION_RUNG.watchdog, "watch", String(line), "warn");
    });

    return rows;
  }

  /* One row per rung, health-colored from its heartbeat state. */
  function rungRows(frame, ctx) {
    var names = Object.keys(frame.rungs).sort();
    return names.map(function (name) {
      var info = asObject(frame.rungs[name]);
      var rungState = String(info.state || "unknown");
      return {
        id: rowId("rung", name),
        kind: "rung",
        tenant: ctx.tenant,
        org: ctx.org,
        repo: ctx.repo,
        lane: "",
        rung: name,
        state: rungState,
        severity: severityFor(rungState),
        detail: info.commit ? "commit " + info.commit
          : (info.beat_age === null || info.beat_age === undefined
            ? "no heartbeat" : "beat " + info.beat_age + "s"),
        beat_age: info.beat_age,
        pid: info.pid
      };
    });
  }

  function matches(row, filters) {
    filters = asObject(filters);
    if (filters.tenant && row.tenant !== filters.tenant) return false;
    if (filters.lane && row.lane !== filters.lane) return false;
    if (filters.rung && row.rung !== filters.rung) return false;
    if (filters.severity && row.severity !== filters.severity) return false;
    return true;
  }

  function applyFilters(rows, filters) {
    return asArray(rows).filter(function (row) { return matches(row, filters); });
  }

  /* Select options, derived from the unfiltered row set so a choice never
   * disappears out from under the operator. The tenant facet also carries the
   * tenants the #151 hierarchy declares: the enterprise view may be asked for
   * a tenant whose repo this portal instance does not serve (an honest zero
   * rows), which is a different question from "that option does not exist". */
  function filterOptions(rows, tenantIds) {
    var options = { tenant: [], lane: [], rung: [], severity: [] };
    asArray(rows).forEach(function (row) {
      ["tenant", "lane", "rung", "severity"].forEach(function (facet) {
        var value = row[facet];
        if (value && options[facet].indexOf(value) === -1) options[facet].push(value);
      });
    });
    asArray(tenantIds).forEach(function (tenantId) {
      if (tenantId && options.tenant.indexOf(tenantId) === -1) options.tenant.push(tenantId);
    });
    Object.keys(options).forEach(function (facet) { options[facet].sort(); });
    return options;
  }

  /* Every tenant the #151 hierarchy declares, org by org. */
  function declaredTenants(hierarchy) {
    var tenants = [];
    asArray(asObject(hierarchy).orgs).forEach(function (entry) {
      asArray(asObject(entry).tenants).forEach(function (tenant) {
        var tenantId = String(asObject(tenant).tenantId || "");
        if (tenantId && tenants.indexOf(tenantId) === -1) tenants.push(tenantId);
      });
    });
    return tenants;
  }

  /* The #151 roll-up: every declared org, aggregating the tenants that are
   * present in the live tenant projection. A tenant the hierarchy declares but
   * the console does not yet serve contributes nothing but still shows under
   * its org — the hierarchy is the key, the projection is the data. */
  function aggregateOrgs(tenantRows, hierarchy) {
    var byTenant = {};
    asArray(tenantRows).forEach(function (row) {
      var item = asObject(row);
      if (item.tenantId) byTenant[String(item.tenantId)] = item;
    });
    return asArray(asObject(hierarchy).orgs).map(function (entry) {
      var org = asObject(entry);
      var agg = {
        id: rowId("org", String(org.orgId || "")),
        kind: "org",
        org: String(org.orgId || ""),
        name: String(org.name || org.orgId || ""),
        tenants: [],
        repos: [],
        present: 0,
        missing: [],
        agents: 0,
        agentsActive: 0,
        agentsPaused: 0,
        budgetUsd: 0,
        usageUsd: 0,
        pendingApprovals: 0,
        utilizationPct: 0
      };
      asArray(org.tenants).forEach(function (entry_) {
        var declared = asObject(entry_);
        var tenantId = String(declared.tenantId || "");
        if (tenantId) agg.tenants.push(tenantId);
        agg.repos = agg.repos.concat(asArray(declared.repos).map(String));
        var row = byTenant[tenantId];
        if (!row) {
          if (tenantId) agg.missing.push(tenantId);
          return;
        }
        agg.present += 1;
        agg.agents += num(row.agents);
        agg.agentsActive += num(row.agentsActive);
        agg.agentsPaused += num(row.agentsPaused);
        agg.budgetUsd += num(row.budgetUsd);
        agg.usageUsd += num(row.usageUsd);
        agg.pendingApprovals += num(row.pendingApprovals);
      });
      agg.utilizationPct = agg.budgetUsd
        ? Math.round(1000 * agg.usageUsd / agg.budgetUsd) / 10 : 0;
      return agg;
    });
  }

  /* Newest-first event history, deduplicated by content, capped at `limit`.
   * `incoming` arrives in file order (oldest first, as slog.jsonl is served)
   * and `history` is already newest-first, so the round is reversed here. */
  function mergeEvents(history, incoming, limit) {
    var cap = num(limit) > 0 ? num(limit) : TIMELINE_LIMIT;
    var seen = {};
    var merged = [];
    asArray(incoming).slice().reverse().concat(asArray(history)).forEach(function (event) {
      var key;
      try { key = JSON.stringify(event); } catch (err) { key = String(event); }
      if (seen[key]) return;
      seen[key] = true;
      merged.push(event);
    });
    return merged.slice(0, cap);
  }

  function eventLine(event) {
    var item = asObject(event);
    var parts = [];
    if (item.ts) parts.push(String(item.ts));
    if (item.level) parts.push(String(item.level));
    if (item.action || item.event || item.type) {
      parts.push(String(item.action || item.event || item.type));
    }
    if (item.detail || item.message) parts.push(String(item.detail || item.message));
    if (!parts.length) {
      try { return JSON.stringify(item); } catch (err) { return String(item); }
    }
    return parts.join("  ");
  }

  function buildModel(overrides) {
    var given = asObject(overrides);
    var hasFrame = Object.prototype.hasOwnProperty.call(given, "frame");
    /* Always a normalized frame, never null: the page renders once before any
     * data has arrived (and the empty-projection case renders `{}`), so every
     * render function can read the frame without a guard. */
    var frame = hasFrame ? normalizeFrame(given.frame) : (state.frame || normalizeFrame({}));
    var hierarchy = Object.prototype.hasOwnProperty.call(given, "hierarchy")
      ? given.hierarchy : state.hierarchy;
    var tenants = Object.prototype.hasOwnProperty.call(given, "tenants")
      ? asArray(given.tenants) : state.tenants;
    var history = Object.prototype.hasOwnProperty.call(given, "history")
      ? asArray(given.history) : state.history;
    var filters = Object.prototype.hasOwnProperty.call(given, "filters")
      ? asObject(given.filters) : state.filters;

    var repo = (frame && frame.repo) || asObject(hierarchy).defaultRepo || "";
    var owner = findTenantForRepo(hierarchy, repo);
    var ctx = { tenant: owner.tenantId, org: owner.orgId, repo: repo };
    var rows = frame ? laneRows(frame, ctx).concat(rungRows(frame, ctx)) : [];

    return {
      frame: frame,
      ctx: ctx,
      rows: rows,
      visible: applyFilters(rows, filters),
      options: filterOptions(rows, declaredTenants(hierarchy)),
      filters: filters,
      orgs: aggregateOrgs(tenants, hierarchy),
      tenants: tenants,
      history: history,
      state: state,
      tuiEventWindow: TUI_EVENT_WINDOW,
      empty: {
        frame: !frame || !frame.present,
        tenants: !tenants.length,
        hierarchy: !asArray(asObject(hierarchy).orgs).length,
        history: !history.length
      }
    };
  }

  /* --------------------------------------------------------------- render */

  function placeholder(doc, scope, label) {
    return el("div", { class: "empty", "data-empty": scope, text: label });
  }

  function badge(text, tone) {
    var klass = tone === "ok" ? "badge ok"
      : tone === "err" ? "badge err"
        : tone === "warn" ? "badge warn" : "badge info";
    return el("span", { class: klass, text: text });
  }

  function panel(doc, id) {
    return doc.getElementById(id);
  }

  function renderStatus(doc, model) {
    var repo = panel(doc, "statusRepo");
    if (repo) repo.textContent = model.frame.repo || "(no projection)";
    var head = panel(doc, "statusHead");
    if (head) head.textContent = model.frame.head || "-";
    var uptime = panel(doc, "statusUptime");
    if (uptime) uptime.textContent = model.frame.uptime || "up ?";
    var frames = panel(doc, "statusFrames");
    if (frames) {
      frames.setAttribute("data-frames", String(state.frames));
      frames.textContent = String(state.frames);
    }
    var loads = panel(doc, "statusLoads");
    if (loads) {
      loads.setAttribute("data-loads", String(state.loads));
      loads.textContent = String(state.loads);
    }
    var stream = panel(doc, "statusStream");
    if (stream) {
      stream.setAttribute("data-stream", state.stream);
      stream.textContent = state.stream === "live" ? "live"
        : state.stream === "error" ? "stream error" : state.stream;
    }
    if (model.frame.now) {
      var now = panel(doc, "statusNow");
      if (now) now.textContent = model.frame.now;
    }
  }

  function renderFilters(doc, model) {
    var facets = ["tenant", "lane", "rung", "severity"];
    facets.forEach(function (facet) {
      var select = panel(doc, "filter" + facet.charAt(0).toUpperCase() + facet.slice(1));
      if (!select) return;
      var current = model.filters[facet] || "";
      var options = model.options[facet] || [];
      if (options.indexOf(current) === -1) current = "";
      clear(select);
      select.appendChild(el("option", { value: "", text: "all " + facet + "s" }));
      options.forEach(function (value) {
        select.appendChild(el("option", { value: value, text: value }));
      });
      select.value = current;
    });
    var summary = panel(doc, "filterSummary");
    if (summary) {
      var total = model.rows.length;
      var visible = model.visible.length;
      summary.textContent = visible === total
        ? total + " row(s)"
        : visible + " of " + total + " row(s)";
      summary.setAttribute("data-visible", String(visible));
      summary.setAttribute("data-total", String(total));
    }
  }

  function renderRungs(doc, model) {
    var body = panel(doc, "rungsBody");
    if (!body) return;
    clear(body);
    var rows = model.rows.filter(function (row) { return row.kind === "rung"; });
    if (!rows.length) {
      body.appendChild(el("tr", {}, [el("td", { colspan: "4" }, [
        placeholder(doc, "rungs", "no rung heartbeats in the projection")])]));
      return;
    }
    rows.forEach(function (row) {
      var hidden = !matches(row, model.filters);
      var tr = el("tr", {
        "data-row": row.id,
        "data-kind": row.kind,
        "data-rung": row.rung,
        "data-tenant": row.tenant,
        "data-lane": row.lane,
        "data-state": row.state,
        hidden: hidden ? true : null
      }, [
        el("td", { class: "mono" }, [row.rung]),
        el("td", {}, [badge(row.state, toneFor(row.state))]),
        el("td", { class: "mono dim" }, [row.detail]),
        el("td", { class: "mono dim" }, [row.pid === null || row.pid === undefined
          ? "no pid" : "pid " + row.pid])
      ]);
      body.appendChild(tr);
    });
  }

  function renderLanes(doc, model) {
    var body = panel(doc, "lanesBody");
    if (!body) return;
    clear(body);
    var rows = model.rows.filter(function (row) { return row.kind === "lane"; });
    if (!rows.length) {
      body.appendChild(el("tr", {}, [el("td", { colspan: "5" }, [
        placeholder(doc, "lanes", "no lanes in the projection")])]));
      return;
    }
    rows.forEach(function (row) {
      var hidden = !matches(row, model.filters);
      body.appendChild(el("tr", {
        "data-row": row.id,
        "data-kind": row.kind,
        "data-tenant": row.tenant,
        "data-lane": row.lane,
        "data-rung": row.rung,
        "data-state": row.state,
        "data-severity": row.severity,
        hidden: hidden ? true : null
      }, [
        el("td", { class: "mono" }, [row.lane]),
        el("td", {}, [row.rung]),
        el("td", {}, [badge(row.severity, row.severity === "err" ? "err"
          : row.severity === "warn" ? "warn" : "info")]),
        el("td", { class: "mono dim" }, [row.tenant || "-"]),
        el("td", { class: "dim" }, [row.detail || ""])
      ]));
    });
  }

  function renderRollup(doc, model) {
    var body = panel(doc, "rollupBody");
    if (!body) return;
    clear(body);
    if (model.empty.hierarchy) {
      body.appendChild(el("tr", {}, [el("td", { colspan: "6" }, [
        placeholder(doc, "rollup", "the #151 tenant hierarchy could not be loaded")])]));
      return;
    }
    if (!model.orgs.length) {
      body.appendChild(el("tr", {}, [el("td", { colspan: "6" }, [
        placeholder(doc, "rollup", "no orgs declared in the tenant hierarchy")])]));
      return;
    }
    model.orgs.forEach(function (org) {
      body.appendChild(el("tr", {
        "data-row": org.id,
        "data-kind": "org",
        "data-org": org.org,
        "data-tenants": org.tenants.join(","),
        "data-present": String(org.present),
        "data-repos": org.repos.join(","),
        "data-agents": String(org.agents),
        "data-agents-active": String(org.agentsActive),
        "data-spend-usd": String(org.usageUsd),
        "data-budget-usd": String(org.budgetUsd),
        "data-utilization-pct": String(org.utilizationPct),
        "data-pending-approvals": String(org.pendingApprovals),
        "data-missing-tenants": org.missing.join(",")
      }, [
        el("td", {}, [el("div", {}, [org.name]), el("div", { class: "mono dim" }, [org.org])]),
        el("td", { class: "mono" }, [org.tenants.join(", ") || "-"]),
        el("td", { class: "mono dim" }, [String(org.repos.length)]),
        el("td", { class: "mono" }, [String(org.agents) + " (" + String(org.agentsActive) + " active)"]),
        el("td", { class: "mono" }, [
          money(org.usageUsd) + " / " + money(org.budgetUsd),
          el("div", { class: "dim" }, [String(org.utilizationPct) + "% utilized"])
        ]),
        el("td", { class: "mono dim" }, [
          String(org.pendingApprovals) + " pending",
          org.missing.length ? el("div", { "data-missing": org.missing.join(",") }, [
            "not served: " + org.missing.join(", ")]) : null
        ])
      ]));
    });
  }

  function renderTimeline(doc, model) {
    var list = panel(doc, "timeline");
    if (!list) return;
    clear(list);
    if (!model.history.length) {
      list.appendChild(placeholder(doc, "timeline", "no events in slog.jsonl yet"));
    } else {
      model.history.forEach(function (event) {
        list.appendChild(el("div", {
          class: "feed-item",
          "data-row": "event",
          "data-kind": "event"
        }, [
          el("span", { class: "mono dim", text: eventLine(event) })
        ]));
      });
    }
    var count = panel(doc, "timelineCount");
    if (count) {
      count.setAttribute("data-count", String(model.history.length));
      count.setAttribute("data-tui-window", String(TUI_EVENT_WINDOW));
      count.textContent = model.history.length + " event(s) in history (TUI window: "
        + TUI_EVENT_WINDOW + ")";
    }
  }

  function renderWatchdog(doc, model) {
    var list = panel(doc, "watchdogList");
    if (!list) return;
    clear(list);
    if (!model.frame.watchdog.length) {
      list.appendChild(placeholder(doc, "watchdog", "no watchdog log"));
      return;
    }
    model.frame.watchdog.forEach(function (line) {
      list.appendChild(el("div", { class: "feed-item", "data-row": "watchdog", "data-kind": "watchdog" }, [
        el("span", { class: "mono dim", text: String(line) })
      ]));
    });
  }

  function render(doc, model) {
    var target = doc || document;
    var built = model === undefined ? buildModel() : model;
    renderStatus(target, built);
    renderFilters(target, built);
    renderRungs(target, built);
    renderLanes(target, built);
    renderRollup(target, built);
    renderTimeline(target, built);
    renderWatchdog(target, built);
    state.renders += 1;
    return built;
  }

  /* ---------------------------------------------------------------- wiring */

  function setFilters(patch) {
    Object.keys(asObject(patch)).forEach(function (key) {
      state.filters[key] = String(patch[key]);
    });
    render(document, buildModel());
  }

  function applyFrame(raw) {
    state.frame = normalizeFrame(raw);
    state.history = mergeEvents(state.history, state.frame.events, TIMELINE_LIMIT);
    render(document, buildModel());
  }

  function onFrame(raw) {
    state.pushes += 1;
    state.frames += 1;
    applyFrame(raw);
  }

  function getJSON(path) {
    return fetch(path, { headers: { Accept: "application/json" } })
      .then(function (response) {
        return response.json().catch(function () { return null; })
          .then(function (payload) {
            if (!response.ok) {
              throw new Error("HTTP " + response.status);
            }
            return payload && payload.data !== undefined ? payload.data : payload;
          });
      });
  }

  /* Each source is fetched independently and each failure is contained: a
   * missing section renders its own placeholder and the page keeps working
   * (the projection, the tenants and the hierarchy are three separate reads). */
  function loadSections() {
    var loads = [
      getJSON(HIERARCHY_PATH).then(function (data) { state.hierarchy = data; })
        .catch(function () { state.hierarchy = state.hierarchy || null; }),
      getJSON(TENANTS_PATH).then(function (data) {
        state.tenants = asArray(asObject(data).tenants);
      }).catch(function () { state.tenants = state.tenants || []; }),
      getJSON(EVENTS_PATH).then(function (data) {
        state.history = mergeEvents(state.history, asArray(data), TIMELINE_LIMIT);
      }).catch(function () { /* the stream still carries the 8-event window */ }),
      getJSON(SNAPSHOT_PATH).then(function (data) {
        state.frame = normalizeFrame(data);
      }).catch(function () { /* the stream's first frame paints anyway */ })
    ];
    return Promise.all(loads).then(function () {
      render(document, buildModel());
    });
  }

  function connect() {
    if (typeof EventSource === "undefined") {
      state.stream = "unsupported";
      render(document, buildModel());
      return null;
    }
    var source = new EventSource(STREAM_PATH);
    source.addEventListener("snapshot", function (event) {
      var payload = null;
      try { payload = JSON.parse(event.data); } catch (err) { payload = null; }
      if (!payload) return;
      state.stream = "live";
      state.error = "";
      onFrame(payload);
    });
    source.onopen = function () {
      state.stream = "live";
      render(document, buildModel());
    };
    source.onerror = function () {
      state.stream = "error";
      state.error = "the push channel dropped; the last frame stays on screen";
      render(document, buildModel());
    };
    return source;
  }

  function wireFilters() {
    [["filterTenant", "tenant"], ["filterLane", "lane"],
      ["filterRung", "rung"], ["filterSeverity", "severity"]].forEach(function (pair) {
      var select = panel(document, pair[0]);
      if (!select) return;
      select.addEventListener("change", function () {
        var patch = {};
        patch[pair[1]] = select.value;
        setFilters(patch);
      });
    });
    var reset = panel(document, "filterReset");
    if (reset) {
      reset.addEventListener("click", function () {
        state.filters = { tenant: "", lane: "", rung: "", severity: "" };
        render(document, buildModel());
      });
    }
  }

  function boot() {
    try {
      state.loads = num(sessionStorage.getItem(LOADS_KEY)) + 1;
      sessionStorage.setItem(LOADS_KEY, String(state.loads));
    } catch (err) { state.loads = 1; }
    wireFilters();
    render(document, buildModel());
    connect();
    loadSections();
  }

  window.FleetDashboard = {
    TUI_EVENT_WINDOW: TUI_EVENT_WINDOW,
    TIMELINE_LIMIT: TIMELINE_LIMIT,
    state: state,
    /* pure, DOM-free */
    normalizeFrame: normalizeFrame,
    findTenantForRepo: findTenantForRepo,
    laneRows: laneRows,
    rungRows: rungRows,
    matches: matches,
    applyFilters: applyFilters,
    filterOptions: filterOptions,
    declaredTenants: declaredTenants,
    aggregateOrgs: aggregateOrgs,
    mergeEvents: mergeEvents,
    buildModel: buildModel,
    issueToken: issueToken,
    /* DOM */
    render: render,
    setFilters: setFilters,
    applyFrame: applyFrame,
    onFrame: onFrame,
    boot: boot,
    connect: connect,
    loadSections: loadSections
  };

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", boot);
  } else {
    boot();
  }
}());
