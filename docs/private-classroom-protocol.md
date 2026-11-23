# Google Classroom web app: private RPCs (reverse-engineered)

Captured 2026-11-22 from classroom.google.com in Chrome, with a personal
Google account and a throwaway class (first empty, later with one student).
Everything below was observed in real requests made by the web app. Where a
section says **replayed**, the library's own request was sent outside a
browser, with exported cookies, to the real server and the result was read
back. What needs a second student, or a student who turns work in, could not
be captured (see the end).

Envelope parameters (confirmed by replay): `f.sid`, `bl` and `at` are the
values of `FdrFJe`, `cfb2h` and `SNlM0e` in the page's `WIZ_global_data`.
The other query parameters are `source-path`, `hl`, `soc-app=1`,
`soc-platform=1`, `soc-device=1`, `_reqid` and `rt=c`.

Field masks are mandatory for the stream-item calls: `n5NjMc` without them
came back with error `[3]`. `n5NjMc` takes 10 top-level arguments
(`[item, readMasks, null, [1,1,1], null×5, [1,1,null,1]]`); `F7Tqub` takes 12
(`[item, updateMasks, readMasks, null, [1,1,1], null×6, [1,1,null,1]]`). The
exact masks are in `web/classroom.py`; requests built from them have the same
length as the captured ones (1038 and 1587 characters).

This is not a documented Google API. It can change without notice.

## Transport

All calls go through Google's `batchexecute` endpoint:

```
POST https://classroom.google.com/_/ClassroomUi/data/batchexecute?rpcids={rpcId}&…
f.req=[[["{rpcId}","<arguments as a JSON string>",null,"generic"]]]&at={xsrf}
```

The reply starts with `)]}'` and carries `["wrb.fr","{rpcId}","<result as a JSON string>", …]`.

Arguments are positional arrays. Most read calls end with long arrays of `1`
that act as field masks (which fields the client wants back).

IDs are decimal strings. The course URL `/c/{x}` is the base64 of the course id.

## Calls seen

| rpc id | Result tag | Does | Arguments (field masks left out) |
|---|---|---|---|
| `lNyLS`, `gXtzob` | `hrw.crs`, `hrq.crs` | course details | `[…, [null, [[courseId]], …]]` |
| `MNXWId` | `hrw.usr` | user profile and notification settings | `[[3], [[[userId], …]]]` |
| `ZZFlc` | `hrt.qr` | list topics | `[[100,null,1,0],[1,1,1],[[[[courseId]]]]]` |
| `lkA1wb` | `hrt.ur` | create topic | `[[[null,[courseId]],"topic name"],[1,1,1]]` |
| `WRrP0b` | | rename topic | `[[["topicId",[courseId]],"new name"],[1],[1,1,1]]` |
| `EjPfY` | | delete topic | `[["topicId",[courseId]]]` |
| `CVt8yf` | `hrw.sub` | write a submission record: draft grade, return, turn in, answer, unsubmit | see "Submissions" |
| `yMOiof` | `hrs.ssl` | tally of the answers to a multiple-choice question | see "Submissions" |
| `o5pSDe` | | sent by the People page; **not the roster** — it returned `[]` for a class that has a student (probably pending invitations) | `[[courseId],100,null,[1,null,1]]` |
| `UG41I` | `hrq.usr` | user profiles (name, email, photo) by user id | see "Roster" |
| `dpT4Vd` | `hrcw.qr` | list classwork, one section at a time | see "Listing classwork" |
| `Zj93ge` | `hrq.sub` | submission records of one item (the gradebook calls it per item; a student gets their own) | see "Submissions" |
| `pONvgf` | | published classwork for the gradebook | `[[10,null,1,0], masks…, [[[2],[[courseId]],[2,5],[2]]]]` |
| `sLc6hf` | `hrq.cmt` | read comments | see "Comments" |
| `jOFnxd` | `hrw.cmt` | post or delete a comment | see "Comments" |
| `cCzVwf` | `hra.laa` | add-on attachments of an item | `[[100,null,1,0],null, mask, [[itemId,[courseId]]]]` |
| `t51ITc`, `tQShAc`, `O1Xqee`, `gkSD8b` | | seen on Classwork / home loads; purpose not established | — |
| `u1xGoc` | `hrq.pau` | resolve a pasted link | `["https://…"]` |
| `n5NjMc` | `hrsi.ur` | create a stream item (the assignment draft) | see below |
| `n5QGnd` | | add an attachment to a stream item | see below |
| `F7Tqub` | `hrsi.ur` | update a stream item, including publishing it | see below |
| `Tkwkod` | `hra.la` | list add-ons offered in the attach row (includes "练习集", practice sets) | `[[100,null,1,0],[courseId],[1,1,1,1],null,[2,1]]` |
| `Bypadc` | `rubric.list` | rubrics of an assignment | `[[null,[[courseId, itemId]]],[10,""]]` |

