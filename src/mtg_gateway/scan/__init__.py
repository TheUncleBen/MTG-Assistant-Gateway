"""Card scanning: resolve physical cards to exact Scryfall cards and keep scan sessions.

Two ways in:

* **Assistant path.** The person photographs cards inside Claude or ChatGPT; the
  assistant reads the names and calls ``resolve_cards``. The tool turns names
  (optionally set code and collector number) into exact cards, flags fuzzy and
  ambiguous matches, and returns a decklist plus ready-made ``changes`` for
  ``propose_deck_changes``.
* **Gateway path.** ``/scan`` is a mobile-first page, installable as a web app,
  behind the gateway's browser login. It uses the phone camera, reads the card
  title on the device (Tesseract.js, WebAssembly, no server-side OCR), confirms
  each card against Scryfall through the gateway, and saves a scan session that
  the assistant fetches with ``get_scan_session``.

Scryfall is the only external service; the gateway talks to it server-side with
the headers its API guidelines require and a 100 ms pacer, so phones never call
Scryfall directly and the Pi is not loaded with image work.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from .art import ArtIndex, ArtSettings
from .routes import add_scan_routes
from .scryfall import ScryfallClient
from .service import ScanService, ScanThresholds
from .store import ScanStore
from .tools import add_scan_tools

if TYPE_CHECKING:
    from mcp.server.mcpserver import MCPServer

    from ..app import AppState

__all__ = ["ArtIndex", "ScanService", "ScanStore", "ScryfallClient", "add_scan"]


def add_scan(server: MCPServer, state: AppState, *, scryfall: ScryfallClient | None = None) -> ScanService:
    """Register the scan tools and browser routes. Returns the service so tests can swap clients."""
    st = state.settings
    client = scryfall or ScryfallClient(
        user_agent=st.archidekt_user_agent,
        lookup_interval=getattr(st, "scryfall_lookup_interval", 0.5),
    )
    art = ArtIndex(
        state.db,
        client,
        ArtSettings(
            enabled=getattr(st, "scan_art_enabled", True),
            max_distance=getattr(st, "scan_art_max_distance", 380),
            min_margin=getattr(st, "scan_art_min_margin", 40),
            max_printings=getattr(st, "scan_art_max_printings", 250),
            image_interval=getattr(st, "scan_art_image_interval", 0.1),
            images_per_hour=getattr(st, "scan_art_images_per_hour", 1500),
            max_galleries=getattr(st, "scan_art_max_galleries", 500),
            gallery_idle_days=getattr(st, "scan_art_gallery_idle_days", 180),
        ),
    )
    service = ScanService(
        state.db,
        client,
        art_matcher=art.match,
        thresholds=ScanThresholds(
            fuzzy_min_similarity=getattr(st, "scan_fuzzy_min_similarity", 0.65),
            fuzzy_confident_similarity=getattr(st, "scan_fuzzy_confident_similarity", 0.8),
            ambiguity_margin=getattr(st, "scan_ambiguity_margin", 0.05),
            auto_add_confidence=getattr(st, "scan_auto_add_confidence", 80.0),
            foil_star_ink_ratio=getattr(st, "scan_foil_star_ink_ratio", 0.09),
            glare_ratio=getattr(st, "scan_glare_ratio", 0.08),
            min_ocr_confidence=getattr(st, "scan_min_ocr_confidence", 50.0),
        ),
    )
    service.art = art  # type: ignore[attr-defined]
    state.scan = service  # type: ignore[attr-defined]
    add_scan_tools(server, service)
    add_scan_routes(server, state, service)
    return service
