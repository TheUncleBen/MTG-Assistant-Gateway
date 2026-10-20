/* The deck search page (browse.py), on top of the shared panel (static/searchpanel.js):
   - "Load more decks" asks /search/api/decks for the next page of the same search and appends its
     tiles under the ones shown; the count follows, and the button says when Archidekt's cap of
     1,000 decks for one search is reached;
   - an Archidekt deck link or deck ID typed in the name box shows "Open this deck" under it (the
     page shows it too when the address already holds one). */
(function () {
  "use strict";
  var $ = function (sel, root) { return (root || document).querySelector(sel); };
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

  // -- Load more decks ----------------------------------------------------------------------------
  var more = $("#s-more"), list = $("ul.decklist"), count = $("#s-count");
  if (!more || !list) return;
  var btn = $("[data-more]", more), note = $("p", more), shown = list.children.length, busy = false;
  // the search whose results are on screen (the page renders it into data-qs; the form may have
  // changed since without being sent), with only the page number changed
  var qs = more.getAttribute("data-qs");
  var shownQuery = new URLSearchParams(qs === null ? location.search : qs);
  function params(page) {
    var out = new URLSearchParams(shownQuery);
    out.set("page", String(page));
    return out.toString();
  }
  function countText(d) {
    // the colour modes the gateway filters itself count the decks kept so far
    if (shownQuery.get("c") && shownQuery.get("cm") !== "atmost") {
      return shown + (shown === 1 ? " deck" : " decks");
    }
    if (typeof d.count !== "number") return "";
    return d.count >= 1000 ? "About " + d.count.toLocaleString() + " decks" : d.count + (d.count === 1 ? " deck" : " decks");
  }
  if (btn) btn.addEventListener("click", function () {
    if (busy) return;
    var page = parseInt(more.getAttribute("data-page") || "1", 10) + 1;
    busy = true;
    btn.disabled = true;
    var saved = btn.textContent;
    btn.textContent = "Loading…";
    fetch("/search/api/decks?" + params(page), { credentials: "same-origin", headers: { Accept: "application/json" } })
      .then(function (r) { return r.json(); })
      .then(function (d) {
        if (!d || !d.ok) throw new Error((d && d.error) || "failed");
        list.insertAdjacentHTML("beforeend", d.html || "");
        shown += d.shown || 0;
        more.setAttribute("data-page", String(d.page || page));
        var text = countText(d);
        if (count && text) count.textContent = text;
        btn.hidden = !d.has_more;
        if (note) note.hidden = !d.at_cap;
        more.hidden = !d.has_more && !d.at_cap;
      })
      .catch(function () {
        if (note) { note.hidden = false; note.textContent = "The next decks could not be loaded. Try again."; }
      })
      .then(function () { busy = false; btn.disabled = false; btn.textContent = saved; });
  });
})();