### Topic

Create: `lkA1wb` returns `["hrt.ur",[[topicId,[courseId]],"name",updateTimeMs,…]]`.

### Assignment

The editor saves a draft as soon as a title is typed.

`n5NjMc` (create), arguments without masks:

```
[[[2, [[ [null,[courseId]], null,null,null,null, "title", null,null,null, [3], …, 1, …, [0], null,
        [[null, "<client UUID>", null, ["edu.rt","plain instructions"], null, null,
          ["edu.rt","plain instructions",null,null,[null,"plain instructions"]]]]],
       [null,null,0,100,0,1,[1],null,null,[null,0],null,0], null, null, [2,2]]]]]
```

* `[3]` at index 9 is the state: `3` draft, `2` published.
* `100` in the second block is the points value.
* The reply carries the new item id: `[[itemId,[courseId]], created, updated, …, [teacherUserId], "title", …]`.

`F7Tqub` (update) takes the same structure with `[itemId,[courseId]]` in the
first slot. Publishing is the same call with `[2]` instead of `[3]`; the reply
then shows `[2,[teacherUserId],publishTimeMs]`.

### Attaching a Google Form

1. `u1xGoc` with the pasted form URL returns the form's title, its Drive id,
   type `7`, a thumbnail and the responder URL. The web app therefore treats a
   Forms link as a **Form attachment**, not as a plain link.
2. `n5QGnd` attaches it:
   `[[[null,null,"<driveId>",7,"application/vnd.google-apps.freebird", …], null, [[itemId,[courseId]]]], masks]`
   and returns an attachment id.
3. The following `F7Tqub` lists it inside the item as
   `[[null,[[itemId,[courseId]],attachmentId]]]`.

Replayed: a draft with the form attached was created, the attachment was read
back in the listing (`item[27][0][4]`, one entry) and the draft was deleted.

**Which link counts as a form (replayed).** `u1xGoc` answers type `7` with the
Drive id only for a link that contains the form's file id:
`…/forms/d/<fileId>/edit` or `…/forms/d/<fileId>/viewform`. For the public
responder link `…/forms/d/e/<long id>/viewform` of the same form it answers
type `4` — a plain web link, no Drive id, a `webthumbnail` picture — although
it still returns the form's title. The library refuses that link with an
explanation instead of attaching the form as a web link. The responder link
of a form is in slot 6 of the type-7 answer.

### Topic and due date of an assignment

Both were captured from the editor and then sent by hand, on create and on update:

* Stream item slot 14 holds the topic: `[["<topicId>", [courseId]]]`.
* The coursework block starts with the due date:
  `[dueMs, null, hasTime, points, 0, 1, [1], null, null, [null,0], null, 0]`.
  A due date with no time is sent as 23:59 in the teacher's time zone with
  `hasTime = 0`; with a time, `hasTime = 1`.

`F7Tqub` sends the whole assignment, so a field left out is cleared.

## Class settings

`gXtzob` reads the course record and `lNyLS` writes it (the same call that
creates a class):

```
gXtzob  [[null,null,2,0], courseReadMask, [null,[[courseId]],null,null,null,null,[1,2]]]
        → ["hrq.crs", null, [course]]
lNyLS   [[3], [[[courseId], course, courseUpdateMask]], courseReadMask]
```

The course record has 54 slots (title at 5, section at 8). Slot 46 is the
grading block `[overallGrade, categories, missingWork]`.

**Default grade for missing work** is `missingWork = [enabled, fraction]`, for
example `[1, 0.6]` for 60 %. Reading the record, changing only this pair and
writing the record back was replayed: switching it on and off and changing
the percentage all took effect, and the class title and section were
unchanged afterwards. Both masks are in `web/classroom.py`.

