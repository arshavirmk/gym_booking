from django.conf import settings
from django.core.exceptions import ValidationError
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
        constraints = [
            models.CheckConstraint(
                condition=models.Q(capacity__gte=1),
                name="session_capacity_positive",
            ),
            models.CheckConstraint(
                condition=models.Q(end_time__gt=models.F("start_time")),
                name="session_end_after_start",
            ),
        ]

    def __str__(self):
        return f"{self.gym_class.name} - {self.start_time:%Y-%m-%d %H:%M}"

    def clean(self):
        super().clean()
        if self.start_time and self.end_time and self.end_time <= self.start_time:
            raise ValidationError(
                {"end_time": "End time must be after the start time."}
            )
        if self.pk and self.capacity < self.booked_count:
            raise ValidationError(
                {
                    "capacity": (
                        "Capacity cannot be lower than the number of active "
                        f"bookings ({self.booked_count})."
                    )
                }
            )

    @property
    def booked_count(self):
        cache = getattr(self, "_prefetched_objects_cache", None)
        if cache and "bookings" in cache:
            return sum(
                1
                for booking in cache["bookings"]
                if booking.status == Booking.Status.ACTIVE
            )
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

    def booking_error_for(self, user):
        """Reason `user` cannot book this session, or None if they can."""
        if user.is_staff:
            return "Administrators cannot book sessions."
        if self.has_started:
            return "This session has already started."
        if self.is_full:
            return "This session is fully booked."
        if self.bookings.filter(user=user, status=Booking.Status.ACTIVE).exists():
            return "You have already booked this session."
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
                fields=["user", "session"],
                condition=models.Q(status="active"),
                name="unique_active_user_session_booking",
            ),
            models.CheckConstraint(
                condition=models.Q(status__in=["active", "cancelled"]),
                name="booking_status_valid",
            ),
        ]
        indexes = [
            models.Index(
                fields=["session", "status"],
                name="booking_session_status_idx",
            )
        ]

    def __str__(self):
        return f"{self.user.username} -> {self.session} ({self.status})"

    @property
    def cancellation_error(self):
        """Reason this booking cannot be cancelled, or None if it can be."""
        if self.status != self.Status.ACTIVE:
            return "This booking is already cancelled."
        if self.session.has_started:
            return "This session has already started."
        if not self.session.can_cancel:
            return (
                "Cancellations are only allowed at least "
                f"{CANCEL_WINDOW_HOURS} hours before the session starts."
            )
        return None

    @property
    def can_rebook(self):
        """True for a cancelled booking whose session is still bookable."""
        if self.status != self.Status.CANCELLED:
            return False
        return self.session.booking_error_for(self.user) is None
