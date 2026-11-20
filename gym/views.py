from django.contrib import messages
from django.contrib.auth import login
from django.contrib.auth.decorators import login_required
from django.db import transaction
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone
from django.views.decorators.http import require_POST

from .forms import RegisterForm
from .models import Booking, GymClass, Session


def home(request):
    classes = GymClass.objects.prefetch_related("sessions__bookings")
    now = timezone.now()
    for gym_class in classes:
        gym_class.upcoming_sessions = [
            s for s in gym_class.sessions.all() if s.end_time > now
        ]
    return render(request, "gym/home.html", {"classes": classes, "now": now})


def register(request):
    if request.user.is_authenticated:
        return redirect("home")
    if request.method == "POST":
        form = RegisterForm(request.POST)
        if form.is_valid():
            user = form.save()
            login(request, user)
            messages.success(request, f"Welcome, {user.username}! Your account is ready.")
            return redirect("home")
    else:
        form = RegisterForm()
    return render(request, "gym/register.html", {"form": form})


@login_required
def my_bookings(request):
    bookings = (
        Booking.objects.filter(user=request.user)
        .select_related("session__gym_class")
        .order_by("-created_at")
    )
    return render(request, "gym/my_bookings.html", {"bookings": bookings})


@login_required
@require_POST
@transaction.atomic
def book_session(request, session_id):
    session = get_object_or_404(
        Session.objects.select_for_update(), id=session_id
    )
    reason = session.booking_error_for(request.user)
    if reason:
        messages.error(request, reason)
    else:
        Booking.objects.create(user=request.user, session=session)
        messages.success(request, f"Booked: {session}.")
    return redirect(request.META.get("HTTP_REFERER", "home"))


@login_required
@require_POST
@transaction.atomic
def cancel_booking(request, booking_id):
    booking = get_object_or_404(
        Booking.objects.select_for_update(),
        id=booking_id,
        user=request.user,
    )
    session = booking.session
    reason = session.cancellation_error_for(request.user)
    if reason:
        messages.error(request, reason)
    else:
        booking.status = Booking.Status.CANCELLED
        booking.save(update_fields=["status"])
        messages.success(request, f"Cancelled: {session}.")
    return redirect("my_bookings")


def session_roster(request, session_id):
    """Admin-only: list of members booked into a session."""
    if not request.user.is_staff:
        messages.error(request, "Administrators only.")
        return redirect("home")
    session = get_object_or_404(
        Session.objects.select_related("gym_class").prefetch_related(
            "bookings__user"
        ),
        id=session_id,
    )
    active = session.bookings.filter(status=Booking.Status.ACTIVE).select_related("user")
    cancelled = session.bookings.filter(status=Booking.Status.CANCELLED).select_related(
        "user"
    )
    return render(
        request,
        "gym/roster.html",
        {"session": session, "active": active, "cancelled": cancelled},
    )