## Roster

Captured 2026-11-22 from the People page and **replayed with exported cookies,
outside a browser**, against a class with one student.

* The student ids are in the course record (`gXtzob`) at slot **7**:
  `[["<userId>"], …]`. The class with one student returned exactly that id;
  the teacher was not in this slot.
* `UG41I` turns ids into profiles:
  `[[null,null,1,0], [1,1,null,1,null,1,null,null,1,1,1,1,null,null,1], [[null, [[null,[userId]], …]]]]`
  answers `["hrq.usr", [], [[["userId"], "Name", "email", null, "photoUrl", …], …]]`.

`o5pSDe`, which the same page also sends, is not the roster (see the table).

## Listing classwork

`dpT4Vd` lists one Classwork section per call:
`[[pageSize,null,2,0], [courseId], section, flag, 2, READ_MASKS]` where the
"no topic" section is `section = null, flag = 1` and a topic section is
`section = ["topicId", [courseId]], flag = 0`. The web app sends one call per
section with `pageSize = 10`; `100` was replayed and accepted. The reply is
`["hrcw.qr", [hasMore], [[2, [streamItem, coursework, …]], …]]`, newest first;
an empty section has no third element. Paging past the first page was not
captured (the library raises if `hasMore` is true).

What a listed envelope carries (read back for a draft made with 7 points, a
due date with a time, a topic and one link attachment):

```
coursework  [dueMs, <a timestamp>, dueHasTime, points, false, 1, [1], …]
item[14]    [["topicId", ["courseId"]]]                      # null without a topic
item[27][0][4]  [[null, [["itemId", ["courseId"]], "attachmentId"]]]   # null without attachments
```

The library takes an item's topic from the section it was listed in and falls
back to `item[14]` for stream queries. Only the attachment id comes back, not
what the attachment points at.

## An update replaces the whole item

`F7Tqub` with the ordinary update masks writes every field it covers, so
whatever is not sent again is cleared. Replayed: a draft with a topic, a link
attachment, instructions, a due date and 7 points was updated with nothing but
a new title — afterwards the topic, the attachment, the instructions and the
due date were gone and the points were back at 100. Sending the same
attachment ids again keeps the attachments (replayed), which is what the web
app does on every save. The public `Classroom.update_*` and `publish` methods
therefore read the item first and send everything back.

## Write statuses: a refused write still answers HTTP 200

The reply to `n5NjMc` and `F7Tqub` is `["hrsi.ur", [[status, envelope], …]]`,
one pair per item sent. `[0]` is success. A refusal comes back as HTTP 200
with a normal-looking reply whose status is not `[0]` — seen: `[13,"generic"]`
— and the item is left exactly as it was. The envelope in a refused reply is
just the request echoed back. So the status has to be checked; the library
raises `StreamItemRejected`. (Earlier versions did not check it and reported a
refused delete as done.)

## Deleting a stream item

There is no separate delete RPC. The Classwork "delete" menu sends `F7Tqub`
with the stream item's slot **8** set to `3` (live items have `2`) and masks
that write only that field:
`[[[null,null,null,null,null,1],[]], [[null,null,null,null,null,1]], [[null,null,null,null,null,1],[]], null, null, [[null,null,null,null,null,1]]]`.
Unlike an ordinary update, the arguments end after the fifth:
`[envelope, deleteMasks, READ_MASKS, null, [1,1,1]]`.

The web app sends the whole item it has. Replayed with a minimal item (id only,
empty title) in the item's own envelope: assignments, materials, announcements
and short-answer questions disappeared from the listings afterwards.

**Questions are the exception.** The question block in the envelope has to be
of the question's own type, or the delete is refused with status
`[13,"generic"]` (replayed in both directions: a short-answer block against a
multiple-choice question, and the reverse). Only the type counts:
`[2,null,[[]]]` with no choices deletes a multiple-choice question. Since the
type cannot be told from the id, the library sends the short-answer form and,
if that is refused, the multiple-choice form.

## Submissions: reading, grading, turning in, returning

A student who is in the class when an assignment or a question is published
gets a **submission record** for it. It appears a second or two after
publishing (replayed: none 0.2 s after publishing, there at 1.8 s). An item
that was published before the student was in the class has no record for
them (observed: `["hrq.sub",[false]]`) until something happens to their work —
a teacher typing a draft grade created one. One call writes the record for
everybody — `CVt8yf` — and what a caller may change depends on who they are.

