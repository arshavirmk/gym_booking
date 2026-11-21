import threading
from datetime import timedelta
from unittest import mock

from django.contrib.auth.models import User
from django.core.exceptions import ValidationError
from django.db import IntegrityError, connection, transaction
from django.test import Client, TestCase, TransactionTestCase
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
        booking = Booking.objects.create(user=self.alice, session=self.session)
        booking.status = Booking.Status.CANCELLED
        booking.save()
        self.session.refresh_from_db()
        self.assertEqual(self.session.booked_count, 0)
        self.assertEqual(self.session.available_spots, 2)

    def test_unique_constraint_blocks_two_active_bookings(self):
        Booking.objects.create(user=self.alice, session=self.session)
        with self.assertRaises(IntegrityError), transaction.atomic():
            Booking.objects.create(user=self.alice, session=self.session)

    def test_unique_constraint_allows_rebooking_after_cancel(self):
        first = Booking.objects.create(user=self.alice, session=self.session)
        first.status = Booking.Status.CANCELLED
        first.save()
        Booking.objects.create(user=self.alice, session=self.session)
        self.assertEqual(
            Booking.objects.filter(user=self.alice, session=self.session).count(), 2
        )

    def test_two_different_users_can_book(self):
        Booking.objects.create(user=self.alice, session=self.session)
        Booking.objects.create(user=self.bob, session=self.session)
        self.assertEqual(self.session.booked_count, 2)

    def test_booked_count_uses_prefetched_bookings(self):
        Booking.objects.create(user=self.alice, session=self.session)
        prefetched = Session.objects.prefetch_related("bookings").get(
            pk=self.session.pk
        )
        with self.assertNumQueries(0):
            self.assertEqual(prefetched.booked_count, 1)


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

    def test_can_rebook_after_cancelling(self):
        first = Booking.objects.create(user=self.alice, session=self.session)
        first.status = Booking.Status.CANCELLED
        first.save()
        self.assertIsNone(self.session.booking_error_for(self.alice))

    def test_cancel_does_not_block_other_members(self):
        self.session.capacity = 1
        self.session.save()
        booking = Booking.objects.create(user=self.alice, session=self.session)
        self.assertIsNotNone(
            self.session.booking_error_for(
                User.objects.create_user("carol", password="pw12345!")
            )
        )
        booking.status = Booking.Status.CANCELLED
        booking.save()
        self.assertIsNone(
            self.session.booking_error_for(
                User.objects.get(username="carol")
            )
        )

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
        self.assertIsNone(self.booking.cancellation_error)

    def test_cannot_cancel_inside_the_window(self):
        self.session.start_time = timezone.now() + timedelta(
            hours=CANCEL_WINDOW_HOURS, minutes=-5
        )
        self.session.end_time = self.session.start_time + timedelta(hours=1)
        self.session.save()
        self.assertFalse(self.session.can_cancel)
        reason = self.booking.cancellation_error
        self.assertIn(f"{CANCEL_WINDOW_HOURS} hours", reason)

    def test_cannot_cancel_started_session(self):
        self.session.start_time = timezone.now() - timedelta(minutes=10)
        self.session.end_time = timezone.now() + timedelta(hours=1)
        self.session.save()
        reason = self.booking.cancellation_error
        self.assertIn("already started", reason.lower())

    def test_cannot_cancel_already_cancelled(self):
        self.booking.status = Booking.Status.CANCELLED
        self.booking.save()
        reason = self.booking.cancellation_error
        self.assertIn("already cancelled", reason.lower())

    def test_can_rebook_flag_respects_started_session(self):
        self.booking.status = Booking.Status.CANCELLED
        self.booking.save()
        self.assertTrue(self.booking.can_rebook)
        self.session.start_time = timezone.now() - timedelta(minutes=10)
        self.session.end_time = timezone.now() + timedelta(hours=1)
        self.session.save()
        self.assertFalse(self.booking.can_rebook)

    def test_can_rebook_flag_respects_full_session(self):
        self.booking.status = Booking.Status.CANCELLED
        self.booking.save()
        bob = User.objects.create_user("bob", password="pw12345!")
        self.session.capacity = 1
        self.session.save()
        Booking.objects.create(user=bob, session=self.session)
        self.assertFalse(self.booking.can_rebook)


