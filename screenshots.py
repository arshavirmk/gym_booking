"""Render pages with the Django test client, then screenshot them with headless Chrome."""
import os
import subprocess
import sys

import django

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "gymproject.settings")
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
django.setup()

from django.contrib.auth.models import User  # noqa: E402
from django.test import Client  # noqa: E402
from django.utils import timezone  # noqa: E402
from datetime import timedelta  # noqa: E402

from gym.models import Booking, GymClass, Session  # noqa: E402

CHROME = r"C:\Program Files\Google\Chrome\Application\chrome.exe"
OUT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "screenshots")
os.makedirs(OUT, exist_ok=True)

# Build a tidy demo set of bookings for the screenshot user
demo, _ = User.objects.get_or_create(
    username="smoketester", defaults={"email": "smoke@example.com"}
)
demo.set_password("S3cure-Pass!")
demo.save()
Booking.objects.filter(user=demo).delete()


def ensure_session(start_in, capacity=12):
    start = timezone.now() + start_in
    return Session.objects.create(
        gym_class=GymClass.objects.order_by("name").first(),
        start_time=start,
        end_time=start + timedelta(hours=1),
        capacity=capacity,
    )


far = Session.objects.filter(
    start_time__gt=timezone.now() + timedelta(days=1),
    end_time__lt=timezone.now() + timedelta(days=4),
).order_by("start_time").first() or ensure_session(timedelta(days=3))
Booking.objects.create(user=demo, session=far)          # cancellable -> button

near = ensure_session(timedelta(minutes=45))
Booking.objects.create(user=demo, session=near)          # inside window -> reason

already = Session.objects.filter(
    start_time__gt=timezone.now() + timedelta(days=4)
).order_by("start_time").first() or ensure_session(timedelta(days=6))
old = Booking.objects.create(user=demo, session=already)
old.status = Booking.Status.CANCELLED
old.save()

member = Client()
member.force_login(demo)

admin = Client()
admin.force_login(User.objects.get(username="admin"))

anon = Client()

pages = [
    ("01-home", member, "/", 1600),
    ("02-register", anon, "/register/", 800),
    ("03-my-bookings", member, "/my-bookings/", 620),
    ("04-admin-classes", admin, "/admin/gym/gymclass/", 1100),
    ("05-admin-sessions", admin, "/admin/gym/session/", 1300),
    ("06-admin-roster", admin, "/", 900),
]

# Roster needs a real session id
booked_session = (
    Session.objects.filter(bookings__status="active")
    .select_related("gym_class")
    .order_by("-bookings__created_at")
    .first()
)
pages[5] = ("06-admin-roster", admin, f"/roster/{booked_session.id}/", 900)

saved = []
for name, client, url, height in pages:
    resp = client.get(url, follow=True)
    if resp.status_code != 200:
        print(f"SKIP {name}: {resp.status_code} {url}")
        continue
    path = os.path.join(OUT, name + ".html")
    with open(path, "w", encoding="utf-8") as fh:
        fh.write(resp.content.decode("utf-8"))
    saved.append((name, path, height))
    print(f"rendered {name} <- {url}")

for name, path, height in saved:
    png = os.path.join(OUT, name + ".png")
    cmd = [
        CHROME, "--headless=new", "--disable-gpu", "--hide-scrollbars",
        "--force-device-scale-factor=1", f"--window-size=1400,{height}",
        f"--screenshot={png}", f"file:///{path.replace(os.sep, '/')}",
    ]
    subprocess.run(cmd, capture_output=True, timeout=60)
    if os.path.exists(png):
        print(f"screenshot {name}.png ({os.path.getsize(png)} bytes)")
    else:
        print(f"FAILED screenshot {name}")

for name, path, _ in saved:
    os.remove(path)
print("done")