### Reading

The gradebook calls `Zj93ge` once per item:
`[[100,null,1,0], mask, [null,[[itemId,[courseId]]]], 0]` (mask in
`web/classroom.py`), answered `["hrq.sub", [hasMore], [record, …]]`; the third
element is missing when there is no record. A teacher gets the students'
records (seen with a class of one student); a student calling the same thing
gets only their own (replayed). The
student's own pages ask for one record by its reference instead:
`[[null,null,2,0], mask, [null,null,[[studentId,[itemId,[courseId]]]]], 1]`.

| Slot | Meaning |
|---|---|
| `0` | `[studentId, [itemId, [courseId]]]`, the record's reference |
| `2` | last update, ms |
| `5` | state: `4` assigned, `2` turned in, `5` unsubmitted by the student, `3` returned by the teacher (all four observed) |
| `7` | `[studentId]` |
| `8` | the grade the student sees; set when the work is returned |
| `9` | the teacher's draft grade (a student reading their own record gets null here) |
| `16` | for a question: `[1]` short answer / `[2]` multiple choice before answering, then `[1, "text"]` / `[2, null, "choice"]` |
| `19` | history, teachers only: `[[state, ms, [actorId], n, null, n], null, 1]` per state change, `[null, [grade, maxPoints, ms, [actorId], null, kind, n], 2]` per grade change — `kind` 1 for a typed draft grade, 2 for a grade returned to the student; grade null when cleared |
| `21` | when it was turned in, ms |
| `32` | `1` in every record seen |

### Writing

```
CVt8yf  [[3], [[ref, record, writeMask]], readMask]      ->  ["hrw.sub", [record]]
ref = [studentId, [itemId, [courseId]]]
```

| Who, what | `record` (33 slots unless cut) | `writeMask` |
|---|---|---|
| teacher types a draft grade | `[ref, null×8, grade]` | `[null,null,null,1]` |
| teacher empties the cell | `[ref]` | `[null,null,null,1]` |
| teacher returns work | slot 5 = `3`, slots 8 and 9 = the grade (both null to return ungraded), slot 32 = `1` | `[1,null,1,1,null×8,1]` |
| student marks as done | slot 5 = `2`, slot 32 = `1` | `[1,null×11,1]` |
| student answers a question | slot 5 = `2`, slot 16 = `[null,"text"]` or `[null,null,"choice"]`, slot 32 = `1` | `[1,null×6,[1],null×4,1]` |
| student unsubmits | `[ref, null×4, 5]` | `[1]` |

All six were captured from the web app, the library's requests equal them
character for character, and all were replayed outside a browser with the
result read back from the other side (the teacher saw each state and answer;
the student saw the returned grade and did not see the draft).

Replayed as well:

* The teacher sending the student's "mark as done" is refused (`CVt8yf`
  answers with error `[3]`); the record stays "assigned".
* The server does **not** check a multiple-choice answer against the choices:
  it stored `"not a choice"` and marked the question turned in. The library's
  `Classroom.answer_question` checks it first.
* After work was returned the student can turn it in again (state back to `2`).

### What a student attaches

Attachments are slot 4 of the submission record and are written as a whole
list with the same call (`record = [ref, null, null, null, [entry, …]]`,
`writeMask = [null,1]`): adding one sends the old ones again plus the new one,
removing one sends what is left. Captured from the student's "Add or create"
menu — a link, then a new Google Doc, then removing the link:

```
link            [null, null, url, 4, null×13, id, null, null, 1]
new Google Doc  [null, null, null, 2, "application/vnd.google-apps.document", null×9, [], null, null, id, null, null, 1]
already there   the same with slot 2 = its url / Drive id, slot 4 = its type, slot 17 = its attachment id
```

For a new entry the web app puts a random number of its own in slot 17 (seen:
`32206126917`, `-13956082662`). A link keeps it as its attachment id; for the
new document the server creates the file in the student's Drive (named
"<student> - <assignment>") and answers with its own id. Read back, an entry
is `[title, null, url or driveId, kind, mimeType, thumbnail, urlToOpen, null×3,
2, null×3, details, 2, null, "attachmentId", null, null, 1]`; slot 13 of the
record counts the attachments.

