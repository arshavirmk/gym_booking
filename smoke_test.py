"""End-to-end smoke test against the real database (not the test database).

Run it after `python manage.py migrate` and `python seed_data.py`:

    python smoke_test.py
"""
import os
import sys
from datetime import timedelta

import django

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "gymproject.settings")
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
django.setup()

from django.contrib.auth.models import User  # noqa: E402
from django.test import Client  # noqa: E402
from django.utils import timezone  # noqa: E402

from gym.models import Booking, GymClass, Session  # noqa: E402

results = []


def check(label, ok, extra=""):
    results.append((ok, label, extra))
    print(f"{'PASS' if ok else 'FAIL'}  {label} {extra}")


def member_client(username, password):
    client = Client()
    response = client.post(
        "/login/", {"username": username, "password": password}, follow=True
    )
    if response.status_code != 200 or b"Hi," not in response.content:
        raise SystemExit(
            f"Could not log in as {username}. Create the accounts first."
        )
    return client


c = Client()

# 1. Public pages load anonymously
for url, expected in [
    ("/", 200),
    ("/login/", 200),
    ("/register/", 200),
    ("/my-bookings/", 302),
]:
    response = c.get(url)
    check(f"GET {url}", response.status_code == expected, f"-> {response.status_code}")

# 2. Anonymous booking is blocked and sends the user to the login page
future = (
    Session.objects.filter(start_time__gt=timezone.now() + timedelta(hours=4))
    .order_by("start_time")
    .first()
)
response = c.post(f"/book/{future.id}/")
target = getattr(response, "url", "")
check(
    "Anonymous booking blocked",
    response.status_code == 302 and "/login/" in target,
    f"-> {response.status_code} {target}",
)

# 3. Register a fresh member
username, password = "smoketester", "S3cure-Pass!"
User.objects.filter(username=username).delete()
response = c.post(
    "/register/",
    {
        "username": username,
        "email": "smoke@example.com",
        "password1": password,
        "password2": password,
    },
    follow=True,
)
check(
    "Registration creates and logs in the account",
    User.objects.filter(username=username).exists()
    and response.status_code == 200
    and b"Hi," in response.content,
)

# 4. Log out and back in through the real login flow
response = c.post("/logout/", follow=True)
check("Logout works", b"Log in" in response.content)
c = member_client(username, password)
check("Login with valid credentials works", True)

# Capture spot count before booking so the check works on repeated runs
future.refresh_from_db()
spots_before = future.booked_count

# 5. Book a future session
c.post(f"/book/{future.id}/", follow=True)
booking = Booking.objects.filter(user__username=username, session=future).first()
check(
    "Member can book a future session",
    booking is not None and booking.status == Booking.Status.ACTIVE,
)

# 6. Double booking blocked
c.post(f"/book/{future.id}/", follow=True)
count = Booking.objects.filter(user__username=username, session=future).count()
check("Double booking rejected", count == 1, f"(rows={count})")

# 7. Spot count decreased by exactly one
future.refresh_from_db()
check(
    "Available spots decreased",
    future.booked_count == spots_before + 1,
    f"(booked={future.booked_count}/{future.capacity}, before={spots_before})",
)

# 8. My Bookings lists it
response = c.get("/my-bookings/")
check(
    "My Bookings lists the booking",
    response.status_code == 200
    and str(future.gym_class.name).encode() in response.content,
    f"-> {response.status_code}",
)

# 9. A GET on the booking endpoint does not 405 after a login redirect
response = c.get(f"/book/{future.id}/")
check(
    "GET on book redirects instead of 405",
    response.status_code == 302,
    f"-> {response.status_code}",
)

# 10. Past sessions cannot be booked
past = Session.objects.filter(end_time__lt=timezone.now()).first()
if past:
    before = Booking.objects.filter(session=past).count()
    c.post(f"/book/{past.id}/", follow=True)
    after = Booking.objects.filter(session=past).count()
    check("Past session booking rejected", before == after)
else:
    check("Past session exists (run seed_data.py)", False, "no past session found")

# 11. Full sessions cannot be booked (throwaway session so no existing
# session, capacity, or other member's booking is touched)
fill_target = Session.objects.create(
    gym_class=GymClass.objects.first(),
    start_time=timezone.now() + timedelta(days=1),
    end_time=timezone.now() + timedelta(days=1, hours=1),
    capacity=1,
)
filler, _ = User.objects.get_or_create(username="filler1")
Booking.objects.get_or_create(user=filler, session=fill_target)
fill_target.refresh_from_db()
before = fill_target.booked_count
c.post(f"/book/{fill_target.id}/", follow=True)
fill_target.refresh_from_db()
check(
    "Full session booking rejected",
    fill_target.is_full and fill_target.booked_count == before,
    f"(booked={fill_target.booked_count}/{fill_target.capacity})",
)
fill_target.delete()  # cascades the filler booking

