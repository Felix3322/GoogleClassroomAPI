# Google Forms editor: private web protocol (reverse-engineered)

> **Live-test finding (2026-11-22): the Forms editor is session-bound and does
> not work from replayed cookies.** With cookies exported from a signed-in
> Chrome profile, a plain `GET /forms/u/0/d/{id}/edit` from a non-browser
> client is redirected to the read-only `/viewform`, and `GET …/create` is
> redirected to the sign-in page — even though the *same* account opens the
> editor normally in the browser. This was reproduced with a fresh export
> (rotated `__Secure-1PSIDTS`/`__Secure-1PSIDCC`) and with a full set of
> browser request headers; neither helped. Classroom's private endpoints, by
> contrast, accepted the same cookies for both reads and writes.
>
> So Google binds the Docs/Forms **write** surface to the browser session
> (device/session binding, its anti-cookie-theft mechanism). Everything below
> was captured and replayed **inside the live browser page**, where the session
> is honoured. It cannot be driven headless from exported cookies. A headless
> client that needs to create or edit Forms must use OAuth and the official
> Forms API (which lacks the private-only settings — see section 8).


Captured 2026-11-22 from the live Forms editor in Chrome, with a personal
Google account and a throwaway test form. Every line marked **verified** was
observed in a real request; lines marked **replayed** were additionally sent
by hand from the page and accepted by the server.

This is not a documented Google API. It can change without notice.

## 1. Loading a form

`GET https://docs.google.com/forms/d/{formId}/edit` (session cookies) returns
HTML with two inline globals.

`FB_LOAD_DATA_` (JSON array), **verified**:

| Path | Meaning |
|---|---|
| `[1]` | current revision number |
| `[3]` | editing session id (`sid`), issued by the server for this page load |
| `[4]` | signed-in user's email |
| `[13]` | form id |
| `[0][3]` | document title (the Drive file name) |
| `[0][14]` | responder path, `e/1FAIpQL…` → `https://docs.google.com/forms/d/e/…/viewform` |
| `[0][1]` | the form model as respondents get it, **without answer keys** |
| `[14]` | the editor's full copy of the form model, with answer keys; same layout |

Form model (`[14]`, and `[0][1]` minus the keys), **verified**:

| Index | Meaning |
|---|---|
| `0` | description (plain) |
| `1` | items, in order |
| `2` | `[confirmationMessage, showLinkToRespondAgain, publishSummary, allowResponseEdits, ?]` |
| `8` | title (plain) |
| `10` | `[progressBar, limitOneResponse, shuffleQuestions, 2, null, null, emailCollection]` |
| `16` | `[[?, ?, showMissedQuestions, showCorrectAnswers, showPointValues], releaseGradesImmediately, isQuiz, ?, ?]` |
| `24` | `[null, richDescription]` |
| `25` | `[null, richTitle]` |

`emailCollection`: `1` do not collect, `2` verified, (`3` responder input, by
elimination; not toggled during capture).

`_docs_flag_initialData.info_params` holds `token` (XSRF) and `ouid`.

## 2. Items

Model form of an item:

```
[itemId, title, description, type, [question, …], …, [null, richTitle]]
question = [questionId, [option, …] | null, required, …, grading]
option   = [text, null, null, null, isOther]
grading  = [points, [[correctAnswer, …]], [feedbackWhenRight], [feedbackWhenWrong]]
```

Item types seen, **verified**: `1` paragraph, `2` multiple choice, `4`
checkboxes, `8` section (page break). `0` is short answer (the well-known
value from the public `FB_PUBLIC_LOAD_DATA_`; not toggled during capture).

`itemId` and `questionId` are 31-bit integers chosen by the **client** when an
item is added. They are the decimal form of the hex IDs the official Forms API
reports, and `questionId` is the `entry.<id>` of the public response form.

## 3. Saving

