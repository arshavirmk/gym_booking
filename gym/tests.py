from datetime import timedelta

from django.contrib.auth.models import User
from django.db import IntegrityError, transaction
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from .models import CANCEL_WINDOW_HOURS, Booking, GymClass, Session


def make_session(start_in_hours=48, capacity=2, end_in_hours=None):
    gym_class, _ = GymClass.objects.get_or_create(
        name="Yoga", defaults={"description": "d", "instructor": "Ava"}
    )
    start = timezone.now() + timedelta(hours=start_in_hours)
    end = start + timedelta(hours=1 if end_in_hours is None else end_in_hours)
    return Session.objects.create(
        gym_class=gym_class, start_time=start, end_time=end, capacity=capacity
    )


class BookingModelTests(TestCase):
    def setUp(self):
        self.alice = User.objects.create_user("alice", password="pw12345!")
        self.bob = User.objects.create_user("bob", password="pw12345!")
        self.session = make_session()

    def test_initial_spots_equal_capacity(self):
        self.assertEqual(self.session.available_spots, 2)
        self.assertFalse(self.session.is_full)

    def test_booking_reduces_available_spots(self):
        Booking.objects.create(user=self.alice, session=self.session)
        self.session.refresh_from_db()
        self.assertEqual(self.session.booked_count, 1)
        self.assertEqual(self.session.available_spots, 1)

    def test_cancelled_booking_frees_the_spot(self):
        b = Booking.objects.create(user=self.alice, session=self.session)
        b.status = Booking.Status.CANCELLED
        b.save()
        self.session.refresh_from_db()
        self.assertEqual(self.session.booked_count, 0)
        self.assertEqual(self.session.available_spots, 2)

    def test_unique_constraint_blocks_double_booking(self):
        Booking.objects.create(user=self.alice, session=self.session)
        with self.assertRaises(IntegrityError), transaction.atomic():
            Booking.objects.create(user=self.alice, session=self.session)

    def test_two_different_users_can_book(self):
        Booking.objects.create(user=self.alice, session=self.session)
        Booking.objects.create(user=self.bob, session=self.session)
        self.assertEqual(self.session.booked_count, 2)


class BookingRuleTests(TestCase):
    def setUp(self):
        self.alice = User.objects.create_user("alice", password="pw12345!")
        self.session = make_session()

    def test_cannot_book_started_session(self):
        self.session.start_time = timezone.now() - timedelta(minutes=5)
        self.session.end_time = timezone.now() + timedelta(minutes=55)
        self.session.save()
        self.assertIsNotNone(self.session.booking_error_for(self.alice))

    def test_cannot_book_past_session(self):
        self.session.start_time = timezone.now() - timedelta(hours=5)
        self.session.end_time = timezone.now() - timedelta(hours=4)
        self.session.save()
        self.assertIsNotNone(self.session.booking_error_for(self.alice))

    def test_can_book_future_session(self):
        self.assertIsNone(self.session.booking_error_for(self.alice))

    def test_cannot_book_full_session(self):
        other = User.objects.create_user("other", password="pw12345!")
        Booking.objects.create(user=other, session=self.session)
        Booking.objects.create(
            user=User.objects.create_user("x", password="pw12345!"),
            session=self.session,
        )
        self.assertTrue(self.session.is_full)
        self.assertIsNotNone(self.session.booking_error_for(self.alice))

    def test_cannot_book_twice(self):
        Booking.objects.create(user=self.alice, session=self.session)
        reason = self.session.booking_error_for(self.alice)
        self.assertIn("already booked", reason.lower())

    def test_admin_cannot_book(self):
        admin = User.objects.create_user("root", password="pw12345!", is_staff=True)
        self.assertIsNotNone(self.session.booking_error_for(admin))


class CancellationRuleTests(TestCase):
    def setUp(self):
        self.alice = User.objects.create_user("alice", password="pw12345!")
        self.session = make_session()
        self.booking = Booking.objects.create(
            user=self.alice, session=self.session
        )

    def test_can_cancel_more_than_window_before_start(self):
        self.assertTrue(self.session.can_cancel)
        self.assertIsNone(self.session.cancellation_error_for(self.alice))

    def test_cannot_cancel_inside_the_window(self):
        self.session.start_time = timezone.now() + timedelta(
            hours=CANCEL_WINDOW_HOURS, minutes=-5
        )
        self.session.end_time = self.session.start_time + timedelta(hours=1)
        self.session.save()
        self.assertFalse(self.session.can_cancel)
        reason = self.session.cancellation_error_for(self.alice)
        self.assertIn(f"{CANCEL_WINDOW_HOURS} hours", reason)

    def test_cannot_cancel_started_session(self):
        self.session.start_time = timezone.now() - timedelta(minutes=10)
        self.session.end_time = timezone.now() + timedelta(hours=1)
        self.session.save()
        reason = self.session.cancellation_error_for(self.alice)
        self.assertIn("already started", reason.lower())

    def test_cannot_cancel_already_cancelled(self):
        self.booking.status = Booking.Status.CANCELLED
        self.booking.save()
        reason = self.session.cancellation_error_for(self.alice)
        self.assertIn("already cancelled", reason.lower())


