/* Archidekt social actions on the gateway's pages: like, bookmark and follow in a deck's banner,
   Follow on an Archidekt user page, and a deck's comments. Moved out of deck.js unchanged in 0.7.29;
   the pages work without it (every control is a form). */
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
        var down = likeBtn.getAttribute("data-state") === "2"; // Q141 item 5A: a down vote is taken back
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
    // it (a status note added already filled is often not read: proofread of WORDING-5, 15)
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
    var extra = null; // Q141 item 4A: an Unfollow beside Follow while the state is not known
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
        // offer both, rather than a Follow that may be wrong (wording draft, WORDING-4)
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
    var form = $("form.newcomment", commentsPanel);
    var textarea = form ? $("textarea", form) : null;
    var replyTo = form ? $(".replyto", form) : null;
    var parentId = null, parentPage = 1;  // the comment replied to, and the thread page it is on (A29)
    var me = null;  // the member's Archidekt user id, from the thread load; own comments get Edit and Delete
    var deckUrl = "/social/api/decks/" + encodeURIComponent(commentsPanel.getAttribute("data-deck")) + "/comments";
    var when = function (iso) {  // one date format across the site (feedback.js)
      if (window.MtgText) return window.MtgText.when(iso);
      var d = new Date(iso);
      return isNaN(d) ? "" : d.toLocaleDateString(undefined, { year: "numeric", month: "short", day: "numeric" });
    };
    // pg: the page of the thread the comment came on, sent with every write so the gateway finds it there
    var render = function (c, pg) {
      pg = pg || 1;
      var box = el("article", "cmt");
      box.setAttribute("data-id", c.id);
      var who = el("div", "who");
      var b = el("b", "", c.owner.username || "someone");
      who.appendChild(b);
      who.appendChild(document.createTextNode(" · " + when(c.created_at) + (c.edited_at ? " · edited" : "")));
      var pts = el("span", "pts");
      var showPoints = function () { pts.textContent = c.points ? " · " + c.points + (c.points === 1 || c.points === -1 ? " point" : " points") : ""; };
      showPoints();
      who.appendChild(pts);
      box.appendChild(who);
      var p = el("p", "", c.text);
      box.appendChild(p);
      var acts = el("div", "acts");
      var reply = el("button", "btn-ghost", "Reply");
      reply.type = "button";
      reply.setAttribute("aria-label", "Reply to " + (c.owner.username || "this comment"));
      reply.addEventListener("click", function () {
        parentId = c.id; parentPage = pg;
        replyTo.hidden = false;
        replyTo.textContent = "";
        replyTo.appendChild(document.createTextNode("Replying to " + (c.owner.username || "this comment") + " "));
        var cancel = el("button", "", "Cancel");
        cancel.type = "button";
        cancel.addEventListener("click", function () { parentId = null; replyTo.hidden = true; });
        replyTo.appendChild(cancel);
        textarea.focus();
      });
      if (canWrite) acts.appendChild(reply);
      // votes on other people's comments (Archidekt's comment vote), asked first like the deck's Like
      if (canWrite && me !== null && c.id && !(c.owner && c.owner.id === me)) {
        var voteBtn = function (up) {
          var btn = el("button", "btn-ghost vote", up ? "▲ Up" : "▼ Down");
          btn.type = "button";
          var mine = function () { return c.user_vote === (up ? 1 : 2); };
          var label = function () {
            btn.setAttribute("aria-pressed", mine() ? "true" : "false");
            btn.setAttribute("aria-label", (mine() ? "Take back your " : "Vote ") + (up ? "up" : "down") + ": comment by " + (c.owner.username || "someone"));
          };
          label();
          btn.addEventListener("click", function () {
            var undoing = mine();
            confirmChip(btn, undoing ? "Take back your vote?" : (up ? "Vote this comment up on Archidekt?" : "Vote this comment down on Archidekt?"), undoing ? "Take back" : "Vote", function () {
              btn.disabled = true;
              socialRequest("POST", deckUrl + "/" + encodeURIComponent(c.id) + "/vote", { vote: undoing ? "none" : (up ? "up" : "down"), page: pg }).then(function (d) {
                btn.disabled = false;
                if (!d.ok) { var n = el("p", "notice error"); n.appendChild(socialProblem(d)); acts.appendChild(n); setTimeout(function () { n.remove(); }, 8000); return; }
                c.user_vote = d.vote; c.points = d.points;
                showPoints();
                Array.prototype.forEach.call(acts.querySelectorAll("button.vote"), function (x) { if (x.relabel) x.relabel(); });
              }).catch(function () {
                btn.disabled = false;
                var n = el("p", "notice error", "No connection; the vote was not sent."); n.setAttribute("role", "alert");
                acts.appendChild(n); setTimeout(function () { n.remove(); }, 8000);
              });
            });
          });
          btn.relabel = label;
          return btn;
        };
        acts.appendChild(voteBtn(true));
        acts.appendChild(voteBtn(false));
      }
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
            // one wording with the assistant's (Q141 item 7A, refusals.EMPTY_COMMENT)
            if (!text) { note.textContent = "The comment is empty: write it first."; ta.focus(); return; }
            note.textContent = "";
            // asked first, like every other write (social review, 0.7.29)
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
        acts.appendChild(edit);
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
              if (!$(".cmt", thread)) thread.appendChild(el("p", "muted", "No comments yet. Be the first."));
            }).catch(function () { yes.disabled = false; bar.textContent = "No connection; the comment was not deleted."; });
          });
          box.insertBefore(bar, acts.nextSibling);
          yes.focus();
        });
        acts.appendChild(del);
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
      socialRequest("GET", thread.getAttribute("data-src")).then(function (d) {
        if (!d.ok) {
          if (d.error === "not_linked" || d.error === "auth") { thread.textContent = ""; var p = el("p", "muted"); p.appendChild(socialProblem(d)); thread.appendChild(p); return; }
          failed(d.message || "The comments could not be loaded.");  // Archidekt busy: offer another try
          return;
        }
        thread.textContent = "";
        me = typeof d.me === "number" ? d.me : null;
        if (countEl) countEl.textContent = d.count === 1 ? "1 comment" : d.count + " comments";
        if (!d.comments.length) { thread.appendChild(el("p", "muted", "No comments yet. Be the first.")); return; }
        d.comments.forEach(function (c) { thread.appendChild(render(c, 1)); });
        if (d.has_more) showMore(2);
      }).catch(function () { failed("The comments could not be loaded: no connection."); });
    };
    // The next page of the thread, added under the comments already shown (A29: every page can be
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
    if ("IntersectionObserver" in window) {
      var io = new IntersectionObserver(function (entries) {
        if (entries.some(function (e) { return e.isIntersecting; })) { load(); io.disconnect(); }
      }, { rootMargin: "200px" });
      io.observe(commentsPanel);
    } else { load(); }
    $$("[data-social=comments]").forEach(function (a) { a.addEventListener("click", load); });
    if (form) form.addEventListener("submit", function (e) {
      e.preventDefault();
      var text = textarea.value.trim();
      if (!text) {
        // one wording with the assistant's (Q141 item 7A, refusals.EMPTY_COMMENT)
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
        socialRequest("POST", "/social/api/decks/" + encodeURIComponent(commentsPanel.getAttribute("data-deck")) + "/comments", { text: text, parent: parentId, page: parentPage }).then(function (d) {
          bar.remove();
          var old = $(".notice", form); if (old) old.remove();  // one notice at a time
          if (!d.ok) { var p = el("p", "notice error"); p.setAttribute("role", "alert"); p.appendChild(socialProblem(d)); form.insertBefore(p, form.firstChild); return; }
          var node = render(d.comment, parentId ? parentPage : 1);
          var parentBox = parentId ? $("[data-id='" + parentId + "']", thread) : null;
          if (parentBox) {
            var kids = $(".replies", parentBox) || parentBox.appendChild(el("div", "replies"));
            kids.appendChild(node);
          } else {
            var empty = $("p.muted", thread); if (empty && /No comments yet/.test(empty.textContent)) empty.remove();
            thread.insertBefore(node, thread.firstChild);
          }
          textarea.value = ""; parentId = null; replyTo.hidden = true;
          // the count is of comments on the deck, not replies (Archidekt's thread count)
          if (countEl && !parentBox) { var n = (parseInt(countEl.textContent, 10) || 0) + 1; countEl.textContent = n === 1 ? "1 comment" : n + " comments"; }
        }).catch(function () {
          bar.remove();
          var old = $(".notice", form); if (old) old.remove();
          var p = el("p", "notice error", "The comment was not posted: no connection. Your text is still in the box.");
          p.setAttribute("role", "alert");
          form.insertBefore(p, form.firstChild);
        });
      });
    });
  }
})();
