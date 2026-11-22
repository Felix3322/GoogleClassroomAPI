# GoogleClassroomAPI cookie export (dev helper)

A minimal Chrome extension that exports this browser's Google cookies to a
Netscape `cookies.txt`, for running `GoogleClassroomAPI`'s live tests
without pasting cookies around.

A plain console script cannot do this: Google's session cookies are HttpOnly,
so `document.cookie` never sees them. Reading them needs the `cookies`
permission, which only an extension has.

## What it does and does not do

- Reads cookies for `google.com` and its subdomains using `chrome.cookies`.
- Writes them to a file you pick, in the browser, with a save dialog.
- Sends nothing anywhere. There is no network code in it — read `background.js`.

The file it makes is the **full sign-in state of the account**, not just
Classroom. Anyone who holds it is signed in as you. Keep it out of the repo,
and delete it once the tests are done.

## Load it

1. Chrome → `chrome://extensions`.
2. Turn on **Developer mode** (top right).
3. **Load unpacked** → pick this `tools/cookie-export` folder.

## Use it

1. Make sure the browser is signed in to the test account.
2. Click the extension's toolbar icon (the puzzle piece → pin it first if you like).
3. In the save dialog, save `gca-cookies.txt` **outside this repo** (e.g. Downloads).
4. Watch the toolbar badge:
   - **green number** — a complete session was written.
   - **amber `!`** — the file was written but base auth cookies (`SID`, `HSID`,
     `SAPISID`, `__Secure-1PSID`) were not all found. That session is only
     half-signed-in: Google will let it read, and use Classroom, but will
     challenge Google Forms edits. Make sure you are fully signed in to the
     account in this Chrome profile, then export again.
   - **red `err` / `0`** — nothing was read.

Then give me the file's path — not its contents.

## Why per-URL, not per-domain

Cookies are collected with `chrome.cookies.getAll({url})` for each Google host,
which is exactly the set the browser sends to that site. A `getAll({domain})`
can miss the non-`Secure` base cookies (`SID`, `HSID`, `APISID`); without them
the exported session is the half-signed-in one described above.
