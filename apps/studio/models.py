"""
The AI tools: chats and their messages, the ready-made assistants, and a Job
for every request that costs something (a chat answer, an image, a voice
note turned into text, a text read aloud).

Media is never stored here: photos and voice notes stay in Telegram (and the
archive channel) and are kept by file_id. Chat texts are kept, so the AI
remembers a conversation; users can delete a chat.
"""

from django.db import models


class Category(models.Model):
    """A shelf of the agent catalog: Law, Learning, Code & tech…"""

    slug = models.SlugField(max_length=40, unique=True)
    emoji = models.CharField(max_length=8, blank=True)
    name = models.CharField(max_length=40, help_text="Translated like the bot's texts.")
    active = models.BooleanField(default=True)
    sort_order = models.PositiveSmallIntegerField(default=100)

    class Meta:
        ordering = ("sort_order", "name")
        verbose_name_plural = "categories"

    def __str__(self):
        return f"{self.emoji} {self.name}".strip()


class Assistant(models.Model):
    """
    An agent of the catalog: a chat that starts with its own instructions (a
    lawyer, a teacher, a programmer…). Users browse them by category, search
    them, or let the AI find one, and start a chat from the agent's card.
    """

    slug = models.SlugField(max_length=40, unique=True,
                            help_text="In its share link: t.me/<bot>?start=agent_<slug>.")
    category = models.ForeignKey(Category, null=True, blank=True, on_delete=models.SET_NULL,
                                 related_name="agents")
    emoji = models.CharField(max_length=8, blank=True)
    name = models.CharField(max_length=60, help_text="Translated like the bot's texts.")
    description = models.CharField(
        max_length=160, help_text="One line on the list, translated like the bot's texts.")
    first_message = models.CharField(
        max_length=200, blank=True,
        help_text="What the bot asks for when it starts, e.g. “Send the text to translate.” "
                  "Translated like the bot's texts.")
    instructions = models.TextField(
        help_text="Told to the AI before every message of the chat (in English; it answers "
                  "in the user's language).")
    starters = models.JSONField(
        default=list, blank=True,
        help_text="Up to three questions users can tap to begin, as a JSON list of texts. "
                  "Translated like the bot's texts.")
    tags = models.CharField(
        max_length=300, blank=True,
        help_text="Words people may search with, comma-separated, in English (the name and "
                  "line are searched in the user's language too).")
    model = models.ForeignKey(
        "ai.AIModel", null=True, blank=True, on_delete=models.SET_NULL,
        limit_choices_to={"use": "chat"},
        help_text="Empty: the model the user chose for their chats.")
    featured = models.BooleanField(default=False,
                                   help_text="Offered first when a user starts a new chat.")
    active = models.BooleanField(default=True)
    sort_order = models.PositiveSmallIntegerField(default=100)

    class Meta:
        ordering = ("sort_order", "name")
        verbose_name = "agent"

    def __str__(self):
        return f"{self.emoji} {self.name}".strip()


class Profile(models.Model):
    """What a user chose for the tools."""

    account = models.OneToOneField("core.Account", on_delete=models.CASCADE,
                                   related_name="studio")
    chat_model = models.ForeignKey("ai.AIModel", null=True, blank=True,
                                   on_delete=models.SET_NULL, related_name="+")
    image_model = models.ForeignKey("ai.AIModel", null=True, blank=True,
                                    on_delete=models.SET_NULL, related_name="+")
    voice = models.CharField(max_length=24, blank=True)
    saved = models.ManyToManyField(Assistant, blank=True, related_name="+",
                                   help_text="⭐ My agents.")
    daily_limit = models.PositiveIntegerField(
        null=True, blank=True, help_text="The most credits a day the user allows themselves.")
    about = models.TextField(
        blank=True, help_text="About me: what the user wrote about themselves; every chat "
                              "gets it.")

    def __str__(self):
        return f"{self.account}"


class Conversation(models.Model):
    """A chat. The newest one a user touched is their current chat."""

    account = models.ForeignKey("core.Account", on_delete=models.CASCADE,
                                related_name="conversations")
    title = models.CharField(max_length=80, blank=True)
    model = models.ForeignKey("ai.AIModel", null=True, blank=True, on_delete=models.SET_NULL,
                              related_name="+")
    assistant = models.ForeignKey(Assistant, null=True, blank=True, on_delete=models.SET_NULL,
                                  related_name="conversations")
    # The AI named it (from its first question and answer).
    titled = models.BooleanField(default=False)
    # Its earlier messages, summarized by AI (memory.py), up to message
    # memory_upto; the ones after go word for word.
    memory = models.TextField(blank=True)
    memory_upto = models.BigIntegerField(null=True, blank=True)
    # Photos and files the user closed: they no longer go with its messages.
    closed_files = models.JSONField(default=list, blank=True)
    # 🧹 Start fresh: the chat this one carries the memory of.
    parent = models.ForeignKey("self", null=True, blank=True, on_delete=models.SET_NULL,
                               related_name="+")
    deleted = models.BooleanField(
        default=False, help_text="Deleted by the user: gone from their chats, kept here.")
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True, db_index=True)

    class Meta:
        ordering = ("-updated_at",)
        indexes = [models.Index(fields=["account", "-updated_at"])]

    @classmethod
    def of(cls, account):
        """The user's chats: the ones they deleted are not theirs to see."""
        return cls.objects.filter(account=account, deleted=False)

    def __str__(self):
        return self.title or f"Chat #{self.pk}"


