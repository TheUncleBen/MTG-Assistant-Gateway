"""Archidekt requests for a member's account-wide lists and community actions (0.7.43, Archidekt
parity release 2): category templates, personal default categories, the color tag list, pinned
cards, custom cards and their pictures, card packages, deck help requests, reports, the deck
registration PDF, the profile and site preferences, notifications and contest entries.

Every route and body here is what archidekt.com's own pages send, read from the site's public
scripts on 2026-10-22 (the parity research, REQUESTS.md); none was exercised live when it was
written. So, as for the deck writes, no answer is trusted beyond its basic shape: every caller
reads back what it changed and reports a mismatch. The first live runs are on the owner's TEST
deck and account."""

from __future__ import annotations

import re
from typing import TYPE_CHECKING, Any

from .archidekt import DECK_ID_RE, ArchidektError, rich_doc

if TYPE_CHECKING:
    from .archidekt import ArchidektClient

HEX_COLOR_RE = re.compile(r"#[0-9a-fA-F]{6}")
# The report kinds Archidekt's pages send (the site's report box, type field)
REPORT_TYPES = ("Deck", "Card", "Custom card", "Comment")
# The custom card fields the site's form sends (its card builder); everything else is dropped
CUSTOM_CARD_FIELDS = (
    "cmc",
    "frontName",
    "frontManaCost",
    "frontText",
    "frontPower",
    "frontToughness",
    "frontLoyalty",
    "frontTypes",
    "frontSubTypes",
    "frontSuperTypes",
    "frontImageUrl",
    "frontArtistName",
    "hasBack",
    "backName",
    "backManaCost",
    "backText",
    "backPower",
    "backToughness",
    "backLoyalty",
    "backTypes",
    "backSubTypes",
    "backSuperTypes",
    "backImageUrl",
    "backArtistName",
    "setCode",
    "collectorNumber",
)
# The keys of a member's private profile the gateway may change, by group (the site's account
# settings and collection pages); Archidekt's password, email, account deletion and Patreon
# routes are never sent from the gateway (R-085)
PROFILE_KEYS = ("bio", "avatar", "contactable", "newsletterable", "comment_notification_emailable")
COLLECTION_KEYS = ("is_collection_public",)
ACCOUNT_SETTING_KEYS = (
    "deckView",
    "deckStack",
    "deckSorting",
    "deckPrices",
    "searchUnique",
    "noUseDefaultCategories",
    "searchGame",
    "show_custom_uploads",
    "noArchidektNotifications",
)


