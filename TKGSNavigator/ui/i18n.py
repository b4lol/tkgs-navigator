"""Plugin translation domain with a fallback to the enigma2 catalog for common words."""
import gettext
from pathlib import Path

DOMAIN = "TKGSNavigator"
LOCALE_DIR = Path(__file__).resolve().parents[1] / "locale"

gettext.bindtextdomain(DOMAIN, str(LOCALE_DIR))


def _(text):
    # Looked up on every call so a language change in the Enigma2 settings applies immediately.
    translated = gettext.dgettext(DOMAIN, text)
    return translated if translated != text else gettext.gettext(text)
