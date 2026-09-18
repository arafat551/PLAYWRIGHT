"""Lecture de la boîte mail (IMAP / Gmail) pour récupérer automatiquement le
code OTP reçu lors d'une authentification 2FA.

Le serveur IMAP et le port sont FIGÉS pour Gmail (imap.gmail.com:993),
comme pour le SMTP. Les identifiants proviennent des Paramètres :
`imap_user` / `imap_password`, avec repli automatique sur `smtp_user` /
`smtp_password` (souvent le même compte Gmail). La lecture utilise
BODY.PEEK : les messages ne sont pas marqués comme lus.

Sans identifiants IMAP configurés (ou en cas d'erreur réseau), le moteur
retombe sur la saisie manuelle du code dans l'interface (statut waiting_otp).
"""
import email
import imaplib
import re
import time

from ..config import get_setting


def _log(msg):
    ts = time.strftime("%H:%M:%S")
    print(f"[MAILBOX {ts}] {msg}", flush=True)

IMAP_HOST = "imap.gmail.com"
IMAP_PORT = 993

MAX_MESSAGES = 10      # messages les plus récents inspectés par tentative
RETRY_DELAY = 3        # secondes entre deux tentatives de lecture
DEFAULT_TIMEOUT = 90   # durée totale de scrutation en automatique

# Six chiffres isolés (pas un numéro de carte ou une grande valeur).
_OTP_RE = re.compile(r"(?<!\d)\d{6}(?!\d)")

# Mots-clés qui révèlent qu'un code de 6 chiffres est bien un code OTP.
_OTP_KEYWORDS = re.compile(
    r"(?i)(otp|one[- ]time|code de vérification|code de validation|"
    r"vérification|verification|validation|confirmation|"
    r"connexion|connectez|login|authentification|authenticate|"
    r"sign in|sign in|sécurité|security|usage unique|jetable|"
    r"code secret|secret code|code d'accès|access code|saisissez|"
    r"enter the code|type the code|votre code)")

_HTML_TAG_RE = re.compile(r"<[^>]+>")


def _credentials():
    """(user, password) pour l'accès IMAP, avec repli sur les identifiants
    SMTP quand les champs IMAP sont vides (même compte Gmail)."""
    user = (get_setting("imap_user") or "").strip()
    pwd = get_setting("imap_password") or ""
    if not user:
        user = (get_setting("smtp_user") or "").strip()
    if not pwd:
        pwd = get_setting("smtp_password") or ""
    return user, pwd


def _open_connection():
    """Ouvrir une connexion IMAP. Retourne conn ou None."""
    user, pwd = _credentials()
    if not (user and pwd):
        return None
    try:
        conn = imaplib.IMAP4_SSL(IMAP_HOST, IMAP_PORT, timeout=20)
    except TypeError:
        try:
            conn = imaplib.IMAP4_SSL(IMAP_HOST, IMAP_PORT)
        except Exception:
            return None
    except Exception:
        return None
    try:
        conn.login(user, pwd)
        return conn
    except Exception:
        try:
            conn.logout()
        except Exception:
            pass
        return None


def extract_otp(subject="", text="", html=""):
    """Extraire le code OTP à 6 chiffres d'un e-mail.

    Retourne le code (chaîne de 6 chiffres) ou None. Quand plusieurs codes
    apparaissent, on privilégie celui qui est proche d'un mot-clé OTP
    (ex. « code de vérification ») ; sinon le dernier code isolé.
    """
    body = _HTML_TAG_RE.sub(" ", html)
    body = body.replace("&nbsp;", " ").replace("&#160;", " ")
    body = re.sub(r"\s+", " ", " ".join(filter(None, [subject, body, text])))

    matches = list(_OTP_RE.finditer(body))
    if not matches:
        return None

    best = None
    for m in matches:
        window = body[max(0, m.start() - 100):m.end() + 100]
        if _OTP_KEYWORDS.search(window):
            best = m.group(0)
    if best is None:
        best = matches[-1].group(0)
    return best


def _decode_part(part):
    charset = part.get_content_charset() or "utf-8"
    try:
        return part.get_payload(decode=True).decode(charset, errors="replace")
    except Exception:
        return ""


def _message_text(msg):
    """Récupérer (subject, texte, html) d'un email.message.Message."""
    subject = str(msg.get("Subject", "") or "")
    text_parts, html_parts = [], []
    try:
        parts = list(msg.walk())
    except Exception:
        parts = [msg]
    for part in parts:
        ct = (part.get_content_type() or "").lower()
        if ct == "text/plain":
            text_parts.append(_decode_part(part))
        elif ct == "text/html":
            html_parts.append(_decode_part(part))
    return subject, " \n".join(text_parts), " \n".join(html_parts)


