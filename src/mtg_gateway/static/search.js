/* The deck search page (browse.py), on top of the shared panel (static/searchpanel.js):
   - a filter pick, the sort, the view or Search redraws the results in place, as Card search
     does: the page is asked for the same address and its #s-results and count take the place of
     the ones shown; the address follows (one history entry a search), so Back and Forward bring
     the earlier results and set the panel's boxes to match, and a shared link shows the same.
     When the page cannot be read that way the address is opened as before;
   - "Load more decks" asks /search/api/decks for the next page of the same search and appends its
     tiles under the ones shown; the count follows, and the button says when Archidekt's cap of
     1,000 decks for one search is reached;
   - an Archidekt deck link or deck ID typed in the name box shows "Open this deck" under it (the
     page shows it too when the address already holds one).
   Without this file the panel's Apply button sends the form, and Load more is not offered. */
(function () {
  "use strict";
  var $ = function (sel, root) { return (root || document).querySelector(sel); };
  var $$ = function (sel, root) { return Array.prototype.slice.call((root || document).querySelectorAll(sel)); };
  var form = $("#searchform");
  if (!form) return;

  // -- Open this deck -----------------------------------------------------------------------------
  var nameBox = $("#s-name", form), under = $(".sp-name .under", form);
  var REF = /^\s*(?:(\d{1,12})|(?:https?:\/\/)?(?:www\.)?archidekt\.com\/decks\/(\d{1,12})(?:[/?#].*)?)\s*$/i;
  function deckRef(text) {
    var m = REF.exec(text || "");
    return m ? (m[1] || m[2]) : "";
  }
  var tpl = under ? under.innerHTML : "";  // the hint drawn by the page, when the address held a link
  function hintHtml(id) {
    if (tpl) {
      return tpl.replace(/Archidekt deck \d+/, "Archidekt deck " + id)
        .replace(/ref=\d+/, "ref=" + encodeURIComponent(id));
    }
    return "<div class='openhint'><span>Archidekt deck " + id + "</span>"
      + "<a class='btn btn-primary' href='/decks/open?ref=" + encodeURIComponent(id) + "'>Open this deck</a></div>";
  }
  function showHint() {
    if (!nameBox || !under) return;
    var id = deckRef(nameBox.value);
    if (!id) { under.innerHTML = ""; return; }
    if (under.getAttribute("data-ref") === id) return;
    under.setAttribute("data-ref", id);
    under.innerHTML = hintHtml(id);
  }
  if (nameBox) {
    nameBox.addEventListener("input", showHint);
    // Enter on a link opens the deck instead of searching for its text
    form.addEventListener("submit", function (e) {
      var id = deckRef(nameBox.value);
      if (id && !/^\d+$/.test(nameBox.value.trim())) {
        e.preventDefault();
        location.href = "/decks/open?ref=" + encodeURIComponent(id);
      }
    });
  }

  // -- the results in place -----------------------------------------------------------------------
  var Panel = window.MtgSearchPanel;  // static/searchpanel.js, loaded before this
  if (form.getAttribute("action") === "/search" && typeof fetch === "function" && window.history
      && typeof history.pushState === "function" && Panel && Panel.setForm) {
    var seq = 0, inflight = null, askedQ = null;  // askedQ: the form as the page's results show it
    function params() {
      var fd = new FormData(form), out = new URLSearchParams();
      fd.forEach(function (v, k) { if (k !== "page" && String(v) !== "") out.append(k, String(v)); });
      return out;
    }
    // the panel's boxes as an address has them (Back and Forward): the panel's own setForm
    function setForm(p) { Panel.setForm(p); }
    // Every ask is numbered and only the latest may draw: a slow answer landing after a newer
    // one is dropped, and its request cut off where the browser can do that.
    function draw(url, push) {
      var mine = ++seq, results = $("#s-results");
      if (!results) { location.href = url; return; }
      if (inflight) { try { inflight.abort(); } catch (e) { /* an older browser */ } }
      var ctl = typeof AbortController === "function" ? new AbortController() : null;
      inflight = ctl;
      var opts = { credentials: "same-origin", headers: { Accept: "text/html" } };
      if (ctl) opts.signal = ctl.signal;
      results.setAttribute("aria-busy", "true");
      fetch(url, opts)
        .then(function (r) { return r.ok ? r.text() : Promise.reject(new Error(String(r.status))); })
        .then(function (html) {
          if (mine !== seq) return;
          var doc = new DOMParser().parseFromString(html, "text/html");
          var fresh = $("#s-results", doc), count = $("#s-count", doc), own = $("#s-count");
          if (!fresh) throw new Error("no results");
          results.replaceWith(document.importNode(fresh, true));
          if (count && own) own.textContent = count.textContent;
          // each search is an entry (Back undoes it); a filter picked in the open phone sheet
          // changes the sheet's own entry instead, as the other panels' live filters do, so Back
          // closes the sheet onto this address (audit N1)
          // (audit L5: a search landing while the sheet's own back() is on its way, or in its
          // popstate, goes the same way, onto the entry the back lands on; pushed, it was lost)
          var P = window.MtgSearchPanel;
          if (push && P && (P.isSheetOpen() || P.backPending())) P.address(url);
          else if (push) { history.pushState({ search: true }, "", url); if (P && P.showing) P.showing(); }
          document.dispatchEvent(new CustomEvent("decks:loaded", { bubbles: true }));  // the tiles' ⋮
        })
        .catch(function (err) {
          if (mine !== seq || (err && err.name === "AbortError")) return;
          location.href = url;  // the page itself, as without a script
        });
    }
    form.addEventListener("submit", function (e) {
      if (e.defaultPrevented) return;  // a deck link in the name box opens the deck
      e.preventDefault();
      var url = "/search?" + params().toString();
      // the phone's Filters sheet stays open while drop-downs are picked (each applies at once,
      // on the sheet's own entry); its own Show results button closes it, and a search the
      // boxes and text still owe goes once the sheet's history entry has been given back, so
      // it is its own entry over the page's, as a Search press is (audit H1: sent before, it
      // was caught in that back's popstate and dropped). A form the results already show (a
      // drop-down applied as it was picked, nothing since) is not asked for again (audit L4)
      var show = e.submitter && e.submitter.classList && e.submitter.classList.contains("sp-show");
      var P = window.MtgSearchPanel, q = params().toString(), changed = q !== askedQ;
      askedQ = q;
      if (show && P && P.isSheetOpen()) P.closeSheet(function () { if (changed) draw(url, true); });
      else draw(url, true);
    });
    // Back or Forward onto another search's entry: the form and the results follow the address
    // (the panel calls this; it knows its own back() over the sheet's entry from a Back press,
    // audit H1, and whether the address is one the page already shows)
    function fromAddress() {
      setForm(new URLSearchParams(location.search));
      askedQ = params().toString();
      draw(location.pathname + location.search, false);
    }
    Panel.onAddress(fromAddress);
    askedQ = params().toString();
  }

  // -- Load more decks ----------------------------------------------------------------------------
  // (read from the page at each press: the results may have been redrawn since the page loaded)
  var busy = false;
  function countText(d, shownQuery, shown) {
    // the colour modes the gateway filters itself count the decks kept so far
    if (shownQuery.get("c") && shownQuery.get("cm") !== "atmost") {
      return shown + (shown === 1 ? " deck" : " decks");
    }
    if (typeof d.count !== "number") return "";
    return d.count >= 1000 ? "About " + d.count.toLocaleString() + " decks" : d.count + (d.count === 1 ? " deck" : " decks");
  }
  document.addEventListener("click", function (e) {
    var btn = e.target.closest ? e.target.closest("#s-more [data-more]") : null;
    if (!btn || busy) return;
    var more = $("#s-more"), list = $("ul.decklist"), count = $("#s-count"), note = more && $("p", more);
    if (!more || !list) return;
    // the search whose results are on screen (the page renders it into data-qs; the form may have
    // changed since without being sent), with only the page number changed
    var qs = more.getAttribute("data-qs");
    var shownQuery = new URLSearchParams(qs === null ? location.search : qs);
    var page = parseInt(more.getAttribute("data-page") || "1", 10) + 1;
    var next = new URLSearchParams(shownQuery);
    next.set("page", String(page));
    busy = true;
    btn.disabled = true;
    var saved = btn.textContent;
    btn.textContent = "Loading…";
    fetch("/search/api/decks?" + next.toString(), { credentials: "same-origin", headers: { Accept: "application/json" } })
      .then(function (r) { return r.json(); })
      .then(function (d) {
        if (!d || !d.ok) throw new Error((d && d.error) || "failed");
        list.insertAdjacentHTML("beforeend", d.html || "");
        more.setAttribute("data-page", String(d.page || page));
        var text = countText(d, shownQuery, list.children.length);
        if (count && text) count.textContent = text;
        btn.hidden = !d.has_more;
        if (note) note.hidden = !d.at_cap;
        more.hidden = !d.has_more && !d.at_cap;
        document.dispatchEvent(new CustomEvent("decks:loaded", { bubbles: true }));
      })
      .catch(function () {
        if (note) { note.hidden = false; note.textContent = "The next decks could not be loaded. Try again."; }
      })
      .then(function () { busy = false; btn.disabled = false; btn.textContent = saved; });
  });
})();
