"""Erkennt Sendungen aus Versand-E-Mails (IMAP, nur lesend).

Dieses Modul hat bewusst keine Home-Assistant-Abhängigkeiten und läuft
synchron im Executor.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
import email
from email.message import Message
from email.policy import default as default_policy
from email.utils import parseaddr, parsedate_to_datetime
import html
import imaplib
import logging
import re
from urllib.parse import parse_qs, unquote, urlparse

from .models import Package, PackageStatus

_LOGGER = logging.getLogger(__name__)

MAX_MESSAGES = 300
FETCH_BATCH = 20

# Absender-Domains der Dienstleister
SENDER_DOMAINS: dict[str, tuple[str, ...]] = {
    "amazon": ("amazon.de", "amazon.com", "amazon.at", "amazon.co.uk"),
    "dhl": ("dhl.de", "dhl.com", "deutschepost.de"),
    "dpd": ("dpd.de", "dpd.com"),
    "gls": ("gls-pakete.de", "gls-group.eu", "gls-group.com", "gls-germany.com"),
    "hermes": ("myhermes.de", "hermesworld.com", "hermes-europe.de"),
    "ups": ("ups.com",),
}

# Domains von Tracking-Links (auch in Shop-Mails)
LINK_DOMAINS: dict[str, tuple[str, ...]] = {
    "dhl": ("dhl.de", "dhl.com"),
    "dpd": ("dpd.de", "dpd.com"),
    "gls": ("gls-pakete.de", "gls-group.eu", "gls-group.com"),
    "hermes": ("myhermes.de", "hermesworld.com"),
    "ups": ("ups.com",),
}

TRACKING_PARAMS = {
    "piececode",
    "idc",
    "trackingnumber",
    "tracking_number",
    "trackingid",
    "tracknum",
    "tracknums",
    "inquirynumber1",
    "parcelnumber",
    "parcelno",
    "pknr",
    "match",
    "shipmentid",
    "sendungsnummer",
    "query",
}

CARRIER_NAMES = {
    "dhl": r"\bDHL\b",
    "dpd": r"\bDPD\b",
    "gls": r"\bGLS\b",
    "hermes": r"\bHermes\b",
    "ups": r"\bUPS\b",
}

SEARCH_FROM = ("amazon", "dhl", "deutschepost", "dpd", "gls", "hermes", "ups")
SEARCH_SUBJECT = ("versand", "versendet", "Sendung", "Paket", "Lieferung", "Zustellung")

ORDER_RE = re.compile(r"\b(\d{3}-\d{7}-\d{7})\b")
UPS_RE = re.compile(r"\b(1Z[0-9A-Z]{16})\b")
KEYWORD_NUMBER_RE = re.compile(
    r"(?:sendungsnummer|paketnummer|trackingnummer|tracking-nummer|tracking number|"
    r"tracking-id|sendungs-nr\.?|paket-nr\.?|paketscheinnummer|sendungsverfolgungsnummer)"
    r"\s*[:#]?\s*([A-Z0-9]{8,35})\b",
    re.IGNORECASE,
)
URL_RE = re.compile(r"https?://[^\s\"'<>]+")
AMAZON_SKIP_RE = re.compile(
    r"rücksendung|ruecksendung|erstattung|rechnung|storniert|bewert|feedback", re.I
)
QUOTED_RE = re.compile(r"[„\"“»](.{3,120}?)[“\"”«]")

STATUS_PATTERNS: list[tuple[PackageStatus, re.Pattern[str]]] = [
    (
        PackageStatus.EXCEPTION,
        r"zustellversuch|konnte nicht zugestellt|nicht zugestellt werden|verzögert|"
        r"verzögerung|problem mit (ihrer|deiner) sendung|delivery attempt",
    ),
    (
        PackageStatus.READY_FOR_PICKUP,
        r"abholbereit|zur abholung bereit|liegt .{0,40}zur abholung|kann .{0,40}abgeholt werden",
    ),
    (
        PackageStatus.DELIVERED,
        r"wurde zugestellt|ist zugestellt|erfolgreich zugestellt|^zugestellt|"
        r"wurde geliefert|ist angekommen|wurde abgegeben|\bdelivered\b",
    ),
    (
        PackageStatus.OUT_FOR_DELIVERY,
        r"heute zugestellt|kommt heute|heute bei (ihnen|dir)|in zustellung|zustellung heute|"
        r"in der zustellung|out for delivery|wird heute geliefert|lieferung heute",
    ),
    (
        PackageStatus.IN_TRANSIT,
        r"versandt|versendet|verschickt|unterwegs|kommt (am|morgen|bald)|ist auf dem weg|"
        r"in transit|shipped|wurde übergeben",
    ),
    (
        PackageStatus.PRE_TRANSIT,
        r"angekündigt|elektronisch übermittelt|wird vorbereitet|^bestellt|bestellbestätigung|"
        r"bestellung eingegangen|label created",
    ),
]
STATUS_PATTERNS = [(s, re.compile(p, re.I | re.M)) for s, p in STATUS_PATTERNS]  # type: ignore[misc]

MONTHS = {
    "jan": 1, "januar": 1, "feb": 2, "februar": 2, "mär": 3, "märz": 3, "maerz": 3,
    "apr": 4, "april": 4, "mai": 5, "jun": 6, "juni": 6, "jul": 7, "juli": 7,
    "aug": 8, "august": 8, "sep": 9, "sept": 9, "september": 9, "okt": 10,
    "oktober": 10, "nov": 11, "november": 11, "dez": 12, "dezember": 12,
}
WEEKDAYS = {
    "montag": 0, "dienstag": 1, "mittwoch": 2, "donnerstag": 3,
    "freitag": 4, "samstag": 5, "sonntag": 6,
}
IMAP_MONTHS = ("Jan", "Feb", "Mar", "Apr", "May", "Jun",
               "Jul", "Aug", "Sep", "Oct", "Nov", "Dec")

DATE_KEYWORD_RE = re.compile(
    r"voraussichtlich|zustellung|lieferung|ankunft|kommt|geliefert|zugestellt|"
    r"lieferdatum|liefertermin|zustelltag|erwartet|arriving|delivery",
    re.I,
)
NUMERIC_DATE_RE = re.compile(r"\b(\d{1,2})\.(\d{1,2})\.(\d{4}|\d{2})?")
TEXT_DATE_RE = re.compile(
    r"\b(\d{1,2})\.?\s+(" + "|".join(sorted(MONTHS, key=len, reverse=True)) + r")\.?(?:\s+(\d{4}))?",
    re.I,
)
RELATIVE_RE = re.compile(r"\b(übermorgen|heute|morgen)\b", re.I)
WEEKDAY_RE = re.compile(r"\b(" + "|".join(WEEKDAYS) + r")\b", re.I)


class MailboxError(Exception):
    """Postfach nicht erreichbar oder Fehler beim Lesen."""


class MailboxAuthError(MailboxError):
    """Anmeldung am Postfach fehlgeschlagen."""


@dataclass
class Finding:
    """Eine in einer E-Mail gefundene Sendung."""

    carrier: str
    number: str
    received: datetime
    subject: str
    status: PackageStatus = PackageStatus.UNKNOWN
    expected: date | None = None
    name: str | None = None


# --------------------------------------------------------------------------- #
# Parsing
# --------------------------------------------------------------------------- #


def _domain_match(host: str, domains: tuple[str, ...]) -> bool:
    host = host.lower()
    return any(host == d or host.endswith("." + d) for d in domains)


def _carrier_from_sender(address: str) -> str | None:
    host = address.rpartition("@")[2]
    for carrier, domains in SENDER_DOMAINS.items():
        if _domain_match(host, domains):
            return carrier
    return None


def _valid_number(value: str) -> str | None:
    value = value.strip().upper()
    if re.fullmatch(r"[A-Z0-9]{8,35}", value) and sum(c.isdigit() for c in value) >= 6:
        return value
    return None


def _number_from_url(url: str) -> tuple[str, str] | None:
    """Gibt (carrier, sendungsnummer) für einen Tracking-Link zurück."""
    parsed = urlparse(url)
    carrier = next(
        (c for c, doms in LINK_DOMAINS.items() if _domain_match(parsed.hostname or "", doms)),
        None,
    )
    if carrier is None:
        return None
    for key, values in parse_qs(parsed.query).items():
        if key.lower() in TRACKING_PARAMS:
            for value in values:
                for part in re.split(r"[,;\s]+", value):
                    if number := _valid_number(part):
                        return carrier, number
    for fragment in (parsed.fragment, *reversed(parsed.path.split("/"))):
        if fragment and (number := _valid_number(fragment)) and len(number) >= 10:
            return carrier, number
    return None


def _html_to_text(raw: str) -> str:
    raw = re.sub(r"(?is)<(style|script|head)\b.*?</\1>", " ", raw)
    raw = re.sub(r"(?i)<br\s*/?>|</(p|div|tr|li|h\d)>", "\n", raw)
    raw = re.sub(r"<[^>]+>", " ", raw)
    return html.unescape(raw)


def _decode(part: Message) -> str:
    payload = part.get_payload(decode=True) or b""
    try:
        return payload.decode(part.get_content_charset() or "utf-8", errors="replace")
    except LookupError:
        return payload.decode("utf-8", errors="replace")


def _extract(msg: Message) -> tuple[str, list[str]]:
    """Liefert (Klartext, URLs) einer Nachricht."""
    plain: list[str] = []
    htmls: list[str] = []
    for part in msg.walk():
        if part.is_multipart() or part.get_content_disposition() == "attachment":
            continue
        ctype = part.get_content_type()
        if ctype == "text/plain":
            plain.append(_decode(part))
        elif ctype == "text/html":
            htmls.append(_decode(part))

    raw_all = html.unescape("\n".join(plain + htmls))
    urls: list[str] = []
    for url in URL_RE.findall(raw_all):
        urls.append(url)
        # Umleitungs-Links (Klick-Tracking) enthalten das Ziel oft URL-kodiert
        urls.extend(URL_RE.findall(unquote(unquote(url))[8:]))

    text = "\n".join(plain) if plain else _html_to_text("\n".join(htmls))
    return re.sub(r"\s+", " ", text), urls


def detect_status(subject: str, text: str) -> PackageStatus:
    for source in (subject, text[:4000]):
        for status, pattern in STATUS_PATTERNS:
            if pattern.search(source):
                return status
    return PackageStatus.UNKNOWN


def _infer_year(day: int, month: int, ref: date) -> date | None:
    try:
        result = date(ref.year, month, day)
    except ValueError:
        return None
    if result < ref - timedelta(days=60):
        result = result.replace(year=ref.year + 1)
    return result


def _date_candidates(window: str, ref: date) -> list[tuple[int, date]]:
    found: list[tuple[int, date]] = []
    for m in NUMERIC_DATE_RE.finditer(window):
        day, month, year = int(m.group(1)), int(m.group(2)), m.group(3)
        try:
            if year:
                y = int(year) + (2000 if len(year) == 2 else 0)
                found.append((m.start(), date(y, month, day)))
            elif d := _infer_year(day, month, ref):
                found.append((m.start(), d))
        except ValueError:
            continue
    for m in TEXT_DATE_RE.finditer(window):
        day, month = int(m.group(1)), MONTHS[m.group(2).lower()]
        try:
            d = date(int(m.group(3)), month, day) if m.group(3) else _infer_year(day, month, ref)
        except ValueError:
            d = None
        if d:
            found.append((m.start(), d))
    for m in RELATIVE_RE.finditer(window):
        offset = {"heute": 0, "morgen": 1, "übermorgen": 2}[m.group(1).lower()]
        found.append((m.start(), ref + timedelta(days=offset)))
    for m in WEEKDAY_RE.finditer(window):
        target = WEEKDAYS[m.group(1).lower()]
        found.append((m.start() + 1000, ref + timedelta(days=(target - ref.weekday()) % 7)))
    return found


def detect_date(subject: str, text: str, ref: date) -> date | None:
    """Sucht ein Lieferdatum in der Nähe typischer Schlüsselwörter."""
    for source in (subject, text[:6000]):
        for kw in DATE_KEYWORD_RE.finditer(source):
            window = source[kw.end() : kw.end() + 90]
            if candidates := _date_candidates(window, ref):
                # explizite Datumsangaben vor reinen Wochentagen bevorzugen
                return min(candidates, key=lambda c: c[0])[1]
    return None


def _quoted_name(subject: str) -> str | None:
    if m := QUOTED_RE.search(subject):
        return m.group(1).strip()
    return None


def parse_message(raw: bytes) -> list[Finding]:
    """Wertet eine einzelne E-Mail aus."""
    msg = email.message_from_bytes(raw, policy=default_policy)
    display_name, sender = parseaddr(str(msg.get("From", "")))
    subject = re.sub(r"\s+", " ", str(msg.get("Subject", ""))).strip()
    try:
        received = parsedate_to_datetime(str(msg["Date"]))
        if received.tzinfo is None:
            received = received.replace(tzinfo=timezone.utc)
    except (TypeError, ValueError):
        received = datetime.now(timezone.utc)

    text, urls = _extract(msg)
    sender_carrier = _carrier_from_sender(sender.lower())
    status = detect_status(subject, text)
    expected = detect_date(subject, text, received.astimezone().date())

    if sender_carrier == "amazon":
        if AMAZON_SKIP_RE.search(subject) or status is PackageStatus.UNKNOWN:
            return []
        order = ORDER_RE.search(subject) or ORDER_RE.search(text)
        if not order:
            return []
        return [
            Finding(
                carrier="amazon",
                number=order.group(1),
                received=received,
                subject=subject,
                status=status,
                expected=expected,
                name=_quoted_name(subject),
            )
        ]

    numbers: dict[str, str] = {}  # nummer -> carrier
    for url in urls:
        if hit := _number_from_url(url):
            numbers.setdefault(hit[1], hit[0])
    for m in UPS_RE.finditer(text):
        numbers.setdefault(m.group(1), "ups")
    for m in KEYWORD_NUMBER_RE.finditer(text):
        number = _valid_number(m.group(1))
        if not number or number in numbers:
            continue
        carrier = sender_carrier
        if carrier is None:
            mentioned = [c for c, p in CARRIER_NAMES.items() if re.search(p, text)]
            carrier = mentioned[0] if len(mentioned) == 1 else None
        if carrier:
            numbers[number] = carrier

    if not numbers:
        return []

    # Mails von Shops: Name = Absendername, Status mindestens "unterwegs"
    name = None
    if sender_carrier is None:
        name = display_name.strip() or None
        if status is PackageStatus.UNKNOWN:
            status = PackageStatus.IN_TRANSIT

    return [
        Finding(carrier, number, received, subject, status, expected, name)
        for number, carrier in numbers.items()
    ]


def merge_findings(findings: list[Finding]) -> dict[tuple[str, str], Package]:
    """Fasst mehrere Mails zur selben Sendung zusammen (neueste gewinnt)."""
    packages: dict[tuple[str, str], Package] = {}
    for f in sorted(findings, key=lambda f: f.received):
        key = (f.carrier, f.number)
        pkg = packages.get(key)
        if pkg is None:
            pkg = packages[key] = Package(f.carrier, f.number, source="email")
        if f.name and not pkg.name:
            pkg.name = f.name
        if f.status is not PackageStatus.UNKNOWN:
            pkg.status = f.status
            pkg.status_text = f.subject
        if f.expected:
            pkg.expected_delivery = f.expected
        pkg.last_update = f.received
    return packages


# --------------------------------------------------------------------------- #
# IMAP
# --------------------------------------------------------------------------- #


def _or_query(terms: list[str]) -> str:
    if len(terms) == 1:
        return terms[0]
    return f"OR {terms[0]} {_or_query(terms[1:])}"


def _imap_date(d: date) -> str:
    return f"{d.day:02d}-{IMAP_MONTHS[d.month - 1]}-{d.year}"


class MailboxScanner:
    """Liest Versand-Mails aus einem IMAP-Postfach (nur lesend)."""

    def __init__(
        self,
        host: str,
        port: int,
        username: str,
        password: str,
        folder: str = "INBOX",
        days: int = 14,
    ) -> None:
        self.host = host
        self.port = int(port)
        self.username = username
        self.password = password
        self.folder = folder or "INBOX"
        self.days = int(days)
        self._cache: dict[bytes, list[Finding]] = {}
        self._uidvalidity: bytes | None = None

    def _connect(self) -> imaplib.IMAP4_SSL:
        try:
            conn = imaplib.IMAP4_SSL(self.host, self.port, timeout=30)
        except (OSError, imaplib.IMAP4.error) as err:
            raise MailboxError(f"Server {self.host} nicht erreichbar: {err}") from err
        try:
            conn.login(self.username, self.password)
        except imaplib.IMAP4.error as err:
            conn.logout()
            raise MailboxAuthError("Benutzername oder Passwort falsch") from err
        return conn

    def _select(self, conn: imaplib.IMAP4_SSL) -> None:
        folder = f'"{self.folder}"' if " " in self.folder else self.folder
        typ, _ = conn.select(folder, readonly=True)
        if typ != "OK":
            raise MailboxError(f"Ordner '{self.folder}' nicht gefunden")

    def test_login(self) -> None:
        conn = self._connect()
        try:
            self._select(conn)
        finally:
            conn.logout()

    def scan(self) -> list[Finding]:
        conn = self._connect()
        try:
            self._select(conn)
            uidvalidity = (conn.response("UIDVALIDITY")[1] or [None])[0]
            if uidvalidity != self._uidvalidity:
                self._cache.clear()
                self._uidvalidity = uidvalidity

            terms = [f'FROM "{s}"' for s in SEARCH_FROM] + [
                f'SUBJECT "{s}"' for s in SEARCH_SUBJECT
            ]
            since = _imap_date(date.today() - timedelta(days=self.days))
            typ, data = conn.uid("SEARCH", None, f"SINCE {since} ({_or_query(terms)})")
            if typ != "OK":
                raise MailboxError("Suche im Postfach fehlgeschlagen")
            uids = (data[0] or b"").split()[-MAX_MESSAGES:]

            new = [u for u in uids if u not in self._cache]
            for i in range(0, len(new), FETCH_BATCH):
                batch = b",".join(new[i : i + FETCH_BATCH]).decode()
                typ, items = conn.uid("FETCH", batch, "(UID BODY.PEEK[])")
                if typ != "OK":
                    continue
                for item in items:
                    if not isinstance(item, tuple):
                        continue
                    m = re.search(rb"UID (\d+)", item[0])
                    if not m:
                        continue
                    try:
                        self._cache[m.group(1)] = parse_message(item[1])
                    except Exception:  # noqa: BLE001 - eine kaputte Mail darf nicht alles stoppen
                        _LOGGER.debug("Mail %s konnte nicht gelesen werden", m.group(1), exc_info=True)
                        self._cache[m.group(1)] = []
        except (OSError, imaplib.IMAP4.error) as err:
            raise MailboxError(f"Fehler beim Lesen des Postfachs: {err}") from err
        finally:
            try:
                conn.logout()
            except (OSError, imaplib.IMAP4.error):
                pass

        current = set(uids)
        self._cache = {u: f for u, f in self._cache.items() if u in current}
        return [f for u in uids for f in self._cache.get(u, [])]
