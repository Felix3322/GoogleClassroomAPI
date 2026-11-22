# Google Classroom web app: private RPCs (reverse-engineered)

Captured 2026-11-22 from classroom.google.com in Chrome, with a personal
Google account and a throwaway class that has no students. Everything below
was observed in real requests made by the web app. Listing and creating
topics, and creating, updating and publishing an assignment, were also sent by
hand with self-built requests and accepted by the server. Nothing involving
students could be captured (the test class is empty).

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
| `o5pSDe` | | sent by the People page; **not the roster** — it returned `[]` for a class that has a student (probably pending invitations) | `[[courseId],100,null,[1,null,1]]` |
| `UG41I` | `hrq.usr` | user profiles (name, email, photo) by user id | see "Roster" |
| `dpT4Vd` | `hrcw.qr` | list classwork, one section at a time | see "Listing classwork" |
| `Zj93ge` | `hrq.sub` | student submissions of one assignment (the gradebook calls it per assignment) | see "Submissions" |
| `pONvgf` | | published classwork for the gradebook | `[[10,null,1,0], masks…, [[[2],[[courseId]],[2,5],[2]]]]` |
| `sLc6hf` | `hrq.cmt` | comments | `[[100,null,1,0], mask, [[null,null,[2,3]],[[itemId,[courseId]]],null,null,[[2]]]]` |
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
an empty section has no third element. A read-back item does **not** carry its
topic, so the topic has to come from the section it was listed in. Paging past
the first page was not captured (the library raises if `hasMore` is true).

## Deleting an assignment

There is no separate delete RPC. The Classwork "delete" menu sends `F7Tqub`
with the stream item's slot **8** set to `3` (live items have `2`) and masks
that write only that field:
`[[[null,null,null,null,null,1],[]], [[null,null,null,null,null,1]], [[null,null,null,null,null,1],[]], null, null, [[null,null,null,null,null,1]]]`.
Replayed with a minimal item (id only, empty title): drafts and assignments
disappeared from the `dpT4Vd` listing afterwards.

## Submissions

The gradebook calls `Zj93ge` once per assignment:
`[[100,null,1,0], mask, [null,[[itemId,[courseId]]]], 0]` (mask in
`web/classroom.py`). Replayed for three published assignments; each answered
`["hrq.sub",[false]]` — no records — while the gradebook showed the student as
"assigned, no grade". The student had not opened or turned in anything, so a
**populated record has never been seen**; its layout is unknown.

## Assigning to individual students (not solved)

In a class with one student the "assign to" picker cannot express it: choosing
that one student re-ticks "All students", so the web app sends the ordinary
whole-class request. The request for named students therefore has not been
captured. Probes that put candidate values into stream item slot 25 (the web
app sends `[0]` there for a whole-class item) were all accepted, but nothing
showed up on read-back, and with one student "only this student" and "all
students" cannot be told apart anyway. Capturing it needs a class with at least
two students.

## Not captured yet

* Assigning to individual students (see above) — needs two students.
* A populated submission record, and turning work in / returning / grading —
  needs a student who has opened or turned in work.
* Listing the teacher's courses: the web app loads them once at start-up, before
  any hook can see the request. A course id can be read from any course link
  (`course_id_from_url`).
* Quiz assignments, grade import, marking a single piece of work as missing or done.
* The other class settings in the same record (overall grade calculation, grade categories, stream options).
* Drive file attachments other than Forms, and how Classroom shares them.

All calls above were replayed **outside a browser with exported cookies** (no
browser page involved), which works for Classroom: unlike Docs/Forms, its
endpoints are not bound to the device.
