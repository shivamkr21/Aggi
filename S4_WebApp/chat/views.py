import json
import logging
import queue
import threading
import time

from django.contrib.auth import authenticate, login, logout
from django.contrib.auth.decorators import login_required
from django.contrib.auth.models import User
from django.db import connection as db_connection
from django.db.models import Count
from django.http import JsonResponse, StreamingHttpResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.views.decorators.http import require_POST

# In-memory cancellation signals keyed by conversation_id.
# Each entry is a threading.Event; worker threads check it between tokens.
_cancel_events: dict[int, threading.Event] = {}
_cancel_lock = threading.Lock()

from .models import Book, Conversation, Message, UserProfile
from .rag_service import answer_question, answer_question_stream, get_retrieval_query

logger = logging.getLogger("aggi.request")


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
            logger.info("LOGIN user=%s", username)
            return redirect("chat_home")
        error = "Invalid username or password."
    return render(request, "chat/login.html", {"error": error})


def logout_view(request):
    logger.info("LOGOUT user=%s", request.user.username if request.user.is_authenticated else "-")
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
    books = Book.objects.filter(is_active=True)
    logger.info("CHAT_OPEN conv=%s user=%s", conversation_id, request.user.username)
    return render(request, "chat/chat.html", {
        "conversation": conversation,
        "conversations": conversations,
        "messages": messages,
        "books": books,
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

    history_messages = list(conversation.messages.filter(status="complete"))

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

    # Pre-create the assistant message as pending so it survives a tab close.
    assistant_msg = Message.objects.create(
        conversation=conversation,
        role="assistant",
        status="pending",
        content="",
    )

    cancel_event = threading.Event()
    with _cancel_lock:
        _cancel_events[conversation_id] = cancel_event

    token_queue: queue.Queue = queue.Queue()
    username = request.user.username
    ask_start = time.monotonic()

    def llm_worker():
        full_response = []
        response_source = ["conversational"]
        captured_citations = [None]
        try:
            for event in answer_question_stream(query, retrieval_query, history_messages):
                if cancel_event.is_set():
                    partial = "".join(full_response).strip()
                    assistant_msg.content = partial or "[Response stopped]"
                    assistant_msg.status = "cancelled"
                    assistant_msg.save(update_fields=["content", "status"])
                    token_queue.put({"type": "cancelled", "content": ""})
                    return

                if event["type"] == "citations":
                    response_source[0] = "medical"
                    captured_citations[0] = event["content"]
                elif event["type"] == "token":
                    full_response.append(event["content"])
                elif event["type"] == "done":
                    complete = "".join(full_response).strip()
                    assistant_msg.content = complete
                    assistant_msg.source = response_source[0]
                    assistant_msg.citations = captured_citations[0]
                    assistant_msg.status = "complete"
                    assistant_msg.save()
                    duration_ms = round((time.monotonic() - ask_start) * 1000)
                    logger.info(
                        "ASK conv=%s msg_id=%s user=%s source=%s duration=%dms",
                        conversation.id, assistant_msg.id, username,
                        response_source[0], duration_ms,
                    )
                    conversation.save()
                    if is_first_message:
                        token_queue.put({"type": "title", "content": conversation.title})
                elif event["type"] == "error":
                    assistant_msg.content = event["content"]
                    assistant_msg.status = "complete"
                    assistant_msg.save()
                    conversation.save()

                token_queue.put(event)

        except Exception:
            assistant_msg.status = "complete"
            assistant_msg.content = "Something went wrong. Please try again."
            assistant_msg.save(update_fields=["content", "status"])
            token_queue.put({"type": "error", "content": "Something went wrong. Please try again."})
        finally:
            token_queue.put(None)  # sentinel — tells SSE generator to stop
            with _cancel_lock:
                _cancel_events.pop(conversation_id, None)
            db_connection.close()  # release DB connection owned by this thread

    threading.Thread(target=llm_worker, daemon=True).start()

    def sse_generator():
        while True:
            event = token_queue.get()
            if event is None:
                break
            yield f"data: {json.dumps(event)}\n\n"

    http_response = StreamingHttpResponse(sse_generator(), content_type="text/event-stream")
    http_response["Cache-Control"] = "no-cache"
    http_response["X-Accel-Buffering"] = "no"
    return http_response


@login_required
@require_POST
def cancel_ask_view(request, conversation_id):
    with _cancel_lock:
        event = _cancel_events.get(conversation_id)
        if event:
            event.set()
    return JsonResponse({"status": "ok"})


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
