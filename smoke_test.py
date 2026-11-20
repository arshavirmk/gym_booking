"""End-to-end smoke test against the real database (not the test DB)."""
import os
import sys
from datetime import timedelta

import django

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "gymproject.settings")
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
django.setup()

from django.test import Client  # noqa: E402
from django.utils import timezone  # noqa: E402

from gym.models import Booking, Session  # noqa: E402

results = []


def check(label, ok, extra=""):
    results.append((ok, label, extra))
    print(f"{'PASS' if ok else 'FAIL'}  {label} {extra}")


c = Client()

# 1. Pages load anonymously
for url, name in [("/", "home"), ("/login/", "login"), ("/register/", "register"),
                  ("/my-bookings/", "my-bookings (should redirect)")]:
    r = c.get(url)
    check(f"GET {url}", (r.status_code == 200) if name != "my-bookings (should redirect)"
          else r.status_code == 302, f"-> {r.status_code}")

# 2. Anonymous booking is blocked
# Pick a session comfortably beyond the 2-hour cancellation window.
future = (
    Session.objects.filter(start_time__gt=timezone.now() + timedelta(hours=4))
    .order_by("start_time")
    .first()
)
r = c.post(f"/book/{future.id}/")
target = getattr(r, "url", "")
check("Anonymous booking blocked", r.status_code == 302 and "/login/" in target,
      f"-> {r.status_code} {target}")

# 3. Register a fresh member
username, pw = "smoketester", "S3cure-Pass!"
from django.contrib.auth.models import User  # noqa: E402
User.objects.filter(username=username).delete()
r = c.post("/register/", {"username": username, "email": "smoke@example.com",
                          "password1": pw, "password2": pw}, follow=True)
check("Registration creates account",
      User.objects.filter(username=username).exists() and r.status_code == 200)

# 4. Book the future session
r = c.post(f"/book/{future.id}/", follow=True)
b = Booking.objects.filter(user__username=username, session=future).first()
check("Member can book a future session", b is not None and b.status == "active")

# 5. Double booking blocked
r = c.post(f"/book/{future.id}/", follow=True)
count = Booking.objects.filter(user__username=username, session=future).count()
check("Double booking rejected", count == 1, f"(bookings={count})")

# 6. Spots went down
future.refresh_from_db()
check("Available spots decreased", future.booked_count == 1,
      f"(booked={future.booked_count}/{future.capacity})")

# 7. My Bookings page shows it
r = c.get("/my-bookings/")
check("My Bookings lists the booking",
      r.status_code == 200 and str(future.gym_class.name).encode() in r.content,
      f"-> {r.status_code}")

# 8. Past session cannot be booked
past = Session.objects.filter(end_time__lt=timezone.now()).first()
if past:
    before = Booking.objects.filter(session=past).count()
    c.post(f"/book/{past.id}/", follow=True)
    after = Booking.objects.filter(session=past).count()
    check("Past session booking rejected", before == after)
else:
    check("Past session exists (seed)", False, "no past session found")

# 9. Full session cannot be booked
from gym.models import GymClass  # noqa: E402

fill_target = Session.objects.filter(start_time__gt=timezone.now()).order_by(
    "start_time").last()
original_capacity = fill_target.capacity
fill_target.capacity = Booking.objects.filter(
    session=fill_target, status="active").count() + 1
fill_target.save()
filler_i = 0
while not fill_target.is_full:
    filler_i += 1
    u, _ = User.objects.get_or_create(username=f"filler{filler_i}",
                                      defaults={"password": "pw12345!"})
    Booking.objects.get_or_create(user=u, session=fill_target)
fill_target.refresh_from_db()
before = fill_target.booked_count
c.post(f"/book/{fill_target.id}/", follow=True)
fill_target.refresh_from_db()
check("Full session booking rejected",
      fill_target.is_full and fill_target.booked_count == before,
      f"(booked={fill_target.booked_count}/{fill_target.capacity})")
# Clean up the temporary full session so the demo database stays realistic
Booking.objects.filter(session=fill_target).exclude(
    user__username=username).delete()
fill_target.capacity = original_capacity
fill_target.save()

# 10. Cancellation works when far from start
r = c.post(f"/cancel/{b.id}/", follow=True)
b.refresh_from_db()
check("Cancellation succeeds >2h before start", b.status == "cancelled",
      f"(status={b.status})")

# 10b. Rebooking the same session after cancellation is allowed
c.post(f"/book/{future.id}/", follow=True)
active = Booking.objects.filter(
    user__username=username, session=future, status=Booking.Status.ACTIVE
).count()
total = Booking.objects.filter(user__username=username, session=future).count()
check("Rebooking after cancellation succeeds", active == 1 and total == 2,
      f"(active={active}, total rows={total})")

# 10c. Booking twice while still active is still blocked
c.post(f"/book/{future.id}/", follow=True)
active = Booking.objects.filter(
    user__username=username, session=future, status=Booking.Status.ACTIVE
).count()
check("Second active booking still rejected", active == 1, f"(active={active})")

# 11. Cancellation blocked inside the 2-hour window
near = Session.objects.create(
    gym_class=GymClass.objects.first(),
    start_time=timezone.now() + timedelta(minutes=30),
    end_time=timezone.now() + timedelta(minutes=90),
    capacity=5,
)
nb = Booking.objects.create(user=User.objects.get(username=username), session=near)
c.post(f"/cancel/{nb.id}/", follow=True)
nb.refresh_from_db()
check("Cancellation blocked inside 2h window", nb.status == "active",
      f"(status={nb.status})")
nb.delete()
near.delete()  # keep the demo database tidy between runs

# 12. Member cannot cancel someone else's booking
victim_booking = Booking.objects.filter(status="active").exclude(
    user__username=username).first()
if victim_booking is None:
    victim = User.objects.create_user("victim", password="pw12345!")
    victim_booking = Booking.objects.create(user=victim, session=future)
if victim_booking:
    before = victim_booking.status
    r = c.post(f"/cancel/{victim_booking.id}/")
    victim_booking.refresh_from_db()
    check("Cannot cancel another user's booking",
          r.status_code == 404 and victim_booking.status == before,
          f"-> {r.status_code}")
else:
    check("Other user's booking exists", False, "none found")

# 13. Member cannot open the roster page
if future:
    r = c.get(f"/roster/{future.id}/")
    target = getattr(r, "url", "")
    check("Member blocked from roster", r.status_code == 302 and target.endswith("/"),
          f"-> {r.status_code} {target}")

# 14. Admin can log in and sees the roster
c2 = Client()
r = c2.post("/login/", {"username": "admin", "password": "admin12345"}, follow=True)
check("Admin login", r.status_code == 200 and "admin" in r.content.decode().lower())
if future:
    r = c2.get(f"/roster/{future.id}/")
    check("Admin sees roster with members",
          r.status_code == 200 and b"smoketester" in r.content, f"-> {r.status_code}")
    r = c2.get("/admin/")
    check("Admin panel loads", r.status_code == 200)

failed = [x for x in results if not x[0]]
print("\n" + "=" * 60)
print(f"{len(results) - len(failed)}/{len(results)} checks passed")
if failed:
    print("FAILED:")
    for _, label, extra in failed:
        print(f"  - {label} {extra}")
sys.exit(1 if failed else 0)
