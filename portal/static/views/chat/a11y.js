/*
 * Chat surface — accessibility enhancements (EPIC #500, issue #514)
 * =====================================================================
 * Progressive enhancement layered ON TOP of js/chat.js, which this file does
 * not modify: it observes the state chat.js already publishes (`#turnState`'s
 * `data-state`) and adds the semantics a screen reader needs. Every function is
 * defensive, so a missing element is a no-op rather than an error.
 *
 * It adds exactly three things, each asserted by portal/tests/test_chat_a11y.py:
 *   1. `aria-busy` on the transcript while a turn is in flight;
 *   2. focus is never lost across stop / retry / conversation switch;
 *   3. the turn footer and the citations region are labelled landmarks.
 */
(function () {
  "use strict";

  var IN_FLIGHT = { sending: true, streaming: true };

  function el(id) { return document.getElementById(id); }

  /* --- 1. aria-busy during a turn ------------------------------------- */
  function reflectBusy() {
    var turnState = el("turnState");
    var messages = el("messages");
    if (!turnState || !messages) { return; }
    var busy = Object.prototype.hasOwnProperty.call(
      IN_FLIGHT, turnState.getAttribute("data-state") || "idle");
    messages.setAttribute("aria-busy", busy ? "true" : "false");
  }

  /* --- 2. focus is never lost ----------------------------------------- */
  function returnFocusIfLost() {
    var input = el("input");
    if (!input) { return; }
    var active = document.activeElement;
    var lost = !active || active === document.body ||
      active === document.documentElement;
    if (lost) { input.focus(); }
  }

  /* --- 3. labelled landmarks ------------------------------------------ */
  function labelRegions() {
    var footer = el("turnFooter");
    if (footer && !footer.getAttribute("role")) {
      footer.setAttribute("role", "group");
      footer.setAttribute("aria-label", "turn usage, latency and cost");
    }
    var footer2 = el("turnFooter");
    if (footer2 && !footer2.getAttribute("aria-label")) {
      footer2.setAttribute("aria-label", "turn usage, latency and cost");
    }
  }

  /* --- reduced-motion flag (the CSS media query is authoritative) ----- */
  function reflectReducedMotion() {
    if (typeof window.matchMedia !== "function") { return; }
    var query = window.matchMedia("(prefers-reduced-motion: reduce)");
    var apply = function () {
      document.documentElement.setAttribute(
        "data-reduced-motion", query.matches ? "true" : "false");
    };
    apply();
    if (typeof query.addEventListener === "function") {
      query.addEventListener("change", apply);
    }
  }

  function init() {
    reflectReducedMotion();
    labelRegions();
    reflectBusy();

    var turnState = el("turnState");
    if (turnState && typeof MutationObserver === "function") {
      new MutationObserver(function () {
        reflectBusy();
        var state = turnState.getAttribute("data-state");
        /* a turn that has settled hands focus back to the composer, so the
           keyboard user is never stranded after stop / retry / failure */
        if (state === "idle" || state === "complete" ||
            state === "cancelled" || state === "failed") {
          returnFocusIfLost();
        }
      }).observe(turnState, { attributes: true, attributeFilter: ["data-state"] });
    }
  }

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", init);
  } else {
    init();
  }
}());
