import os
import sys
from datetime import timedelta

import django

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "gymproject.settings")
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
django.setup()

from django.utils import timezone  # noqa: E402

from gym.models import GymClass, Session  # noqa: E402

if GymClass.objects.exists():
    print("Sample data already present, skipping.")
    sys.exit(0)

now = timezone.localtime().replace(minute=0, second=0, microsecond=0)

samples = [
    ("Yoga", "Improve flexibility and calm the mind.", "Ava Patel", 20, 2),
    ("Spin", "High-energy indoor cycling class.", "Marcus Lee", 15, 1),
    ("Boxing", "Learn fundamentals and get a full-body workout.", "Diego Ramirez", 12, 3),
    ("Pilates", "Core strength and controlled movement.", "Sofia Novak", 18, 4),
    ("HIIT", "Short bursts of intense exercise.", "Liam O'Connor", 25, 5),
]

for name, desc, instructor, capacity, day_offset in samples:
    gym_class = GymClass.objects.create(
        name=name, description=desc, instructor=instructor
    )
    for hour, duration in ((7, 1), (12, 1), (18, 1.5)):
        start = now + timedelta(days=day_offset)
        start = start.replace(hour=hour)
        end = start + timedelta(hours=duration)
        Session.objects.create(
            gym_class=gym_class,
            start_time=start,
            end_time=end,
            capacity=capacity,
        )
    # One session in the past so we can test the "already started" rule
    past_start = now - timedelta(days=1)
    Session.objects.create(
        gym_class=gym_class,
        start_time=past_start.replace(hour=10),
        end_time=past_start.replace(hour=11),
        capacity=capacity,
    )

print(f"Created {GymClass.objects.count()} classes and {Session.objects.count()} sessions.")
