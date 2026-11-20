# Gym Class Booking System

A Django web application where gym members register, browse classes, and book
session spots — and administrators manage classes, schedules, and rosters.

Built with **Django + SQLite**, Django's built-in authentication, and Bootstrap 5.

---

## Features

### Member
- Register, log in, log out
- Browse all gym classes and their upcoming sessions with **spots remaining**
- Book a session (one booking per member per session)
- View **My Bookings**
- Cancel a booking (when allowed)

### Administrator
- Full Django admin panel at `/admin/` to create and edit classes, sessions, and capacities
- View who is booked into each session (active and cancelled) — inline in the admin,
  or as a readable roster page at `/roster/<session_id>/`
- Staff accounts see a **View roster** button on the homepage instead of a booking button

---

## Business Rules

| # | Rule | Where it is enforced |
|---|------|----------------------|
| 1 | A session cannot be booked once it is at capacity | `Session.booking_error_for()` |
| 2 | A member cannot book the same session twice | `Session.booking_error_for()` **and** a DB `UniqueConstraint` on `(user, session)` |
| 3 | Sessions that have started or are in the past cannot be booked | `Session.booking_error_for()` |
| 4 | Cancellation is allowed only at least **2 hours** before the start time | `Session.cancellation_error_for()`, `CANCEL_WINDOW_HOURS` |
| 5 | Members can only see and cancel **their own** bookings | `@login_required` + `get_object_or_404(..., user=request.user)` |
| 6 | Cancelled bookings free up their spot | `Session.booked_count` counts only `ACTIVE` bookings |

All booking and cancellation decisions live in **one place** —
`gym/models.py` — so the rules stay consistent across every page.
Views simply call `session.booking_error_for(user)` and show the returned
reason string if it is not `None`.

> **Note:** because of rule 2's permanent unique constraint, a member who
> cancels a booking **cannot re-book the same session**. This follows the
> project specification as written.

---

## Tech Stack

- Python 3 + **Django 6.1**
- **SQLite** database (no setup required)
- Django's built-in auth (`django.contrib.auth`)
- Django Admin for administration
- HTML templates + **Bootstrap 5** (CDN)
- Django test framework for automated tests

---

## Getting Started

### 1. Clone and create a virtual environment

```bash
git clone <your-repo-url>
cd gym-booking
python -m venv venv
```

Activate it:

- **Windows:** `venv\Scripts\Activate`
- **macOS / Linux:** `source venv/bin/activate`

### 2. Install dependencies

```bash
pip install -r requirements.txt
```

### 3. Apply migrations

```bash
python manage.py migrate
```

### 4. Create an administrator account

```bash
python manage.py createsuperuser
```

Follow the prompts. Then log in at <http://127.0.0.1:8000/admin/>.

### 5. (Optional) Load sample classes and sessions

```bash
python seed_data.py
```

Creates 5 classes (Yoga, Spin, Boxing, Pilates, HIIT), 20 sessions, and one
session in the past so you can test the "already started" rule.

### 6. Run the development server

```bash
python manage.py runserver
```

Open <http://127.0.0.1:8000/>.

---

## Running the Tests

27 automated tests cover every business rule and the main views:

```bash
python manage.py test gym
```

Expected output ends with `OK`.

There is also an end-to-end smoke test that exercises the real database:

```bash
python smoke_test.py
```

---

## Project Structure

```
gymproject/
├── manage.py              # Django command-line tool
├── gymproject/
│   ├── settings.py        # Installed apps, DB, auth redirects
│   └── urls.py            # Root URL configuration
├── gym/
│   ├── models.py          # GymClass, Session, Booking + ALL business rules
│   ├── views.py           # home, register, book, my_bookings, cancel, roster
│   ├── urls.py            # App URL routes
│   ├── forms.py           # Registration form
│   ├── admin.py           # Admin registrations (classes, sessions, rosters)
│   ├── tests.py           # 27 tests
│   └── templates/gym/     # base, home, register, login, my_bookings, roster
├── seed_data.py           # Sample classes and sessions
├── smoke_test.py          # End-to-end checks against the real DB
├── screenshots.py         # Regenerates the screenshots below
└── screenshots/           # Demo screenshots
```

---

## Screenshots

| | |
|---|---|
| ![Homepage](screenshots/01-home.png) | ![My Bookings](screenshots/03-my-bookings.png) |
| Homepage with classes, sessions and spots left | My Bookings: cancel button, blocked-cancellation reason, cancelled badge |
| ![Register](screenshots/02-register.png) | ![Admin](screenshots/05-admin-sessions.png) |
| Registration | Admin panel — sessions with capacity and spots left |

Additional screenshots: `04-admin-classes.png`, `06-admin-roster.png`
(admin view of the members booked into a session).

---

## Manual Test Scenarios

| Scenario | Expected result |
|---|---|
| Anonymous user clicks **Book** | Redirected to the login page; no booking created |
| Member books a future session | Booking created, spot count decreases |
| Member books the same session again | Rejected: "You have already booked this session." |
| Book a session that is already full | Rejected: "This session is fully booked." |
| Book a session that has started | Rejected: "This session has already started." |
| Cancel more than 2 hours before start | Cancellation succeeds; the spot is freed |
| Cancel less than 2 hours before start | Rejected with the 2-hour notice |
| Cancel after the session started | Rejected |
| Member opens another member's booking URL | HTTP 404 |
| Member opens a roster URL | Redirected to the homepage |
| Admin opens a roster URL | Sees the list of booked members |

---

## Optional Extensions

Not implemented — left as future work:

- Filter classes by day or category
- Booking confirmation emails
- Waiting list for full sessions
- Django REST Framework API