class ViewTests(TestCase):
    def setUp(self):
        self.alice = User.objects.create_user("alice", password="pw12345!")
        self.bob = User.objects.create_user("bob", password="pw12345!")
        self.session = make_session()
        self.home = reverse("home")

    def test_home_lists_session_and_spots(self):
        resp = self.client.get(self.home)
        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, self.session.gym_class.name)
        self.assertContains(resp, "Spots left")

    def test_anonymous_cannot_book(self):
        resp = self.client.post(reverse("book_session", args=[self.session.id]))
        self.assertEqual(resp.status_code, 302)
        self.assertIn(reverse("login"), resp.url)
        self.assertEqual(Booking.objects.count(), 0)

    def test_member_can_book_and_sees_it_in_my_bookings(self):
        self.client.login(username="alice", password="pw12345!")
        resp = self.client.post(
            reverse("book_session", args=[self.session.id]), follow=True
        )
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(Booking.objects.filter(user=self.alice).count(), 1)

        page = self.client.get(reverse("my_bookings"))
        self.assertContains(page, self.session.gym_class.name)

    def test_double_booking_via_view_is_rejected(self):
        self.client.login(username="alice", password="pw12345!")
        url = reverse("book_session", args=[self.session.id])
        self.client.post(url)
        self.client.post(url)
        self.assertEqual(Booking.objects.filter(user=self.alice).count(), 1)

    def test_booking_beyond_capacity_is_rejected(self):
        self.session.capacity = 1
        self.session.save()
        Booking.objects.create(user=self.bob, session=self.session)
        self.client.login(username="alice", password="pw12345!")
        self.client.post(reverse("book_session", args=[self.session.id]))
        self.assertEqual(Booking.objects.filter(user=self.alice).count(), 0)

    def test_booking_started_session_is_rejected(self):
        self.session.start_time = timezone.now() - timedelta(minutes=1)
        self.session.end_time = timezone.now() + timedelta(hours=1)
        self.session.save()
        self.client.login(username="alice", password="pw12345!")
        self.client.post(reverse("book_session", args=[self.session.id]))
        self.assertEqual(Booking.objects.count(), 0)

    def test_member_can_cancel(self):
        booking = Booking.objects.create(user=self.alice, session=self.session)
        self.client.login(username="alice", password="pw12345!")
        self.client.post(reverse("cancel_booking", args=[booking.id]))
        booking.refresh_from_db()
        self.assertEqual(booking.status, Booking.Status.CANCELLED)

    def test_cannot_cancel_inside_window_via_view(self):
        self.session.start_time = timezone.now() + timedelta(hours=1)
        self.session.end_time = self.session.start_time + timedelta(hours=1)
        self.session.save()
        booking = Booking.objects.create(user=self.alice, session=self.session)
        self.client.login(username="alice", password="pw12345!")
        self.client.post(reverse("cancel_booking", args=[booking.id]))
        booking.refresh_from_db()
        self.assertEqual(booking.status, Booking.Status.ACTIVE)

    def test_user_cannot_see_or_cancel_other_users_bookings(self):
        other_booking = Booking.objects.create(
            user=self.bob, session=self.session
        )
        self.client.login(username="alice", password="pw12345!")

        page = self.client.get(reverse("my_bookings"))
        self.assertNotContains(page, "bob")

        resp = self.client.post(
            reverse("cancel_booking", args=[other_booking.id])
        )
        self.assertEqual(resp.status_code, 404)
        other_booking.refresh_from_db()
        self.assertEqual(other_booking.status, Booking.Status.ACTIVE)

    def test_roster_requires_staff(self):
        self.client.login(username="alice", password="pw12345!")
        resp = self.client.get(reverse("session_roster", args=[self.session.id]))
        self.assertEqual(resp.status_code, 302)
        self.assertIn(reverse("home"), resp.url)

    def test_roster_shows_booked_members_to_staff(self):
        Booking.objects.create(user=self.alice, session=self.session)
        admin = User.objects.create_user(
            "root", password="pw12345!", is_staff=True
        )
        self.client.login(username="root", password="pw12345!")
        resp = self.client.get(reverse("session_roster", args=[self.session.id]))
        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, "alice")
        self.assertContains(resp, admin.username)

    def test_registration_creates_logged_in_user(self):
        resp = self.client.post(
            reverse("register"),
            {
                "username": "newbie",
                "email": "newbie@example.com",
                "password1": "S3cure-Pass!",
                "password2": "S3cure-Pass!",
            },
            follow=True,
        )
        self.assertEqual(resp.status_code, 200)
        self.assertTrue(User.objects.filter(username="newbie").exists())
        self.assertEqual(self.client.session.get("_auth_user_id"), str(
            User.objects.get(username="newbie").id
        ))