# 12. Cancellation works well before the start time
c.post(f"/cancel/{booking.id}/", follow=True)
booking.refresh_from_db()
check(
    "Cancellation succeeds >2h before start",
    booking.status == Booking.Status.CANCELLED,
    f"(status={booking.status})",
)

# 13. Re-booking after cancelling is allowed and creates a second row
c.post(f"/book/{future.id}/", follow=True)
active = Booking.objects.filter(
    user__username=username, session=future, status=Booking.Status.ACTIVE
).count()
total = Booking.objects.filter(user__username=username, session=future).count()
check(
    "Rebooking after cancellation succeeds",
    active == 1 and total == 2,
    f"(active={active}, rows={total})",
)

# 14. A second active booking is still rejected
c.post(f"/book/{future.id}/", follow=True)
active = Booking.objects.filter(
    user__username=username, session=future, status=Booking.Status.ACTIVE
).count()
check("Second active booking still rejected", active == 1, f"(active={active})")

# 15. Cancellation inside the 2-hour window is blocked
near = Session.objects.create(
    gym_class=GymClass.objects.first(),
    start_time=timezone.now() + timedelta(minutes=30),
    end_time=timezone.now() + timedelta(minutes=90),
    capacity=5,
)
near_booking = Booking.objects.create(
    user=User.objects.get(username=username), session=near
)
c.post(f"/cancel/{near_booking.id}/", follow=True)
near_booking.refresh_from_db()
check(
    "Cancellation blocked inside 2h window",
    near_booking.status == Booking.Status.ACTIVE,
    f"(status={near_booking.status})",
)
near_booking.delete()
near.delete()

# 16. A member cannot cancel another member's booking
victim_booking = (
    Booking.objects.filter(status="active")
    .exclude(user__username=username)
    .first()
)
if victim_booking is None:
    victim, created = User.objects.get_or_create(username="victim")
    if created:
        victim.set_password("pw12345!")
        victim.save()
    victim_booking = Booking.objects.create(user=victim, session=future)
before_status = victim_booking.status
response = c.post(f"/cancel/{victim_booking.id}/")
victim_booking.refresh_from_db()
check(
    "Cannot cancel another member's booking",
    response.status_code == 404 and victim_booking.status == before_status,
    f"-> {response.status_code}",
)

# 17. Open redirect attempts via ?next= are ignored
response = c.post(
    f"/book/{future.id}/",
    {"next": "https://evil.example.com/"},
)
check(
    "External next parameter ignored",
    response.status_code == 302 and response["Location"] == "/",
    f"-> {response.get('Location')}",
)

# 18. Anonymous users cannot open the roster
response = Client().get(f"/roster/{future.id}/")
check(
    "Anonymous blocked from roster",
    response.status_code == 302 and "/login/" in response["Location"],
    f"-> {response.status_code}",
)

# 19. Members cannot open the roster
response = c.get(f"/roster/{future.id}/")
check(
    "Member blocked from roster",
    response.status_code == 403,
    f"-> {response.status_code}",
)

# 20. A staff account can log in and use the roster and admin panel
staff, created = User.objects.get_or_create(
    username="smokeadmin",
    defaults={"email": "smokeadmin@example.com", "is_staff": True},
)
if created:
    staff.set_password("Sm0ke-Admin!")
    staff.save()
staff_admin = member_client("smokeadmin", "Sm0ke-Admin!")
response = staff_admin.get(f"/roster/{future.id}/")
check(
    "Staff sees the roster",
    response.status_code == 200 and username.encode() in response.content,
    f"-> {response.status_code}",
)
response = staff_admin.get("/admin/")
check("Admin panel loads", response.status_code == 200, f"-> {response.status_code}")

staff.delete()

# Clean up temporary test accounts so repeated runs stay tidy
User.objects.filter(username__startswith="filler").delete()
User.objects.filter(username="victim").delete()

failed = [item for item in results if not item[0]]
print("\n" + "=" * 60)
print(f"{len(results) - len(failed)}/{len(results)} checks passed")
if failed:
    print("FAILED:")
    for _, label, extra in failed:
        print(f"  - {label} {extra}")
sys.exit(1 if failed else 0)