Turning in afterwards is exactly the "mark as done" request: the attachments
are not sent again. The teacher reads them in the same slot of the student's
record.

Replayed outside a browser, each result read back by the student and by the teacher:

* a link, then a new Doc: both are listed, and the teacher's read of the record
  shows them before the work is turned in;
* removing one; sending an empty list removes all of them;
* turning in keeps the attachments; adding one to turned-in work is refused
  (error `[3]`);
* a file that is already in the student's Drive is attached with the same
  file entry and its Drive id in slot 2 — the type may be left out, the server
  fills in title and type. A file that had been turned in with other work was
  refused with error `[7, …]` (presumably because turning work in hands the
  file over to the teacher);
* a link to a Google Doc sent as a link entry comes back as a Drive-file
  attachment.

Reading them needs the right mask: the gradebook's `Zj93ge` mask leaves slot 4
empty; index 3 of the mask is what returns it (the student-work page asks for
`[null,1,null,1]`). The library reads with the mask of the write replies,
which is the gradebook's plus index 3 — accepted from teachers and students,
by item and by reference.

**The other "new" entries.** "New: Slides" was captured too: the same entry
with `"application/vnd.google-apps.presentation"`. Sheets and Drawings were
replayed with `…spreadsheet` and `…drawing`; each created a file of the right
kind (read back as `…punch`, `…ritz`, `…drawing`, opening under
`docs.google.com/presentation|spreadsheets|drawings/`).

A created file outlives its assignment: after the assignment was deleted, the
file's id could still be attached to another one (replayed).

**"File" (upload from the computer) is not a Classroom call.** The menu entry
opens Google Drive's own picker, an iframe served from
`drive.google.com/picker`; the file is uploaded to Drive there and Classroom
only receives its Drive id, which it attaches like any existing file. Outside
a browser the exported cookies do not get into Drive (replayed with an
export a quarter of an hour old: `drive.google.com/drive/…` answers 302 to the sign-in
page and the picker 401, while Classroom answers 200), so the upload step
cannot be done with cookies. A file that is in the student's Drive already can
be attached by its id.

**Returning several at once.** The call takes a list of `[ref, record, mask]`
entries, but a request with two "return" entries — two assignments of the same
student — was refused with error `[13]` and changed nothing. What the web app
sends when several students of one item are returned could not be captured
with a class of one student. The library returns one student per request.

After answering a multiple-choice question the page asks for the tally:
`yMOiof [[null,null,1,0],[[itemId,[courseId]]]]` ->
`["hrs.ssl", [[[0], itemRef, 2, [[["choice", count], …], itemRef]]]]`.
It answers teachers too.

## Comments