class SessionIntegrityTests(TestCase):
    def setUp(self):
        self.alice = User.objects.create_user("alice", password="pw12345!")
        self.bob = User.objects.create_user("bob", password="pw12345!")
        self.gym_class = GymClass.objects.create(
            name="Spin", description="d", instructor="Lee"
        )

    def test_database_rejects_zero_capacity(self):
        start = timezone.now() + timedelta(days=1)
        with self.assertRaises(IntegrityError), transaction.atomic():
            Session.objects.create(
                gym_class=self.gym_class,
                start_time=start,
                end_time=start + timedelta(hours=1),
                capacity=0,
            )

    def test_database_rejects_end_before_start(self):
        start = timezone.now() + timedelta(days=1)
        with self.assertRaises(IntegrityError), transaction.atomic():
            Session.objects.create(
                gym_class=self.gym_class,
                start_time=start,
                end_time=start - timedelta(minutes=30),
                capacity=5,
            )

    def test_database_rejects_unknown_booking_status(self):
        session = make_session()
        with self.assertRaises(IntegrityError), transaction.atomic():
            Booking.objects.create(
                user=self.alice, session=session, status="bogus"
            )

    def test_clean_rejects_capacity_below_active_bookings(self):
        session = make_session(capacity=2)
        Booking.objects.create(user=self.alice, session=session)
        Booking.objects.create(user=self.bob, session=session)
        session.capacity = 1
        with self.assertRaises(ValidationError) as caught:
            session.full_clean()
        self.assertIn("capacity", caught.exception.error_dict)

    def test_clean_accepts_capacity_equal_to_active_bookings(self):
        session = make_session(capacity=2)
        Booking.objects.create(user=self.alice, session=session)
        session.capacity = 2
        session.full_clean()

    def test_clean_rejects_invalid_time_range(self):
        session = make_session()
        session.end_time = session.start_time - timedelta(minutes=1)
        with self.assertRaises(ValidationError) as caught:
            session.full_clean()
        self.assertIn("end_time", caught.exception.error_dict)


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

    def test_home_hides_ended_sessions(self):
        past_class = GymClass.objects.create(name="PastOnly", instructor="X")
        start = timezone.now() - timedelta(days=2)
        Session.objects.create(
            gym_class=past_class,
            start_time=start,
            end_time=start + timedelta(hours=1),
            capacity=5,
        )
        resp = self.client.get(self.home)
        self.assertContains(resp, "PastOnly")
        self.assertContains(resp, "No upcoming sessions.")

    def test_home_marks_started_but_unfinished_sessions(self):
        make_session(start_in_hours=-1, end_in_hours=2)
        resp = self.client.get(self.home)
        self.assertContains(resp, "Started")
        self.assertNotContains(resp, ">Book<")

    def test_home_shows_full_badge_and_no_book_button(self):
        self.session.capacity = 1
        self.session.save()
        Booking.objects.create(user=self.bob, session=self.session)
        self.client.login(username="alice", password="pw12345!")
        resp = self.client.get(self.home)
        self.assertContains(resp, "FULL")
        self.assertContains(resp, "Fully booked")
        self.assertNotContains(resp, ">Book<")

    def test_home_shows_login_link_for_anonymous(self):
        resp = self.client.get(self.home)
        self.assertContains(resp, "Log in to book")

    def test_home_shows_roster_link_for_staff_even_when_full(self):
        staff = User.objects.create_user("root", password="pw12345!", is_staff=True)
        full = make_session(capacity=1)
        Booking.objects.create(user=self.bob, session=full)
        self.client.force_login(staff)
        resp = self.client.get(self.home)
        self.assertContains(resp, "View roster")

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

    def test_staff_cannot_book(self):
        staff = User.objects.create_user("root", password="pw12345!", is_staff=True)
        self.client.force_login(staff)
        self.client.post(reverse("book_session", args=[self.session.id]))
        self.assertEqual(
            Booking.objects.filter(user=staff).count(), 0
        )

    def test_get_book_redirects_instead_of_405(self):
        self.client.login(username="alice", password="pw12345!")
        resp = self.client.get(reverse("book_session", args=[self.session.id]))
        self.assertRedirects(resp, self.home)
        self.assertEqual(Booking.objects.count(), 0)

    def test_get_cancel_redirects_instead_of_405(self):
        booking = Booking.objects.create(user=self.alice, session=self.session)
        self.client.login(username="alice", password="pw12345!")
        resp = self.client.get(reverse("cancel_booking", args=[booking.id]))
        self.assertRedirects(resp, reverse("my_bookings"))
        booking.refresh_from_db()
        self.assertEqual(booking.status, Booking.Status.ACTIVE)

    def test_booking_unknown_session_returns_404(self):
        self.client.login(username="alice", password="pw12345!")
        resp = self.client.post(reverse("book_session", args=[999999]))
        self.assertEqual(resp.status_code, 404)

    def test_cancel_unknown_booking_returns_404(self):
        self.client.login(username="alice", password="pw12345!")
        resp = self.client.post(reverse("cancel_booking", args=[999999]))
        self.assertEqual(resp.status_code, 404)

    def test_booking_next_parameter_is_respected(self):
        self.client.login(username="alice", password="pw12345!")
        resp = self.client.post(
            reverse("book_session", args=[self.session.id]),
            {"next": reverse("my_bookings")},
        )
        self.assertRedirects(resp, reverse("my_bookings"))

    def test_booking_ignores_external_next(self):
        self.client.login(username="alice", password="pw12345!")
        for target in ("https://evil.example.com/", "//evil.example.com/"):
            resp = self.client.post(
                reverse("book_session", args=[self.session.id]),
                {"next": target},
            )
            self.assertEqual(resp["Location"], self.home)

    def test_integrity_error_is_handled_gracefully(self):
        Booking.objects.create(user=self.alice, session=self.session)
        self.client.login(username="alice", password="pw12345!")
        with mock.patch.object(Session, "booking_error_for", return_value=None):
            resp = self.client.post(
                reverse("book_session", args=[self.session.id]), follow=True
            )
        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, "already booked")
        self.assertEqual(
            Booking.objects.filter(user=self.alice, session=self.session).count(), 1
        )

    def test_member_can_cancel(self):
        booking = Booking.objects.create(user=self.alice, session=self.session)
        self.client.login(username="alice", password="pw12345!")
        self.client.post(reverse("cancel_booking", args=[booking.id]))
        booking.refresh_from_db()
        self.assertEqual(booking.status, Booking.Status.CANCELLED)

    def test_member_can_rebook_after_cancelling(self):
        booking = Booking.objects.create(user=self.alice, session=self.session)
        self.client.login(username="alice", password="pw12345!")
        self.client.post(reverse("cancel_booking", args=[booking.id]))

        page = self.client.get(reverse("my_bookings"))
        self.assertContains(page, "Book again")

        resp = self.client.post(
            reverse("book_session", args=[self.session.id]), follow=True
        )
        self.assertEqual(resp.status_code, 200)
        active = Booking.objects.filter(
            user=self.alice,
            session=self.session,
            status=Booking.Status.ACTIVE,
        )
        self.assertEqual(active.count(), 1)

    def test_book_again_hidden_when_session_started(self):
        booking = Booking.objects.create(user=self.alice, session=self.session)
        booking.status = Booking.Status.CANCELLED
        booking.save()
        self.session.start_time = timezone.now() - timedelta(minutes=10)
        self.session.end_time = timezone.now() + timedelta(hours=1)
        self.session.save()
        self.client.login(username="alice", password="pw12345!")
        page = self.client.get(reverse("my_bookings"))
        self.assertNotContains(page, "Book again")

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

    def test_anonymous_cannot_view_roster(self):
        resp = self.client.get(reverse("session_roster", args=[self.session.id]))
        self.assertEqual(resp.status_code, 302)
        self.assertIn(reverse("login"), resp.url)

    def test_roster_requires_staff(self):
        self.client.login(username="alice", password="pw12345!")
        resp = self.client.get(reverse("session_roster", args=[self.session.id]))
        self.assertEqual(resp.status_code, 302)
        self.assertIn(reverse("home"), resp.url)

    def test_roster_shows_booked_members_to_staff(self):
        Booking.objects.create(user=self.alice, session=self.session)
        staff = User.objects.create_user("root", password="pw12345!", is_staff=True)
        self.client.force_login(staff)
        resp = self.client.get(reverse("session_roster", args=[self.session.id]))
        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, "Active bookings (1)")
        self.assertContains(resp, ">alice<")

    def test_roster_shows_cancelled_bookings(self):
        booking = Booking.objects.create(user=self.alice, session=self.session)
        booking.status = Booking.Status.CANCELLED
        booking.save()
        staff = User.objects.create_user("root", password="pw12345!", is_staff=True)
        self.client.force_login(staff)
        resp = self.client.get(reverse("session_roster", args=[self.session.id]))
        self.assertContains(resp, "Cancelled bookings (1)")
        self.assertContains(resp, "No active bookings.")


