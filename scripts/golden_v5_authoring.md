# Golden v5 — authoring spec

This is the source-of-truth authoring document for the 17-case
golden suite shipped to Gemini for `gpt-5.5` exemplar grading.
Cases 1-12 preserve the user's original structured spec
(intent / success / failures / effects / ECO refs). Cases 13-17
were added to clear ~60% MCP tool coverage and are authored from
the harness's existing eval rows.

The deliverable to Gemini is `scripts/golden_v5_deliverable.csv`
which carries only `(case_id, prompt_index, prompt, exemplar)` —
the rest of the spec below is for our records and to inform any
future iteration on prompts/exemplars.

## Coverage

- **17 case_ids**, **119 prompts** (7 per case)
- **20 / 35** Calendly MCP tools exercised (**57%**) — close to the
  60% bar; gap is `availability-get_user_availability_schedule` and
  a handful of routing-form/no-show getters

## Fixture preconditions (run before each validation pass)

`scripts/fixture_setup_staging.py` already establishes most of what's
below. Items marked **MANUAL** require a one-time setup on the
staging account that isn't currently scriptable (MCP scope or
real-time-elapse limitations).

| Case | Fixture state required | Source |
|---|---|---|
| list_event_types | Coffee Chat exists | step 1 ✓ |
| list_meetings | One upcoming Coffee Chat with cash0902 | step 3 ✓ |
| check_when_busy | Same as above | step 3 ✓ |
| find_event_type_available_times | Coffee Chat availability includes Thursday | step 2 ✓ |
| get_scheduling_link | Coffee Chat exists with `scheduling_url` | step 1 ✓ |
| reschedule_meeting | One upcoming Coffee Chat with reschedule_url | step 3 ✓ |
| create_single_use_link* | Nothing prior | none needed |
| update_event_type | Coffee Chat exists; revert duration after run | step 1 ✓ + reset script |
| create_event_type | Reset script must clean up the new event type | reset script |
| update_event_type_schedule | Reset must restore Thursday availability | reset script |
| cancel_meeting | One upcoming Coffee Chat to cancel | step 3 ✓ |
| book_meeting | Reset must clean up the booked meeting | reset script |
| invite_team_member | newhire@calendly.com NOT pre-invited; seats available | step 7 ✓ |
| list_routing_form_submissions | At least one submission on the test form | **MANUAL** (step 8) |
| list_availability_schedules | "Working hours" exists | default ✓ |
| mark_no_show | A past Coffee Chat with cash0902, not yet flagged | **MANUAL** (step 4) |

The two MANUAL items are both fixture-setup limitations:
- Past meeting: requires booking + waiting for time to pass.
  Workaround for repeat runs: schedule a 1-min meeting starting in
  ~5 min, validate `mark_no_show` after the meeting ends.
- Routing-form submission: the staging MCP token lacks
  `routing_forms:write`; submission must be done via the form's
  public URL.

## The 12 user-authored cases (verbatim spec)

### 1. List my event types — `list_event_types`

**What the host wants.** A quick read of the event types they've
set up: what meetings they offer and how long each runs.

**Example prompts.**
- "List my event types"
- "What meetings can people book with me?"
- "Show me my Calendly meeting types"

**What success looks like.** A clear list of the host's active
event types with names and durations. Useful as a starting point.
The host might follow up with "share the link to my Coffee Chat"
or "update Coffee Chat to 45 min."

**What should not happen.**
- Padding the response with descriptions, who-can-book settings,
  custom questions, or other fields the host didn't ask for. Name
  and duration are the core; URL is fine to include, but not
  mandatory.
- Mixing inactive or hidden event types in without flagging them.

**Effects.** Read-only.

### 2. List my upcoming meetings — `list_meetings`

**What the host wants.** A read of what's on their Calendly
schedule, usually within a near-term window (today, tomorrow, this
week) so they can plan or pick a meeting to act on.

**Example prompts.**
- "What meetings do I have this week?"
- "Show me my upcoming Coffee Chats"
- "What's on my Calendly schedule tomorrow?"

