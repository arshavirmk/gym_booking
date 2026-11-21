# PROJECT STATUS — Gym Class Booking System

**Status: COMPLETE — ready for local handover**
**Last verified: 2026-11-21**

---

## 1. Summary

A Django 6.1.3 + SQLite gym class booking application with two roles
(member and administrator). Fully reviewed, debugged, tested, and verified
from a clean setup. **Everything runs locally — nothing was uploaded to
GitHub or any remote repository.**

---

## 2. Test Results (all verified by running)

| Command | Result |
|---|---|
| `python manage.py test gym` | **83/83 tests pass (OK)** |
| `python smoke_test.py` | **25/25 checks pass** (repeatable) |
| `python manage.py check` | **No issues** |
| `python manage.py makemigrations --check` | **No pending migrations** |
| Clean-setup (fresh venv + migrate + seed + test + smoke) | **All pass** |

### Test coverage (83 tests)
- `BookingModelTests` (7) — spot counting, constraints, prefetch cache
- `BookingRuleTests` (8) — capacity, started/past, duplicate, staff, rebook
- `CancellationRuleTests` (6) — 2-hour window, started, double-cancel, can_rebook
- `SessionIntegrityTests` (6) — DB check constraints, Session.clean validation
- `ViewTests` (25) — all pages, booking/cancel flows, redirects, open-redirect
- `AuthenticationTests` (14) — login/logout/register, password leak, next param
- `AdminPermissionTests` (7) — read-only bookings, capacity guard, panel access
- `EdgeCaseTests` (6) — past-session view, weak password, message classes, 404s
- `ConcurrencyTests` (1) — 8 parallel threads against capacity 3, no overselling

---

## 3. Bugs Found & Fixed (this review phase)

### Security
1. **Password echoed into HTML** on failed registration (`register.html`
   rendered raw widget values) → now uses `{{ field }}` widget rendering.
2. **Open redirect** via `HTTP_REFERER` / `next` parameter → replaced with
   `_safe_redirect_target()` using `url_has_allowed_host_and_scheme`.
3. **Invalid Bootstrap message class** (`alert-error` never styled) →
   `MESSAGE_TAGS = {ERROR: "danger"}` in settings.
4. **Staff could delete booking rows** in admin → `has_delete_permission`
   now returns `False` (bookings fully read-only: add/change/delete blocked).

### Correctness / race conditions
5. **Unhandled `IntegrityError` race** in `book_session` returned 500 →
   nested `transaction.atomic()` savepoint + graceful catch.
6. **SQLite `select_for_update` is a no-op** → `transaction_mode: "IMMEDIATE"`
   + `timeout: 20` so booking check+insert is serialized.
7. **Admin could set capacity below active bookings** → `Session.clean()`
   validation + admin form rejection.
8. **Admin could set `end_time <= start_time`** → DB `CheckConstraint`
   `session_end_after_start` + `Session.clean()`.
9. **`capacity = 0` allowed** → DB `CheckConstraint` `session_capacity_positive`.
10. **Invalid booking status allowed** → DB `CheckConstraint`
    `booking_status_valid`.
11. **GET on POST-only endpoints returned 405** after login-next redirect →
    now graceful redirect with an info message.

### UX / templates
12. **Staff couldn't open roster for full sessions** (Book button shown
    instead) → template reorders staff check first.
13. **"1 classes available"** grammar → `pluralize:"es"`.
14. **Long cancellation reason text** mangled the My Bookings table →
    disabled Cancel button + `.cancel-note` styled block.
15. **N+1 queries on homepage** → `Prefetch(to_attr="upcoming_sessions")` +
    cache-aware `Session.booked_count`.

### Scripts
16. **`smoke_test.py` crashed on repeat runs** (`create_user("victim")`
    duplicate) → `get_or_create`; spot-count check now relative; temporary
    filler/victim users cleaned up at end (verified 2 consecutive runs).
17. **`screenshots.py` created duplicate near-sessions each run** → reuses
    an existing session in the 20min–2h window.
