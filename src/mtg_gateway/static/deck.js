/* Deck page, deck list, search and collection helpers. The pages work without this file (every
   control is a form); this only makes them feel like Archidekt:
   - the toolbar's View as / Group by / Sort by selects apply on change, the local filter narrows
     the cards as you type, and card-name inputs autocomplete through the gateway's own Scryfall
     search;
   - on touch screens a tap fans a stack out and a tap on a card opens a viewer with the card
     large and what can be done with it (grid view, stacks, text rows alike);
   - on the member's own deck, cards can be dragged between categories (mouse, or press and hold
     on touch); the moves are saved to Archidekt in one go, with a snapshot first. */
(function () {
  "use strict";
  var $ = function (sel, root) { return (root || document).querySelector(sel); };
  var $$ = function (sel, root) { return Array.prototype.slice.call((root || document).querySelectorAll(sel)); };
  var csrfInput = $("input[name=csrf]");
  var CSRF = csrfInput ? csrfInput.value : "";
  function el(tag, cls, text) {
    var e = document.createElement(tag);
    if (cls) e.className = cls;
    if (text !== undefined) e.textContent = text;
    return e;
  }

  // -- forms that apply on change ----------------------------------------------------------
  $$("#viewform, #listform, #searchform").forEach(function (form) {
    $$("select", form).forEach(function (sel) {
      sel.addEventListener("change", function () { form.requestSubmit ? form.requestSubmit() : form.submit(); });
    });
  });

  // -- Probability of draw (hypergeometric, like Archidekt's stats tab) --------------------------
  var oddsBox = $("#odds");
  var oddsData = $("#odds-data");
  if (oddsBox && oddsData) {
    var odds;
    try { odds = JSON.parse(oddsData.textContent); } catch (e) { odds = null; }
    if (odds && odds.size) {
      var lf = [0];
      for (var i = 1; i <= odds.size; i++) lf.push(lf[i - 1] + Math.log(i));
      var lchoose = function (a, b) { return b < 0 || b > a ? -Infinity : lf[a] - lf[b] - lf[a - b]; };
      var pExact = function (N, K, n, k) {
        if (k > K || k > n || n - k > N - K) return 0;
        return Math.exp(lchoose(K, k) + lchoose(N - K, n - k) - lchoose(N, n));
      };
      var pAtLeast = function (N, K, n, k) {
        var p = 0;
        for (var j = k; j <= Math.min(K, n); j++) p += pExact(N, K, n, j);
        return p;
      };
      var form = $(".oddsform", oddsBox);
      var tbody = $("tbody", oddsBox);
      var render = function () {
        var mode = form.elements.mode.value;
        var k = Math.max(0, parseInt(form.elements.k.value, 10) || 0);
        var n = Math.min(odds.size, Math.max(1, parseInt(form.elements.n.value, 10) || 7));
        var group = odds.groups[form.elements.by.value] || {};
        tbody.textContent = "";
        Object.keys(group).forEach(function (name) {
          var K = group[name];
          var p = mode === "exact" ? pExact(odds.size, K, n, k) : pAtLeast(odds.size, K, n, k);
          var tr = el("tr");
          tr.appendChild(el("td", null, name));
          tr.appendChild(el("td", null, String(K)));
          tr.appendChild(el("td", null, (p >= 0.995 && p < 1 ? ">99" : Math.round(p * 100)) + "%"));
          tbody.appendChild(tr);
        });
      };
      form.addEventListener("input", render);
      form.addEventListener("change", render);
      render();
    }
  }

  // -- live local filter over the rendered cards (rows and image cards carry data-name) ----------
  var q = $("#q");
  var cards = $("#cards");
  if (q && cards) {
    var items = $$("[data-name]", cards);
    var stacks = $$(".stack", cards);
    var apply = function () {
      var needle = q.value.trim().toLowerCase();
      items.forEach(function (el) {
        el.style.display = !needle || el.getAttribute("data-name").indexOf(needle) >= 0 ? "" : "none";
      });
      stacks.forEach(function (st) {
        st.style.display = $$("[data-name]", st).some(function (el) { return el.style.display !== "none"; }) ? "" : "none";
      });
    };
    q.addEventListener("input", apply);
    if (q.form) q.form.addEventListener("submit", function (ev) { if (document.activeElement === q) { ev.preventDefault(); apply(); } });
  }

  // -- card-name autocomplete (Quick add, the search form's commander, the collection's add box) -
  var names = $("#cardnames");
  if (names) {
    var timer = null;
    $$("input[list=cardnames]").forEach(function (input) {
      input.addEventListener("input", function () {
        clearTimeout(timer);
        var v = input.value.trim();
        if (v.length < 3) return;
        timer = setTimeout(function () {
          fetch("/scan/api/search?q=" + encodeURIComponent(v), { credentials: "same-origin" })
            .then(function (r) { return r.ok ? r.json() : { names: [] }; })
            .then(function (d) {
              names.textContent = "";
              (d.names || []).slice(0, 12).forEach(function (n) {
                var o = document.createElement("option");
                o.value = n;
                names.appendChild(o);
              });
            })
            .catch(function () {});
        }, 250);
      });
    });
  }

  // -- deck page only (the social block further down runs on every deck, own or not) -------
  if (cards && cards.classList.contains("deckview")) {
  var deckId = cards.getAttribute("data-deck");
  var own = cards.hasAttribute("data-own");
  var touch = window.matchMedia("(hover: none)").matches;

  // -- card viewer (static/cardview.js shows the whole card; this adds the deck's actions) -------
  var CardView = window.MtgCardView;
  function closeViewer() { if (CardView) CardView.close(); }
  function openViewer(card) {
    if (!CardView) return;
    var name = card.getAttribute("data-card") || "";
    var acts = [];
    var group = card.closest(".stack");
    if (own && deckId) {
      var edit = el("a", "btn btn-primary", "Edit in deck editor");
      edit.href = "/decks/" + encodeURIComponent(deckId) + "/edit#card-" + encodeURIComponent(name);
      acts.push(edit);
      if (group && cards.hasAttribute("data-own")) {
        var move = el("button", "btn", "Move to another category…");
        move.type = "button";
        move.addEventListener("click", function () { closeViewer(); pickCategory(card); });
        acts.push(move);
      }
    }
    var ownBtn = el("button", "btn", "I own this card");
    ownBtn.type = "button";
    ownBtn.addEventListener("click", function () {
      ownBtn.disabled = true;
      ownBtn.textContent = "Adding…";
      fetch("/collection/api/add", {
        method: "POST", credentials: "same-origin",
        headers: { "Content-Type": "application/json", "X-CSRF-Token": CSRF },
        body: JSON.stringify({ items: [{ name: name, quantity: 1 }], source: "manual" })
      }).then(function (r) { return r.json(); }).then(function (d) {
        ownBtn.textContent = d.ok && d.added && d.added.length ? "Added to your collection" : "Could not add (" + ((d && d.message) || "lookup failed") + ")";
      }).catch(function () { ownBtn.textContent = "Could not add: no connection"; });
    });
    acts.push(ownBtn);
    var scry = el("a", "btn", "Open on Scryfall");
    scry.href = "https://scryfall.com/search?q=" + encodeURIComponent("!\"" + name + "\"");
    scry.target = "_blank";
    scry.rel = "noopener noreferrer";
    acts.push(scry);
    CardView.open(CardView.fromElement(card), acts);
  }

  // -- stacks and grid: tap to fan out, tap a card to open it ----------------------------------
  var isStacks = cards.classList.contains("stacks");
  cards.addEventListener("click", function (e) {
    var card = e.target.closest(".c, .row");
    if (!card || !cards.contains(card) || dragged) return;
    var fan = card.closest(".cards");
    if (isStacks && touch && fan && !fan.classList.contains("fanned")) {
      // first tap on a collapsed stack fans it out; cards behind the top one were not visible yet
      $$(".cards.fanned", cards).forEach(function (f) { f.classList.remove("fanned"); });
      fan.classList.add("fanned");
      return;
    }
    openViewer(card);
  });
  cards.addEventListener("keydown", function (e) {
    if ((e.key === "Enter" || e.key === " ") && e.target.classList && e.target.classList.contains("c")) {
      e.preventDefault();
      openViewer(e.target);
    }
  });

  // -- own deck: drag cards between categories, moves become one proposal ---------------------
  if (own && deckId) {
  var moves = {}; // card name -> {from, to}
  var bar = document.createElement("div");
  bar.className = "movebar";
  bar.setAttribute("role", "region");
  bar.setAttribute("aria-label", "Pending category moves");
  var review = el("button", "btn-primary", "Save moves");
  review.type = "button";
  var undo = el("button", null, "Undo all");
  undo.type = "button";
  var count = el("span", "count", "");
  var status = el("p", "status", "");
  status.setAttribute("role", "status");
  bar.appendChild(review);
  bar.appendChild(undo);
  bar.appendChild(count);
  bar.appendChild(status);
  cards.parentNode.insertBefore(bar, cards.nextSibling);

  function refreshBar() {
    var n = Object.keys(moves).length;
    bar.classList.toggle("show", n > 0);
    count.textContent = n === 1 ? "1 card moved" : n + " cards moved";
    review.disabled = n === 0;
  }
  function moveCard(card, target) {
    var from = card.closest(".stack");
    if (!target || target === from) return;
    var name = card.getAttribute("data-card");
    var original = moves[name] ? moves[name].from : from.getAttribute("data-group");
    var to = target.getAttribute("data-group");
    $(".cards, .rows", target).appendChild(card);
    if (to === original) delete moves[name];
    else moves[name] = { from: original, to: to };
    refreshBar();
  }
  undo.addEventListener("click", function () { location.reload(); });
  // The member's own moves are saved to Archidekt at once (one proposal, applied with its
  // snapshot); the assistant never reaches this path.
  review.addEventListener("click", function () {
    var changes = Object.keys(moves).map(function (name) { return { action: "set_category", card_name: name, categories: [moves[name].to] }; });
    if (!changes.length) return;
    review.disabled = true;
    status.textContent = "Saving to Archidekt…";
    fetch("/api/v1/proposals", {
      method: "POST", credentials: "same-origin",
      headers: { "Content-Type": "application/json", "X-CSRF-Token": CSRF },
      body: JSON.stringify({ kind: "edit", deck_id: deckId, changes: changes, apply: true, confirmed: true })
    }).then(function (r) { return r.json(); }).then(function (d) {
      if (d.ok && d.applied) { location.href = "/decks/" + encodeURIComponent(deckId) + "?ok=saved"; return; }
      if (d.ok && d.proposal_id) { location.href = "/proposals/" + encodeURIComponent(d.proposal_id); return; }
      status.textContent = "Could not save: " + ((d && d.message) || "unknown error");
      review.disabled = false;
    }).catch(function () { status.textContent = "Could not save: no connection"; review.disabled = false; });
  });

  function pickCategory(card) {
    var groups = $$(".stack", cards).map(function (st) { return st.getAttribute("data-group"); });
    var from = card.closest(".stack").getAttribute("data-group");
    var choice = window.prompt("Move “" + card.getAttribute("data-card") + "” from " + from + " to which category?\n\n" + groups.join("\n"));
    if (!choice) return;
    var target = $$(".stack", cards).filter(function (st) { return st.getAttribute("data-group").toLowerCase() === choice.trim().toLowerCase(); })[0];
    if (target) moveCard(card, target);
  }

  // mouse drag (native drag and drop)
  var dragged = null;
  $$(".c, .row", cards).forEach(function (c) { c.setAttribute("draggable", "true"); });
  cards.addEventListener("dragstart", function (e) {
    var card = e.target.closest(".c, .row");
    if (!card) return;
    dragged = card;
    card.classList.add("dragging");
    try { e.dataTransfer.setData("text/plain", card.getAttribute("data-card")); e.dataTransfer.effectAllowed = "move"; } catch (err) { /* older browsers */ }
  });
  cards.addEventListener("dragover", function (e) {
    var st = e.target.closest(".stack");
    if (!dragged || !st) return;
    e.preventDefault();
    $$(".stack.dropping", cards).forEach(function (s) { if (s !== st) s.classList.remove("dropping"); });
    st.classList.add("dropping");
  });
  cards.addEventListener("dragleave", function (e) {
    var st = e.target.closest(".stack");
    if (st && !st.contains(e.relatedTarget)) st.classList.remove("dropping");
  });
  cards.addEventListener("drop", function (e) {
    var st = e.target.closest(".stack");
    if (!dragged || !st) return;
    e.preventDefault();
    st.classList.remove("dropping");
    moveCard(dragged, st);
  });
  cards.addEventListener("dragend", function () {
    if (dragged) dragged.classList.remove("dragging");
    $$(".stack.dropping", cards).forEach(function (s) { s.classList.remove("dropping"); });
    var was = dragged;
    dragged = null;
    if (was) setTimeout(function () { /* swallow the click that follows a drop */ }, 0);
  });

  // touch drag: press and hold a card for 350 ms, then slide it onto another category
  var hold = null, touchCard = null, ghost = null, lastTarget = null;
  cards.addEventListener("touchstart", function (e) {
    var card = e.target.closest(".c, .row");
    if (!card || e.touches.length !== 1) return;
    var t = e.touches[0];
    hold = setTimeout(function () {
      touchCard = card;
      dragged = card;
      card.classList.add("dragging");
      ghost = card.cloneNode(true);
      ghost.classList.remove("dragging");
      ghost.style.cssText = "position:fixed;z-index:70;width:" + card.offsetWidth + "px;pointer-events:none;opacity:.9;left:" + (t.clientX - card.offsetWidth / 2) + "px;top:" + (t.clientY - 40) + "px;margin:0";
      document.body.appendChild(ghost);
      if (navigator.vibrate) navigator.vibrate(20);
    }, 350);
  }, { passive: true });
  cards.addEventListener("touchmove", function (e) {
    if (!touchCard) { clearTimeout(hold); return; }
    e.preventDefault();
    var t = e.touches[0];
    ghost.style.left = (t.clientX - touchCard.offsetWidth / 2) + "px";
    ghost.style.top = (t.clientY - 40) + "px";
    var under = document.elementFromPoint(t.clientX, t.clientY);
    var st = under ? under.closest(".stack") : null;
    if (lastTarget && lastTarget !== st) lastTarget.classList.remove("dropping");
    if (st) st.classList.add("dropping");
    lastTarget = st;
  }, { passive: false });
  function endTouch() {
    clearTimeout(hold);
    if (!touchCard) return;
    if (lastTarget) { lastTarget.classList.remove("dropping"); moveCard(touchCard, lastTarget); }
    touchCard.classList.remove("dragging");
    if (ghost) ghost.remove();
    ghost = null; lastTarget = null; touchCard = null;
    setTimeout(function () { dragged = null; }, 0);
  }
  cards.addEventListener("touchend", endTouch);
  cards.addEventListener("touchcancel", endTouch);
  } // own deck
  } // deck view

  // -- Archidekt social actions: like, bookmark, follow, comments ------------------------------------
  // Each is the person's own click: a confirmation chip appears first, then the gateway sends the
  // action to Archidekt under their linked session (/social/api). Nothing here is reachable by an
  // assistant.
  function socialRequest(method, url, body) {
    return fetch(url, {
      method: method, credentials: "same-origin",
      headers: { "Content-Type": "application/json", "X-CSRF-Token": CSRF },
      body: body === undefined ? undefined : JSON.stringify(body)
    }).then(function (r) { return r.json().then(function (d) { d.status = r.status; return d; }); });
  }
  function socialProblem(d) {
    if (d && (d.error === "not_linked" || d.error === "auth")) {
      var a = el("a", "", "Link your Archidekt account");
      a.href = "/account";
      var frag = document.createDocumentFragment();
      frag.appendChild(a);
      frag.appendChild(document.createTextNode(" to like, bookmark, follow or comment."));
      return frag;
    }
    return document.createTextNode((d && d.message) || "That did not work; try again.");
  }
  function confirmChip(btn, question, yesLabel, onYes) {
    var old = btn.parentNode.querySelector(".confirm");
    if (old) old.dismiss(); // one question at a time; the other button comes back
    var chip = el("span", "confirm");
    chip.setAttribute("role", "group");
    chip.appendChild(document.createTextNode(question + " "));
    var yes = el("button", "btn-primary", yesLabel);
    yes.type = "button";
    var no = el("button", "", "No");
    no.type = "button";
    chip.appendChild(yes);
    chip.appendChild(no);
    btn.hidden = true;
    btn.parentNode.insertBefore(chip, btn.nextSibling);
    var restore = function () { chip.remove(); btn.hidden = false; btn.focus(); };
    chip.dismiss = function () { chip.remove(); btn.hidden = false; };
    no.addEventListener("click", restore);
    chip.addEventListener("keydown", function (e) { if (e.key === "Escape") restore(); });
    yes.addEventListener("click", function () { chip.remove(); btn.hidden = false; onYes(); });
    yes.focus();
  }
  var social = $(".banner .social");
  if (social) {
    var noteEl = null;
    var note = function (content) {
      if (noteEl) noteEl.remove();
      noteEl = el("p", "note");
      if (typeof content === "string") noteEl.textContent = content; else noteEl.appendChild(content);
      social.appendChild(noteEl);
    };
    var deck = social.getAttribute("data-deck");
    var setLabel = function (btn, text) { var sp = btn.querySelector("span"); if (sp) sp.textContent = text; };
    var busy = function (btn, on) { btn.disabled = on; };
    var likeBtn = $("[data-social=vote]", social);
    if (likeBtn) {
      likeBtn.addEventListener("click", function () {
        var liked = likeBtn.getAttribute("data-state") === "1";
        confirmChip(likeBtn, liked ? "Remove your like?" : "Like this deck on Archidekt?", liked ? "Remove" : "Like", function () {
          busy(likeBtn, true);
          socialRequest("POST", "/social/api/decks/" + encodeURIComponent(deck) + "/vote", { vote: liked ? "none" : "up" }).then(function (d) {
            busy(likeBtn, false);
            if (!d.ok) { note(socialProblem(d)); return; }
            likeBtn.setAttribute("data-state", String(d.vote));
            likeBtn.classList.toggle("on", d.vote === 1);
            likeBtn.setAttribute("aria-pressed", d.vote === 1 ? "true" : "false");
            $(".n", likeBtn).textContent = d.points;
            setLabel(likeBtn, d.vote === 1 ? "Liked" : "Like");
            if (noteEl) noteEl.remove();
          }).catch(function () { busy(likeBtn, false); note("No connection."); });
        });
      });
    }
    var markBtn = $("[data-social=bookmark]", social);
    if (markBtn) {
      markBtn.addEventListener("click", function () {
        var on = markBtn.getAttribute("data-state") === "1";
        confirmChip(markBtn, on ? "Remove the bookmark?" : "Bookmark this deck on Archidekt?", on ? "Remove" : "Bookmark", function () {
          busy(markBtn, true);
          socialRequest("POST", "/social/api/decks/" + encodeURIComponent(deck) + "/bookmark", { on: !on }).then(function (d) {
            busy(markBtn, false);
            if (!d.ok) { note(socialProblem(d)); return; }
            markBtn.setAttribute("data-state", d.bookmarked ? "1" : "0");
            markBtn.classList.toggle("on", d.bookmarked);
            markBtn.setAttribute("aria-pressed", d.bookmarked ? "true" : "false");
            setLabel(markBtn, d.bookmarked ? "Bookmarked" : "Bookmark");
            if (noteEl) noteEl.remove();
          }).catch(function () { busy(markBtn, false); note("No connection."); });
        });
      });
    }
  }
  // Follow buttons live in the deck banner and on user pages alike.
  $$("[data-social=follow]").forEach(function (btn) {
    var user = btn.getAttribute("data-user");
    var name = btn.getAttribute("data-name") || "this user";
    var holder = btn.closest(".social") || btn.parentNode;
    var say = function (content) {
      var old = holder.querySelector(".note");
      if (old) old.remove();
      var p = el("p", "note");
      if (typeof content === "string") p.textContent = content; else p.appendChild(content);
      holder.appendChild(p);
    };
    var paint = function (following) {
      btn.setAttribute("data-state", following ? "1" : "0");
      btn.classList.toggle("on", following);
      btn.setAttribute("aria-pressed", following ? "true" : "false");
      var sp = btn.querySelector("span");
      if (sp) sp.textContent = (following ? "Following " : "Follow ") + name;
    };
    socialRequest("GET", "/social/api/users/" + encodeURIComponent(user) + "/follow").then(function (d) {
      if (d.ok && d.self) { btn.hidden = true; return; }
      if (d.ok) paint(d.following);
    }).catch(function () {});
    btn.addEventListener("click", function () {
      var on = btn.getAttribute("data-state") === "1";
      confirmChip(btn, on ? "Stop following " + name + "?" : "Follow " + name + " on Archidekt?", on ? "Unfollow" : "Follow", function () {
        btn.disabled = true;
        socialRequest("POST", "/social/api/users/" + encodeURIComponent(user) + "/follow", { on: !on }).then(function (d) {
          btn.disabled = false;
          if (!d.ok) { say(socialProblem(d)); return; }
          paint(d.following);
          var old = holder.querySelector(".note");
          if (old) old.remove();
        }).catch(function () { btn.disabled = false; say("No connection."); });
      });
    });
  });
  // The comment thread loads when its panel comes into view; posting asks first.
  var commentsPanel = $("#comments");
  if (commentsPanel) {
    var thread = $(".thread", commentsPanel);
    var countEl = $("[data-count]", commentsPanel);
    var form = $("form.newcomment", commentsPanel);
    var textarea = $("textarea", form);
    var replyTo = $(".replyto", form);
    var parentId = null;
    var me = null;  // the member's Archidekt user id, from the thread load; own comments get Edit and Delete
    var deckUrl = "/social/api/decks/" + encodeURIComponent(commentsPanel.getAttribute("data-deck")) + "/comments";
    var when = function (iso) {
      var d = new Date(iso);
      return isNaN(d) ? "" : d.toLocaleDateString(undefined, { year: "numeric", month: "short", day: "numeric" });
    };
    var render = function (c) {
      var box = el("article", "cmt");
      box.setAttribute("data-id", c.id);
      var who = el("div", "who");
      var b = el("b", "", c.owner.username || "someone");
      who.appendChild(b);
      who.appendChild(document.createTextNode(" · " + when(c.created_at) + (c.edited_at ? " · edited" : "")));
      if (c.points) who.appendChild(document.createTextNode(" · " + c.points + (c.points === 1 ? " point" : " points")));
      box.appendChild(who);
      var p = el("p", "", c.text);
      box.appendChild(p);
      var acts = el("div", "acts");
      var reply = el("button", "btn-ghost", "Reply");
      reply.type = "button";
      reply.addEventListener("click", function () {
        parentId = c.id;
        replyTo.hidden = false;
        replyTo.textContent = "";
        replyTo.appendChild(document.createTextNode("Replying to " + (c.owner.username || "this comment") + " "));
        var cancel = el("button", "", "Cancel");
        cancel.type = "button";
        cancel.addEventListener("click", function () { parentId = null; replyTo.hidden = true; });
        replyTo.appendChild(cancel);
        textarea.focus();
      });
      acts.appendChild(reply);
      if (me !== null && c.owner && c.owner.id === me) {
        var edit = el("button", "btn-ghost", "Edit");
        edit.type = "button";
        edit.addEventListener("click", function () {
          if ($("form.editcomment", box)) return;
          var f = el("form", "editcomment");
          var ta = document.createElement("textarea");
          ta.value = c.text; ta.rows = 3; ta.maxLength = 2000; ta.setAttribute("aria-label", "Edit your comment");
          var save = el("button", "btn-primary", "Save");
          var cancel = el("button", "", "Cancel"); cancel.type = "button";
          var note = el("span", "muted small", "");
          f.appendChild(ta); f.appendChild(save); f.appendChild(cancel); f.appendChild(note);
          cancel.addEventListener("click", function () { f.remove(); p.hidden = false; });
          f.addEventListener("submit", function (ev) {
            ev.preventDefault();
            var text = ta.value.trim();
            if (!text) { ta.focus(); return; }
            save.disabled = true; note.textContent = "Saving…";
            socialRequest("PATCH", deckUrl + "/" + encodeURIComponent(c.id), { text: text }).then(function (d) {
              if (!d.ok) { save.disabled = false; note.textContent = ""; note.appendChild(socialProblem(d)); return; }
              c.text = d.comment.text; p.textContent = c.text; p.hidden = false; f.remove();
              if (!/edited/.test(who.textContent)) who.appendChild(document.createTextNode(" · edited"));
            }).catch(function () { save.disabled = false; note.textContent = "Network error; nothing was changed."; });
          });
          p.hidden = true;
          box.insertBefore(f, acts);
          ta.focus();
        });
        acts.appendChild(edit);
        var del = el("button", "btn-ghost del", "Delete");
        del.type = "button";
        del.addEventListener("click", function () {
          if ($(".confirmbar", box)) return;
          var bar = el("div", "confirmbar notice warn");
          bar.setAttribute("role", "alertdialog");
          bar.appendChild(document.createTextNode("Delete this comment on Archidekt? "));
          var yes = el("button", "btn-danger", "Delete"); yes.type = "button";
          var no = el("button", "", "Keep"); no.type = "button";
          bar.appendChild(yes); bar.appendChild(no);
          no.addEventListener("click", function () { bar.remove(); });
          yes.addEventListener("click", function () {
            yes.disabled = true;
            socialRequest("DELETE", deckUrl + "/" + encodeURIComponent(c.id)).then(function (d) {
              if (!d.ok) { yes.disabled = false; bar.textContent = ""; bar.appendChild(socialProblem(d)); bar.appendChild(no); return; }
              box.remove();
              if (countEl) countEl.textContent = d.count === 1 ? "1 comment" : d.count + " comments";
              if (!$(".cmt", thread)) thread.appendChild(el("p", "muted", "No comments yet. Be the first."));
            }).catch(function () { yes.disabled = false; bar.textContent = "Network error; nothing was changed."; });
          });
          box.insertBefore(bar, acts.nextSibling);
          yes.focus();
        });
        acts.appendChild(del);
      }
      box.appendChild(acts);
      if (c.replies && c.replies.length) {
        var kids = el("div", "replies");
        c.replies.forEach(function (k) { kids.appendChild(render(k)); });
        box.appendChild(kids);
      }
      return box;
    };
    var loaded = false;
    var load = function () {
      if (loaded) return;
      loaded = true;
      socialRequest("GET", thread.getAttribute("data-src")).then(function (d) {
        if (!d.ok) { thread.textContent = ""; var p = el("p", "muted"); p.appendChild(socialProblem(d)); thread.appendChild(p); return; }
        thread.textContent = "";
        me = typeof d.me === "number" ? d.me : null;
        if (countEl) countEl.textContent = d.count === 1 ? "1 comment" : d.count + " comments";
        if (!d.comments.length) { thread.appendChild(el("p", "muted", "No comments yet. Be the first.")); return; }
        d.comments.forEach(function (c) { thread.appendChild(render(c)); });
        if (d.has_more) {
          var more = el("a", "muted small", "More comments on Archidekt");
          more.href = "https://archidekt.com/decks/" + encodeURIComponent(commentsPanel.getAttribute("data-deck"));
          more.target = "_blank"; more.rel = "noopener noreferrer";
          thread.appendChild(more);
        }
      }).catch(function () { loaded = false; });
    };
    if ("IntersectionObserver" in window) {
      var io = new IntersectionObserver(function (entries) {
        if (entries.some(function (e) { return e.isIntersecting; })) { load(); io.disconnect(); }
      }, { rootMargin: "200px" });
      io.observe(commentsPanel);
    } else { load(); }
    $$("[data-social=comments]").forEach(function (a) { a.addEventListener("click", load); });
    form.addEventListener("submit", function (e) {
      e.preventDefault();
      var text = textarea.value.trim();
      if (!text) { textarea.focus(); return; }
      var old = $(".confirmbar", form);
      if (old) old.remove();
      var bar = el("div", "confirmbar");
      bar.appendChild(document.createTextNode("Post this comment publicly on Archidekt?"));
      var yes = el("button", "btn-primary", "Post");
      yes.type = "button";
      var no = el("button", "", "Cancel");
      no.type = "button";
      bar.appendChild(yes); bar.appendChild(no);
      form.insertBefore(bar, form.querySelector("button[type=submit]"));
      no.addEventListener("click", function () { bar.remove(); textarea.focus(); });
      yes.addEventListener("click", function () {
        yes.disabled = true;
        socialRequest("POST", "/social/api/decks/" + encodeURIComponent(commentsPanel.getAttribute("data-deck")) + "/comments", { text: text, parent: parentId }).then(function (d) {
          bar.remove();
          if (!d.ok) { var p = el("p", "notice error"); p.appendChild(socialProblem(d)); form.insertBefore(p, form.firstChild); return; }
          var old = $(".notice", form); if (old) old.remove();
          var node = render(d.comment);
          var parentBox = parentId ? $("[data-id='" + parentId + "']", thread) : null;
          if (parentBox) {
            var kids = $(".replies", parentBox) || parentBox.appendChild(el("div", "replies"));
            kids.appendChild(node);
          } else {
            var empty = $("p.muted", thread); if (empty && /No comments yet/.test(empty.textContent)) empty.remove();
            thread.insertBefore(node, thread.firstChild);
          }
          textarea.value = ""; parentId = null; replyTo.hidden = true;
          if (countEl) { var n = (parseInt(countEl.textContent, 10) || 0) + 1; countEl.textContent = n === 1 ? "1 comment" : n + " comments"; }
        }).catch(function () { bar.remove(); });
      });
    });
  }
})();