def _search_ids(conn, recipient=None):
    """Liste des UID du dossier INBOX, triées du plus ancien au plus récent.

    Si un destinataire est fourni, on filtre d'abord sur l'en-tête To ;
    si aucun résultat, on retombe sur tout le courrier récent."""
    try:
        if recipient:
            typ, data = conn.search(None, "TO", '"%s"' % recipient)
            if typ == "OK":
                ids = data[0].split()
                if ids:
                    return ids
        typ, data = conn.search(None, "ALL")
    except Exception:
        return []
    if typ != "OK":
        return []
    return data[0].split()


def snapshot_latest_id(recipient=None):
    """Retourner l'ID du mail le plus récent pour `recipient`, ou b'0'.

    À appeler AVANT le login pour mémoriser l'état de la boîte.
    Ensuite on ne considérera que les mails dont l'ID est > snapshot_id.
    """
    conn = _open_connection()
    if not conn:
        return b"0"
    try:
        conn.select("INBOX")
        ids = _search_ids(conn, recipient)
        if ids:
            latest = ids[-1]
            _log(f"Snapshot IMAP: dernier mail id={latest.decode('ascii','replace')}")
            return latest
        return b"0"
    except Exception:
        return b"0"
    finally:
        try:
            conn.logout()
        except Exception:
            pass


def _try_fetch(user, pwd, recipient, since_id=None):
    """Une seule lecture IMAP : retourne le code OTP trouvé, sinon None.

    Si `since_id` est fourni, on ne considère que les mails dont l'ID
    est strictement supérieur (i.e. reçus APRÈS cette valeur).
    """
    conn = _open_connection()
    if not conn:
        return None
    try:
        conn.select("INBOX")
        ids = _search_ids(conn, recipient)
        if since_id:
            try:
                since_num = int(since_id)
            except (TypeError, ValueError):
                since_num = 0
            ids = [mid for mid in ids if int(mid) > since_num]
        _log(f"{len(ids)} mail(s) recent(s) pour recipient={recipient}"
             + (f" (depuis id={since_id})" if since_id else ""))
        # Parcourir du PLUS RÉCENT au plus ancien pour toujours
        # récupérer le code le plus récent (les codes OTP expirent).
        for mid in reversed(ids[-MAX_MESSAGES:]):
            try:
                typ, data = conn.fetch(mid.decode("ascii", "replace"), "(BODY.PEEK[])")
            except Exception:
                continue
            if typ != "OK" or not data or not isinstance(data[0], tuple):
                continue
            try:
                msg = email.message_from_bytes(data[0][1])
            except Exception:
                continue
            subject, text, html = _message_text(msg)
            code = extract_otp(subject, text, html)
            if code:
                _log(f"OTP trouvé: {code} (mail: {subject[:60]})")
                return code
        _log("Aucun code OTP trouvé dans les nouveaux mails")
        return None
    except Exception as e:
        _log(f"Erreur IMAP: {e}")
        return None
    finally:
        try:
            conn.logout()
        except Exception:
            pass


def fetch_otp_code(recipient=None, timeout=DEFAULT_TIMEOUT, since_id=None):
    """Scruter la boîte mail jusqu'à trouver un code OTP.

    `recipient` : adresse du projet à laquelle l'application envoie le code.
    `since_id`  : ID du dernier mail connu AVANT le login. On ne lit que
                  les mails plus récents pour éviter les anciens codes.
    Retourne le code (6 chiffres) ou None si indisponible / délai dépassé.
    """
    user, pwd = _credentials()
    if not (user and pwd):
        _log("Pas d'identifiants IMAP configurés")
        return None

    _log(f"Scrutation IMAP pour recipient={recipient}, timeout={timeout}s"
         + (f", since_id={since_id}" if since_id else ""))
    deadline = time.time() + max(timeout, 10)
    while time.time() < deadline:
        code = _try_fetch(user, pwd, recipient, since_id=since_id)
        if code:
            _log(f"Code OTP récupéré: {code}")
            return code
        remaining = int(deadline - time.time())
        _log(f"Pas encore de code, nouvelle tentative dans {RETRY_DELAY}s (reste {remaining}s)")
        time.sleep(RETRY_DELAY)
    _log("Délai dépassé, aucun code OTP trouvé")
    return None