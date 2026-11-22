# OAuth setup (one-time)

> **Not needed for the Classroom API.** The public `Classroom` class works with
> exported cookies only. This page is kept for the later Google Forms work,
> which will need the official Forms API.

The library runs headless with an OAuth refresh token. Getting that token needs
a one-time setup in Google Cloud and a single browser consent. After that the
library refreshes tokens itself and never needs a browser again.

## 1. A Google Cloud project with the two APIs

1. Open the [Google Cloud console](https://console.cloud.google.com/), create a
   project (or pick one).
2. **APIs & Services → Library**, enable both:
   - **Google Classroom API**
   - **Google Forms API**

## 2. OAuth consent screen

1. **APIs & Services → OAuth consent screen.**
2. User type: **External** is fine for a personal account (Internal if you have
   Google Workspace and only staff will use it).
3. Fill in the app name and your contact email.
4. **Scopes:** you can leave this empty here; the library requests its scopes at
   consent time. For reference they are:
   - `.../auth/forms.body`
   - `.../auth/forms.responses.readonly`
   - `.../auth/classroom.courses`
   - `.../auth/classroom.coursework.students`
   - `.../auth/classroom.rosters.readonly`
   - `.../auth/classroom.topics`
5. **Test users:** add the teacher account you will run as. While the app is in
   "testing" mode only listed test users can consent, which is exactly what you
   want for yourself — no Google review needed.

> You do **not** need to publish the app or pass Google verification to use it
> yourself or with a handful of test users. Verification is only needed if you
> later expose this to many unaffiliated users.

## 3. An OAuth client (Desktop app)

1. **APIs & Services → Credentials → Create credentials → OAuth client ID.**
2. Application type: **Desktop app.**
3. Create, then **Download JSON**. Save it as `client_secret.json`. Treat it as
   a secret.

## 4. First consent → a refresh token

```bash
pip install GoogleClassroomAPI[oauth]      # or: pip install -e .[oauth] from a checkout
python examples/oauth_quickstart.py authorize client_secret.json
```

A browser opens; sign in as the teacher account and allow the scopes. The
script writes `token.json` (contains the refresh token — treat it as a
password). From here everything is headless:

```bash
python examples/oauth_quickstart.py courses            # find a course id
python examples/oauth_quickstart.py demo <course_id>   # publish a demo quiz
python examples/oauth_quickstart.py answers <course_id>
python examples/oauth_quickstart.py withdraw <course_id>
```

## Embedding in a service

`token.json` is a portable `authorized_user` document. In your own service:

```python
from google_classroom_api import connect_oauth, Store
import json

info = json.load(open("token.json"))
pipeline = connect_oauth(info, Store("gca.sqlite3"))   # refreshes tokens itself
```

Store the token wherever you keep secrets. `connect_oauth` persists rotated
tokens back to the `Store` (table `teacher_token`) by default; pass your own
`on_token_update` callback to store them elsewhere. One teacher = one token =
one `connect_oauth`; run several side by side for several teachers.

## What OAuth cannot do (and the workaround)

The official API has **no field for the class-level "default grade for missing
work"** setting. Two options:

- Set it once by hand in the Classroom web UI (it is a per-class, one-time
  setting), **or**
- use the cookie-based web Classroom backend for that one call — see
  `docs/architecture.md`. Classroom (unlike Forms) still accepts exported
  cookies off the browser.

A few Forms settings are also unavailable over the official API (when grades are
released, showing correct answers after submit, question-order shuffle, progress
bar, one-response limit). `publish_lesson` returns the names it could not set in
`PublishResult.dropped_form_settings`.