class AuthenticationTests(TestCase):
    def setUp(self):
        self.alice = User.objects.create_user(
            "alice", email="alice@example.com", password="pw12345!"
        )
        self.home = reverse("home")

    def test_login_page_renders(self):
        resp = self.client.get(reverse("login"))
        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, "Log in")

    def test_login_with_valid_credentials(self):
        resp = self.client.post(
            reverse("login"),
            {"username": "alice", "password": "pw12345!"},
            follow=True,
        )
        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, "Hi, alice")

    def test_login_with_invalid_credentials_shows_error(self):
        resp = self.client.post(
            reverse("login"), {"username": "alice", "password": "wrong-password"}
        )
        self.assertEqual(resp.status_code, 200)
        self.assertContains(
            resp, "Please enter a correct username and password"
        )

    def test_login_redirects_to_next(self):
        url = f"{reverse('login')}?next={reverse('my_bookings')}"
        resp = self.client.post(
            url,
            {
                "username": "alice",
                "password": "pw12345!",
                "next": reverse("my_bookings"),
            },
        )
        self.assertRedirects(resp, reverse("my_bookings"))

    def test_login_ignores_external_next(self):
        url = f"{reverse('login')}?next=https://evil.example.com/"
        resp = self.client.post(
            url, {"username": "alice", "password": "pw12345!"}
        )
        self.assertRedirects(resp, self.home)

    def test_logout_requires_post(self):
        self.client.login(username="alice", password="pw12345!")
        resp = self.client.get(reverse("logout"))
        self.assertEqual(resp.status_code, 405)

    def test_logout_post_logs_user_out(self):
        self.client.login(username="alice", password="pw12345!")
        resp = self.client.post(reverse("logout"))
        self.assertRedirects(resp, self.home)
        resp = self.client.get(reverse("my_bookings"))
        self.assertEqual(resp.status_code, 302)

    def test_anonymous_my_bookings_redirects_to_login(self):
        resp = self.client.get(reverse("my_bookings"))
        self.assertEqual(resp.status_code, 302)
        self.assertIn(reverse("login"), resp.url)

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
        user = User.objects.get(username="newbie")
        self.assertEqual(self.client.session.get("_auth_user_id"), str(user.id))

    def test_registration_does_not_echo_passwords_on_error(self):
        resp = self.client.post(
            reverse("register"),
            {
                "username": "alice",
                "email": "other@example.com",
                "password1": "Uniqu3-Pass!",
                "password2": "Uniqu3-Pass!",
            },
        )
        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, "already exists")
        self.assertNotIn("Uniqu3-Pass!", resp.content.decode())

    def test_registration_rejects_mismatched_passwords(self):
        resp = self.client.post(
            reverse("register"),
            {
                "username": "newbie",
                "email": "newbie@example.com",
                "password1": "S3cure-Pass!",
                "password2": "Different-Pass!",
            },
        )
        self.assertEqual(resp.status_code, 200)
        self.assertFalse(User.objects.filter(username="newbie").exists())

    def test_registration_handles_duplicate_submission(self):
        self.client.post(
            reverse("register"),
            {
                "username": "newbie",
                "email": "newbie@example.com",
                "password1": "S3cure-Pass!",
                "password2": "S3cure-Pass!",
            },
        )
        self.client.post(reverse("logout"))
        resp = self.client.post(
            reverse("register"),
            {
                "username": "newbie",
                "email": "newbie@example.com",
                "password1": "S3cure-Pass!",
                "password2": "S3cure-Pass!",
            },
        )
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(User.objects.filter(username="newbie").count(), 1)

    def test_authenticated_user_redirected_away_from_register(self):
        self.client.login(username="alice", password="pw12345!")
        resp = self.client.get(reverse("register"))
        self.assertRedirects(resp, self.home)

    def test_login_page_preserves_next_in_form(self):
        resp = self.client.get(f"{reverse('login')}?next={reverse('my_bookings')}")
        self.assertContains(resp, 'name="next"')
        self.assertContains(resp, reverse("my_bookings"))


