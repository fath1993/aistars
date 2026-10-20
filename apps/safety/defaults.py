"""
The image policy and blocklist of MyShortClip, as the owner confirmed them on
2026-10-15: every listed category is banned, real people included, and the
user's own face too. They apply to image requests (Create an image, Edit a
photo). Chat is checked more lightly (checks.CHAT_BLOCKS). Both are edited in
the admin (Safety → Policy, Safety → Blocked words).
"""

POLICY = """\
Block the request when the prompt asks for, or the photo shows, any of these.
Use the category name given for each.

sexual: nudity, sexual acts, sexualised or fetish content, revealing poses or
clothing meant to arouse.
minors: anyone who is, or looks, under 18, in any situation.
violence: violence, gore, injuries, weapons used against people or animals,
cruelty.
hate: hate, harassment, threats, extremist symbols, or mocking a group or a
person.
self_harm: suicide or self-harm.
real_person: any real or identifiable person, famous or private, by name or in
a photo of a real person, the user's own face included. Clearly invented,
generic adults that are not in a real photo are allowed.
brand: brands, logos, trademarks, or recognisable branded products.
copyright: characters, scenes or worlds from films, series, games, comics or
books, and the style of a named living artist.
illegal: drugs, crime, fraud, or anything else illegal.
other: anything else meant to deceive or harm, such as fake news,
impersonation or deepfakes.

Allow everything else: landscapes, nature, animals, objects, food,
architecture, abstract scenes, and invented adults in ordinary, non-sexual
situations.
"""

# (term, category). Whole words only: "sexy" does not match "Essex". The AI
# judge reads meaning in any language; these stop the plainest image requests
# for free. None of them blocks a chat message ("naked eye").
BLOCKED_TERMS = [
    ("nude", "sexual"), ("naked", "sexual"), ("nudity", "sexual"), ("nsfw", "sexual"),
    ("porn", "sexual"), ("porno", "sexual"), ("pornographic", "sexual"),
    ("topless", "sexual"), ("hentai", "sexual"), ("erotic", "sexual"), ("sexy", "sexual"),
    ("gore", "violence"),
]
