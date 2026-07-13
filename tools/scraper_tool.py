from __future__ import annotations

import asyncio
import random
import re
import urllib.parse
from typing import Any

from playwright.async_api import async_playwright

# ─── Anti-bot helpers ─────────────────────────────────────────────────────────

_USER_AGENTS = [
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/123.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.4.1 Safari/605.1.15",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:125.0) Gecko/20100101 Firefox/125.0",
]

_STEALTH_SCRIPT = """
Object.defineProperty(navigator, 'webdriver', { get: () => undefined });
Object.defineProperty(navigator, 'plugins',   { get: () => [1, 2, 3, 4, 5] });
Object.defineProperty(navigator, 'languages', { get: () => ['es-PE', 'es', 'en-US'] });
window.chrome = { runtime: {} };
Object.defineProperty(navigator, 'platform', { get: () => 'Win32' });
"""

_LAUNCH_ARGS = [
    "--disable-blink-features=AutomationControlled",
    "--disable-dev-shm-usage",
    "--no-sandbox",
    "--disable-setuid-sandbox",
    "--disable-infobars",
    "--disable-extensions",
    "--disable-notifications",
    "--lang=es-PE",
]


async def _human_delay(min_s: float = 0.8, max_s: float = 2.2) -> None:
    await asyncio.sleep(random.uniform(min_s, max_s))


async def _human_scroll(page, iterations: int = 4) -> None:
    for _ in range(iterations):
        amount = random.randint(500, 1600)
        await page.evaluate(f"window.scrollBy(0, {amount})")
        await _human_delay(0.7, 2.0)