def _id(value: Any, what: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
        raise ArchidektError("contract", f"{what} is not a number")
    return value


def _deck(deck_id: Any) -> str:
    if not DECK_ID_RE.fullmatch(str(deck_id)):
        raise ArchidektError("contract", "deck id is not a number")
    return str(deck_id)


def _rows(body: Any, *keys: str) -> list[dict[str, Any]]:
    """The list in ``body`` (itself, or under the first of ``keys`` it has), dicts only."""
    rows = body
    if isinstance(body, dict):
        rows = next((body[k] for k in keys if isinstance(body.get(k), list)), None)
    if not isinstance(rows, list):
        raise ArchidektError("contract", "unexpected list shape")
    return [r for r in rows if isinstance(r, dict)]


class AccountRequests:
    """Mixed into ArchidektClient: one method per request, each checking what it sends."""

    # -- category templates: /users/category/ ------------------------------------------------

    async def templates(self: ArchidektClient, token: str) -> list[dict[str, Any]]:
        return _rows(await self._request("GET", "/users/category/", token=token, fresh=True), "results")

    @staticmethod
    def _template_body(fields: dict[str, Any]) -> dict[str, Any]:
        name = fields.get("name")
        if not isinstance(name, str) or not name.strip():
            raise ArchidektError("contract", "template name is empty")
        flags = ("includedInDeck", "includedInPrice", "isPremier")
        if any(not isinstance(fields.get(k), bool) for k in flags):
            raise ArchidektError("contract", "template switches must be true or false")
        return {"name": name, **{k: fields[k] for k in flags}}

    async def create_template(self: ArchidektClient, token: str, fields: dict[str, Any]) -> Any:
        body = {**AccountRequests._template_body(fields), "id": None}
        return await self._request("POST", "/users/category/", token=token, json_body=body)

    async def update_template(
        self: ArchidektClient, token: str, template_id: int, fields: dict[str, Any]
    ) -> Any:
        tid = _id(template_id, "template id")
        body = {**AccountRequests._template_body(fields), "id": tid}
        return await self._request("PATCH", f"/users/category/{tid}/", token=token, json_body=body)

    async def delete_template(self: ArchidektClient, token: str, template_id: int) -> None:
        await self._request("DELETE", f"/users/category/{_id(template_id, 'template id')}/", token=token)

    # -- personal default categories: /users/globalCategory/ ------------------------------------

    async def default_categories(self: ArchidektClient, token: str, page: int = 1) -> dict[str, Any]:
        """``{total, next, previous, categories, cards}``; 100 cards a page, as the site lists them."""
        body = await self._request(
            "GET", "/users/globalCategory/", token=token, params={"page": max(1, int(page))}, fresh=True
        )
        if not isinstance(body, dict) or not isinstance(body.get("cards"), list):
            raise ArchidektError("contract", "unexpected default categories shape")
        return body

    async def set_default_categories(
        self: ArchidektClient, token: str, oracle_card_id: int, names: list[str]
    ) -> Any:
        """The card's whole list, first name = its main category (the site's star)."""
        if not isinstance(names, list) or any(not isinstance(n, str) or not n.strip() for n in names):
            raise ArchidektError("contract", "category names must be text")
        oid = _id(oracle_card_id, "card id")
        return await self._request(
            "POST", f"/users/globalCategory/oracleCard/{oid}/", token=token, json_body={"categories": names}
        )

    # -- the color tag list: /decks/colorTags/ ---------------------------------------------------

    async def color_tags(self: ArchidektClient, token: str) -> list[dict[str, Any]]:
        return _rows(await self._request("GET", "/decks/colorTags/", token=token, fresh=True), "results")

    @staticmethod
    def _tag_body(name: Any, color: Any) -> dict[str, Any]:
        if not isinstance(name, str) or not name.strip() or len(name) > 32:
            raise ArchidektError("contract", "color tag name must be 1 to 32 characters")
        if not isinstance(color, str) or not HEX_COLOR_RE.fullmatch(color):
            raise ArchidektError("contract", "color must be a hex color like #fa890d")
        return {"name": name, "color": color}

    async def create_color_tag(self: ArchidektClient, token: str, name: str, color: str) -> Any:
        body = AccountRequests._tag_body(name, color)
        return await self._request("POST", "/decks/colorTags/", token=token, json_body=body)

    async def update_color_tag(self: ArchidektClient, token: str, tag: dict[str, Any]) -> Any:
        """The whole tag object with its new name and color, as the site patches it."""
        tid = _id(tag.get("id"), "color tag id")
        body = {**tag, **AccountRequests._tag_body(tag.get("name"), tag.get("color")), "id": tid}
        return await self._request("PATCH", f"/decks/colorTags/{tid}/", token=token, json_body=body)

    async def delete_color_tag(self: ArchidektClient, token: str, tag_id: int) -> None:
        await self._request("DELETE", f"/decks/colorTags/{_id(tag_id, 'color tag id')}/", token=token)

    # -- pinned cards: /users/pins/ ----------------------------------------------------------------

    async def pins(self: ArchidektClient, token: str) -> list[dict[str, Any]]:
        return _rows(await self._request("GET", "/users/pins/", token=token, fresh=True), "cards")

    async def pin_cards(self: ArchidektClient, token: str, cards: list[dict[str, Any]]) -> Any:
        """``cards``: ``{cardId, notes, modifier, quantity}`` each (Pin card sends one, quantity 1)."""
        body = []
        for c in cards:
            qty = c.get("quantity")
            if isinstance(qty, bool) or not isinstance(qty, int) or qty < 1:
                raise ArchidektError("contract", "pin quantity must be 1 or more")
            body.append(
                {
                    "cardId": _id(c.get("cardId"), "card id"),
                    "notes": str(c.get("notes") or ""),
                    "modifier": str(c.get("modifier") or "Normal"),
                    "quantity": qty,
                }
            )
        if not body:
            raise ArchidektError("contract", "no cards to pin")
        return await self._request("POST", "/users/pins/", token=token, json_body={"cards": body})

    async def update_pin(
        self: ArchidektClient,
        token: str,
        pin_id: int,
        *,
        card_id: int,
        quantity: int,
        notes: str,
        modifier: str,
    ) -> Any:
        if isinstance(quantity, bool) or not isinstance(quantity, int) or quantity < 1:
            raise ArchidektError("contract", "pin quantity must be 1 or more (remove the pin instead)")
        body = {
            "card": _id(card_id, "card id"),
            "quantity": quantity,
            "notes": str(notes),
            "modifier": modifier,
        }
        return await self._request(
            "PATCH", f"/users/pins/{_id(pin_id, 'pin id')}/", token=token, json_body=body
        )

    async def delete_pin(self: ArchidektClient, token: str, pin_id: int) -> None:
        await self._request("DELETE", f"/users/pins/{_id(pin_id, 'pin id')}/", token=token)

    async def delete_all_pins(self: ArchidektClient, token: str) -> None:
        await self._request("DELETE", "/users/pins/deleteAll/", token=token)

    # -- custom cards: /decks/customCards/ and the picture upload --------------------------------

    async def custom_cards(
        self: ArchidektClient, token: str, search: str = "", page: int = 1
    ) -> dict[str, Any]:
        """``{count, next, previous, results}``, by name as the site lists them."""
        params = {
            "search": search,
            "orderBy": "name",
            "orderDirection": "ascending",
            "page": max(1, int(page)),
        }
        body = await self._request("GET", "/decks/customCards/", token=token, params=params, fresh=True)
        if not isinstance(body, dict) or not isinstance(body.get("results"), list):
            raise ArchidektError("contract", "unexpected custom card list shape")
        return body

    @staticmethod
    def _custom_body(card: dict[str, Any], owner: int) -> dict[str, Any]:
        body: dict[str, Any] = {k: card.get(k) for k in CUSTOM_CARD_FIELDS}
        for k, v in list(body.items()):
            if isinstance(v, str) and not v.strip():
                body[k] = None  # the site sends an empty field as null
        if not body.get("frontName"):
            raise ArchidektError("contract", "custom cards need a front name")
        if not any(body.get(k) for k in ("frontTypes", "frontSubTypes", "frontSuperTypes")):
            raise ArchidektError("contract", "custom cards need at least one type")
        body["hasBack"] = bool(body.get("hasBack"))
        if not body["hasBack"]:
            for k in CUSTOM_CARD_FIELDS:
                if k.startswith("back"):
                    body[k] = None
        cmc = body.get("cmc")
        body["cmc"] = cmc if isinstance(cmc, (int, float)) and not isinstance(cmc, bool) and cmc >= 0 else 0
        body["owner"] = _id(owner, "user id")
        return body

    async def create_custom_card(self: ArchidektClient, token: str, card: dict[str, Any], owner: int) -> Any:
        body = {**AccountRequests._custom_body(card, owner), "id": None}
        resp = await self._request(
            "POST", "/decks/customCards/", token=token, json_body={"customCards": [body]}
        )
        if isinstance(resp, dict) and isinstance(resp.get("customCards"), list) and resp["customCards"]:
            return resp["customCards"][0]
        return resp

    async def update_custom_card(
        self: ArchidektClient, token: str, card_id: int, card: dict[str, Any], owner: int
    ) -> Any:
        cid = _id(card_id, "custom card id")
        body = {**AccountRequests._custom_body(card, owner), "id": cid}
        return await self._request("PATCH", f"/decks/customCards/{cid}/", token=token, json_body=body)

    async def delete_custom_card(self: ArchidektClient, token: str, card_id: int) -> None:
        await self._request("DELETE", f"/decks/customCards/{_id(card_id, 'custom card id')}/", token=token)

    async def upload_image(self: ArchidektClient, token: str, png: bytes) -> str:
        """PUT /users/uploadImage/ with the picture as the raw body, named tmp.png as the site
        names its redrawn 500 x 700 crop. The answer's ``imageUrl``."""
        resp = await self._request("PUT", "/users/uploadImage/", token=token, upload=(png, "tmp.png"))
        url = resp.get("imageUrl") if isinstance(resp, dict) else None
        if not isinstance(url, str) or not url.startswith("https://"):
            raise ArchidektError("contract", "Archidekt did not answer the upload with a picture address")
        return url

    # -- card packages ---------------------------------------------------------------------------

    async def create_package(self: ArchidektClient, token: str, fields: dict[str, Any]) -> dict[str, Any]:
        """POST /decks/packages/: a package is a deck with a ``cardPackage`` block (keywords
        required, budget 1 to 4 or none, intended format ids)."""
        name = fields.get("name")
        pkg = fields.get("cardPackage") or {}
        if not isinstance(name, str) or not name.strip():
            raise ArchidektError("contract", "package name is empty")
        if not isinstance(pkg.get("keywords"), str) or not pkg["keywords"].strip():
            raise ArchidektError("contract", "package keywords are required")
        budget = pkg.get("budget")
        if budget is not None and budget not in (1, 2, 3, 4):
            raise ArchidektError("contract", "budget must be 1 to 4 or none")
        formats = pkg.get("intendedFormats") or []
        if any(isinstance(f, bool) or not isinstance(f, int) for f in formats):
            raise ArchidektError("contract", "intended formats must be format ids")
        body: dict[str, Any] = {
            "name": name,
            "deckFormat": None,
            "description": "",
            "featured": "",
            "playmat": "",
            "private": bool(fields.get("private")),
            "cardPackage": {"keywords": pkg["keywords"], "budget": budget, "intendedFormats": list(formats)},
        }
        if fields.get("parent_folder") is not None:
            body["parent_folder"] = _id(fields["parent_folder"], "folder id")
        resp = await self._request("POST", "/decks/packages/", token=token, json_body=body)
        if not isinstance(resp, dict) or not str(resp.get("id") or "").isdigit():
            raise ArchidektError("contract", "Archidekt did not answer with the new package's id")
        return resp

    async def update_package(
        self: ArchidektClient, token: str, package_id: int, block: dict[str, Any]
    ) -> Any:
        """PATCH /decks/packages/{cardPackage id}/ with the whole block (the id is the package
        block's own, not the deck's)."""
        pid = _id(package_id, "package id")
        return await self._request("PATCH", f"/decks/packages/{pid}/", token=token, json_body=block)

    async def packages_list(
        self: ArchidektClient,
        token: str,
        *,
        owner: str | None = None,
        bookmarks: bool = False,
        name: str = "",
        intended_format: int | None = None,
        budget: int | None = None,
        page: int = 1,
    ) -> dict[str, Any]:
        """``{count, next, results}``: card packages, most imported first, as the site's Packages
        page asks for them (``packages=true``): ``owner`` for one member's ("mine"), ``bookmarks``
        for the ones the member follows, neither for everyone's; ``name``, ``intended_format`` (a
        format id) and ``budget`` (1 to 4) are the page's filters."""
        params = {
            "name": name,
            "orderBy": "-importCount",
            "intendedFormat": "" if intended_format is None else str(int(intended_format)),
            "budget": "" if budget is None else str(int(budget)),
            "packages": "true",
            "ascending": "false",
            "page": str(int(page)),
        }
        if owner is not None:
            params["ownerUsername"] = owner
        if bookmarks:
            params["bookmarks"] = "true"
        body = await self._request("GET", "/decks/v3/", token=token, params=params, fresh=True)
        if not isinstance(body, dict) or not isinstance(body.get("results"), list):
            raise ArchidektError("contract", "unexpected package list shape")
        return body

    async def package_cards(self: ArchidektClient, token: str, package_deck_id: str) -> dict[str, Any]:
        """GET /decks/{id}/?incrementPackageImport=true: the package's cards, read as the site
        reads them when one is added to a deck, which counts one import of the package. Sent
        only when the member's change is applied, never for a preview."""
        did = _deck(package_deck_id)
        body = await self._request(
            "GET", f"/decks/{did}/", token=token, params={"incrementPackageImport": "true"}, fresh=True
        )
        if not isinstance(body, dict) or not isinstance(body.get("cards"), list):
            raise ArchidektError("contract", "unexpected package shape")
        return body

    # -- deck help requests ----------------------------------------------------------------------

    async def create_deck_help(
        self: ArchidektClient, token: str, deck_id: str, goals: str, issues: str
    ) -> Any:
        body = {"active": True, "goals": rich_doc(goals), "issues": rich_doc(issues)}
        return await self._request("POST", f"/decks/{_deck(deck_id)}/help/", token=token, json_body=body)

    async def update_deck_help(
        self: ArchidektClient, token: str, help_id: int, goals: str, issues: str, *, active: bool
    ) -> Any:
        """Edit, or delist with ``active`` false, as the site does (it never sends its DELETE)."""
        body = {"active": bool(active), "goals": rich_doc(goals), "issues": rich_doc(issues)}
        return await self._request(
            "PATCH", f"/decks/help/{_id(help_id, 'help request id')}/", token=token, json_body=body
        )

    # -- reports to Archidekt's moderators -------------------------------------------------------

    async def report(self: ArchidektClient, token: str, url: str, kind: str, message: str) -> Any:
        if kind not in REPORT_TYPES:
            raise ArchidektError("contract", "unknown report kind")
        if not url.startswith("https://archidekt.com/"):
            raise ArchidektError("contract", "a report names a page on archidekt.com")
        if not message.strip():
            raise ArchidektError("contract", "a report needs a message")
        body = {"url": url, "type": kind, "message": message}
        return await self._request("POST", "/users/moderator/report/", token=token, json_body=body)

    # -- the deck registration PDF ---------------------------------------------------------------

    async def registration_pdf(self: ArchidektClient, token: str, fields: dict[str, Any]) -> str:
        """POST /decks/exportPdf/ with the flat fields the site's form sends; the answer's
        ``fileUrl``. Creates nothing on the member's account."""
        resp = await self._request("POST", "/decks/exportPdf/", token=token, json_body=fields)
        url = resp.get("fileUrl") if isinstance(resp, dict) else None
        if not isinstance(url, str) or not url.startswith("https://"):
            raise ArchidektError("contract", "Archidekt did not answer with the PDF's address")
        return url

    # -- the member's private profile: /users/{id}/private/ ------------------------------------

    async def user_private(self: ArchidektClient, token: str, user_id: int) -> dict[str, Any]:
        uid = _id(user_id, "user id")
        body = await self._request("GET", f"/users/{uid}/private/", token=token, fresh=True)
        if not isinstance(body, dict):
            raise ArchidektError("contract", "unexpected profile shape")
        return body

    async def patch_user_private(
        self: ArchidektClient,
        token: str,
        user_id: int,
        *,
        profile: dict[str, Any] | None = None,
        settings: dict[str, Any] | None = None,
        username: str | None = None,
    ) -> dict[str, Any]:
        """One group a call, as the site sends them; only the known keys go."""
        body: dict[str, Any] = {}
        if profile:
            bad = set(profile) - set(PROFILE_KEYS) - set(COLLECTION_KEYS)
            if bad:
                raise ArchidektError("contract", f"unknown profile keys: {sorted(bad)}")
            body["profile"] = dict(profile)
        if settings:
            bad = set(settings) - set(ACCOUNT_SETTING_KEYS)
            if bad:
                raise ArchidektError("contract", f"unknown account setting keys: {sorted(bad)}")
            body["accountSettings"] = dict(settings)
        if username is not None:
            if not username.strip():
                raise ArchidektError("contract", "username is empty")
            body["username"] = username
        if not body:
            raise ArchidektError("contract", "nothing to change")
        uid = _id(user_id, "user id")
        resp = await self._request("PATCH", f"/users/{uid}/private/", token=token, json_body=body)
        return resp if isinstance(resp, dict) else {}

    async def upload_avatar(
        self: ArchidektClient, token: str, user_id: int, picture: bytes, name: str
    ) -> Any:
        uid = _id(user_id, "user id")
        return await self._request("PUT", f"/users/uploadAvatar/{uid}/", token=token, upload=(picture, name))

    # -- notifications (reads only: Archidekt has no mark-as-read) -------------------------------

    async def notification_count(self: ArchidektClient, token: str, user_id: int) -> int:
        uid = _id(user_id, "user id")
        body = await self._request("GET", f"/users/{uid}/notificationCount/", token=token, fresh=True)
        n = body.get("notificationCount") if isinstance(body, dict) else None
        if isinstance(n, bool) or not isinstance(n, int) or n < 0:
            raise ArchidektError("contract", "unexpected notification count shape")
        return n

    async def notifications(self: ArchidektClient, token: str, user_id: int) -> list[dict[str, Any]]:
        uid = _id(user_id, "user id")
        body = await self._request("GET", f"/users/{uid}/notifications/", token=token, fresh=True)
        return _rows(body, "notifications")

    # -- contest entries -------------------------------------------------------------------------

    async def contest_entries(
        self: ArchidektClient, token: str | None, thread_id: int
    ) -> list[dict[str, Any]]:
        tid = _id(thread_id, "thread id")
        body = await self._request(
            "GET", f"/comments/contestSubmission/decks/{tid}/", token=token, fresh=True
        )
        return _rows(body, "results")

    async def contest_submit(
        self: ArchidektClient, token: str, owner: int, thread_id: int, deck_id: str
    ) -> Any:
        body = {
            "owner": _id(owner, "user id"),
            "parentPost": _id(thread_id, "thread id"),
            "deck": int(_deck(deck_id)),
        }
        return await self._request("POST", "/comments/contestSubmission/", token=token, json_body=body)

    async def contest_withdraw(self: ArchidektClient, token: str, submission_id: int) -> None:
        sid = _id(submission_id, "entry id")
        await self._request("DELETE", f"/comments/contestSubmission/delete/{sid}/", token=token)