class AdminPermissionTests(TestCase):
    def setUp(self):
        self.superuser = User.objects.create_superuser(
            "root", "root@example.com", "pw12345!"
        )
        self.alice = User.objects.create_user("alice", password="pw12345!")
        self.bob = User.objects.create_user("bob", password="pw12345!")
        self.session = make_session()
        self.booking = Booking.objects.create(user=self.alice, session=self.session)
        self.client.force_login(self.superuser)

    def _session_change_data(self, capacity):
        return {
            "gym_class": str(self.session.gym_class_id),
            "start_time_0": self.session.start_time.strftime("%Y-%m-%d"),
            "start_time_1": self.session.start_time.strftime("%H:%M:%S"),
            "end_time_0": self.session.end_time.strftime("%Y-%m-%d"),
            "end_time_1": self.session.end_time.strftime("%H:%M:%S"),
            "capacity": str(capacity),
            "bookings-TOTAL_FORMS": "0",
            "bookings-INITIAL_FORMS": "0",
            "bookings-MIN_NUM_FORMS": "0",
            "bookings-MAX_NUM_FORMS": "0",
            "_save": "Save",
        }

    def test_admin_panel_index_loads(self):
        resp = self.client.get(reverse("admin:index"))
        self.assertEqual(resp.status_code, 200)

    def test_admin_booking_list_is_readable(self):
        resp = self.client.get(reverse("admin:gym_booking_changelist"))
        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, "alice")

    def test_admin_booking_page_is_view_only(self):
        resp = self.client.get(
            reverse("admin:gym_booking_change", args=[self.booking.pk])
        )
        self.assertEqual(resp.status_code, 200)
        self.assertNotContains(resp, 'name="_save"')

    def test_admin_cannot_post_booking_changes(self):
        resp = self.client.post(
            reverse("admin:gym_booking_change", args=[self.booking.pk]),
            {
                "user": str(self.alice.pk),
                "session": str(self.session.pk),
                "status": "cancelled",
                "_save": "Save",
            },
        )
        self.assertEqual(resp.status_code, 403)
        self.booking.refresh_from_db()
        self.assertEqual(self.booking.status, Booking.Status.ACTIVE)

    def test_admin_cannot_delete_bookings(self):
        resp = self.client.post(
            reverse("admin:gym_booking_delete", args=[self.booking.pk]),
            {"post": "yes"},
        )
        self.assertEqual(resp.status_code, 403)
        self.assertTrue(Booking.objects.filter(pk=self.booking.pk).exists())

    def test_admin_cannot_reduce_capacity_below_active_bookings(self):
        Booking.objects.create(user=self.bob, session=self.session)
        resp = self.client.post(
            reverse("admin:gym_session_change", args=[self.session.pk]),
            self._session_change_data(capacity=1),
        )
        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, "Capacity cannot be lower")
        self.session.refresh_from_db()
        self.assertEqual(self.session.capacity, 2)

    def test_admin_can_increase_capacity(self):
        resp = self.client.post(
            reverse("admin:gym_session_change", args=[self.session.pk]),
            self._session_change_data(capacity=9),
        )
        self.assertRedirects(resp, reverse("admin:gym_session_changelist"))
        self.session.refresh_from_db()
        self.assertEqual(self.session.capacity, 9)


