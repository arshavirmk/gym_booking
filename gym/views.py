from django.contrib import messages
from django.contrib.auth import login
from django.contrib.auth.decorators import login_required
from django.core.exceptions import PermissionDenied
from django.db import IntegrityError, transaction
from django.db.models import Prefetch
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone
from django.utils.http import url_has_allowed_host_and_scheme

from .forms import RegisterForm
from .models import Booking, GymClass, Session


def _safe_redirect_target(request, default="home"):
    """Return a validated local URL to redirect to, or `default`."""
    target = request.POST.get("next") or request.GET.get("next") or ""
    if target and url_has_allowed_host_and_scheme(
        target,
        allowed_hosts={request.get_host()},
        require_https=request.is_secure(),
    ):
        return target
    return default


def home(request):
    classes = GymClass.objects.prefetch_related(
        Prefetch(
            "sessions",
            queryset=Session.objects.filter(
                end_time__gt=timezone.now()
            ).prefetch_related("bookings"),
            to_attr="upcoming_sessions",
        )
    )
    return render(request, "gym/home.html", {"classes": classes})


def register(request):
    if request.user.is_authenticated:
        return redirect("home")
    if request.method == "POST":
        form = RegisterForm(request.POST)
        if form.is_valid():
            user = form.save()
            login(request, user)
            messages.success(
                request, f"Welcome, {user.username}! Your account is ready."
            )
            return redirect("home")
    else:
        form = RegisterForm()
    return render(request, "gym/register.html", {"form": form})


@login_required
def my_bookings(request):
    bookings = (
        Booking.objects.filter(user=request.user)
        .select_related("user", "session__gym_class")
        .order_by("-created_at")
    )
    return render(request, "gym/my_bookings.html", {"bookings": bookings})


@login_required
def book_session(request, session_id):
    if request.method != "POST":
        messages.info(request, "Choose a session and press the Book button.")
        return redirect("home")

    with transaction.atomic():
        session = get_object_or_404(
            Session.objects.select_for_update(), pk=session_id
        )
        reason = session.booking_error_for(request.user)
        if reason:
            messages.error(request, reason)
        else:
            try:
                with transaction.atomic():
                    Booking.objects.create(user=request.user, session=session)
            except IntegrityError:
                messages.error(request, "You have already booked this session.")
            else:
                messages.success(request, f"Booked: {session}.")

    return redirect(_safe_redirect_target(request, "home"))


@login_required
def cancel_booking(request, booking_id):
    if request.method != "POST":
        messages.info(request, "Open My Bookings and press the Cancel button.")
        return redirect("my_bookings")

    with transaction.atomic():
        booking = get_object_or_404(
            Booking.objects.select_for_update(),
            pk=booking_id,
            user=request.user,
        )
        reason = booking.cancellation_error
        if reason:
            messages.error(request, reason)
        else:
            booking.status = Booking.Status.CANCELLED
            booking.save(update_fields=["status"])
            messages.success(request, f"Cancelled: {booking.session}.")

    return redirect("my_bookings")


@login_required
def session_roster(request, session_id):
    """Staff only: the list of members booked into a session."""
    if not request.user.is_staff:
        raise PermissionDenied

    session = get_object_or_404(
        Session.objects.select_related("gym_class"), pk=session_id
    )
    bookings = session.bookings.select_related("user")
    active = bookings.filter(status=Booking.Status.ACTIVE)
    cancelled = bookings.filter(status=Booking.Status.CANCELLED)
    return render(
        request,
        "gym/roster.html",
        {"session": session, "active": active, "cancelled": cancelled},
    )