class Role(models.TextChoices):
    USER = "user", "User"
    ASSISTANT = "assistant", "AI"


class Message(models.Model):
    conversation = models.ForeignKey(Conversation, on_delete=models.CASCADE,
                                     related_name="messages")
    role = models.CharField(max_length=10, choices=Role.choices)
    text = models.TextField(blank=True)
    # Telegram file_ids of the photos sent with it.
    photos = models.JSONField(default=list, blank=True)
    # The files sent with it: [{"file_id", "name", "mime", "size", "pages"}].
    files = models.JSONField(default=list, blank=True)
    voice = models.BooleanField(default=False, help_text="Typed from a voice note.")
    job = models.ForeignKey("Job", null=True, blank=True, on_delete=models.SET_NULL,
                            related_name="messages")
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ("id",)

    def __str__(self):
        return f"{self.get_role_display()}: {self.text[:60]}"


class Kind(models.TextChoices):
    CHAT = "chat", "Chat answer"
    IMAGE = "image", "Image"
    EDIT = "edit", "Photo edit"
    TRANSCRIBE = "transcribe", "Voice to text"
    SPEECH = "speech", "Text to speech"


class Status(models.TextChoices):
    QUEUED = "queued", "Queued"
    RUNNING = "running", "Being made"
    DONE = "done", "Done"
    FAILED = "failed", "Not done"


ACTIVE = (Status.QUEUED, Status.RUNNING)


class Job(models.Model):
    """
    One request that costs something. The most it can cost is held when it
    starts (`hold`), what it really cost is charged when it is done
    (`charged`, never more than the hold), and everything is returned when it
    fails (billing.services).
    """

    account = models.ForeignKey("core.Account", on_delete=models.PROTECT, related_name="jobs")
    kind = models.CharField(max_length=12, choices=Kind.choices, db_index=True)
    status = models.CharField(max_length=8, choices=Status.choices, default=Status.QUEUED,
                              db_index=True)
    model = models.ForeignKey("ai.AIModel", on_delete=models.PROTECT, related_name="jobs")
    conversation = models.ForeignKey(Conversation, null=True, blank=True,
                                     on_delete=models.SET_NULL, related_name="jobs")
    # The chat's agent, kept when the chat is deleted (usage by agent).
    assistant = models.ForeignKey(Assistant, null=True, blank=True, on_delete=models.SET_NULL,
                                  related_name="jobs")
    # Chat: the message answered. Image: the description. Speech: the text.
    prompt = models.TextField(blank=True)
    # Telegram file_ids sent in: photos, or the voice note.
    photos = models.JSONField(default=list, blank=True)
    # size, quality, voice, seconds, the file name of the audio, what follows.
    options = models.JSONField(default=dict, blank=True)
    screening = models.ForeignKey("safety.Screening", null=True, blank=True,
                                  on_delete=models.SET_NULL, related_name="+")
    demo = models.BooleanField(default=False, help_text="Made in Demo mode: no provider.")

    estimate_micros = models.BigIntegerField(default=0, help_text="The most it can cost you.")
    # What the provider reported: text and file tokens read, tokens written
    # (images: image tokens made).
    input_tokens = models.PositiveIntegerField(default=0)
    output_tokens = models.PositiveIntegerField(default=0)
    # Of input_tokens: read from the provider's cache (cheaper).
    cached_tokens = models.PositiveIntegerField(default=0)
    cost_micros = models.BigIntegerField(default=0, help_text="What it cost you.")
    hold = models.PositiveIntegerField(default=0, help_text="Credits held when it started.")
    charged = models.PositiveIntegerField(default=0, help_text="Credits it was charged.")

    chat_id = models.BigIntegerField()
    # The message the answer is written into (chat, voice to text).
    message_id = models.BigIntegerField(null=True, blank=True)
    # What was made, in Telegram: the photo or voice note, and the original file.
    result_file_id = models.CharField(max_length=255, blank=True)
    original_file_id = models.CharField(max_length=255, blank=True)
    archive_message_id = models.BigIntegerField(null=True, blank=True)
    error = models.CharField(max_length=500, blank=True)

    created_at = models.DateTimeField(auto_now_add=True, db_index=True)
    started_at = models.DateTimeField(null=True, blank=True)
    finished_at = models.DateTimeField(null=True, blank=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ("-id",)
        indexes = [models.Index(fields=["account", "status"])]
        verbose_name = "request"

    def __str__(self):
        return f"#{self.pk} {self.get_kind_display()} ({self.get_status_display()})"


class AlbumItem(models.Model):
    """
    One piece of a Telegram album. Telegram hands a bot an album (photos or
    files sent as one message) as separate updates sharing a media_group_id;
    the pieces wait here a moment, then go to the AI together, as the one
    message the user sent.
    """

    account = models.ForeignKey("core.Account", on_delete=models.CASCADE, related_name="+")
    group = models.CharField(max_length=64, db_index=True)
    chat_id = models.BigIntegerField()
    message_id = models.BigIntegerField()
    caption = models.TextField(blank=True)
    item = models.JSONField(default=dict)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ("message_id",)

    def __str__(self):
        return f"{self.group} #{self.message_id}"