class EdgeCaseTests(TestCase):
    def setUp(self):
        self.alice = User.objects.create_user("alice", password="pw12345!")
        self.session = make_session()

    def test_booking_past_session_via_view_is_rejected(self):
        self.session.start_time = timezone.now() - timedelta(hours=5)
        self.session.end_time = timezone.now() - timedelta(hours=4)
        self.session.save()
        self.client.login(username="alice", password="pw12345!")
        self.client.post(reverse("book_session", args=[self.session.id]))
        self.assertEqual(Booking.objects.count(), 0)

    def test_roster_unknown_session_returns_404(self):
        staff = User.objects.create_user("root", password="pw12345!", is_staff=True)
        self.client.force_login(staff)
        resp = self.client.get(reverse("session_roster", args=[999999]))
        self.assertEqual(resp.status_code, 404)

    def test_registration_rejects_weak_password(self):
        resp = self.client.post(
            reverse("register"),
            {
                "username": "weakling",
                "email": "weak@example.com",
                "password1": "12345678",
                "password2": "12345678",
            },
        )
        self.assertEqual(resp.status_code, 200)
        self.assertFalse(User.objects.filter(username="weakling").exists())

    def test_error_messages_use_bootstrap_danger_class(self):
        Booking.objects.create(user=self.alice, session=self.session)
        self.client.login(username="alice", password="pw12345!")
        resp = self.client.post(
            reverse("book_session", args=[self.session.id]), follow=True
        )
        self.assertContains(resp, "alert-danger")
        self.assertContains(resp, "already booked")

    def test_success_message_on_booking(self):
        self.client.login(username="alice", password="pw12345!")
        resp = self.client.post(
            reverse("book_session", args=[self.session.id]), follow=True
        )
        self.assertContains(resp, "alert-success")
        self.assertContains(resp, "Booked:")

    def test_home_renders_without_errors_for_all_user_states(self):
        for client in (Client(), self.client):
            resp = client.get(reverse("home"))
            self.assertEqual(resp.status_code, 200)
        staff = User.objects.create_user("root", password="pw12345!", is_staff=True)
        self.client.force_login(staff)
        resp = self.client.get(reverse("home"))
        self.assertEqual(resp.status_code, 200)


class ConcurrencyTests(TransactionTestCase):
    def test_parallel_bookings_never_exceed_capacity(self):
        session = make_session(capacity=3)
        capacity = session.capacity
        users = [
            User.objects.create_user(f"racer{i}", password="pw12345!")
            for i in range(8)
        ]
        clients = []
        for user in users:
            client = Client()
            client.force_login(user)
            clients.append(client)

        barrier = threading.Barrier(len(clients), timeout=20)
        failures = []

        def attempt(client):
            try:
                barrier.wait()
                client.post(reverse("book_session", args=[session.id]))
            except Exception as exc:  # pragma: no cover - diagnostic only
                failures.append(repr(exc))
            finally:
                connection.close()

        threads = [
            threading.Thread(target=attempt, args=(client,)) for client in clients
        ]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join(timeout=30)

        active = Booking.objects.filter(
            session=session, status=Booking.Status.ACTIVE
        ).count()
        self.assertLessEqual(active, capacity)
        self.assertEqual(active, capacity)
        self.assertEqual(failures, [])
