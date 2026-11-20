from django.contrib import admin

from .models import Booking, GymClass, Session


class SessionInline(admin.TabularInline):
    model = Session
    extra = 1
    fields = ("start_time", "end_time", "capacity")
    readonly_fields = ()


@admin.register(GymClass)
class GymClassAdmin(admin.ModelAdmin):
    list_display = ("name", "instructor", "session_count")
    search_fields = ("name", "instructor")
    inlines = [SessionInline]

    @admin.display(description="Sessions")
    def session_count(self, obj):
        return obj.sessions.count()


class BookingInline(admin.TabularInline):
    model = Booking
    extra = 0
    readonly_fields = ("user", "created_at", "status")
    can_delete = False
    fields = ("user", "status", "created_at")


@admin.register(Session)
class SessionAdmin(admin.ModelAdmin):
    list_display = (
        "gym_class",
        "start_time",
        "end_time",
        "capacity",
        "booked_count_display",
        "available_spots_display",
    )
    list_filter = ("gym_class", "start_time")
    search_fields = ("gym_class__name", "gym_class__instructor")
    inlines = [BookingInline]

    @admin.display(description="Booked")
    def booked_count_display(self, obj):
        return obj.booked_count

    @admin.display(description="Spots left")
    def available_spots_display(self, obj):
        return obj.available_spots


@admin.register(Booking)
class BookingAdmin(admin.ModelAdmin):
    list_display = ("user", "session", "status", "created_at")
    list_filter = ("status", "session__gym_class")
    search_fields = ("user__username", "user__email", "session__gym_class__name")
    readonly_fields = ("user", "session", "created_at")
