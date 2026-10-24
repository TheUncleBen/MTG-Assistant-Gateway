/* Archidekt social actions on the gateway's pages: like, bookmark and follow in a deck's banner,
   Follow on an Archidekt user page, and a deck's comments (and a forum thread's replies, which are
   the same panel: forum_page.py). Moved out of deck.js unchanged in 0.7.29; the pages work without
   it (every control is a form).

   Parity release 2 (mockup screens/60-forum.js): each comment has a ⋮ (also a right-click, a press
   and hold, or the Menu key) with Reply and Copy link, Edit… and Delete… on your own, Vote up,
   Vote down and Report comment… on others' (the page's Report comment box, #reportcomment, which sends the report
   to its review page). Under the comment sit its points as a vote button (▲ 3: a press votes it
   up, a second takes the vote back) and Reply, as the mockup's post footer; everything else is
   in the ⋮. The date is the site's one list date (feedback.js MtgText.rel, with the full date
   and time as its tooltip). The forum's reply box has Bold, Italic and Link buttons that put the reply's
   marks around the selected words (**bold**, _italic_, [words](https://…); formatting.marks_doc
   writes them as Archidekt's own formatting), and Post reply first shows the reply as it will
   appear and asks once more (Keep editing / Post reply). window.MtgComments lets the thread page's
   own script (static/forum.js) start a reply and open the report box for the first post. */
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

  // -- Archidekt social actions: like, bookmark, follow, comments ------------------------------------
  // Each is the person's own click: a confirmation chip appears first, then the gateway sends the
  // action to Archidekt under their linked session (/social/api). Nothing here is reachable by an
  // assistant.
  function socialRequest(method, url, body) {
    return fetch(url, {
      method: method, credentials: "same-origin",
      headers: { "Content-Type": "application/json", "X-CSRF-Token": CSRF },
      body: body === undefined ? undefined : JSON.stringify(body)
    }).then(function (r) {
      return r.json().then(function (d) { d.status = r.status; return d; }, function () {
        // not the gateway's JSON answer (an error page): the request reached it, the outcome is unknown
        return { ok: false, status: r.status, error: "server",
                 message: "The gateway hit a problem. Reload the page to see what reached Archidekt." };
      });
    });
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
    chip.setAttribute("aria-label", question);
    chip.appendChild(el("span", "", question)); // in a span, so a long name in it can wrap (.confirm > span)
    var yes = el("button", "btn-primary", yesLabel);
    yes.type = "button";
    var no = el("button", "", "No");
    no.type = "button";
    chip.appendChild(yes);
    chip.appendChild(no);
    // hidden by style too: a button styled display:block (a form's Save) ignores the hidden attribute
    var show = function (on) { btn.hidden = !on; btn.style.display = on ? "" : "none"; };
    show(false);
    btn.parentNode.insertBefore(chip, btn.nextSibling);
    var restore = function () { chip.remove(); show(true); btn.focus(); };
    chip.dismiss = function () { chip.remove(); show(true); };
    no.addEventListener("click", restore);
    chip.addEventListener("keydown", function (e) { if (e.key === "Escape") restore(); });
    yes.addEventListener("click", function () { chip.remove(); show(true); onYes(); });
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
        var down = likeBtn.getAttribute("data-state") === "2"; // a down vote is taken back
        var ask = down ? "Remove your down vote?" : liked ? "Remove your like?" : "Like this deck on Archidekt?";
        confirmChip(likeBtn, ask, down ? "Remove vote" : liked ? "Remove" : "Like", function () {
          busy(likeBtn, true);
          socialRequest("POST", "/social/api/decks/" + encodeURIComponent(deck) + "/vote", { vote: liked || down ? "none" : "up" }).then(function (d) {
            busy(likeBtn, false);
            if (!d.ok) { note(socialProblem(d)); return; }
            likeBtn.setAttribute("data-state", String(d.vote));
            likeBtn.classList.toggle("on", d.vote === 1);
            likeBtn.setAttribute("aria-pressed", d.vote === 1 ? "true" : "false");
            $(".n", likeBtn).textContent = d.points;
            setLabel(likeBtn, d.vote === 1 ? "Liked" : d.vote === 2 ? "Voted down" : "Like");
            if (noteEl) noteEl.remove();
          }).catch(function () { busy(likeBtn, false); note(down ? "No connection; your vote was not changed." : "No connection; the like was not changed."); });
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
          }).catch(function () { busy(markBtn, false); note("No connection; the bookmark was not changed."); });
        });
      });
    }
  }
  // Follow buttons live in the deck banner and on user pages alike.
  $$("[data-social=follow]").forEach(function (btn) {
    var user = btn.getAttribute("data-user");
    var name = btn.getAttribute("data-name") || "this user";
    var holder = btn.closest(".social") || btn.parentNode;
    // one live note, there and empty from the start, so a screen reader announces what is set in
    // it (a status note added already filled is often not read)
    var live = holder.querySelector(".note[role=status]");
    if (!live) {
      live = el("p", "note");
      live.setAttribute("role", "status");
      live.style.margin = "0";
      holder.appendChild(live);
    }
    var say = function (content) {
      live.textContent = "";
      live.style.margin = "";
      if (typeof content === "string") live.textContent = content; else live.appendChild(content);
    };
    var hush = function () { live.textContent = ""; live.style.margin = "0"; };
    var paint = function (following) {
      btn.setAttribute("data-state", following ? "1" : "0");
      btn.classList.toggle("on", following);
      btn.setAttribute("aria-pressed", following ? "true" : "false");
      var sp = btn.querySelector("span");
      if (sp) sp.textContent = (following ? "Following " : "Follow ") + name;
    };
    var extra = null; // an Unfollow beside Follow while the state is not known
    var send = function (b, on) {
      b.disabled = true;
      socialRequest("POST", "/social/api/users/" + encodeURIComponent(user) + "/follow", { on: on }).then(function (d) {
        b.disabled = false;
        if (!d.ok) { say(socialProblem(d)); return; }
        if (extra) { extra.remove(); extra = null; }
        paint(d.following);
        hush();
        btn.focus(); // the confirm and an Unfollow it came from are gone: focus stays on the page's button
      }).catch(function () { b.disabled = false; say("No connection; the follow was not changed."); });
    };
    socialRequest("GET", "/social/api/users/" + encodeURIComponent(user) + "/follow").then(function (d) {
      if (d.ok && d.self) { btn.hidden = true; return; }
      if (!d.ok) return;
      paint(d.following);
      if (d.unknown && !extra) {
        // the following list was read only in part, and they are not in that part: say so and
        // offer both, rather than a Follow that may be wrong
        extra = el("button", btn.className.replace(/\bon\b/, "").trim(), "");
        extra.type = "button";
        extra.setAttribute("data-social", "unfollow");
        extra.appendChild(el("span", "", "Unfollow " + name));
        if (btn.disabled) {
          // writes switched off: off like Follow, with the same reason (the server refuses it too)
          extra.disabled = true;
          ["title", "aria-describedby"].forEach(function (k) {
            if (btn.hasAttribute(k)) extra.setAttribute(k, btn.getAttribute(k));
          });
        }
        btn.parentNode.insertBefore(extra, btn.nextSibling);
        extra.addEventListener("click", function () {
          confirmChip(extra, "Stop following " + name + "?", "Unfollow", function () { send(extra, false); });
        });
        say("We could not check whether you follow " + name + ".");
      }
    }).catch(function () {});
    btn.addEventListener("click", function () {
      var on = btn.getAttribute("data-state") === "1";
      confirmChip(btn, on ? "Stop following " + name + "?" : "Follow " + name + " on Archidekt?", on ? "Unfollow" : "Follow", function () {
        send(btn, !on);
      });
    });
  });
  // The comment thread loads when its panel comes into view; posting asks first.
  var commentsPanel = $("#comments");
  if (commentsPanel) {
    var thread = $(".thread", commentsPanel);
    var countEl = $("[data-count]", commentsPanel);
    // writes switched off on this gateway: the thread is read-only (no form, Reply, votes or edits)
    var canWrite = commentsPanel.getAttribute("data-writes") !== "off";
    var noneYet = commentsPanel.hasAttribute("data-forum") ? "No replies yet. Be the first." : "No comments yet. Be the first.";
    var form = $("form.newcomment", commentsPanel);
    var textarea = form ? $("textarea", form) : null;
    var replyTo = form ? $(".replyto", form) : null;
    var parentId = null, parentPage = 1;  // the comment replied to, and the thread page it is on
    var me = null;  // the member's Archidekt user id, from the thread load; own comments get Edit and Delete
    var deckUrl = "/social/api/decks/" + encodeURIComponent(commentsPanel.getAttribute("data-deck")) + "/comments";
    // the comment's date: the site's one list date (ui.when on the server, MtgText.rel here:
    // "2 h ago", then "Oct 12"), with the full date and time as its tooltip; feedback.js keeps
    // the text current while the page stays open
    var when = function (iso) {
      var t = el("time", "ui-when");
      t.setAttribute("datetime", iso || "");
      t.setAttribute("data-fmt", "rel");
      var T = window.MtgText, d = new Date(iso);
      if (T && T.rel) { t.textContent = T.rel(iso); t.title = T.when(iso); }
      else t.textContent = isNaN(d) ? "" : d.toLocaleDateString(undefined, { month: "short", day: "numeric" });
      return t;
    };
    // pg: the page of the thread the comment came on, sent with every write so the gateway finds it there
    // the reply box set to answer one comment (or the thread itself, with no id)
    var replyingTo = function (id, name, pg) {
      if (!form) return;
      parentId = id; parentPage = pg || 1;
      if (!id) { replyTo.hidden = true; textarea.focus(); return; }
      replyTo.hidden = false;
      replyTo.textContent = "";
      replyTo.appendChild(document.createTextNode("Replying to " + (name || "this comment") + " "));
      var cancel = el("button", "", "Cancel");
      cancel.type = "button";
      cancel.addEventListener("click", function () { parentId = null; replyTo.hidden = true; });
      replyTo.appendChild(cancel);
      textarea.focus();
    };
    var render = function (c, pg) {
      pg = pg || 1;
      var box = el("article", "cmt");
      box.setAttribute("data-id", c.id);
      box.id = "c" + c.id;
      box.tabIndex = 0;
      box.setAttribute("data-owner", c.owner.username || "");
      box.setAttribute("data-page", String(pg));
      var who = el("div", "who");
      var b = el("b", "", c.owner.username || "someone");
      who.appendChild(b);
      who.appendChild(document.createTextNode(" · "));
      who.appendChild(when(c.created_at));
      if (c.edited_at) who.appendChild(document.createTextNode(" · edited"));
      // the points, under the comment: a vote button on someone else's comment, plain on your own
      var pts = el("span", "pts");
      var pointsText = function () { var n = c.points || 0; return n + (n === 1 || n === -1 ? " point" : " points"); };
      var showPoints = function () { pts.textContent = pointsText(); };
      showPoints();
      // the ⋮: the same menu a right-click, a press and hold or the Menu key opens (menuFor);
      // with writes off the comments are read only and carry no buttons (the menu's Copy link
      // stays on a right-click)
      if (window.MtgMenu && canWrite) {
        var kb = window.MtgMenu.kebab("Menu for " + (c.owner.username || "this") + "'s comment");
        who.appendChild(kb);
      }
      box.appendChild(who);
      var p = el("p", "", c.text);
      box.appendChild(p);
      var acts = el("div", "acts");
      var does = { reply: null, up: null, down: null, edit: null, del: null, own: !!(me !== null && c.owner && c.owner.id === me) };
      box.__cmt = { c: c, pg: pg, does: does };
      // the vote on someone else's comment (Archidekt's vote up), asked first like the deck's
      // Like: the footer's ▲ button carries the points and takes the vote back on a second press;
      // Vote down lives in the ⋮ only (does.down), asked with the same chip on the ▲ button
      if (canWrite && me !== null && c.id && !(c.owner && c.owner.id === me)) {
        var btn = el("button", "btn-ghost vote");
        btn.type = "button";
        if (window.MtgMenu) btn.appendChild(window.MtgMenu.icon("plus")); else btn.appendChild(document.createTextNode("▲ "));
        var count = el("span", "n", String(c.points || 0));
        btn.appendChild(count);
        var mine = function () { return c.user_vote === 1; };
        var label = function () {
          count.textContent = String(c.points || 0);
          btn.setAttribute("aria-pressed", mine() ? "true" : "false");
          btn.setAttribute("aria-label", pointsText() + ". " + (mine() ? "Take back your vote" : "Vote up") + ": comment by " + (c.owner.username || "someone"));
          btn.title = mine() ? "You voted this up" : "Vote up";
        };
        label();
        // asks on the ▲ button, then sends the vote ("up", "down" or "none" to take it back)
        var cast = function (want, question, yesLabel) {
          confirmChip(btn, question, yesLabel, function () {
            btn.disabled = true;
            socialRequest("POST", deckUrl + "/" + encodeURIComponent(c.id) + "/vote", { vote: want, page: pg }).then(function (d) {
              btn.disabled = false;
              if (!d.ok) { var n = el("p", "notice error"); n.appendChild(socialProblem(d)); acts.appendChild(n); setTimeout(function () { n.remove(); }, 8000); return; }
              c.user_vote = d.vote; c.points = d.points;
              showPoints();
              label();
            }).catch(function () {
              btn.disabled = false;
              var n = el("p", "notice error", "No connection; the vote was not sent."); n.setAttribute("role", "alert");
              acts.appendChild(n); setTimeout(function () { n.remove(); }, 8000);
            });
          });
        };
        btn.addEventListener("click", function () {
          if (mine()) cast("none", "Take back your vote?", "Take back"); else cast("up", "Vote this comment up on Archidekt?", "Vote");
        });
        btn.relabel = label;
        does.up = btn;
        does.down = function () {
          if (c.user_vote === 2) cast("none", "Take back your vote?", "Take back"); else cast("down", "Vote this comment down on Archidekt?", "Vote");
        };
        acts.appendChild(btn);
      } else {
        acts.appendChild(pts);
      }
      var reply = el("button", "btn-ghost", "Reply");
      reply.type = "button";
      reply.setAttribute("aria-label", "Reply to " + (c.owner.username || "this comment"));
      reply.addEventListener("click", function () { replyingTo(c.id, c.owner.username, pg); });
      if (canWrite) { acts.appendChild(reply); does.reply = reply; }
      // Edit and Delete on your own comment live in the ⋮ (Edit…, Delete…), as the mockup has
      // them: the buttons the menu presses are kept off the page
      if (canWrite && me !== null && c.owner && c.owner.id === me) {
        var edit = el("button", "btn-ghost", "Edit");
        edit.type = "button";
        edit.setAttribute("aria-label", "Edit your comment");
        edit.addEventListener("click", function () {
          if ($("form.editcomment", box)) return;
          var f = el("form", "editcomment");
          var ta = document.createElement("textarea");
          ta.value = c.text; ta.rows = 3; ta.maxLength = 2000; ta.setAttribute("aria-label", "Edit your comment");
          var save = el("button", "btn-primary", "Save");
          var cancel = el("button", "", "Cancel"); cancel.type = "button";
          var note = el("span", "muted small", "");
          note.setAttribute("role", "status"); // read out when it says the comment is empty
          f.appendChild(ta); f.appendChild(save); f.appendChild(cancel); f.appendChild(note);
          cancel.addEventListener("click", function () { f.remove(); p.hidden = false; });
          f.addEventListener("submit", function (ev) {
            ev.preventDefault();
            var text = ta.value.trim();
            // one wording with the assistant's (refusals.EMPTY_COMMENT)
            if (!text) { note.textContent = "The comment is empty: write it first."; ta.focus(); return; }
            note.textContent = "";
            // asked first, like every other write
            confirmChip(save, "Change your public comment on Archidekt?", "Change", function () {
              save.disabled = true; note.textContent = "Saving…";
              socialRequest("PATCH", deckUrl + "/" + encodeURIComponent(c.id), { text: text, page: pg }).then(function (d) {
                if (!d.ok) { save.disabled = false; note.textContent = ""; note.appendChild(socialProblem(d)); return; }
                c.text = d.comment.text; p.textContent = c.text; p.hidden = false; f.remove();
                if (!/edited/.test(who.textContent)) who.appendChild(document.createTextNode(" · edited"));
              }).catch(function () { save.disabled = false; note.textContent = "No connection; the comment was not changed."; });
            });
          });
          p.hidden = true;
          box.insertBefore(f, acts);
          ta.focus();
        });
        does.edit = edit;
        var del = el("button", "btn-ghost del", "Delete");
        del.type = "button";
        del.setAttribute("aria-label", "Delete your comment");
        del.addEventListener("click", function () {
          if ($(".confirmbar", box)) return;
          var bar = el("div", "confirmbar notice warn");
          bar.setAttribute("role", "alertdialog");
          bar.setAttribute("aria-label", "Delete this comment on Archidekt?");
          bar.addEventListener("keydown", function (e) { if (e.key === "Escape") { bar.remove(); del.focus(); } });
          bar.appendChild(document.createTextNode("Delete this comment on Archidekt? "));
          var yes = el("button", "btn-danger", "Delete"); yes.type = "button";
          var no = el("button", "", "Keep"); no.type = "button";
          bar.appendChild(yes); bar.appendChild(no);
          no.addEventListener("click", function () { bar.remove(); });
          yes.addEventListener("click", function () {
            yes.disabled = true;
            socialRequest("DELETE", deckUrl + "/" + encodeURIComponent(c.id) + "?page=" + pg).then(function (d) {
              if (!d.ok) { yes.disabled = false; bar.textContent = ""; bar.appendChild(socialProblem(d)); bar.appendChild(no); return; }
              box.remove();
              if (countEl) countEl.textContent = d.count === 1 ? "1 comment" : d.count + " comments";
              if (!$(".cmt", thread)) thread.appendChild(el("p", "muted", noneYet));
            }).catch(function () { yes.disabled = false; bar.textContent = "No connection; the comment was not deleted."; });
          });
          box.insertBefore(bar, acts.nextSibling);
          yes.focus();
        });
        does.del = del;
      }
      box.appendChild(acts);
      if (c.replies && c.replies.length) {
        var kids = el("div", "replies");
        c.replies.forEach(function (k) { kids.appendChild(render(k, pg)); });
        box.appendChild(kids);
      }
      return box;
    };
    var loaded = false;
    var failed = function (message) {
      // say what happened and offer another try (a silent empty thread looked like "no comments")
      loaded = false;
      thread.textContent = "";
      var p = el("p", "notice error");
      p.setAttribute("role", "alert");
      p.appendChild(document.createTextNode(message + " "));
      var retry = el("button", "btn mini-inline", "Try again");
      retry.type = "button";
      retry.addEventListener("click", load);
      p.appendChild(retry);
      thread.appendChild(p);
    };
    var load = function () {
      if (loaded) return;
      loaded = true;
      thread.textContent = "";
      var loading = el("p", "muted", "Loading comments…");
      loading.setAttribute("role", "status");
      thread.appendChild(loading);
      socialRequest("GET", thread.getAttribute("data-src")).then(show)
        .catch(function () { failed("The comments could not be loaded: no connection."); });
    };
    var show = function (d) {
      if (!d.ok) {
        if (d.error === "not_linked" || d.error === "auth") { thread.textContent = ""; var p = el("p", "muted"); p.appendChild(socialProblem(d)); thread.appendChild(p); return; }
        failed(d.message || "The comments could not be loaded.");  // Archidekt busy: offer another try
        return;
      }
      thread.textContent = "";
      me = typeof d.me === "number" ? d.me : null;
      if (countEl) countEl.textContent = d.count === 1 ? "1 comment" : d.count + " comments";
      if (!d.comments.length) { thread.appendChild(el("p", "muted", noneYet)); return; }
      d.comments.forEach(function (c) { thread.appendChild(render(c, 1)); });
      if (d.has_more) showMore(2);
    };
    // The next page of the thread, added under the comments already shown (every page can be
    // voted on and replied to, not only the first).
    var showMore = function (pg) {
      var wrap = el("p", "more-comments");
      var btn = el("button", "btn", "Show more comments");
      btn.type = "button";
      wrap.appendChild(btn);
      thread.appendChild(wrap);
      btn.addEventListener("click", function () {
        btn.disabled = true;
        btn.textContent = "Loading more comments…";
        var src = thread.getAttribute("data-src");
        socialRequest("GET", src + (src.indexOf("?") < 0 ? "?" : "&") + "page=" + pg).then(function (d) {
          if (!d.ok) {
            btn.disabled = false; btn.textContent = "Show more comments";
            var n = el("span", "muted small"); n.appendChild(document.createTextNode(" ")); n.appendChild(socialProblem(d));
            wrap.appendChild(n);
            return;
          }
          var seen = {};
          $$(".cmt", thread).forEach(function (x) { seen[x.getAttribute("data-id")] = true; });
          wrap.remove();
          // a vote since the first page loaded can move a comment from one page to the next: shown once
          d.comments.forEach(function (c) { if (!seen[String(c.id)]) thread.appendChild(render(c, pg)); });
          if (d.has_more) showMore(pg + 1);
        }).catch(function () {
          btn.disabled = false; btn.textContent = "Show more comments";
          var n = el("span", "muted small", " No connection; try again.");
          wrap.appendChild(n);
        });
      });
    };
    // A page that read the thread already (the forum's thread page) hands it over as JSON in the
    // panel, in the comment route's shape: drawn from that, with no second read.
    var pre = $("script[type='application/json']", commentsPanel);
    if (pre) {
      var given = null;
      try { given = JSON.parse(pre.textContent); } catch (err) { given = null; }
      pre.remove();
      if (given && given.comments) { loaded = true; show({ ok: true, root: given.root, count: given.count, comments: given.comments, page: given.page, has_more: given.has_more, me: given.me }); }
    }
    if ("IntersectionObserver" in window) {
      var io = new IntersectionObserver(function (entries) {
        if (entries.some(function (e) { return e.isIntersecting; })) { load(); io.disconnect(); }
      }, { rootMargin: "200px" });
      io.observe(commentsPanel);
    } else { load(); }
    $$("[data-social=comments]").forEach(function (a) { a.addEventListener("click", load); });

    // -- the comment's ⋮ menu (static/cardmenu.js's layer): Reply, Copy link; Edit…, Delete… on
    //    your own; Vote up, Vote down, Report comment… on others' -----------------------------------
    var Menu = window.MtgMenu, Box = window.MtgBox;
    var isForum = commentsPanel.hasAttribute("data-forum");
    var reportBox = document.getElementById("reportcomment");
    // the page's Report comment box, filled for this comment, then opened (its form posts to the
    // report route, which sends the member to the report's review page)
    var openReport = function (id, who, pg, back) {
      if (!reportBox || !Box) return;
      var set = function (name, value) { var i = reportBox.querySelector("[name='" + name + "']"); if (i) i.value = value; };
      set("comment_id", String(id)); set("page", String(pg || 1)); set("who", who || "");
      var t = reportBox.getAttribute("data-t"), h = reportBox.querySelector(".bh h2");
      if (t && h) h.textContent = t.replace("{name}", who || "this");
      var n = reportBox.querySelector(".boxnote"); if (n) { n.hidden = true; n.textContent = ""; }
      var ta = reportBox.querySelector("textarea"); if (ta) ta.value = "";
      Box.openElement(reportBox, { returnFocus: back || null });
    };
    var commentLink = function (id) { return location.origin + location.pathname + "#c" + id; };
    var menuFor = function (box, x, y) {
      if (!Menu || !box.__cmt) return;
      var c = box.__cmt.c, does = box.__cmt.does, pg = box.__cmt.pg;
      var who = c.owner.username || "someone";
      var items = [];
      if (does.reply) items.push(Menu.item("Reply", "comment", function () { does.reply.click(); }));
      items.push(Menu.item("Copy link", "link", function () { Menu.copyAndSay(commentLink(c.id), "Copied the comment's link."); }));
      if (does.own) {
        if (does.edit) items.push(Menu.item("Edit…", "edit", function () { does.edit.click(); }));
        if (does.del) { items.push(Menu.sep()); items.push(Menu.item("Delete…", "trash", function () { does.del.click(); }, { danger: true })); }
      } else {
        if (does.up) items.push(Menu.item(c.user_vote === 1 ? "Take back your vote" : "Vote up", "plus", function () { does.up.click(); }));
        if (does.down) items.push(Menu.item(c.user_vote === 2 ? "Take back your vote" : "Vote down", "minus", function () { does.down(); }));
        if (reportBox) { items.push(Menu.sep()); items.push(Menu.item("Report comment…", "flag", function () { openReport(c.id, who, pg, box); })); }
      }
      Menu.open({ head: who + "'s comment", label: "Menu for " + who + "'s comment", items: items, focusBack: box }, x, y);
    };
    if (Menu) Menu.bind(thread, {
      itemOf: function (t) {
        var box = t && t.closest ? t.closest(".cmt") : null;
        return box && thread.contains(box) && box.__cmt ? box : null;
      },
      skip: "button, input, select, textarea, form, label, a",
      button: ".cmt .who .kebab",
      open: menuFor
    });

    // -- the reply box's text style buttons: the marks go around the selected words -------------
    var mark = function (before, after, placeholder) {
      if (!textarea) return;
      var v = textarea.value, s = textarea.selectionStart, e = textarea.selectionEnd;
      var picked = v.slice(s, e) || placeholder;
      var out = before + picked + after;
      textarea.value = v.slice(0, s) + out + v.slice(e);
      textarea.focus();
      textarea.setSelectionRange(s + before.length, s + before.length + picked.length);
    };
    var askLink = function () {
      if (!Box) { mark("[", "](https://)", "link text"); return; }
      var s = textarea.selectionStart, e = textarea.selectionEnd, picked = textarea.value.slice(s, e);
      var wrap = el("div", "field");
      var lab = el("label", null, "Web address"); lab.htmlFor = "rt-url";
      var input = el("input"); input.type = "url"; input.id = "rt-url"; input.placeholder = "https://"; input.autocomplete = "off";
      wrap.appendChild(lab); wrap.appendChild(input);
      var b = Box.confirm({
        title: "Add a link", body: wrap, ok: "Add link", icon: "link", returnFocus: textarea,
        onOk: function (bx) {
          var url = input.value.trim();
          if (!/^https?:\/\/\S+$/i.test(url)) { bx.note("Give a web address that starts with http:// or https://.", true); return true; }
          textarea.setSelectionRange(s, e);
          mark("[", "](" + url + ")", picked || url.replace(/^https?:\/\//i, ""));
          return false;
        }
      });
      void b;
    };
    $$(".rt button[data-mark]", form || document.createElement("div")).forEach(function (btn) {
      btn.addEventListener("click", function () {
        var kind = btn.getAttribute("data-mark");
        if (kind === "bold") mark("**", "**", "bold words");
        else if (kind === "italic") mark("_", "_", "italic words");
        else askLink();
      });
    });
    // the marks drawn as they will appear (bold, italic, a link to its address): a DOM, never HTML
    var MARK_RE = /\*\*([^*\n]+?)\*\*|_([^_\n]+?)_|\[([^\]\n]+?)\]\(([^)\s]+?)\)/g;
    var hasMarks = function (text) {
      MARK_RE.lastIndex = 0;
      var m;
      while ((m = MARK_RE.exec(text))) { if (!m[4] || /^https?:\/\/\S+$/i.test(m[4])) return true; }
      return false;
    };
    var preview = function (text) {
      var out = el("p", "post-preview");
      var pos = 0, m;
      MARK_RE.lastIndex = 0;
      while ((m = MARK_RE.exec(text))) {
        if (m[4] && !/^https?:\/\/\S+$/i.test(m[4])) continue;  // not a web address: as typed
        out.appendChild(document.createTextNode(text.slice(pos, m.index)));
        if (m[1]) out.appendChild(el("strong", null, m[1]));
        else if (m[2]) out.appendChild(el("em", null, m[2]));
        else { var a = el("a", null, m[3]); a.href = m[4]; a.rel = "noopener noreferrer nofollow"; a.target = "_blank"; out.appendChild(a); }
        pos = m.index + m[0].length;
      }
      out.appendChild(document.createTextNode(text.slice(pos)));
      return out;
    };
    var postIt = function (text, after) {
      var body = { text: text, parent: parentId, page: parentPage };
      if (isForum && hasMarks(text)) body.marks = true;
      return socialRequest("POST", "/social/api/decks/" + encodeURIComponent(commentsPanel.getAttribute("data-deck")) + "/comments", body).then(after);
    };
    window.MtgComments = { replyTo: replyingTo, report: openReport, preview: preview, hasMarks: hasMarks };

    if (form) form.addEventListener("submit", function (e) {
      e.preventDefault();
      var text = textarea.value.trim();
      if (!text) {
        // one wording with the assistant's (refusals.EMPTY_COMMENT)
        var was = $(".notice", form); if (was) was.remove();
        var empty = el("p", "notice error", "The comment is empty: write it first.");
        empty.setAttribute("role", "alert");
        form.insertBefore(empty, form.firstChild);
        textarea.focus();
        return;
      }
      var stale = $(".notice", form); // the comment has words now: the empty notice goes
      if (stale) stale.remove();
      var old = $(".confirmbar", form);
      if (old) old.remove();
      var landed = function (d) {
        var old = $(".notice", form); if (old) old.remove();  // one notice at a time
        if (!d.ok) { var p = el("p", "notice error"); p.setAttribute("role", "alert"); p.appendChild(socialProblem(d)); form.insertBefore(p, form.firstChild); return; }
        var node = render(d.comment, parentId ? parentPage : 1);
        var parentBox = parentId ? $("[data-id='" + parentId + "']", thread) : null;
        if (parentBox) {
          var kids = $(".replies", parentBox) || parentBox.appendChild(el("div", "replies"));
          kids.appendChild(node);
        } else {
          var empty = $("p.muted", thread); if (empty && /No (comments|replies) yet/.test(empty.textContent)) empty.remove();
          thread.insertBefore(node, thread.firstChild);
        }
        textarea.value = ""; parentId = null; replyTo.hidden = true;
        // the count is of comments on the deck, not replies (Archidekt's thread count)
        if (countEl && !parentBox) { var n = (parseInt(countEl.textContent, 10) || 0) + 1; countEl.textContent = n === 1 ? "1 comment" : n + " comments"; }
      };
      var lost = function () {
        var old = $(".notice", form); if (old) old.remove();
        var p = el("p", "notice error", "The comment was not posted: no connection. Your text is still in the box.");
        p.setAttribute("role", "alert");
        form.insertBefore(p, form.firstChild);
      };
      if (isForum && Box) {
        // the forum's reply: shown as it will appear, asked once more (the mockup's Post this reply?)
        var name = form.getAttribute("data-me-name") || commentsPanel.getAttribute("data-me-name") || "";
        var wrap = el("div");
        wrap.appendChild(preview(text));
        wrap.appendChild(el("p", "muted small", "Posted publicly under “" + (name || "your Archidekt name") + "” in this thread on Archidekt. You can edit or delete it afterwards."));
        var sending = false;
        Box.confirm({
          title: "Post this reply?", body: wrap, ok: "Post reply", icon: "send", cancel: "Keep editing", returnFocus: textarea,
          onOk: function () {
            if (sending) return true;
            sending = true;
            return postIt(text, landed).then(function () { return false; }, function () { lost(); return false; });
          }
        });
        return;
      }
      var bar = el("div", "confirmbar");
      bar.appendChild(document.createTextNode("Post this comment publicly on Archidekt?"));
      var yes = el("button", "btn-primary", "Post");
      yes.type = "button";
      var no = el("button", "", "Cancel");
      no.type = "button";
      bar.appendChild(yes); bar.appendChild(no);
      form.insertBefore(bar, form.querySelector("button[type=submit]"));
      yes.focus(); // as every other confirm here: the answer is one key away
      no.addEventListener("click", function () { bar.remove(); textarea.focus(); });
      yes.addEventListener("click", function () {
        yes.disabled = true;
        postIt(text, function (d) { bar.remove(); landed(d); }).catch(function () { bar.remove(); lost(); });
      });
    });
  }
})();
