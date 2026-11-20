from django.conf import settings
from django.db import models
from django.utils import timezone

CANCEL_WINDOW_HOURS = 2


class GymClass(models.Model):
    name = models.CharField(max_length=100)
    description = models.TextField(blank=True)
    instructor = models.CharField(max_length=100)

    class Meta:
        ordering = ["name"]

    def __str__(self):
        return self.name


class Session(models.Model):
    gym_class = models.ForeignKey(
        GymClass, on_delete=models.CASCADE, related_name="sessions"
    )
    start_time = models.DateTimeField()
    end_time = models.DateTimeField()
    capacity = models.PositiveIntegerField(default=20)

    class Meta:
        ordering = ["start_time"]

    def __str__(self):
        return f"{self.gym_class.name} - {self.start_time:%Y-%m-%d %H:%M}"

    @property
    def booked_count(self):
        return self.bookings.filter(status=Booking.Status.ACTIVE).count()

    @property
    def available_spots(self):
        return max(self.capacity - self.booked_count, 0)

    @property
    def is_full(self):
        return self.available_spots <= 0

    @property
    def has_started(self):
        return self.start_time <= timezone.now()

    @property
    def minutes_until_start(self):
        delta = self.start_time - timezone.now()
        return int(delta.total_seconds() // 60)

    @property
    def can_cancel(self):
        return self.minutes_until_start >= CANCEL_WINDOW_HOURS * 60

    @property
    def is_past(self):
        return self.end_time <= timezone.now()

    def booking_error_for(self, user):
        """Return a reason string if `user` cannot book this session, else None."""
        if user.is_staff:
            return "Administrators cannot book sessions."
        if self.has_started:
            return "This session has already started."
        if self.is_full:
            return "This session is fully booked."
        if self.bookings.filter(user=user).exists():
            return "You have already booked this session."
        return None

    def cancellation_error_for(self, user):
        """Return a reason string if `user` cannot cancel, else None."""
        booking = self.bookings.filter(user=user).first()
        if booking is None:
            return "You have no booking for this session."
        if booking.status == Booking.Status.CANCELLED:
            return "This booking is already cancelled."
        if self.has_started:
            return "This session has already started."
        if not self.can_cancel:
            return (
                f"Cancellations are only allowed at least "
                f"{CANCEL_WINDOW_HOURS} hours before the session starts."
            )
        return None


class Booking(models.Model):
    class Status(models.TextChoices):
        ACTIVE = "active", "Active"
        CANCELLED = "cancelled", "Cancelled"

    user = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="bookings"
    )
    session = models.ForeignKey(
        Session, on_delete=models.CASCADE, related_name="bookings"
    )
    created_at = models.DateTimeField(auto_now_add=True)
    status = models.CharField(
        max_length=10, choices=Status.choices, default=Status.ACTIVE
    )

    class Meta:
        ordering = ["-created_at"]
        constraints = [
            models.UniqueConstraint(
                fields=["user", "session"], name="unique_user_session_booking"
            )
        ]

    def __str__(self):
        return f"{self.user.username} -> {self.session} ({self.status})"

    @property
    def cancellation_error(self):
        """Reason this booking cannot be cancelled, or None if it can be."""
        if self.status != self.Status.ACTIVE:
            return "This booking is already cancelled."
        return self.session.cancellation_error_for(self.user)
