"""
Registration: the user's own phone number, and the country it belongs to.

The number comes only from Telegram's contact button, which shares the number
of the user's own Telegram account (verified by SMS when it was created). A
contact whose user_id is not the sender's is someone else's and is refused
before it gets here.
"""

import phonenumbers
from django.utils import timezone

# phonenumbers' codes for numbers with no country: unknown, and
# non-geographic ranges such as Telegram's anonymous +888 numbers.
NO_COUNTRY = {"ZZ", "001"}


def parse(phone):
    """Telegram may send the number without its '+'. None if unreadable."""
    digits = "".join(ch for ch in str(phone) if ch.isdigit())
    if not digits:
        return None
    try:
        return phonenumbers.parse("+" + digits)
    except phonenumbers.NumberParseException:
        return None


def country_code(number) -> str:
    """
    ISO code of the number's country, or '' if it has none.

    A number the library does not know as valid still has a country: Telegram
    verified it by SMS, and the library's ranges lag behind new ones. Then the
    calling code decides (+44 -> GB); shared codes go to their main country
    (+1 -> US, +7 -> RU).
    """
    if number is None:
        return ""
    region = (phonenumbers.region_code_for_number(number)
              or phonenumbers.region_code_for_country_code(number.country_code))
    return "" if not region or region in NO_COUNTRY else region


def register(account, phone):
    """Store the number, its country and the time. Returns the account."""
    from .models import Account, Country

    number = parse(phone)
    account.phone = (phonenumbers.format_number(number, phonenumbers.PhoneNumberFormat.E164)
                     if number else str(phone)[:32])
    account.country = Country.objects.filter(code=country_code(number)).first()
    account.registered_at = timezone.now()
    Account.objects.filter(pk=account.pk).update(
        phone=account.phone, country=account.country,
        registered_at=account.registered_at)
    return account