Captured from the student's assignment page (posting a private comment and a
class comment, deleting the class comment) and from the teacher's student-work
page (listing a student's private comments at start-up). All of it was
replayed for both sides.

```
jOFnxd  [[op], [[ref, comment]], mask]        op 2 = post, 4 = delete      ->  ["hrw.cmt", [comment]]
class comment     ref = [commentId, [itemId,[courseId]], null, 2]
private comment   ref = [commentId, null, [studentId,[itemId,[courseId]]], 3]
comment = [ref, null, null, null, [authorId], null, null, null, flag, null, null,
           ["edu.rt", text, null, null, [null, text]]]
flag    = [2] for a class comment, [1] for a private one
mask    = [1,1,1,1,null,null,[1],1,null,3,[1,null,1]]
```

`commentId` is null when posting. To delete, the web app sends the comment
back as it had read it, i.e. with its id and with the formatted text
(`<div>…</div>`) in the last slot. A comment as returned:
`[ref, created, updated, ms, [authorId], null, null, state, flag, "c:…:co:…", null, textBlock]`
with state `2` live, `3` deleted.

```
sLc6hf  [[100,null,1,0], mask, filter]        ->  ["hrq.cmt", [hasMore], [comment, …]]   newest first
class comments of an item        filter = [[null,null,states], [[itemId,[courseId]]], null, null, [[2]]]
private comments of a submission filter = [[null,null,states], null, null, [[studentId,[itemId,[courseId]]]], [[1]]]
one comment by reference         sLc6hf [[null,null,2,0], mask, [null,null,[ref]]]
```

`states` is `[2]` for live comments or `[2,3]` to include deleted ones.
Replayed:

* a private comment is seen by the student and the teacher, each can add to
  the thread; a student lists their own thread with the same filter;
* a deleted comment disappears for the student and is still listed for the
  teacher when deleted ones are asked for (the web app tells the student so);
* a student deleting the teacher's class comment is refused (error `[7]`); a
  teacher can delete a student's.

## Keeping an exported session alive (it cannot be done)

The browser keeps itself signed in by posting to
`https://accounts.google.com/RotateCookies` (body `[000,"-0000000000000000000"]`)
every few minutes and receiving a fresh `__Secure-1PSIDTS`. Sent with exported
cookies the request is accepted (200) but only `__Secure-1PSIDCC` /
`__Secure-3PSIDCC` come back, never a new `__Secure-1PSIDTS`.

Controlled run: one export copied twice; one copy called RotateCookies every
10 minutes and saved what came back, the other was never renewed, and both
were checked against Classroom at each round. Both still answered about 113
minutes after the export and both were redirected to the sign-in page at the
next check, about 123 minutes after it. The twelve renewals before that did
not issue a single new token and did not keep the renewed copy alive any
longer than the untouched one. An earlier export was found dead after roughly
two hours as well (not timed). So an exported session lasts about two hours
and has to be exported again; whatever lets the browser keep its session does
not travel with the cookies.

## Several accounts in one browser

A browser profile signed in to several Google accounts sends the same cookies
for all of them, so an export carries all of them. The web app tells them
apart by a number in the path: the page is `/u/<n>/h` and the calls go to
`/u/<n>/_/ClassroomUi/data/batchexecute` with `source-path=/u/<n>/…` (read from
a tab of account 3). Replayed with an export holding accounts 0, 2 and 3:

* no number at all, the form the library used from the start, is account 0;
* `/u/3/` acted as account 3 (a student of the test class);
* a number nobody is signed in under (`/u/4/h`) answers `302 -> /u/0/h`, i.e.
  falls back to account 0 without saying so — the library checks where the
  page was served from and refuses;
* a signed-out slot (`/u/1/`) redirects to the sign-in page.

The page's `WIZ_global_data` has the account's e-mail address under `oPEP7c`;
the user's own Classroom id sits in the client configuration right after the
favicon address (`…classroom/favicon.png","<userId>",false,…`).

## Stream item kinds

`n5NjMc` (create) and `F7Tqub` (update) carry the item in a tagged envelope,
and listings return the same envelope:

```
assignment    [2, [item, coursework, null, null, [2,2]]]
announcement  [3, null, [item]]
question      [5, null, null, [item, coursework, questionBlock]]
material      [7, null, null, null, null, [item]]
```

The item is the same 28-slot record for all four. An announcement has an
empty title and its words in the body (`item[27][0]`), exactly where an
assignment keeps its instructions; on read-back the text is at
`item[27][0][6][1]`. Captured for all four as drafts (typing into a composer
saves a draft at once). Creating, listing and deleting drafts of every kind
was replayed. **Publishing** an announcement, a material or a question was not
captured from the web app: the library sends the same `F7Tqub` update it uses
to publish an assignment, with that kind's envelope and the item state set to
`2`. Replayed for all three (and for both question types): the item came back
published (state 2) in the listings, questions also as gradebook columns, and
deleting the published item removed it.

## Questions

A question's coursework block is an assignment's with `[2]` instead of `[1]`
in slot 6. The question block is

```
[1, []]                          short answer
[2, null, [["choice", …]]]       multiple choice (one answer)
```

The composer first creates a short-answer draft (`n5NjMc`, 1082 characters)
and turns it into multiple choice with an update (`F7Tqub`, 1568 characters);
the library's requests are the same length and start the same. Creating a
multiple-choice question in one `n5NjMc` was replayed and read back. In a
listing (`dpT4Vd`) a question comes back in the same envelope with its block,
so the choices can be read from it.

The three checkboxes of the composer ("students can reply to each other",
"students can edit their answer", "students can see the class summary") were
left at their defaults; where they are encoded is not known. Students'
answers to a question have not been seen.

## Renaming and deleting a topic

`WRrP0b [[["topicId",[courseId]],"new name"],[1],[1,1,1]]` renames and
`EjPfY [["topicId",[courseId]]]` deletes, both captured from the topic's menu
and replayed (create, rename, read back, delete, gone). Deleting a topic that
still has a post under it keeps the post: it is listed in the "no topic"
section afterwards with `item[14]` empty (replayed with a draft).

## Link attachments

The composer's "link" button: `u1xGoc ["url"]`, then `n5QGnd` with the
descriptor `[null, null, url, 4, null×16, 1]` (kind 4; a Form is kind 7 with
its Drive id), then `F7Tqub` listing the returned attachment id in the body:
`[null, uuid, null, null, [[null, [[itemId,[courseId]], attachmentId]]]]` when
the item has no text. Captured on a material; replayed on a material draft and
on an assignment draft (one attachment read back each time). The listing's
mask returns the attachment reference only, not the link itself.

## Stream queries

`pONvgf` — `[[pageSize,null,1,0], READ_MASKS, [[lifecycle, [[courseId]], kinds, states]]]`,
answered `["hrsi.qr", [hasMore], [envelope, …]]`. Filters seen:

| Used by | lifecycle | kinds | states |
|---|---|---|---|
| Stream page feed | `[2,3]` | null (all) | `[2]` published |
| its drafts drawer | `[2]` | `[3]` announcements | `[3]` drafts |
| gradebook columns | `[2]` | `[2,5]` | `[2]` |

`lifecycle` filters on the item's slot 8 (replayed): `[2]` returns live items
only, `[3]` only deleted ones, `[2,3]` both. So the Stream page's own query
keeps returning a post after it was deleted, marked `3`; the library asks for
`[2]`. `kinds` are the envelope tags (2 assignment, 3 announcement, 5 question,
7 material) and `states` the item state (2 published, 3 draft). Classwork
listings (`dpT4Vd`) do not return deleted items.

## Listing the user's courses

The home page sends `gXtzob` at start-up with the query
`[[null,[[1]]], null, null, [1,2,3], null, null, [1,2]]` and paging
`[100,null,1,0]` (single-course reads put `[null, [[courseId]], …]` there).
The reply has the same shape as a single-course read, `["hrq.crs", […], [course, …]]`,
with id, name and section at slots 0, 5 and 8. The mask it uses does not return
the student list.

Start-up requests like this one cannot be seen by a hook installed after the
page loads. They were captured by loading the page in a hidden same-origin
iframe and patching its `fetch` before the app's scripts ran.

## Assigning to individual students (not solved)

In a class with one student the "assign to" picker cannot express it: choosing
that one student re-ticks "All students", so the web app sends the ordinary
whole-class request. The request for named students therefore has not been
captured.

It was looked for by experiment, with the test student as the judge (does
the student get a submission record and see the item?). Nine spellings of
stream item slot 25 — which the web app sends as `[0]` — were created and
published, among them `[1,[[studentId]]]`, `[1,[[someoneElse]]]`,
`[2,[[studentId]]]`, `[1]` and `[2]`. Every one was accepted, the student saw
every item including those "for somebody else", and no item read back with
anything in slot 25 (a whole-class item does not have it either). So slot 25
is not where the audience lives, or it is ignored without something else that
is not known. Capturing the real request needs a class with at least two
students.

## Not captured yet

* Assigning to individual students (see above) — needs two students.
* Returning several students of one item in one request — needs two students.
* The question composer's three checkboxes, and students replying to each other's answers.
* Quiz assignments, grade import, marking a single piece of work as missing or done.
  ("Import grades" exists on the student-work page of an assignment with a Form but stayed
  hidden for the test assignment; it would give totals only, never the answers.)
* The other class settings in the same record (overall grade calculation, grade categories, stream options).
* A teacher attaching Drive files other than Forms, and how Classroom shares them.
* Paging past the first 100 of anything.

All calls above were replayed **outside a browser with exported cookies** (no
browser page involved), which works for Classroom: unlike Docs, Forms and
Drive, its endpoints are not bound to the device. The same cookies sent to
the other hosts, less than half an hour after the export and while Classroom
still accepted them: a form's `/edit` opened by its
owner is redirected to the public `/viewform` page, as for somebody who is not
signed in; the form's response download answers 401; Drive answers 302 to the
sign-in page. So the content of a Google Form — its questions as an editor
sees them and the students' responses — cannot be reached this way. What can
be collected with cookies alone are answers given in Classroom itself: one
Classroom question per question, read from the submission records.
