# Architecture: one logic, pluggable backends

> **Status (v0.1):** the public API is the cookie-based `Classroom` class
> (`cookie_classroom.py`), which uses only the reverse-engineered web protocol.
> This document describes the lesson pipeline and its official-API (OAuth)
> backends, which are kept for the later Google Forms work and are **not** used
> by the current Classroom API.

The user-facing logic (lessons, students, answers) is backend-agnostic. It
drives two backends through the protocols in `src/google_classroom_api/backends.py`:

```
Lesson / Student / Answer  (models.py)
        │
     Pipeline  (pipeline.py)  ── judge (pipeline.py) ── Store (store.py)
        │  FormsBackend                 │  ClassroomBackend
        ▼                               ▼
   OAuthFormsBackend          OAuthClassroomBackend   OR   web.WebClassroomBackend
   (oauth_backends.py)        (oauth_backends.py)          (web/backend.py, cookies)
        │                               │
   forms.py + formbuilder.py    classroom.py            web/classroom.py
   + drive.py (official API)    (official API)          (reverse-engineered)
        └──────────── transport.py (GoogleSession: retry, refresh, idempotency)
```

- **Forms is always OAuth.** There is no headless web forms backend (see
  "Why Forms must be OAuth" below).
- **Classroom is OAuth by default** (`OAuthClassroomBackend` over `classroom.py`)
  — it can assign to named students and read rosters and submissions. The cookie
  backend (`web.WebClassroomBackend`) is the alternative when you need the
  "default grade for missing work" setting the official API lacks.

`formbuilder.plan_form` is the single place the form-diff logic lives: given the
form as the Forms API reports it and a lesson, it produces the batchUpdate
requests that make the form match, with the fewest edits, keeping each
question's id across reorders and re-edits so collected answers stay attached.
`OAuthFormsBackend.prepare_form` runs it and maps the server-assigned ids from
the `createItem` replies back onto the slots. Writes go through
`transport.GoogleSession`, which refreshes the token, retries safe/transient
failures, and raises `UncertainOutcome` rather than blindly repeating a create.

## Capability matrix

| Capability | OAuth Forms | OAuth Classroom | Cookie Classroom |
|---|---|---|---|
| Create form, add questions | ✅ | — | — |
| Quiz: points, correct answers, feedback | ✅ | — | — |
| Read per-question answers + per-question score | ✅ | — (read from Forms) | — |
| Create assignment, attach form | — | ✅ (form as link) | ✅ (form as form) |
| **Assign to named students** | — | ✅ `INDIVIDUAL_STUDENTS` | ❌ not reverse-engineered |
| Read roster / submission state / grade | — | ✅ | ❌ |
| **Default grade for missing work** | — | ❌ no API field | ✅ |
| Grade-release timing, show answers after submit, shuffle, progress bar, one-response limit | ❌ | — | ✅ (but web Forms is browser-only) |
| Runs headless / embeddable | ✅ | ✅ | ⚠ cookies expire; Classroom only |

The recommended embedding is **OAuth for both halves**. It is the only fully
headless path, and it is the only path that can assign to named students — the
primary requirement.

## Why Forms must be OAuth

Measured 2026-11-22 (see `docs/private-forms-protocol.md`). With cookies
exported from a signed-in Chrome (even 2 seconds old, with the Docs `OSID`
cookies present and sent), a non-browser client is redirected to the Google
sign-in page by **every** service that mints a per-service session: Docs, Forms,
Drive, Sheets, Mail, "My Account". Only `classroom.google.com` accepts the same
cookies. In the same browser, the same account loads all of them normally.

The difference is a **device-bound session**: the browser attaches a signed,
device-held credential to each request, which an exported cookie set cannot
reproduce. So the reverse-engineered Forms editor (`web/forms.py`) only works
inside a live browser and is kept for experiments, not for the headless
pipeline. Classroom's private `batchexecute` surface does not enforce this, so
`web/classroom.py` / `WebClassroomBackend` still work with cookies — useful only
for the one setting (missing-work grade) the official API lacks.

## Matching answers to students

Forms responses carry the respondent's **verified email**, not a Classroom user
id. The pipeline joins on email: `sync_roster` records each student's Google
id + email, and `sync_responses` maps a response's email to the bound site
student. So collect the email (`FormSettings.collect_email = "verified"`, the
default) and make sure the students you target are bound.

## Idempotency and no double-counting

`publish_lesson` is safe to re-run: it reuses the stored form and assignment and
only sends what changed. `sync_responses` stores one record per
(response, question) and returns only new or changed records, so points awarded
from its `created` list cannot be counted twice — even across resubmissions,
which arrive as a newer timestamp on the same response id.
