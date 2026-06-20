from django.shortcuts import get_object_or_404, redirect, render
from django.views.decorators.http import require_POST

from .models import Conversation, Message
from .rag_service import answer_question


def _get_or_create_conversation(request):
    """Each browser session gets its own conversation thread -- this is the
    'memory container'. No login system for this basic version, so the
    Django session key is what ties a visitor back to their conversation."""
    if not request.session.session_key:
        request.session.create()
    session_key = request.session.session_key

    conversation = Conversation.objects.filter(session_key=session_key).order_by("-created_at").first()
    if conversation is None:
        conversation = Conversation.objects.create(session_key=session_key)

    return conversation


def chat_view(request):
    conversation = _get_or_create_conversation(request)
    messages = conversation.messages.all()
    return render(request, "chat/chat.html", {"messages": messages})


@require_POST
def ask_view(request):
    conversation = _get_or_create_conversation(request)

    query = request.POST.get("query", "").strip()
    if query:
        # Snapshot the history *before* adding this turn, so the new
        # question isn't redundantly replayed back to the model as its own
        # "memory".
        history_messages = list(conversation.messages.all())

        Message.objects.create(conversation=conversation, role="user", content=query)
        answer = answer_question(query, history_messages)
        Message.objects.create(conversation=conversation, role="assistant", content=answer)

    return redirect("chat")


@require_POST
def new_conversation_view(request):
    """Start a fresh thread -- clears memory by simply pointing the session
    at a brand-new Conversation row; old ones stay in the DB untouched."""
    if not request.session.session_key:
        request.session.create()

    Conversation.objects.create(session_key=request.session.session_key)
    return redirect("chat")
