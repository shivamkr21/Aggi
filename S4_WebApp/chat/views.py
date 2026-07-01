import json

from django.contrib.auth import authenticate, login, logout
from django.contrib.auth.decorators import login_required
from django.contrib.auth.models import User
from django.db.models import Count
from django.http import StreamingHttpResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.views.decorators.http import require_POST

from .models import Conversation, Message, UserProfile
from .rag_service import answer_question, answer_question_stream, get_retrieval_query


def login_view(request):
    if request.user.is_authenticated:
        return redirect("chat_home")
    error = None
    if request.method == "POST":
        username = request.POST.get("username", "").strip()
        password = request.POST.get("password", "")
        user = authenticate(request, username=username, password=password)
        if user:
            login(request, user)
            return redirect("chat_home")
        error = "Invalid username or password."
    return render(request, "chat/login.html", {"error": error})


def logout_view(request):
    logout(request)
    return redirect("login")


def register_view(request):
    if request.user.is_authenticated:
        return redirect("chat_home")
    error = None
    if request.method == "POST":
        username = request.POST.get("username", "").strip()
        password = request.POST.get("password", "")
        password2 = request.POST.get("password2", "")
        if not username or not password:
            error = "Username and password are required."
        elif password != password2:
            error = "Passwords do not match."
        elif User.objects.filter(username=username).exists():
            error = "Username already taken."
        else:
            user = User.objects.create_user(username=username, password=password)
            UserProfile.objects.create(user=user)
            login(request, user)
            return redirect("chat_home")
    return render(request, "chat/register.html", {"error": error})


@login_required
def chat_home(request):
    conv = Conversation.objects.filter(user=request.user, is_deleted=False).first()
    if not conv:
        conv = Conversation.objects.create(user=request.user)
    return redirect("chat", conversation_id=conv.id)


@login_required
def chat_view(request, conversation_id):
    # If the conversation doesn't exist, belongs to another user, or has been
    # soft-deleted, redirect home rather than showing a raw 404 page.
    try:
        conversation = Conversation.objects.get(id=conversation_id, user=request.user, is_deleted=False)
    except Conversation.DoesNotExist:
        return redirect("chat_home")
    conversations = Conversation.objects.filter(user=request.user, is_deleted=False)
    messages = conversation.messages.all()
    return render(request, "chat/chat.html", {
        "conversation": conversation,
        "conversations": conversations,
        "messages": messages,
    })


@login_required
@require_POST
def ask_view(request, conversation_id):
    try:
        conversation = Conversation.objects.get(id=conversation_id, user=request.user, is_deleted=False)
    except Conversation.DoesNotExist:
        return redirect("chat_home")

    query = request.POST.get("query", "").strip()
    if not query:
        return redirect("chat", conversation_id=conversation_id)

    history_messages = list(conversation.messages.all())

    # Rewrite follow-up queries into standalone questions for ChromaDB retrieval.
    # The original query is preserved in the DB for comparison; the rewritten
    # version is stored in rewritten_query so both are visible in the admin.
    retrieval_query = get_retrieval_query(query, history_messages)

    Message.objects.create(
        conversation=conversation,
        role="user",
        content=query,
        rewritten_query=retrieval_query if retrieval_query != query else None,
    )

    is_first_message = not history_messages
    if is_first_message:
        conversation.title = query[:80]
    conversation.save()

    full_response = []
    response_source = ["conversational"]
    captured_citations = [None]

    def sse_generator():
        try:
            for event in answer_question_stream(query, retrieval_query, history_messages):
                if event["type"] == "citations":
                    response_source[0] = "medical"
                    captured_citations[0] = event["content"]
                elif event["type"] == "token":
                    full_response.append(event["content"])
                elif event["type"] == "done":
                    complete = "".join(full_response).strip()
                    Message.objects.create(
                        conversation=conversation,
                        role="assistant",
                        source=response_source[0],
                        citations=captured_citations[0],
                        content=complete,
                    )
                    conversation.save()
                    if is_first_message:
                        yield f"data: {json.dumps({'type': 'title', 'content': conversation.title})}\n\n"
                elif event["type"] == "error":
                    error_text = event["content"]
                    Message.objects.create(
                        conversation=conversation,
                        role="assistant",
                        source=None,
                        content=error_text,
                    )
                    conversation.save()

                yield f"data: {json.dumps(event)}\n\n"

        except Exception:
            error_event = {"type": "error", "content": "Something went wrong. Please try again."}
            yield f"data: {json.dumps(error_event)}\n\n"

    response = StreamingHttpResponse(sse_generator(), content_type="text/event-stream")
    response["Cache-Control"] = "no-cache"
    response["X-Accel-Buffering"] = "no"
    return response


@login_required
@require_POST
def new_conversation_view(request):
    # If an empty conversation already exists, navigate to it instead of
    # creating another blank one.
    empty = (
        Conversation.objects
        .annotate(msg_count=Count("messages"))
        .filter(user=request.user, is_deleted=False, msg_count=0, title="New Chat")
        .first()
    )
    if empty:
        return redirect("chat", conversation_id=empty.id)
    conv = Conversation.objects.create(user=request.user)
    return redirect("chat", conversation_id=conv.id)


@login_required
@require_POST
def rename_conversation_view(request, conversation_id):
    conversation = get_object_or_404(Conversation, id=conversation_id, user=request.user)
    new_title = request.POST.get("title", "").strip()
    if new_title:
        conversation.title = new_title[:80]
        conversation.save(update_fields=["title"])
    return redirect("chat", conversation_id=conversation_id)


@login_required
@require_POST
def delete_conversation_view(request, conversation_id):
    conv = get_object_or_404(Conversation, id=conversation_id, user=request.user)
    # Soft delete — hide from UI but keep in DB so it remains traceable.
    conv.is_deleted = True
    conv.save(update_fields=["is_deleted"])
    remaining = Conversation.objects.filter(user=request.user, is_deleted=False).first()
    if not remaining:
        remaining = Conversation.objects.create(user=request.user)
    return redirect("chat", conversation_id=remaining.id)
