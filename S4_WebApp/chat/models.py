from django.db import models


class Conversation(models.Model):
    """A single chat thread. This is the 'memory' container -- every message
    that belongs to a conversation is stored here so we can rebuild the
    back-and-forth and hand it back to the LLM as context on the next turn."""

    session_key = models.CharField(max_length=40, db_index=True)
    created_at = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        return f"Conversation #{self.pk} ({self.created_at:%Y-%m-%d %H:%M})"


class Message(models.Model):
    """One turn in a conversation -- either the user's question or the
    assistant's answer. Stored in order so the conversation can be replayed
    both to the screen (chat history) and to the LLM (conversation memory)."""

    ROLE_CHOICES = [
        ("user", "User"),
        ("assistant", "Assistant"),
    ]

    conversation = models.ForeignKey(Conversation, on_delete=models.CASCADE, related_name="messages")
    role = models.CharField(max_length=10, choices=ROLE_CHOICES)
    content = models.TextField()
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["created_at", "id"]

    def __str__(self):
        return f"[{self.role}] {self.content[:60]}"
