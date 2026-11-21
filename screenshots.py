"""Render pages with the Django test client, then screenshot them with headless Chrome.

Usage:
    python screenshots.py

Requires Chrome installed at the path in CHROME and a database that already
contains some classes and sessions (see seed_data.py). The script temporarily
adds demo bookings for the "smoketester" account so every booking state is
visible in the screenshots.
"""
import os
import subprocess
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

CHROME = r"C:\Program Files\Google\Chrome\Application\chrome.exe"
OUT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "screenshots")
os.makedirs(OUT, exist_ok=True)

if not Session.objects.exists():
    raise SystemExit("No sessions found. Run: python seed_data.py")

demo, _ = User.objects.get_or_create(
    username="smoketester", defaults={"email": "smoke@example.com"}
)
demo.set_password("S3cure-Pass!")
demo.save()
Booking.objects.filter(user=demo).delete()

gym_class = GymClass.objects.order_by("name").first()


def ensure_session(start_in, capacity=12):
    start = timezone.now() + start_in
    return Session.objects.create(
        gym_class=gym_class,
        start_time=start,
        end_time=start + timedelta(hours=1),
        capacity=capacity,
    )


far = Session.objects.filter(
    start_time__gt=timezone.now() + timedelta(days=1),
    end_time__lt=timezone.now() + timedelta(days=4),
).order_by("start_time").first() or ensure_session(timedelta(days=3))
Booking.objects.create(user=demo, session=far)

near = Session.objects.filter(
    start_time__gt=timezone.now() + timedelta(minutes=20),
    start_time__lt=timezone.now() + timedelta(hours=2),
).first() or ensure_session(timedelta(minutes=45))
Booking.objects.create(user=demo, session=near)

already = Session.objects.filter(
    start_time__gt=timezone.now() + timedelta(days=4)
).order_by("start_time").first() or ensure_session(timedelta(days=6))
old_booking = Booking.objects.create(user=demo, session=already)
old_booking.status = Booking.Status.CANCELLED
old_booking.save()

member = Client()
member.force_login(demo)

admin_user = User.objects.filter(is_superuser=True).order_by("id").first()
if admin_user is None:
    raise SystemExit("No superuser found. Run: python manage.py createsuperuser")

admin = Client()
admin.force_login(admin_user)

anon = Client()

booked_session = (
    Session.objects.filter(bookings__status="active")
    .select_related("gym_class")
    .order_by("-bookings__created_at")
    .first()
)

pages = [
    ("01-home", member, "/", 1600),
    ("02-register", anon, "/register/", 800),
    ("03-my-bookings", member, "/my-bookings/", 640),
    ("04-admin-classes", admin, "/admin/gym/gymclass/", 1100),
    ("05-admin-sessions", admin, "/admin/gym/session/", 1300),
    ("06-admin-roster", admin, f"/roster/{booked_session.id}/", 900),
]

saved = []
for name, client, url, height in pages:
    response = client.get(url, follow=True)
    if response.status_code != 200:
        print(f"SKIP {name}: {response.status_code} {url}")
        continue
    path = os.path.join(OUT, name + ".html")
    with open(path, "w", encoding="utf-8") as handle:
        handle.write(response.content.decode("utf-8"))
    saved.append((name, path, height))
    print(f"rendered {name} <- {url}")

for name, path, height in saved:
    png = os.path.join(OUT, name + ".png")
    command = [
        CHROME,
        "--headless=new",
        "--disable-gpu",
        "--hide-scrollbars",
        "--force-device-scale-factor=1",
        f"--window-size=1400,{height}",
        f"--screenshot={png}",
        f"file:///{path.replace(os.sep, '/')}",
    ]
    subprocess.run(command, capture_output=True, timeout=60)
    if os.path.exists(png):
        print(f"screenshot {name}.png ({os.path.getsize(png)} bytes)")
    else:
        print(f"FAILED screenshot {name}")

for _, path, _ in saved:
    os.remove(path)

print("done")
