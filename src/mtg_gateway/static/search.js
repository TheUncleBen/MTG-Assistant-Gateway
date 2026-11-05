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
  if (form.getAttribute("action") === "/search" && typeof fetch === "function" && window.history
      && typeof history.pushState === "function") {
    var seq = 0, inflight = null, asked = location.pathname + location.search;
    function params() {
      var fd = new FormData(form), out = new URLSearchParams();
      fd.forEach(function (v, k) { if (k !== "page" && String(v) !== "") out.append(k, String(v)); });
      return out;
    }
    // the panel's boxes as an address has them (Back and Forward): a box the address does not
    // name goes back to what the page was drawn with
    function setForm(p) {
      Array.prototype.forEach.call(form.elements, function (el) {
        if (!el.name || el.type === "hidden" || el.type === "submit" || el.type === "button") return;
        if (el.closest(".sortcw")) return;  // the phone's combined sort follows the real boxes
        if (el.type === "checkbox") el.checked = p.getAll(el.name).indexOf(el.value) >= 0;
        else if (el.type === "radio") el.checked = p.has(el.name) ? p.get(el.name) === el.value : el.defaultChecked;
        else if (el.tagName === "SELECT") {
          var want = p.get(el.name), at = -1;
          Array.prototype.forEach.call(el.options, function (o, i) {
            if (want !== null ? o.value === want : o.defaultSelected) at = i;
          });
          el.selectedIndex = at >= 0 ? at : 0;
          if (window.MtgSelect) window.MtgSelect.refresh(el);
        } else el.value = p.get(el.name) || "";
      });
      // the drop-downs' own labels and the phone's sort, without sending the form (no bubbling)
      $$("details.pickm, .rbar .g.sort .sortf, .rbar .g.sort .dir", form).forEach(function (el) {
        el.dispatchEvent(new Event("change"));
      });
      if (window.MtgSearchPanel) window.MtgSearchPanel.badge();
    }
    // Every ask is numbered and only the latest may draw: a slow answer landing after a newer
    // one is dropped, and its request cut off where the browser can do that.
    function draw(url, push) {
      var mine = ++seq, results = $("#s-results");
      if (push) asked = url;  // the search the address will name
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
          var P = window.MtgSearchPanel;
          if (push && P && P.isSheetOpen()) P.address(url);
          else if (push) history.pushState({ search: true }, "", url);
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
      // was caught in that back's popstate and dropped). A search the entry already names
      // (a drop-down's) is not asked for again
      var show = e.submitter && e.submitter.classList && e.submitter.classList.contains("sp-show");
      var P = window.MtgSearchPanel;
      if (show && P && P.isSheetOpen()) P.closeSheet(function () { if (url !== asked) draw(url, true); });
      else draw(url, true);
    });
    window.addEventListener("popstate", function () {
      // the panel's own back() over its sheet's entry is not a Back press (audit H1)
      var P = window.MtgSearchPanel;
      if (P && P.backPending && P.backPending()) return;
      asked = location.pathname + location.search;
      setForm(new URLSearchParams(location.search));
      draw(location.pathname + location.search, false);
    });
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
