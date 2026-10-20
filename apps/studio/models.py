"""
The AI tools: chats and their messages, the ready-made assistants, and a Job
for every request that costs something (a chat answer, an image, a voice
note turned into text, a text read aloud).

Media is never stored here: photos and voice notes stay in Telegram (and the
archive channel) and are kept by file_id. Chat texts are kept, so the AI
remembers a conversation; users can delete a chat.
"""

from django.db import models


class Assistant(models.Model):
    """A ready-made helper: a chat that starts with its own instructions."""

    slug = models.SlugField(max_length=40, unique=True)
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
    model = models.ForeignKey(
        "ai.AIModel", null=True, blank=True, on_delete=models.SET_NULL,
        limit_choices_to={"use": "chat"},
        help_text="Empty: the model the user chose for their chats.")
    active = models.BooleanField(default=True)
    sort_order = models.PositiveSmallIntegerField(default=100)

    class Meta:
        ordering = ("sort_order", "name")

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
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True, db_index=True)

    class Meta:
        ordering = ("-updated_at",)
        indexes = [models.Index(fields=["account", "-updated_at"])]

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