18. **`smoke_test.py` full-session check mutated a real session** (changed
    its capacity and deleted other members' bookings) → now uses a
    throwaway session that is deleted afterwards (no side effects).
19. **Admin screenshots rendered unstyled** (`/static/` URLs don't resolve
    from `file:///`) → `screenshots.py` now rewrites static references to
    absolute `file:///` paths via the staticfiles finders.

### Configuration
20. **Dead `MAILERS` setting** (not a real Django setting) removed.
21. **`requirements.txt` unpinned** → pinned to `Django==6.1.3`.
22. **Duplicate assertion block** left in `tests.py` from diagnostics removed.
23. **`test_db.sqlite3`** added to `.gitignore` (file-backed test DB).

---

## 4. Files Modified / Created / Removed

### Created
- `gym/migrations/0003_booking_booking_session_status_idx_and_more.py`
  (index `booking_session_status_idx` + 3 check constraints) — **applied**
- `PROJECT_STATUS.md` (this file)

### Rewritten / heavily modified
- `gym/models.py` — constraints, `Session.clean`, cache-aware `booked_count`,
  `booking_error_for`, `cancellation_error`, `can_rebook`
- `gym/views.py` — safe redirects, atomic booking, staff roster, Prefetch
- `gym/forms.py` — added `LoginForm` with Bootstrap widget classes
- `gym/admin.py` — read-only bookings (add/change/delete blocked), inline,
  admin site titles
- `gym/urls.py` — `LoginView(authentication_form=LoginForm)`
- `gymproject/urls.py` — removed duplicate admin title assignments
- `gymproject/settings.py` — MESSAGE_TAGS, DB OPTIONS (IMMEDIATE + timeout),
  TEST NAME (file-backed), DEFAULT_AUTO_FIELD, ALLOWED_HOSTS
- `gym/tests.py` — full rewrite → 83 tests
- All 6 templates (`base`, `home`, `register`, `login`, `my_bookings`, `roster`)
- `smoke_test.py` — rewritten → 25 checks, repeatable
- `screenshots.py` — session reuse, superuser detection, guards
- `README.md` — rewritten for local-only handover
- `requirements.txt` — pinned `Django==6.1.3`
- `.gitignore` — added `test_db.sqlite3`

### Not modified (verified correct)
- `manage.py`, `gymproject/asgi.py`, `gymproject/wsgi.py`, `__init__.py`
  files, `gym/apps.py`, migrations `0001`/`0002`, `seed_data.py`

---

## 5. Remaining Limitations (documented, not bugs)

1. **`DEBUG` and `SECRET_KEY` come from `DJANGO_DEBUG` /
   `DJANGO_SECRET_KEY` env vars** with local-dev fallbacks — correct for
   local development only; set real values before any shared use.
2. **SQLite** is single-writer by design; `transaction_mode=IMMEDIATE`
   serializes writes. Fine for a class project / small gym, not for
   high-traffic production (would need PostgreSQL).
3. **`screenshots.py` requires Chrome** at the default Windows path and is
   only needed to regenerate demo images.
4. **No email/password-reset flow** — out of scope for this project.
5. Bookings are **immutable by design** in the admin (no edit/delete) to
   protect capacity accounting; cancellation is only via the member UI.

---

## 6. How to Run (local only)

```powershell
# from the project root
python -m venv venv
venv\Scripts\Activate
pip install -r requirements.txt
python manage.py migrate
python manage.py createsuperuser      # create the admin account
python seed_data.py                   # optional sample data
python manage.py runserver            # http://127.0.0.1:8000/
```

Tests:
```powershell
python manage.py test gym             # 83 tests, expect "OK"
python smoke_test.py                  # 25 checks, expect "25/25"
```

Admin panel: `http://127.0.0.1:8000/admin/` with the superuser you created.

---

## 7. Confirmation

- **Nothing was pushed, published, or uploaded to GitHub or any remote.**
  `git remote -v` is empty; the repository has only local commits.
- The real database (`db.sqlite3`) and user data were preserved.
- No destructive operations were performed.

---

## 8. Second-Round Review (external code review) � applied

All 11 review points verified against the actual code before any change;
only confirmed issues were fixed.

### Changes made
1. **Roster permissions** � authenticated non-staff users now get
   **HTTP 403** (`PermissionDenied`) instead of a redirect; anonymous
   users still redirect to login (`@login_required`). Tests and smoke
   test updated to assert 403; staff access unchanged (tested).
2. **SECRET_KEY / DEBUG from environment** � `DJANGO_SECRET_KEY` and
   `DJANGO_DEBUG` env vars with safe local-dev fallbacks; app works with
   no env vars set. `.env` is git-ignored (user added). An older dev key
   exists in git history � history was NOT rewritten (as instructed);
   documented in README.
3. **TIME_ZONE = Asia/Tehran** � confirmed appropriate (machine is Iran
   Standard Time); `USE_TZ=True` unchanged; storage stays UTC; all app
   code already used `timezone.now()` (verified: zero naive-datetime
   usages). `seed_data.py` now seeds in local time so sample classes
   land at 7:00/12:00/18:00 local. Screenshots regenerated.
4. **SQLite concurrency** � `transaction_mode: "IMMEDIATE"` verified
   present in settings AND supported by installed Django 6.1.3
   (`django/db/backends/sqlite3/base.py` defines the allowed modes).
   Concurrency test re-run independently: OK (8 threads, capacity 3 ?
   exactly 3 booked, 0 failures). README claim is accurate.
5. **my_bookings queries** � added `user` to `select_related` (the
   `can_rebook` path touched `booking.user` per cancelled row ? N+1).
   No other optimization needed; `session__gym_class` already joined.
6. **Started sessions on homepage** � verified already correct:
   ongoing sessions are listed (`end_time > now`), show a "Started"
   badge, and never show a Book button (unit test + screenshot proof).
7. **Full file review** � settings, both urls.py, forms, admin, all
   templates, tests, requirements, smoke/screenshots/seed scripts,
   migrations, .gitignore re-inspected; no further defects found.
   User's own commit `f18e04a` added `.env` to .gitignore (kept).

### Verification results (this round)
| Check | Result |
|---|---|
| `manage.py test gym` | **83/83 OK** |
| `ConcurrencyTests` (run alone) | **OK** |
| `smoke_test.py` | **25/25** |
| `manage.py check` | no issues |
| `makemigrations --check` | no changes detected |
| Clean setup (fresh temp dir: venv, install, check, migrate, seed, superuser, tests, smoke) | **all pass** |
| Screenshots (6, regenerated after TZ change) | verified visually |

### Git (this round)
- Remote `origin` now exists (created by the project owner; local branch
  `main` tracks `origin/main`). **Nothing was pushed by this review** �
  no push, no history rewrite, no destructive commands.
- Local commit added for these changes.