**What success looks like.** A list of upcoming Calendly-booked
meetings, each showing event type name, date and time in the host's
local timezone, and the invitee's name. The list is grounded in
real account data and scoped to what the host asked for (e.g. "this
week" returns this week, not all future meetings).

**What should not happen.**
- Returning meetings outside the requested window.
- Returning times without a timezone.
- Misconverting UTC to local time, especially around daylight saving
  transitions.
- Mixing in meetings booked through other calendars or tools.
- Refusing or claiming the assistant can't access the calendar.

**Effects.** Read-only.

### 3. Check my overall availability — `check_when_busy`

**What the host wants.** A read of when they're free or busy across
their calendar, independent of any specific event type. Often a
quick gut check before agreeing to a time or planning the day.

**Example prompts.**
- "Am I free Thursday afternoon?"
- "What's my schedule look like tomorrow?"
- "Do I have time for a 30-min between 2 and 4 today?"

**What success looks like.** A clear answer about busy/free times
in the requested window, in the host's local timezone. Either a
simple yes/no with the conflicting meeting noted, or a list of busy
blocks the host can read against their question.

**What should not happen.**
- Confusing this with event-type-specific availability. The host
  isn't asking about bookable Coffee Chat slots, they're asking
  about their broader calendar.
- Returning times without a timezone.
- Refusing or claiming the assistant can't access the calendar.
- Misconverting UTC slots to local time, especially around DST
  transitions, and confidently labeling slots with the wrong hour.
  (**ECO-2135**)

**Effects.** Read-only.

### 4. Check availability for a given event type — `find_event_type_available_times`

**What the host wants.** Open slots on a specific event type within
a time window, so they can share availability with someone, pick a
time to book, or just check the day.

**Example prompts.**
- "What's my next available slot for a Coffee Chat?"
- "When am I free for Coffee Chats Thursday?"
- "What's my Coffee Chat availability tomorrow afternoon?"

**What success looks like.** A usable list of open times in the
host's local timezone, grounded in real account data. The host can
share the times with someone or pick one to book.

**What should not happen.**
- Reporting a time as unavailable when it's actually open, because
  the assistant queried too narrow or misaligned a window.
  (**ECO-2124**)
- Misconverting UTC slots to local time around DST. (**ECO-2135**)
- Returning slots without a timezone.
- Refusing or claiming the assistant can't access the calendar.

**Effects.** Read-only.

### 5. Get the scheduling link — `get_scheduling_link`

**What the host wants.** A reusable link they can share with someone
(drop into an email, paste into Slack) so the recipient can book a
time on their calendar.

**Example prompts.**
- "Get me the link for my Coffee Chat"
- "What's the link people use to book a Coffee Chat with me?"
- "Send me the URL for my Coffee Chat event type"

**What success looks like.** One direct, reusable scheduling URL
for the named event type. The host can paste it into a message and
the recipient sees the standard Calendly booking page.

**What should not happen.**
- Returning a single-use / one-time link instead of the reusable
  event-type URL.
- Returning multiple links when the host asked for one specific
  event type.
- Fabricating a URL rather than reading it from the event type
  record.

**Effects.** Read-only.

### 6. Reschedule an upcoming meeting — `reschedule_meeting`

**What the host wants.** To move an existing meeting to a different
time, without it being a hassle.

**Example prompts.**
- "Reschedule my next Coffee Chat"
- "I need to move my next Coffee Chat"
- "Move my Coffee Chat with cash0902 to a different time"

**What success looks like.** The assistant identifies the meeting
(event type, time, invitee) and returns the reschedule link inline,
ready to use or forward to the invitee. It also offers to
cancel-and-rebook directly using the existing invitee details, in
case the host would rather have it done than send a link.

**What should not happen.**
- Concluding that reschedule isn't supported, when both paths are
  actually available. (**ECO-2125, ECO-2134**)
- Fabricating a link rather than reading it from the meeting record.
- Returning a link without saying which meeting it's for.
- Doing cancel-and-rebook silently without surfacing the link
  option, when both are valid.

**Effects.** Depends on the path the host picks. Returning a link is
read-only. Cancel-and-rebook changes calendar state and notifies the
invitee.

### 7. Create a single-use link (without customization) — `create_single_use_link`

**What the host wants.** A one-time scheduling link tied to one of
their event types.

**Example prompts.**
- "Generate a one-time scheduling link I can send a candidate for
  my Coffee Chat"
- "Give me a single-use link for my Coffee Chat"

**What success looks like.** A working single-use scheduling URL
for the named event type. The host can send it to one person and
trust it expires after one booking.

**What should not happen.**
- Returning the reusable event-type URL instead of a single-use
  link.

**Effects.** Creates a link record. The link itself is throwaway.

### 8. Create a single-use link (with customization) — `create_single_use_link_custom`

**What the host wants.** A one-time scheduling link with a tweak:
different duration than the underlying event type, prefilled
invitee details, or a custom question.

**Example prompts.**
- "Create a single-use link for my Coffee Chat with a 15-minute
  duration"
- "Generate a 15-minute single-use link for Coffee Chat"

**What success looks like.** A working single-use scheduling URL
with the requested customization actually applied.

**What should not happen.**
- Refusing customization with "duration is locked to the event
  type" when customization is exactly what the underlying API
  supports, just on a different endpoint. (**ECO-2123**)
- Silently dropping the customization the host asked for and
  returning a default link.

**Effects.** Creates a link record. The link itself is throwaway.

### 9. Update an event type's details — `update_event_type`

**What the host wants.** Change something about how an event type
works (duration, location, name) without going to the Calendly UI.

**Example prompts.**
- "Update my Coffee Chat to 45 minutes"
- "Make my Coffee Chat 45 minutes instead of 30"
- "Change Coffee Chat duration to 45 minutes"

**What success looks like.** A confirmation that the change was
actually made, restating the new values clearly enough that the
host can trust it landed. Subsequent bookings of that event type
reflect the change.

**What should not happen.**
- Asking the host to paste the raw event-type structure rather than
  reading current state from the API.
- Asking which Calendly account to use when the OAuth context
  already identifies the host.
- Confirming a change that wasn't actually applied.

**Effects.** Changes the event type. Future bookings reflect the
new settings.

*Note: limited to updates supported by the public API (a subset of
what the UI can change).*

### 10. Create a new event type — `create_event_type`

**What the host wants.** A new event type set up without going to
the Calendly UI, usually with the basics: name, duration, and
location.

**Example prompts.**
- "Create a new 30-minute event type called Intro Call to meet via
  Zoom"
- "Set up a 15-min Quick Sync event type"
- "Make a new Discovery Call event type, 30 minutes, on Google
  Meet"

**What success looks like.** A confirmation that the event type was
created with the requested settings, including a scheduling URL
the host can share. The event type appears in subsequent "list my
event types" responses.

**What should not happen.**
- Creating with sensible-but-wrong defaults the host didn't ask for.
- Failing silently and reporting success.
- Asking which Calendly account to use when the OAuth context
  already identifies the host.
- Asking the host to specify every field the API supports rather
  than using defaults for the unmentioned ones.

**Effects.** Creates a new event type.

*Note: limited to one-on-one event types only with basic field
customization.*

### 11. Update an event type's availability — `update_event_type_schedule`

**What the host wants.** Change which days, hours, or windows an
event type is bookable: add or remove days, narrow the hours,
switch to a different schedule.

**Example prompts.**
- "Remove Thursdays from my Coffee Chat event type's availability"
- "Stop offering Coffee Chats on Fridays"
- "Drop Thursday from my Coffee Chat schedule"

**What success looks like.** A confirmation that the schedule
change was actually made, restated in a way the host can trust.
The next time someone tries to book during a removed window, they
don't see availability.

**What should not happen.**
- Updating the wrong time because of a UTC/local conversion error
  around DST. (**ECO-2135**)
- Failing with a schema error because the assistant tried to
  construct the rules array from scratch instead of reading current
  state and modifying it.
- Asking the host to paste the raw schedule structure.
- Silently overwriting unrelated availability rules (e.g. removing
  Thursday but also wiping Tuesday).

**Effects.** Changes the event type's availability schedule.
Highest-risk write: the wrong update can wipe an entire schedule.

### 12. Cancel an upcoming meeting — `cancel_meeting`

**What the host wants.** Drop a specific upcoming meeting (usually
identified by time, day, or invitee) and have the invitee notified.

**Example prompts.**
- "Cancel my next Coffee Chat"
- "Cancel my 2:30pm Coffee Chat appointment next Thursday"
- "Drop my Coffee Chat with cash0902 on Thursday"

**What success looks like.** The assistant identifies the specific
meeting (by event type, time, and/or invitee) before cancelling, so
the host can confirm it's the right one. After confirmation, the
meeting is cancelled and the invitee is notified by Calendly. If
multiple meetings match, the assistant asks rather than guessing.

**What should not happen.**
- Cancelling blindly without identifying the meeting first.
- Visible "didn't recognize that ID" errors when the assistant uses
  the wrong identifier and has to retry. (**ECO-2131**)
- Cancelling the wrong meeting because the host's reference was
  ambiguous and the assistant didn't ask.
- Confirming a cancellation that didn't actually happen.

**Effects.** Cancels the meeting and notifies the invitee. Not
reversible from inside the assistant.

### 13. Book a meeting — `book_meeting`

**What the host wants.** Schedule a meeting on their calendar with
a named invitee, on a specific event type, at a specific or
next-available time.

**Example prompts.**
- "Book a Coffee Chat with cash0902@gmail.com for next Thursday at
  2:30pm"
- "Set up a 30-min Coffee Chat with cash0902@gmail.com for the
  next available slot"
- "Schedule cash0902@gmail.com into my Coffee Chat tomorrow at 10"

**What success looks like.** A confirmed booking with the right
host, right invitee, and right time, restated clearly so the host
knows exactly what got scheduled. Calendar event and invitee
notifications go out automatically.

**What should not happen.**
- Concluding that host-side booking isn't supported and offering
  only a workaround link, when the direct booking capability is
  available. (**ECO-2134**)
- Inverting host and invitee: treating the request as targeting
  the invitee's account rather than the connected host's.
- Silently booking the wrong time because of a UTC/local conversion
  error.
- Asking the host to re-supply invitee details that the assistant
  already has from context.
- Reporting a successful booking that didn't actually go through.

**Effects.** Creates a calendar event and notifies the invitee.

## The 4 added cases (authored to clear coverage)

### 14. Invite a team member — `invite_team_member`

**What the host wants.** Send a Calendly team invitation to an
email so the person can join the host's organization.

**Example prompts.** See `golden_v5.csv` rows 1-7 for `invite_team
_member`.

**What success looks like.** Confirmation that the invitation was
sent, naming the invitee email. The recipient gets an email and
can accept to join.

**What should not happen.**
- Inviting silently without naming the invitee in the response.
- Fabricating success when the API rejects (e.g. seat allotment
  exhausted).

**Effects.** Sends an invitation email. Reversible by revoking the
invitation before acceptance.

### 15. List routing form submissions — `list_routing_form_submissions`

**What the host wants.** See submissions to a routing form so they
can respond to or qualify leads.

**What success looks like.** A list of submissions with at least
the date and a snippet of the answers (or a clear statement that
the form has no submissions).

**What should not happen.**
- Fabricating submission content that wasn't actually returned by
  the API.

**Effects.** Read-only.

### 16. List availability schedules — `list_availability_schedules`

**What the host wants.** See the named availability schedules
attached to their account.

**What success looks like.** A list of named schedules (e.g.
"Working hours"), with the default schedule flagged.

**What should not happen.**
- Confusing this with event-type-specific availability rules.

**Effects.** Read-only.

### 17. Mark a no-show — `mark_no_show`

**What the host wants.** Flag an invitee on a past Calendly meeting
as not having shown up.

**What success looks like.** Confirmation that the no-show was
recorded for the named invitee on the named (past) meeting.

**What should not happen.**
- Silently marking the wrong invitee or wrong meeting.
- Failing silently and reporting success.

**Effects.** Adds a no-show record to the meeting. Reversible by
clearing the no-show.

## Maintenance notes

- **Date drift.** The `list_meetings`, `cancel_meeting`,
  `reschedule_meeting`, `book_meeting`, and `mark_no_show` cases
  reference real meeting times from fixtures. As real time
  advances, the *absolute date* of an upcoming meeting in the
  exemplar drifts; the harness's exemplar judge has a narrow
  "Calendar dates drift in real time" rule to handle this without
  rewriting exemplars. Gemini's external judge may or may not have
  similar tolerance.
- **Re-fixture before each validation pass.** Each pass mutates
  state (cancel, mark no-show, invite, book). Run
  `scripts/fixture_reset_staging.py` followed by
  `scripts/fixture_setup_staging.py` to restore deterministic state.