async def _new_stealth_context(p):
    """Creates a Playwright browser + context with anti-detection settings."""
    browser = await p.chromium.launch(headless=True, args=_LAUNCH_ARGS)
    w = 1280 + random.randint(-60, 60)
    h = 900  + random.randint(-40, 40)
    context = await browser.new_context(
        user_agent=random.choice(_USER_AGENTS),
        viewport={"width": w, "height": h},
        locale="es-PE",
        timezone_id="America/Lima",
        extra_http_headers={
            "Accept-Language": "es-PE,es;q=0.9,en;q=0.5",
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/webp,*/*;q=0.8",
        },
    )
    await context.add_init_script(_STEALTH_SCRIPT)
    return browser, context

# ─── Currency detection ────────────────────────────────────────────────────────
EXCHANGE_RATE_PEN_USD = 3.75   # S/ per $1 USD (approximate, Lima 2024-2025)

_USD_KEYWORDS = [
    "dólares", "dolares", "usd", "en dólares", "en dolares",
    "precio en dólares", "precio en dolares", "us$", "precio usd",
    "pagos en dólares", "venta en dólares", "cobro en dólares",
]
_PEN_KEYWORDS = [
    "soles", "nuevos soles", "en soles", "precio en soles", "s/.",
    "precio soles",
]

# ─── Peru cities ──────────────────────────────────────────────────────────────
# Facebook Marketplace scopes results ONLY via the city slug in the URL path.
# lat/lon parameters are silently ignored by Facebook — do NOT use coordinates.
# Slugs are the exact strings Facebook uses in marketplace/<slug>/vehicles.
PERU_CITIES: dict[str, dict[str, Any]] = {
    "lima":     {"slug": "lima",     "label": "Lima"},
    "arequipa": {"slug": "arequipa", "label": "Arequipa"},
    "trujillo": {"slug": "trujillo", "label": "Trujillo"},
    "chiclayo": {"slug": "chiclayo", "label": "Chiclayo"},
    "piura":    {"slug": "piura",    "label": "Piura"},
    "cusco":    {"slug": "cusco",    "label": "Cusco"},
    "iquitos":  {"slug": "iquitos",  "label": "Iquitos"},
    "huancayo": {"slug": "huancayo", "label": "Huancayo"},
    "tacna":    {"slug": "tacna",    "label": "Tacna"},
    "pucallpa": {"slug": "pucallpa", "label": "Pucallpa"},
}

# ─── Peru listing filter ───────────────────────────────────────────────────────

# Disqualifying: strongly suggests the listing is NOT from Peru
_NON_PERU_SIGNALS = [
    r"\bcarfax\b",
    r"\bvin\s*(number|#|:)\b",
    r"\bclean\s*title\b",
    r"\bsalvage\s*title\b",
    r"\bobd\s*ii\b",
    r"\bsmog\s*(check|test)\b",
    r"\bregistration\s*(expires?|valid)\b",
    r"\bstate\s*inspection\b",
    r"\b\d[\d,\.]*k?\s+miles?\b",   # "85k miles", "85,000 miles" — not "miles de km"
    # US states / cities
    r"\btexas\b", r"\bflorida\b", r"\bcalifornia\b", r"\bgeorgia\b",
    r"\bcarolina\b", r"\bvirgin[ai]a\b", r"\btenness?ee\b",
    r"\bariz?ona\b", r"\bnevada\b", r"\butah\b", r"\bcolorado\b",
    r"\bwashington\s*(state|dc)\b", r"\bhouston\b", r"\bdallas\b",
    r"\batlanta\b", r"\bmiami\b", r"\bchicago\b", r"\bphoenix\b",
    r"\blos\s+angeles\b", r"\bsan\s+diego\b", r"\bsan\s+antonio\b",
]

# English-only car phrases: appear in US listings but never in Peruvian ones
_ENGLISH_ONLY_SIGNALS = [
    r"\bfor\s+sale\b",
    r"\bruns?\s+(great|good|well|fine)\b",
    r"\bdrives?\s+(great|good|well|fine)\b",
    r"\bone\s+(careful\s+)?owner\b",
    r"\bask(ing)?\s+\$",
    r"\bpriced?\s+to\s+(sell|move)\b",
    r"\bnewly\s+(serviced|painted|detailed|wrapped)\b",
    r"\bfull\s+service\s+history\b",
    r"\bno\s+(accidents?|issues?|rust)\b",
    r"\bprice\s+is\s+(firm|negotiable)\b",
    r"\bmust\s+sell\b",
    r"\btest\s+drive\b",
    r"\bprivate\s+seller\b",
]

# Confirming: strongly suggests the listing IS from Peru
_PERU_SIGNALS = [
    r"\bsoat\b",
    r"\brevisión\s*técnica\b",
    r"\btarjeta\s*de\s*propiedad\b",
    r"\bsunarp\b",
    r"\bs/\.?\s*\d",
    r"\bsoles?\b",
    r"\bnuevos\s*soles\b",
    r"\bgnv\b",
    r"\blima\b|\btrujillo\b|\barequipa\b|\bchiclayo\b|\bpiura\b|\bcusco\b|\bhuancayo\b|\btacna\b",
    r"\bperu\b|\bperú\b",
    r"\bdueño\b",
    r"\bpapeles\b",
    r"\bpropietari[ao]\b",
]

# Spanish language markers: words/phrases that appear in Spanish but not English
_SPANISH_MARKERS = [
    r"\bvend[eo]\b",           # vendo, vende
    r"\bnegociable\b",
    r"\bkilometraje\b",
    r"\bautomátic[ao]\b",
    r"\bbuen\s*estado\b",
    r"\binteresados?\b",
    r"\bconsult[ae]s?\b",
    r"\bcelular\b",
    r"\bvehículo\b",
    r"\bplacas?\b",
    r"\brecién\b",
    r"\bsolo\s+\d+\s*(dueño|propietario)\b",
    r"\bmantenimiento\b",
    r"\bal\s+día\b",
]


def is_peru_listing(title: str, description: str, condition: str = "") -> tuple[bool, str]:
    """
    Returns (True, reason) if listing appears to be from Peru, else (False, reason).

    Logic (checked in order):
      1. Any non-Peru signal → reject immediately
      2. Any Peru signal → accept immediately
      3. Spanish accented characters in text → accept (Spanish listing = likely Peru)
      4. Any Spanish language marker → accept
      5. Any English-only car phrase → reject
      6. Default: reject (require positive evidence of Peru, not just absence of US signals)
    """
    combined_lower = f"{title} {description} {condition}".lower()
    combined_raw   = f"{title} {description} {condition}"

    # 1. Non-Peru disqualifiers (checked first — highest precision)
    for pattern in _NON_PERU_SIGNALS:
        if re.search(pattern, combined_lower, re.IGNORECASE):
            return False, f"Señal no-Perú: {pattern}"

    # 2. Peru-specific confirmers
    for pattern in _PERU_SIGNALS:
        if re.search(pattern, combined_lower, re.IGNORECASE):
            return True, "Señal Perú confirmada"

    # 3. Spanish language detection via accented characters (á é í ó ú ü ñ)
    if re.search(r"[áéíóúüñÁÉÍÓÚÜÑ]", combined_raw):
        return True, "Descripción en español (caracteres acentuados)"

    # 4. Spanish language markers (unaccented but clearly Spanish words)
    for pattern in _SPANISH_MARKERS:
        if re.search(pattern, combined_lower, re.IGNORECASE):
            return True, "Marcadores en español detectados"

    # 5. English-only car phrases → definitely not Peru
    for pattern in _ENGLISH_ONLY_SIGNALS:
        if re.search(pattern, combined_lower, re.IGNORECASE):
            return False, f"Publicación en inglés: {pattern}"

    # 6. Default: no confirming signals found → reject
    #    (better to miss an edge case than to pull in US listings)
    return False, "Sin señales confirmatorias de Perú — descartado"


def detect_currency(price_str: str, description: str = "", title: str = "") -> dict[str, Any]:
    """
    Detects whether a price is in USD or PEN (soles) and normalises it.

    Returns:
        currency      : "USD" | "PEN"
        amount_original: numeric value in original currency (or None)
        amount_usd     : value converted to USD (or None)
        display        : original price string
        note           : human-readable explanation of the detection
    """
    combined = f"{price_str} {description} {title}".lower()

    # ── Determine currency ─────────────────────────────────────────────────────
    if re.match(r"^\s*s/", price_str, re.IGNORECASE):
        is_pen = True
        note = "S/ detectado en el precio → Soles"
    elif any(kw in combined for kw in _PEN_KEYWORDS):
        is_pen = True
        note = "Mención de soles en descripción"
    elif any(kw in combined for kw in _USD_KEYWORDS):
        is_pen = False
        note = "Mención de dólares/USD en descripción"
    elif "$" in price_str and "s/" not in price_str.lower():
        # $ without S/ → USD
        is_pen = False
        note = "Símbolo $ → asumido USD"
    else:
        # Default for Lima: soles
        is_pen = True
        note = "Sin marcador de moneda → asumido Soles (Lima por defecto)"

    # ── Extract numeric amount ─────────────────────────────────────────────────
    # Remove currency symbols
    num_str = re.sub(r"[^\d.,]", "", price_str)

    amount: float | None = None
    try:
        # In Peru: "6.000" or "6,000" both mean six thousand
        # Rule: if the part(s) after a dot/comma are exactly 3 digits → thousands separator
        if "." in num_str and "," not in num_str:
            parts = num_str.split(".")
            if len(parts) >= 2 and all(len(p) == 3 for p in parts[1:]):
                num_str = num_str.replace(".", "")   # 6.000 → 6000
        elif "," in num_str and "." not in num_str:
            parts = num_str.split(",")
            if len(parts) >= 2 and all(len(p) == 3 for p in parts[1:]):
                num_str = num_str.replace(",", "")   # 6,000 → 6000
            else:
                num_str = num_str.replace(",", ".")  # 6,5 → 6.5 (decimal)
        elif "." in num_str and "," in num_str:
            # "6.000,00" → European format
            num_str = num_str.replace(".", "").replace(",", ".")

        amount = float(num_str) if num_str else None
    except ValueError:
        amount = None

    amount_usd: float | None = None
    if amount is not None:
        amount_usd = round(amount / EXCHANGE_RATE_PEN_USD) if is_pen else amount

    return {
        "currency": "PEN" if is_pen else "USD",
        "amount_original": amount,
        "amount_usd": amount_usd,
        "display": price_str,
        "note": note,
    }


# ─── Phone / WhatsApp extraction ──────────────────────────────────────────────

def extract_phone_number(text: str) -> str | None:
    """
    Extracts a Peruvian mobile number from free text and returns it as
    '51XXXXXXXXX' (ready for a wa.me link), or None if not found.

    Handles formats: +51 987 654 321 | 987654321 | cel: 987-654-321 | wa.me/51987...
    """
    # wa.me link already embedded
    m = re.search(r"wa\.me/(?:51)?(\d{9})", text)
    if m:
        return "51" + m.group(1)

    # With country code (51 or +51)
    m = re.search(r"(?:\+?51)[\s\-]?(9\d{2})[\s\-]?(\d{3})[\s\-]?(\d{3})", text)
    if m:
        return "51" + m.group(1) + m.group(2) + m.group(3)

    # Without country code — 9-digit number starting with 9
    # Preceded by common Peruvian keywords or a word boundary
    m = re.search(
        r"(?:cel(?:ular)?|whatsapp|wsp|wa|tlf|telf|tel|llam[ae]r?|escrib[ei]r?|contact[ao]r?)[:\s]*"
        r"(9\d{2})[\s\-]?(\d{3})[\s\-]?(\d{3})",
        text, re.IGNORECASE,
    )
    if m:
        return "51" + m.group(1) + m.group(2) + m.group(3)

    # Bare 9-digit number (last resort — only if nothing else found)
    m = re.search(r"\b(9\d{2})[\s\-]?(\d{3})[\s\-]?(\d{3})\b", text)
    if m:
        return "51" + m.group(1) + m.group(2) + m.group(3)

    return None


# ─── Vehicle detail extraction from free text ─────────────────────────────────

def extract_vehicle_details(text: str) -> dict[str, Any]:
    """Parses km, transmission, fuel type and listing age from raw text."""
    details: dict[str, Any] = {}

    # Kilometraje (handles "85.000 km", "85,000 km", "85000 km", "85 mil km")
    km_match = re.search(
        r"(\d{1,3}[.,]\d{3}|\d{2,6})\s*(km|kilómetros|kilometros|millas)",
        text, re.IGNORECASE,
    )
    if km_match:
        km_raw = km_match.group(1).replace(".", "").replace(",", "")
        try:
            details["kilometraje"] = int(km_raw)
        except ValueError:
            pass

    mil_match = re.search(r"(\d+)\s*mil\s*km", text, re.IGNORECASE)
    if mil_match and "kilometraje" not in details:
        details["kilometraje"] = int(mil_match.group(1)) * 1000

    # Transmisión
    if re.search(r"\bautomátic[ao]\b|\bautomatic\b|\bA/T\b", text, re.IGNORECASE):
        details["transmision"] = "Automático"
    elif re.search(r"\bmanual\b|\bmecánic[ao]\b|\bM/T\b", text, re.IGNORECASE):
        details["transmision"] = "Manual"

    # Combustible (order matters: GNV/GLP before gasolina)
    if re.search(r"\bgnv\b|\bgas\s*natural\b|\bgas\s*vehicular\b", text, re.IGNORECASE):
        details["combustible"] = "GNV"
    elif re.search(r"\bglp\b|\bgas\s*licuado\b", text, re.IGNORECASE):
        details["combustible"] = "GLP"
    elif re.search(r"\bdiesel\b|\bdiésel\b", text, re.IGNORECASE):
        details["combustible"] = "Diésel"
    elif re.search(r"\bgasolina\b|\bgasohol\b|\bpetrol\b", text, re.IGNORECASE):
        details["combustible"] = "Gasolina"

    # Único dueño
    if re.search(r"único\s*dueño|unico\s*dueño|1\s*(solo\s*)?dueño|primer\s*dueño", text, re.IGNORECASE):
        details["unico_dueno"] = True

    # Red flags rápidos (para pre-filtrar antes de llamar a la IA)
    red_flags = []
    if re.search(r"papeles?\s*(en\s*trámite|incompletos?|en\s*proceso)", text, re.IGNORECASE):
        red_flags.append("papeles en trámite")
    if re.search(r"(motor|caja)\s*(reparad[ao]|reconstruid[ao]|overhaul)", text, re.IGNORECASE):
        red_flags.append("motor/caja reparado")
    if re.search(r"chocad[ao]|sinestro|accidente|golpe", text, re.IGNORECASE):
        red_flags.append("historial de choque")
    if re.search(r"sin\s*(soat|placa|tarjeta\s*de\s*propiedad)", text, re.IGNORECASE):
        red_flags.append("documentación incompleta")
    if red_flags:
        details["red_flags_detectados"] = red_flags

    return details


# ─── Single-item scraper (used by Telegram bot) ───────────────────────────────

async def scrape_item_url(url: str) -> dict[str, Any] | None:
    """
    Opens a single Facebook Marketplace item URL and returns a car_data dict
    compatible with AcquisitionAgent / Orchestrator.
    Returns None on failure.
    """
    try:
        async with async_playwright() as p:
            browser, context = await _new_stealth_context(p)
            page = await context.new_page()
            try:
                await page.goto(url, wait_until="domcontentloaded", timeout=30000)
                await _human_delay(1.5, 3.0)

                # Title
                title = ""
                for sel in ["h1", '[data-testid="marketplace_pdp_title"]']:
                    el = page.locator(sel)
                    if await el.count() > 0:
                        title = (await el.first.inner_text()).strip()
                        break

                # Price — scan all text nodes for price-like patterns
                price = ""
                for el in await page.locator("span, div").all():
                    try:
                        t = (await el.inner_text()).strip()
                        if re.search(r"^[S$][/\s]?[\d.,]{3,}", t) and len(t) < 25:
                            price = t
                            break
                    except Exception:
                        pass

                # Full description
                desc_candidates: list[str] = []
                for d in await page.locator('div[dir="auto"]').all():
                    t = (await d.inner_text()).strip()
                    if len(t) > 30:
                        desc_candidates.append(t)
                full_description = max(desc_candidates, key=len) if desc_candidates else ""

                # Condition
                condition = ""
                cond_el = page.locator('text="Estado"').locator("xpath=..")
                if await cond_el.count() > 0:
                    condition = (await cond_el.inner_text()).replace("Estado", "").strip()

                # First image
                image_url = ""
                img_el = page.locator('img[src*="scontent"]')
                if await img_el.count() > 0:
                    image_url = await img_el.first.get_attribute("src") or ""

                # Year
                year_text   = f"{title} {full_description}"
                year_matches = re.findall(r"\b(19[5-9]\d|20[0-2]\d)\b", year_text)
                year = int(year_matches[0]) if year_matches else None

                combined = f"{full_description} {condition}"
                details  = extract_vehicle_details(combined)
                currency = detect_currency(price, full_description, title)
                phone    = extract_phone_number(f"{full_description} {condition}")

                return {
                    "title":             title or "Auto sin título",
                    "price":             price,
                    "currency":          currency["currency"],
                    "price_usd":         currency["amount_usd"],
                    "price_amount":      currency["amount_original"],
                    "currency_note":     currency["note"],
                    "url":               url,
                    "image_url":         image_url,
                    "condition":         condition,
                    "description":       full_description,
                    "city":              "lima",
                    "año":               year,
                    "kilometraje":       details.get("kilometraje"),
                    "transmision":       details.get("transmision"),
                    "combustible":       details.get("combustible"),
                    "unico_dueno":       details.get("unico_dueno", False),
                    "red_flags_scraper": details.get("red_flags_detectados", []),
                    "whatsapp_number":   phone,
                    "raw_data": (
                        f"Moneda: {currency['currency']} | "
                        f"Precio: {price} | USD: ${currency['amount_usd']} | "
                        f"Año: {year or 'desconocido'} | "
                        f"Km: {details.get('kilometraje','desconocido')} | "
                        f"Transmisión: {details.get('transmision','desconocida')} | "
                        f"Combustible: {details.get('combustible','desconocido')} | "
                        f"Descripción: {full_description}"
                    ),
                }
            finally:
                await context.close()
                await browser.close()
    except Exception as e:
        print(f"[scrape_item_url] Error: {e}")
        return None


# ─── Facebook Scraper ──────────────────────────────────────────────────────────

class FacebookScraper:
    def __init__(
        self,
        city: str = "lima",
        min_price: int = 2000,
        max_price: int = 15000,
        query: str = "",
    ) -> None:
        self.city      = city.lower().strip()
        self.min_price = min_price
        self.max_price = max_price
        self.query     = query

    def _build_url(self) -> str:
        """
        Builds a Facebook Marketplace URL scoped to a Peruvian city.
        Price params are omitted when min_price=0 and max_price=0 (no filter).
        """
        city_info = PERU_CITIES.get(self.city, PERU_CITIES["lima"])
        slug = city_info["slug"]

        # Build price fragment — skip entirely when both are 0 (no price filter)
        if self.min_price > 0 or self.max_price > 0:
            price_qs = (
                f"&minPrice={self.min_price}" if self.min_price > 0 else ""
            ) + (
                f"&maxPrice={self.max_price}" if self.max_price > 0 else ""
            )
        else:
            price_qs = ""

        if self.query:
            base   = f"https://www.facebook.com/marketplace/{slug}/search/"
            params = f"?query={urllib.parse.quote(self.query)}{price_qs}&exact=false"
        else:
            base   = f"https://www.facebook.com/marketplace/{slug}/vehicles"
            params = f"?{price_qs.lstrip('&')}&exact=false" if price_qs else "?exact=false"

        return base + params

    async def scrape_cars(self, max_items: int = 5) -> list[dict[str, Any]]:
        results: list[dict[str, Any]] = []
        skipped_non_peru = 0

        try:
            async with async_playwright() as p:
                browser, context = await _new_stealth_context(p)
                page = await context.new_page()

                url = self._build_url()
                print(f"Scraping: {url}")

                await page.goto(url, wait_until="domcontentloaded", timeout=60000)
                await _human_delay(2.5, 4.5)

                # ── Human-like scroll to load more listings ────────────────────
                await _human_scroll(page, iterations=random.randint(3, 5))

                # ── Collect listing links ──────────────────────────────────────
                elements  = await page.locator('a[href*="/marketplace/item/"]').all()
                seen_urls: set[str] = set()

                for el in elements:
                    if len(results) >= max_items:
                        break

                    href = await el.get_attribute("href")
                    if not href or href in seen_urls:
                        continue
                    seen_urls.add(href)

                    full_url    = "https://www.facebook.com" + href.split("?")[0]
                    text_lines  = [
                        ln.strip()
                        for ln in (await el.inner_text()).split("\n")
                        if ln.strip()
                    ]

                    # ── Extract price and title from card ─────────────────────
                    price = ""
                    title = ""
                    image_url = ""

                    img_el = el.locator("img")
                    if await img_el.count() > 0:
                        image_url = await img_el.first.get_attribute("src") or ""

                    for line in text_lines:
                        if re.search(r"[\$S/]|\d{3,}", line) and not price:
                            price = line
                        elif len(line) > 5 and not title and line != price:
                            title = line

                    if not (title and price):
                        continue

                    # ── Open item page for full details ───────────────────────
                    await _human_delay(0.5, 1.5)   # pause between card reads
                    item_page        = await context.new_page()
                    full_description = ""
                    condition        = ""
                    structured       : dict[str, Any] = {}

                    try:
                        await item_page.goto(full_url, wait_until="domcontentloaded", timeout=30000)
                        await _human_delay(1.5, 3.0)

                        # Full description
                        desc_el = item_page.locator('div[dir="auto"]')
                        desc_candidates: list[str] = []
                        for d in await desc_el.all():
                            t = (await d.inner_text()).strip()
                            if len(t) > 30:
                                desc_candidates.append(t)
                        if desc_candidates:
                            full_description = max(desc_candidates, key=len)

                        # Condition
                        cond_el = item_page.locator('text="Estado"').locator("xpath=..")
                        if await cond_el.count() > 0:
                            condition = (await cond_el.inner_text()).replace("Estado", "").strip()

                        # Structured details (year, km, transmission, fuel from sidebar)
                        details_text = ""
                        for sel in [
                            '[aria-label*="Detalles"]',
                            'div[class*="x1dr75xp"]',   # FB internal class (may change)
                        ]:
                            el_det = item_page.locator(sel)
                            if await el_det.count() > 0:
                                details_text = await el_det.first.inner_text()
                                break

                        # Also search year in title/description
                        year_search_text = f"{title} {full_description} {details_text}"
                        year_matches = re.findall(r"\b(19[5-9]\d|20[0-2]\d)\b", year_search_text)
                        if year_matches:
                            structured["año"] = int(year_matches[0])

                        # Extract km, transmission, fuel
                        combined_text = f"{full_description} {details_text} {condition}"
                        structured.update(extract_vehicle_details(combined_text))

                    except Exception as e:
                        print(f"  ⚠ Error en {full_url}: {e}")
                    finally:
                        await item_page.close()

                    # ── Phone / WhatsApp extraction ──────────────────────────
                    phone_number = extract_phone_number(
                        f"{full_description} {condition} {title}"
                    )

                    # ── Peru filter ───────────────────────────────────────────
                    # Include card text (price with S/, location like "Lima, Peru")
                    # so the filter can use those signals even with sparse descriptions.
                    card_context = " ".join(text_lines)
                    is_peru, filter_reason = is_peru_listing(
                        title,
                        f"{full_description} {card_context}",
                        condition,
                    )
                    if not is_peru:
                        skipped_non_peru += 1
                        print(f"  ✗ FILTRADO (no-Perú): {title[:45]} — {filter_reason}")
                        continue

                    # ── Currency detection ────────────────────────────────────
                    currency_info = detect_currency(price, full_description, title)

                    # ── Build car record ──────────────────────────────────────
                    car: dict[str, Any] = {
                        "title":         title,
                        "price":         price,
                        # Currency info
                        "currency":      currency_info["currency"],
                        "price_usd":     currency_info["amount_usd"],
                        "price_amount":  currency_info["amount_original"],
                        "currency_note": currency_info["note"],
                        # Details
                        "url":           full_url,
                        "image_url":     image_url,
                        "condition":     condition,
                        "description":   full_description,
                        "city":          PERU_CITIES.get(self.city, {}).get("label", self.city),
                        # Structured
                        "año":           structured.get("año"),
                        "kilometraje":   structured.get("kilometraje"),
                        "transmision":   structured.get("transmision"),
                        "combustible":   structured.get("combustible"),
                        "unico_dueno":   structured.get("unico_dueno", False),
                        "red_flags_scraper": structured.get("red_flags_detectados", []),
                        "whatsapp_number": phone_number,
                        # Raw text for AI
                        "raw_data": (
                            f"Moneda: {currency_info['currency']} | "
                            f"Precio original: {price} | "
                            f"Precio en USD: ${currency_info['amount_usd']} | "
                            f"Nota moneda: {currency_info['note']} | "
                            f"Año: {structured.get('año', 'desconocido')} | "
                            f"Km: {structured.get('kilometraje', 'desconocido')} | "
                            f"Transmisión: {structured.get('transmision', 'desconocida')} | "
                            f"Combustible: {structured.get('combustible', 'desconocido')} | "
                            f"Condición: {condition} | "
                            f"Descripción: {full_description}"
                        ),
                    }
                    results.append(car)
                    print(
                        f"  ✓ {title[:45]} | {price} "
                        f"({'S/' if currency_info['currency']=='PEN' else '$'}"
                        f"{currency_info['amount_original']:,.0f} → "
                        f"${currency_info['amount_usd']:,.0f} USD) "
                        f"| {currency_info['note']}"
                        if currency_info["amount_original"] else f"  ✓ {title[:45]} | {price}"
                    )

                await context.close()
                await browser.close()

        except Exception as e:
            print(f"Error en scraping: {e}")

        if skipped_non_peru:
            print(f"\n  📍 {skipped_non_peru} publicación(es) filtrada(s) por estar fuera de Perú.")

        return results


if __name__ == "__main__":
    scraper = FacebookScraper("trujillo", 3000, 15000)
    autos   = asyncio.run(scraper.scrape_cars(3))
    for a in autos:
        print(a)