```
POST https://docs.google.com/forms/u/0/d/{formId}/save
     ?id={formId}&sid={sid}&vc=0&c=0&w=1&flr=0&token={token}&ouid={ouid}
X-Same-Domain: 1
Content-Type: application/x-www-form-urlencoded;charset=UTF-8

rev={revision}&bundles=[{"commands":[[cmdId,cmdId,"<payload as a JSON string>"], …],"sid":"{sid}","reqId":{n}}]
```

* `sid` must be the one from the page (**replayed**: a random one is rejected
  with HTTP 550, error code 13).
* Without `X-Same-Domain: 1` the server answers 400 "page not found" (**replayed**).
* `reqId` counts up from 0 within one session.
* `rev` is the revision the client believes is current. The server accepted a
  stale one and applied the command on top (**replayed**), so there is no
  optimistic lock here.
* A malformed payload gives HTTP 400, error code 3 (**replayed**).
* Success: `)]}'` then JSON with `revisionRanges` and `metadata.serverRevision`.
  One revision is consumed per command.

### Commands

| Command id | Does | Payload |
|---|---|---|
| `32427935` | set form title | `["", null, title]` **verified** |
| `25101318` | set form description | `["", null, description]` **replayed** |
| `25290104` | add item | `[index, item]` **verified** |
| `24479122` | replace item | `[index, item]` **verified** |
| `28252204` | move item | `[fromIndex, toIndex]` **replayed** (id found by reading the editor's JavaScript) |
| `28511809` | delete item | `[index]` **verified** |
| `112282607` | quiz settings | `[[[true, true, showMissed, showCorrect, showPoints], releaseImmediately, isQuiz, null, false]]` **verified** |
| `28132780` | after-submit settings | `[[confirmationMessage, showLinkToRespondAgain, publishSummary, allowResponseEdits, false]]` **verified** |
| `47603585` | responder settings | `[[progressBar, limitOneResponse, shuffleQuestions, 2, null, null, emailCollection]]` **verified** |

Item as sent in `25290104` / `24479122` (note: the title travels in the last slot):

```
question item: [itemId, null, null, type, [question], null, null, null, null, null, null, null, null, title]
section:       [itemId, null, null, 8, null, null, null, null, null, null, null, null, null, title]
question:      [questionId, [option, …] | null, required, null, null, null, null, null, false, grading?]
```

Details established by sending a payload and reading the model back (**replayed**):

* The item's description is slot 2: `[itemId, null, description, type, …, title]`.
* The grading block is `[points, correctAnswers, whenRight, whenWrong, generalFeedback]`,
  the same order as the fields of `Grading` in the official Forms API.
* Each correct answer is its own list: `[["4"],["four"]]`. `[["4","four"]]` is
  accepted as well, but the server silently keeps only `"4"`. This holds for
  checkbox questions too.
* A text question with a key or points is
  `[questionId, null, required, null, null, null, null, null, false, grading]`.
* A paragraph question accepts `[3, null, null, null, ["Sample answer."]]`
  (points and general feedback) and is rejected with HTTP 400 when the feedback
  sits in the right/wrong slots.

Not captured yet: images, response validation, general feedback on choice questions.

The editor's JavaScript registers 24 command ids in one table
(`{25290104: …, 23475663: …, 24479122: …, 28252204: …, 29353535: …, 28511809: …,
31358495: …, 525004503: …, 28132780: …, 2519752: …, 25101318: …, 25149807: …,
32427935: …, 100276673: …, 58986495: …, 94701729: …, 58167415: …, 28175106: …,
112282607: …, 124862738: …, 47603585: …, 152311694: …, 140028851: …, 525099936: …}`).
The ones not listed above have not been identified.

A session can be opened without running the editor: fetching the edit page and
reading `sid`, `token`, `ouid` and the revision out of the HTML is enough for
`save` to be accepted (**replayed**).

## 4. Publishing

```
POST /forms/u/0/d/{formId}/publish?sid=…&vc=0&c=0&w=1&flr=0&token=…&ouid=…
request=[0]
```

answers `{"status":0}` (**verified**). Afterwards
`GET /forms/u/0/d/{formId}/publishingsettings` returns
`{"published":true,"requireSignin":false,"domainClosed":false,…,"specialPublishedMetadata":{"acceptingResponses":true,…}}`.

The `request` message has three fields, read from the editor's JavaScript:
a boolean (sent as `0` by the plain "Publish" button; the other button is
"Publish and notify"), an optional message and a list. So this endpoint only
publishes.

**Closing the form to responses** has its own endpoint (**replayed** both ways,
checked against `publishingsettings`):

```
POST /forms/u/0/d/{formId}/setacceptingresponses?…   request=[0]   stop accepting
                                                      request=[1]   accept again
```

**Creating a form** needs no official API (**replayed**):
`GET /forms/u/0/create` answers with the editor page of a brand-new form. Its
id is `FB_LOAD_DATA_[13]`, and the session in that page can save at once. A
new form starts at revision 1 with one placeholder multiple-choice question.
Every `GET` makes another form.

**Not found:** unpublishing. Ruled out by replay: other values of `request`
on `/publish` (`[1]`, `[2]`, `[3]` answer `status 0` but change nothing;
`[0,1]` is rejected), and commands `31358495` and `525004503` with boolean
pairs (rejected). The editor's JavaScript has no endpoint with "unpublish" in
its name. Restricting responders to named people goes through the Drive
sharing dialog, which runs in its own frame and was not captured.

Other endpoint names found in the editor's JavaScript and not explored:
`copy`, `trash`, `deletesingleresponse`, `downloadresponses`,
`getresponsecount`, `gradequestion`, `prefill`, `emailform`,
`notifyresponders`, `validateregex`, `linkform`, `importform`.

`GET …/getalternateresponderaudience` returns the Classroom classes the form
is attached to, as `[[["<class name>", null, "<class group address>"]]]`: the
editor offers the class group as a responder audience.

**Quiz mode cannot simply be switched off** once questions carry answer keys:
the quiz command with `isQuiz = false` was accepted on an empty form and
rejected with HTTP 400 after a key had been set (**replayed**).

## 5. Reading responses

All three take the same query parameters as `publish` and answer
`)]}'` + `{"status":0,"prsj":"<JSON string>"}` (**replayed** for the first, **verified** for the second).

| Call | `prsj` |
|---|---|
| `GET …/responsemetadata` | `[[[responseId, respondentEmail, submittedMs], …]]` |
| `POST …/getsingleresponse` with body `viewresponse={responseId}` | one response, see below |
| `GET …/aggregatestatistics` | the form model plus aggregated counts |

One response:

```
[[submittedMs, null, respondentEmail, null,
  [[null, questionId, [answer, …], 0], …],          # raw answers
  1, "<id>", null,
  [[questionId, maxPoints, earnedPoints, 0], …],     # per-question score (quiz)
  [null, maxTotal, earnedTotal],
  …, firstSubmittedMs, lastSubmittedMs, …]]
```

A question the respondent left blank has no entry in the answers list.

## 6. The public responder page

`GET https://docs.google.com/forms/d/e/{responderId}/viewform` embeds
`FB_PUBLIC_LOAD_DATA_` with the same model. The form posts to
`…/formResponse` with `entry.{questionId}` fields. After submitting a quiz the
respondent gets a link to `…/viewscore?viewscore=…`.

## 7. How the data sits in the HTML

* `var FB_LOAD_DATA_ = [[null, …` is followed by other statements in the same
  `<script>`, so parse one JSON value starting at the `[`.
* `_docs_flag_initialData={…,"info_params":{"token":"…","ouid":"…"},…};`

## 8. What this adds over the official Forms API

Settings with no field in the official API, all settable here: when grades
are released, what respondents may see after submitting (missed questions,
correct answers, point values), limit to one response, allow response
editing, question shuffling, progress bar, link to submit another response.
