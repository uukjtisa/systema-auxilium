"""
systema/common/greeting.py

The greeting shown at the top of a fresh session: time-aware, day-aware, and
aware of how you have actually been using the app.

Pure text and pure stdlib — the banner widget lives in `ui/chat/bubbles.py`.
Kept separate so the wording can be tested and edited without standing up a
window.

FIVE POOLS, merged per session
  * `_TIME_LINES[bucket]`      — morning / afternoon / evening / night.
  * `_DAY_LINES[weekday]`      — Monday..Sunday, valid at any hour. Genuinely
                                 any hour: see `_DAY_OPENING_LINES`.
  * `_DAY_OPENING_LINES[wd]`   — day-of-week lines that assume the day is
                                 STARTING ("A fresh week"). Morning and
                                 afternoon only.
  * `_DAY_TIME_LINES[(d, b)]`  — combinations worth saying out loud
                                 ("Friday night", "Monday morning").
  * `_SIGNAL_LINES[signal]`    — what your usage says: first session ever,
                                 back after ten minutes, back after two weeks,
                                 fourth session today, third late night running.

WHY THERE IS NO MODEL BEHIND THIS
Generating greetings with an LLM was considered and rejected. It would spend
tokens on every session open, put a network call behind the FIRST thing on
screen (so a slow provider means an empty window), expose conversation history
for decorative text, and ship copy nobody had read. Signal-driven selection
from curated pools gets the "it noticed" feeling for free, instantly, offline,
and reproducibly. Signals are weighted UP so they surface often when they
apply — "Back already?" twenty minutes later lands harder than anything a
model would have written.

AUTHORING RULES
  * Every named phrasing has a name-less twin AT THE SAME INDEX. An unset name
    must still read as a whole greeting, never a dangling comma. A test walks
    every pair.
  * Punctuation is optional when writing a line — `_punctuate` adds a full stop
    if you leave one off, so adding lines stays frictionless.
  * Placeholder names ("USER", "ADMIN", …) count as unset.
  * No emoji — house rule. Plain text only.
  * Em dashes are used sparingly on purpose; most lines use commas or a second
    sentence instead.
"""

import random
import re
from datetime import datetime, timedelta

# Hour ranges are inclusive of the start, exclusive of the end, walking
# forward from 05:00. "night" is everything left over, which is why it wraps.
_BUCKETS = (
    ("morning", 5, 12),
    ("afternoon", 12, 17),
    ("evening", 17, 22),
)

# When the small hours end. Before this, the calendar day has rolled over but
# the NIGHT has not — see greeting_weekday().
SMALL_HOURS_END = 5

# Names that mean "not set". Shared with the identity-prompt builder in
# app/controller.py, which imports this rather than keeping its own copy —
# two lists of placeholder names WILL drift, and the failure is silent.
PLACEHOLDER_NAMES = ("USER", "USERNAME", "NAME", "YOUR NAME", "ADMIN",
                     "ADMINISTRATOR", "DEFAULT", "UNKNOWN", "NONE", "NULL")

# How much more often a signal line is drawn than an ordinary one. Signals are
# the whole point — when the app can tell you have been up all night, saying so
# should not be a one-in-forty coincidence.
SIGNAL_WEIGHT = 6


# ── time of day ──────────────────────────────────────────────────────────────
_TIME_LINES = {
    "morning": (
        ("Good morning, {name}", "Good morning"),
        ("Morning, {name}", "Morning"),
        ("Early start, {name}", "An early start"),
        ("A fresh page, {name}", "A fresh page"),
        ("Ready when you are, {name}", "Ready when you are"),
        ("What's first, {name}?", "What's first?"),
        ("Let's get into it, {name}", "Let's get into it"),
        ("Coffee first, {name}?", "Coffee first?"),
        ("Clean slate, {name}", "A clean slate"),
        ("Bright and early, {name}", "Bright and early"),
        ("What are we building, {name}?", "What are we building?"),
        ("Good morning, {name}. The day's still blank", "Good morning. The day's still blank"),
        ("Morning. Where do we start, {name}?", "Morning. Where do we start?"),
        ("Nice and early, {name}", "Nice and early"),
        ("Right then, {name}. Let's begin", "Right then. Let's begin"),
        ("Let's make it a good one, {name}", "Let's make it a good one"),
        ("Plenty of day ahead, {name}", "Plenty of day ahead"),
        ("Up with the first light, {name}", "Up with the first light"),
        ("Good morning. What's the plan, {name}?", "Good morning. What's the plan?"),
        ("Fresh start, {name}", "A fresh start"),
    ),
    "afternoon": (
        ("Good afternoon, {name}", "Good afternoon"),
        ("Afternoon, {name}", "Afternoon"),
        ("Back at it, {name}", "Back at it"),
        ("Where were we, {name}?", "Where were we?"),
        ("What's next, {name}?", "What's next?"),
        ("Picking up again, {name}", "Picking up again"),
        ("Middle of the day, {name}", "Middle of the day"),
        ("At your service, {name}", "At your service"),
        ("Halfway there, {name}", "Halfway there"),
        ("Still plenty of day left, {name}", "Still plenty of day left"),
        ("Good afternoon, {name}. Round two", "Good afternoon. Round two"),
        ("Afternoon. What are we on, {name}?", "Afternoon. What are we on?"),
        ("Good afternoon. Ready when you are, {name}",
         "Good afternoon. Ready when you are"),
        ("Let's keep it moving, {name}", "Let's keep it moving"),
        ("Second wind, {name}?", "Second wind?"),
        ("Good afternoon, {name}. The useful part of the day", "Good afternoon. The useful part of the day"),
        ("Straight to it, {name}?", "Straight to it?"),
        ("Good afternoon, {name}. Back on shift", "Good afternoon. Back on shift"),
        ("What needs doing, {name}?", "What needs doing?"),
        ("Good afternoon. Let's get somewhere, {name}",
         "Good afternoon. Let's get somewhere"),
    ),
    "evening": (
        ("Good evening, {name}", "Good evening"),
        ("Evening, {name}", "Evening"),
        ("Winding down, {name}?", "Winding down?"),
        ("Still going, {name}?", "Still going?"),
        ("One more thing, {name}?", "One more thing?"),
        ("Good evening, {name}. On the evening shift", "Good evening. On the evening shift"),
        ("Let's finish something, {name}", "Let's finish something"),
        ("Quiet enough to think, {name}", "Quiet enough to think"),
        ("Good evening, {name}. Lights on", "Good evening. Lights on"),
        ("The last stretch, {name}", "The last stretch"),
        ("What's left, {name}?", "What's left?"),
        ("Off the clock, {name}?", "Off the clock?"),
        ("Good evening. Something small, {name}?", "Good evening. Something small?"),
        ("Evening. Let's tie it off, {name}", "Evening. Let's tie it off"),
        ("The day's nearly done, {name}", "The day's nearly done"),
        ("Good evening. No rush, {name}", "Good evening. No rush"),
        ("Good evening, {name}. Best hours for this", "Good evening. The best hours for this"),
        ("Evening. What's on your mind, {name}?", "Evening. What's on your mind?"),
        ("Let's land it, {name}", "Let's land it"),
        ("Good evening. Plenty of time, {name}", "Good evening. Plenty of time"),
    ),
    "night": (
        ("Still up, {name}?", "Still up?"),
        ("A late one, {name}", "A late one"),
        ("Burning the midnight oil, {name}", "Burning the midnight oil"),
        ("The quiet hours, {name}", "The quiet hours"),
        ("Can't sleep, {name}?", "Can't sleep?"),
        ("Nobody else is awake, {name}", "Nobody else is awake"),
        ("Just us at this hour, {name}", "Just us at this hour"),
        ("Up in the small hours, {name}", "Up in the small hours"),
        ("The world's asleep, {name}", "The world's asleep"),
        ("One more push, {name}?", "One more push?"),
        ("Keeping night owl hours, {name}?", "Keeping night owl hours?"),
        ("Working late, {name}?", "Working late?"),
        # (Was "It's gone midnight" — correct British English for "past
        # midnight", and the user stopped to ask whether it was a bug. A
        # greeting that has to be parsed has already failed.)
        ("It's past midnight, {name}", "It's past midnight"),
        # A real fact, dryly delivered, that presumes NOTHING about why anyone
        # is awake — "still up" and "just woke up at 2am" are equally likely,
        # and a line that guesses wrong reads as a nag either way.
        ("Melatonin peaks around now, {name}. Do with that what you will",
         "Melatonin peaks around now. Do with that what you will"),
        ("Enjoy the quiet, {name}", "Enjoy the quiet"),
        ("Late, but let's be useful, {name}", "Late, but let's be useful"),
        ("Still here, {name}? Good", "Still here? Good"),
        ("It's quiet out there, {name}", "It's quiet out there"),
        ("No interruptions now, {name}", "No interruptions now"),
        ("Whatever hour this is, {name}, let's work", "Whatever hour this is, let's work"),
    ),
}

# ── day of week (Monday = 0, matching datetime.weekday()) ────────────────────
# The registers you asked for, mixed: plain, empathetic, and forward-looking.
#
# _DAY_LINES is genuinely valid AT ANY HOUR — that is now true rather than
# merely claimed. Lines that assume the day is just BEGINNING ("A fresh week",
# "Back to it") live in _DAY_OPENING_LINES below.
#
# THE BUG THIS SPLIT EXISTS FOR
# They used to share one bucket. At 04:00 on a Tuesday, greeting_weekday()
# correctly rolled back to Monday (you are still living Monday night) and then
# the pool happily offered "A fresh week" about a Monday that had been over for
# four hours. Roughly a third of every night greeting came from this bucket.
# Nothing was deleted to fix it — the two registers are simply filed apart, so
# each line still appears wherever it is actually true.
_DAY_LINES = {
    0: (
        ("Happy Monday, {name}", "Happy Monday"),
        ("Monday. Take it gently, {name}", "Monday. Take it gently"),
        ("Mondays are heavy, {name}. Let's go slow",
         "Mondays are heavy. Let's go slow"),
        ("Monday. One thing at a time, {name}", "Monday. One thing at a time"),
    ),
    1: (
        ("Happy Tuesday, {name}", "Happy Tuesday"),
        ("Happy Tuesday, {name}. Steady one", "Happy Tuesday. A steady one"),
        ("Tuesday's a good day for it, {name}", "Tuesday's a good day for it"),
        ("Monday's behind us, {name}", "Monday's behind us"),
        ("Tuesday. The week has started properly, {name}",
         "Tuesday. The week has started properly"),
        ("Quietly productive sort of day, {name}",
         "A quietly productive sort of day"),
        ("Tuesday. Nothing in the way, {name}", "Tuesday. Nothing in the way"),
    ),
    2: (
        ("Happy Wednesday, {name}", "Happy Wednesday"),
        ("Happy midweek, {name}", "Happy midweek"),
        ("Happy hump day, {name}", "Happy hump day"),
        ("Halfway through the week, {name}", "Halfway through the week"),
        ("Wednesday. Downhill from here, {name}", "Wednesday. Downhill from here"),
        ("The middle of it, {name}", "The middle of it"),
        ("Wednesday. Keep the pace, {name}", "Wednesday. Keep the pace"),
        ("Two days down, {name}", "Two days down"),
    ),
    3: (
        ("Happy Thursday, {name}", "Happy Thursday"),
        ("Happy Thursday, {name}. Nearly there", "Happy Thursday. Nearly there"),
        ("Almost Friday, {name}", "Almost Friday"),
        ("One more sleep to Friday, {name}", "One more sleep to Friday"),
        ("Thursday. Nearly there, {name}", "Thursday. Nearly there"),
        ("The week's last real push, {name}", "The week's last real push"),
        ("Thursday. Tomorrow's the good one, {name}",
         "Thursday. Tomorrow's the good one"),
        ("Nearly the weekend, {name}", "Nearly the weekend"),
    ),
    4: (
        ("Happy Friyay, {name}!", "Happy Friyay!"),
        ("Happy Friday, {name}!", "Happy Friday!"),
        ("Friyay, {name}!", "Friyay!"),
        ("Friday at last, {name}", "Friday at last"),
        ("Made it to Friday, {name}", "Made it to Friday"),
        ("It's Friyay, {name}", "It's Friyay"),
        ("Friday! Big day tomorrow, {name}?", "Friday! Big day tomorrow?"),
        ("Friday. You've earned the weekend, {name}",
         "Friday. You've earned the weekend"),
        ("Last one before the weekend, {name}", "The last one before the weekend"),
    ),
    5: (
        ("Happy Saturday, {name}", "Happy Saturday"),
        ("Weekend mode, {name}. Enjoy it", "Weekend mode. Enjoy it"),
        ("Happy Saturday, {name}! Enjoy it", "Happy Saturday! Enjoy it"),
        ("No alarms today, {name}", "No alarms today"),
        ("Saturday. Nothing owed to anyone, {name}",
         "Saturday. Nothing owed to anyone"),
        ("Happy weekend, {name}!", "Happy weekend!"),
        ("Happy Saturday, {name}. The good one", "Happy Saturday. The good one"),
        ("Saturday. Take your time, {name}", "Saturday. Take your time"),
    ),
    6: (
        ("Happy Sunday, {name}", "Happy Sunday"),
        ("Happy Sunday, {name}. Rest a little", "Happy Sunday. Rest a little"),
        ("A slow Sunday, {name}", "A slow Sunday"),
        ("Sunday reset, {name}", "Sunday reset"),
        ("Last day of the weekend, {name}", "The last day of the weekend"),
        ("Sunday. Tomorrow can wait, {name}", "Sunday. Tomorrow can wait"),
        ("Big week ahead, {name}", "A big week ahead"),
        ("Sunday. No pressure, {name}", "Sunday. No pressure"),
        ("Easing into the week, {name}", "Easing into the week"),
    ),
}

# Lines that assume the day is STARTING. Offered in the morning and afternoon
# only — "Back to it" is as wrong at 22:30 as it is at 04:00, so this is not
# merely a small-hours patch.
#
# Wednesday and Thursday are deliberately empty: every one of their lines
# ("Halfway through the week", "One more sleep to Friday") is a statement about
# where the week stands, which stays true at any hour.
_DAY_OPENING_LINES = {
    0: (
        ("Monday again, {name}", "Monday again"),
        ("A fresh week, {name}", "A fresh week"),
        ("Let's ease into it, {name}", "Let's ease into it"),
        ("New week, new page, {name}", "New week, new page"),
        ("Whole week ahead, {name}", "A whole week ahead"),
        ("Back to it, {name}", "Back to it"),
    ),
    1: (
        ("Into the week proper, {name}", "Into the week proper"),
    ),
    2: (),
    3: (),
    4: (
        ("Happy Friday! Let's finish well, {name}",
         "Happy Friday! Let's finish well"),
        ("Friday. Whatever's left, let's clear it, {name}",
         "Friday. Whatever's left, let's clear it"),
    ),
    5: (
        ("Weekend project, {name}?", "Weekend project?"),
        ("Saturday. Build something fun, {name}", "Saturday. Build something fun"),
    ),
    6: (
        ("Sunday. Tidy something up, {name}", "Sunday. Tidy something up"),
    ),
}

#: Buckets in which a day is still plausibly "beginning".
_OPENING_BUCKETS = ("morning", "afternoon")

# ── every day x every part of the day ────────────────────────────────────────
# All 28 cells are filled. A half-populated table meant Tuesday afternoon fell
# back to generic lines while Friday evening had five of its own, and the
# unevenness showed.
_DAY_TIME_LINES = {
    # ── Monday ───────────────────────────────────────────────────────────────
    (0, "morning"): (
        ("Good Monday morning, {name}", "Good Monday morning"),
        ("Let's start the week, {name}", "Let's start the week"),
        ("Monday morning. Gently, {name}", "Monday morning. Gently"),
        ("Monday morning. Coffee, then code, {name}?",
         "Monday morning. Coffee, then code?"),
        ("Monday morning. Nobody's ready, {name}", "Monday morning. Nobody's ready"),
    ),
    (0, "afternoon"): (
        ("Happy Monday afternoon, {name}", "Happy Monday afternoon"),
        ("Monday's found its feet, {name}", "Monday's found its feet"),
        ("Monday afternoon. The worst of it is past, {name}",
         "Monday afternoon. The worst of it is past"),
    ),
    (0, "evening"): (
        ("Monday's nearly done, {name}", "Monday's nearly done"),
        ("You survived Monday, {name}", "You survived Monday"),
        ("Monday evening. Four to go, {name}", "Monday evening. Four to go"),
    ),
    (0, "night"): (
        ("Monday's over, {name}", "Monday's over"),
        ("Late on a Monday, {name}. Long day?", "Late on a Monday. Long day?"),
        ("Monday night. You must be tired, {name}",
         "Monday night. You must be tired"),
    ),
    # ── Tuesday ──────────────────────────────────────────────────────────────
    (1, "morning"): (
        ("Good Tuesday morning, {name}", "Good Tuesday morning"),
        ("Tuesday morning. Easier than yesterday, {name}",
         "Tuesday morning. Easier than yesterday"),
        ("Tuesday morning. Good working day, {name}",
         "Tuesday morning. A good working day"),
    ),
    (1, "afternoon"): (
        ("Happy Tuesday afternoon, {name}", "Happy Tuesday afternoon"),
        ("Tuesday afternoon. Quietly getting on with it, {name}",
         "Tuesday afternoon. Quietly getting on with it"),
        ("Tuesday. Head down, {name}", "Tuesday. Head down"),
    ),
    (1, "evening"): (
        ("Good Tuesday evening, {name}", "Good Tuesday evening"),
        ("Tuesday evening. Nothing dramatic, {name}",
         "Tuesday evening. Nothing dramatic"),
    ),
    (1, "night"): (
        ("Happy Tuesday night, {name}", "Happy Tuesday night"),
        ("Late on a Tuesday, {name}", "Late on a Tuesday"),
    ),
    # ── Wednesday ────────────────────────────────────────────────────────────
    (2, "morning"): (
        ("Good midweek morning, {name}", "Good midweek morning"),
        ("Wednesday morning. Good stretch ahead, {name}",
         "Wednesday morning. A good stretch ahead"),
        ("Wednesday morning. Over the hump by lunch, {name}",
         "Wednesday morning. Over the hump by lunch"),
    ),
    (2, "afternoon"): (
        ("Happy Wednesday afternoon, {name}", "Happy Wednesday afternoon"),
        ("Officially downhill now, {name}", "Officially downhill now"),
        ("Wednesday afternoon. Halfway done, {name}",
         "Wednesday afternoon. Halfway done"),
    ),
    (2, "evening"): (
        ("Good midweek evening, {name}", "Good midweek evening"),
        ("Wednesday evening. Weekend's in reach, {name}",
         "Wednesday evening. The weekend's in reach"),
    ),
    (2, "night"): (
        ("Happy Wednesday night, {name}", "Happy Wednesday night"),
        ("Midweek and still up, {name}?", "Midweek and still up?"),
    ),
    # ── Thursday ─────────────────────────────────────────────────────────────
    (3, "morning"): (
        ("Good Thursday morning, {name}", "Good Thursday morning"),
        ("Thursday morning. One more after this, {name}",
         "Thursday morning. One more after this"),
        ("Thursday. Nearly the good day, {name}", "Thursday. Nearly the good day"),
    ),
    (3, "afternoon"): (
        ("Happy Thursday afternoon, {name}", "Happy Thursday afternoon"),
        ("Thursday afternoon. Tomorrow's Friday, {name}",
         "Thursday afternoon. Tomorrow's Friday"),
    ),
    (3, "evening"): (
        ("Thursday evening. Almost Friday, {name}",
         "Thursday evening. Almost Friday"),
        ("One more day, {name}", "One more day"),
        ("Thursday evening. Big day tomorrow, {name}",
         "Thursday evening. Big day tomorrow"),
    ),
    (3, "night"): (
        ("Happy Thursday night, {name}", "Happy Thursday night"),
        ("Late on a Thursday, {name}. Friday will hurt",
         "Late on a Thursday. Friday will hurt"),
    ),
    # ── Friday ───────────────────────────────────────────────────────────────
    (4, "morning"): (
        ("Friyay morning, {name}!", "Friyay morning!"),
        ("Friday morning. Last push, {name}", "Friday morning. Last push"),
        ("Friday morning. Let's clear the decks, {name}",
         "Friday morning. Let's clear the decks"),
        ("Friday morning. Big day tomorrow, {name}?",
         "Friday morning. Big day tomorrow?"),
    ),
    (4, "afternoon"): (
        ("Friday afternoon. Nearly there, {name}", "Friday afternoon. Nearly there"),
        ("Happy Friyay afternoon, {name}!", "Happy Friyay afternoon!"),
        ("Friday afternoon. Weekend's in sight, {name}",
         "Friday afternoon. The weekend's in sight"),
        ("Friday afternoon. Last stretch of the week, {name}",
         "Friday afternoon. The last stretch of the week"),
    ),
    (4, "evening"): (
        ("Happy Friyay evening, {name}!", "Happy Friyay evening!"),
        ("The weekend starts now, {name}", "The weekend starts now"),
        ("Friday evening. Happy weekend, {name}!",
         "Friday evening. Happy weekend!"),
        ("Friday evening. You've earned this, {name}",
         "Friday evening. You've earned this"),
    ),
    (4, "night"): (
        ("Friday night and still working, {name}?",
         "Friday night and still working?"),
        ("The weekend's here, {name}", "The weekend's here"),
        ("Friday night. Whole weekend ahead, {name}",
         "Friday night. A whole weekend ahead"),
    ),
    # ── Saturday ─────────────────────────────────────────────────────────────
    (5, "morning"): (
        ("Good Saturday morning, {name}", "Good Saturday morning"),
        ("No rush today, {name}", "No rush today"),
        ("Saturday morning. Best time to build, {name}",
         "Saturday morning. The best time to build"),
        ("Saturday morning. Nothing scheduled, {name}",
         "Saturday morning. Nothing scheduled"),
    ),
    (5, "afternoon"): (
        ("Happy Saturday afternoon, {name}", "Happy Saturday afternoon"),
        ("Saturday afternoon. Proper project time, {name}",
         "Saturday afternoon. Proper project time"),
        ("Weekend afternoon, {name}. No deadlines",
         "A weekend afternoon. No deadlines"),
    ),
    (5, "evening"): (
        ("Good Saturday evening, {name}", "Good Saturday evening"),
        ("Saturday evening. One more day off, {name}",
         "Saturday evening. One more day off"),
    ),
    (5, "night"): (
        ("Happy Saturday night, {name}!", "Happy Saturday night!"),
        ("Saturday night and we're coding, {name}",
         "Saturday night and we're coding"),
        ("Saturday night. No alarm tomorrow, {name}",
         "Saturday night. No alarm tomorrow"),
    ),
    # ── Sunday ───────────────────────────────────────────────────────────────
    (6, "morning"): (
        ("Good Sunday morning, {name}", "Good Sunday morning"),
        ("Slow Sunday morning, {name}", "A slow Sunday morning"),
        ("Sunday morning. Take it easy, {name}", "Sunday morning. Take it easy"),
    ),
    (6, "afternoon"): (
        ("Happy Sunday afternoon, {name}", "Happy Sunday afternoon"),
        ("Sunday afternoon. Best part of the weekend, {name}",
         "Sunday afternoon. The best part of the weekend"),
        ("Sunday afternoon. Tomorrow can wait, {name}",
         "Sunday afternoon. Tomorrow can wait"),
    ),
    (6, "evening"): (
        ("Good Sunday evening, {name}", "Good Sunday evening"),
        ("Sunday evening. Big week tomorrow, {name}",
         "Sunday evening. A big week tomorrow"),
        ("Sunday evening. Set yourself up for Monday, {name}",
         "Sunday evening. Set yourself up for Monday"),
    ),
    (6, "night"): (
        ("Happy Sunday night, {name}!", "Happy Sunday night!"),
        ("Ready for Monday, {name}?", "Ready for Monday?"),
        ("Sunday night. Get some sleep after this, {name}",
         "Sunday night. Get some sleep after this"),
    ),
}

# ── usage signals ────────────────────────────────────────────────────────────
# Names are the keys returned by collect_signals(). These are weighted UP:
# noticing something is the entire value of the feature.
_SIGNAL_LINES = {
    "first_ever": (
        ("Hello, {name}. First time here", "Hello. First time here"),
        ("Welcome, {name}. Let's get acquainted", "Welcome. Let's get acquainted"),
        ("Hello, {name}. Ask me for anything", "Hello. Ask me for anything"),
        ("First session, {name}. Nice to meet you",
         "First session. Nice to meet you"),
        ("Welcome aboard, {name}", "Welcome aboard"),
        ("New here, {name}? Let's start simple", "New here? Let's start simple"),
    ),
    "first_today": (
        ("First one today, {name}", "First one today"),
        ("Starting the day with this, {name}?", "Starting the day with this?"),
        ("Here we go, {name}", "Here we go"),
        ("First session of the day, {name}", "First session of the day"),
    ),
    "second_today": (
        ("Round two today, {name}", "Round two today"),
        ("Back for more, {name}", "Back for more"),
        ("Second time today, {name}", "Second time today"),
    ),
    "many_today": (
        ("We've been at this a while, {name}", "We've been at this a while"),
        ("Another one, {name}? Good", "Another one? Good"),
        ("You're on a roll today, {name}", "You're on a roll today"),
        ("You've been busy today, {name}", "You've been busy today"),
        ("Busy day, {name}", "Busy day"),
    ),
    "back_soon": (
        ("Back already, {name}?", "Back already?"),
        ("That was quick, {name}", "That was quick"),
        ("Miss me, {name}?", "Miss me?"),
        ("Forgot something, {name}?", "Forgot something?"),
        # NOT "Good afternoon, {name}. Round two" — `back_soon` fires purely on
        # a <30min gap and is deliberately NOT hour-gated, so that line
        # announced the afternoon at breakfast and at 2am. Signal lines are
        # weighted SIGNAL_WEIGHT times, so it was not a rare glitch either.
        # Every other line in this pool is time-neutral; this one now is too.
        ("Round two, {name}", "Round two"),
    ),
    "back_after_hours": (
        ("Welcome back, {name}", "Welcome back"),
        ("There you are, {name}", "There you are"),
        ("Back again, {name}", "Back again"),
        ("Good to see you, {name}", "Good to see you"),
    ),
    "back_after_a_day": (
        ("Back after a day, {name}", "Back after a day"),
        ("Yesterday feels far off, {name}", "Yesterday feels far off"),
        ("Picking up from yesterday, {name}", "Picking up from yesterday"),
    ),
    "back_after_days": (
        ("Been a few days, {name}", "It's been a few days"),
        ("Welcome back, {name}. It's been a bit",
         "Welcome back. It's been a bit"),
        ("There you are, {name}. Been a while", "There you are. It's been a while"),
        ("Good to have you back, {name}", "Good to have you back"),
    ),
    "back_after_ages": (
        ("Long time, {name}", "Long time"),
        ("Welcome back, {name}. It's been weeks",
         "Welcome back. It's been weeks"),
        ("Look who it is, {name}", "Look who it is"),
        ("Been a while, {name}. Where were we?",
         "It's been a while. Where were we?"),
    ),
    "late_night_streak": (
        ("Another late one, {name}", "Another late one"),
        ("That's a few nights running, {name}", "That's a few nights running"),
        ("You keep strange hours, {name}", "You keep strange hours"),
        ("Late again, {name}. Sleep is also a feature",
         "Late again. Sleep is also a feature"),
        ("The night shift continues, {name}", "The night shift continues"),
    ),
    "early_bird": (
        ("You're up early, {name}", "You're up early"),
        ("Before the world wakes, {name}", "Before the world wakes"),
        ("Early one, {name}", "An early one"),
        ("Good morning, {name}. On the sunrise shift", "Good morning. On the sunrise shift"),
    ),
    "dead_of_night": (
        ("It's the middle of the night, {name}", "It's the middle of the night"),
        ("Nothing good happens at this hour, {name}. Let's prove that wrong",
         "Nothing good happens at this hour. Let's prove that wrong"),
        ("It's deep into the night, {name}", "It's deep into the night"),
        ("Everyone sensible is asleep, {name}", "Everyone sensible is asleep"),
    ),
    "weekend": (
        ("Happy weekend, {name}. Getting some work in?", "Happy weekend. Getting some work in?"),
        ("Working through the weekend, {name}?", "Working through the weekend?"),
        ("Weekend build, {name}?", "Weekend build?"),
    ),
    "month_first": (
        ("New month, {name}", "A new month"),
        ("First of the month, {name}", "First of the month"),
        ("Fresh month, fresh start, {name}", "Fresh month, fresh start"),
    ),
    "new_year": (
        ("Happy new year, {name}!", "Happy new year!"),
        ("New year, {name}. Let's build something",
         "A new year. Let's build something"),
    ),
}

# ── elevated privileges ──────────────────────────────────────────────────────
# Mixed registers ON PURPOSE: some warm, some clinical, some dry, a couple with
# a friendly close. Every one of them names the responsibility — that is the
# job of this line, and it is why they all point at discretion in some form.
_ADMIN_LINES = (
    # warm-professional
    ("Running with administrator privileges, {name}. Prompt responsibly",
     "Running with administrator privileges. Prompt responsibly"),
    ("Full system privileges are active, {name}. Let's be deliberate",
     "Full system privileges are active. Let's be deliberate"),
    ("Administrator session, {name}. Nothing on this machine is out of reach, so aim carefully",
     "Administrator session. Nothing on this machine is out of reach, so aim carefully"),
    ("You're running elevated, {name}. Worth double-checking anything destructive",
     "You're running elevated. Worth double-checking anything destructive"),
    # friendly close
    ("Administrator privileges are active, {name}. Prompt responsibly!",
     "Administrator privileges are active. Prompt responsibly!"),
    ("Elevated session, {name}. Let's use it well!",
     "Elevated session. Let's use it well!"),
    ("Admin rights are on, {name}. Have fun, be careful!",
     "Admin rights are on. Have fun, be careful!"),
    # clinical
    ("Elevated privileges active. Operate with discretion",
     "Elevated privileges active. Operate with discretion"),
    ("Administrator mode. Full system access is in effect, so operate with care",
     "Administrator mode. Full system access is in effect, so operate with care"),
    ("Privilege level: administrator. Proceed with care",
     "Privilege level: administrator. Proceed with care"),
    ("Elevated session in effect. Discretion advised",
     "Elevated session in effect. Discretion advised"),
    # dry
    ("Booted as administrator, {name}. Measure twice",
     "Booted as administrator. Measure twice"),
    ("Admin mode, {name}. I can reach anything on this machine, so aim me carefully",
     "Admin mode. I can reach anything on this machine, so aim me carefully"),
    ("Elevated, {name}. No safety net down here, so tread carefully",
     "Elevated. No safety net down here, so tread carefully"),
    ("Administrator privileges, {name}. Think before you point me at something",
     "Administrator privileges. Think before you point me at something"),
)

_ROOT_LINES = (
    ("Running as root, {name}. Prompt responsibly",
     "Running as root. Prompt responsibly"),
    ("Root privileges are active, {name}. Let's be deliberate",
     "Root privileges are active. Let's be deliberate"),
    ("Root session, {name}. Nothing on this system is out of reach, so aim carefully",
     "Root session. Nothing on this system is out of reach, so aim carefully"),
    ("You're running as root, {name}. Worth double-checking anything destructive",
     "You're running as root. Worth double-checking anything destructive"),
    ("Root access is on, {name}. Have fun, be careful!",
     "Root access is on. Have fun, be careful!"),
    ("Root privileges active. Operate with discretion",
     "Root privileges active. Operate with discretion"),
    ("Privilege level: root. Proceed with care",
     "Privilege level: root. Proceed with care"),
    ("Root session in effect. Discretion advised",
     "Root session in effect. Discretion advised"),
    ("Booted as root, {name}. Measure twice",
     "Booted as root. Measure twice"),
    ("Root, {name}. No safety net down here, so tread carefully",
     "Root. No safety net down here, so tread carefully"),
    ("Running as root, {name}. Think before you point me at something",
     "Running as root. Think before you point me at something"),
    ("Root access, {name}. Nothing is out of reach, so operate with discretion",
     "Root access. Nothing is out of reach, so operate with discretion"),
)

# Terminal punctuation a line may already carry. Anything else gets a full stop
# added, so a new phrasing can be written without remembering to punctuate it —
# an unpunctuated greeting reads as though it got cut off.
_TERMINALS = ".!?…:"


def _punctuate(line: str) -> str:
    line = (line or "").rstrip()
    if not line or line[-1] in _TERMINALS:
        return line
    return line + "."


def is_placeholder_name(user_name: str) -> bool:
    """True when the "name" is a default the user never actually set."""
    return (user_name or "").strip().upper() in PLACEHOLDER_NAMES


def time_bucket(now: datetime | None = None) -> str:
    """Which part of the day it is: morning / afternoon / evening / night."""
    hour = (now or datetime.now()).hour
    for name, start, end in _BUCKETS:
        if start <= hour < end:
            return name
    return "night"


def greeting_weekday(now: datetime | None = None) -> int:
    """Which weekday this greeting BELONGS to — not always today's.

    THE BUG THIS EXISTS FOR
    At 02:00 on a Tuesday the app said "Happy Tuesday night!". The calendar
    agrees, and nobody else does: at 2am you are still living MONDAY night.
    The night is the one span that outlives the date change, so anchoring a
    night phrasing to `now.weekday()` names the wrong day for five hours every
    single day.

    Same defect the task scheduler had with a 22:00-01:00 window, and the same
    answer: the after-midnight half belongs to the day it STARTED on.
    """
    now = now or datetime.now()
    if now.hour < SMALL_HOURS_END:
        return (now - timedelta(days=1)).weekday()
    return now.weekday()


# ── signals ──────────────────────────────────────────────────────────────────

_SESSION_ID_RE = re.compile(r"^(\d{2})_(\d{2})_(\d{4})_(\d{2})_(\d{2})_(\d{2})")


def parse_session_time(session_id: str):
    """Session ids are `%m_%d_%Y_%H_%M_%S_%f`-shaped, which makes them the most
    reliable clock in the app — the human-readable date field is localised
    display text. Returns None for anything that doesn't match."""
    m = _SESSION_ID_RE.match(str(session_id or ""))
    if not m:
        return None
    try:
        mo, d, y, h, mi, s = (int(x) for x in m.groups())
        return datetime(y, mo, d, h, mi, s)
    except ValueError:
        return None


def collect_signals(session_times, now: datetime | None = None) -> set:
    """Which usage signals apply right now, from prior session start times.

    `session_times` is an iterable of datetimes (or session-id strings, which
    are parsed). Everything here is derived locally from timestamps the app
    already has — no content is read, and nothing leaves the machine.
    """
    now = now or datetime.now()
    times = []
    for item in (session_times or []):
        t = item if isinstance(item, datetime) else parse_session_time(item)
        if t is not None and t <= now:
            times.append(t)
    times.sort()

    signals = set()
    hour = now.hour

    if not times:
        signals.add("first_ever")
    else:
        gap_h = (now - times[-1]).total_seconds() / 3600.0
        if gap_h < 0.5:
            signals.add("back_soon")
        elif gap_h < 14:
            signals.add("back_after_hours")
        elif gap_h < 48:
            signals.add("back_after_a_day")
        elif gap_h < 24 * 14:
            signals.add("back_after_days")
        else:
            signals.add("back_after_ages")

        today = [t for t in times if t.date() == now.date()]
        if not today:
            signals.add("first_today")
        elif len(today) == 1:
            signals.add("second_today")
        elif len(today) >= 3:
            signals.add("many_today")

        # Three or more of the last five sessions started in the small hours,
        # AND it is night right now.
        #
        # The hour gate is not optional. Every line in this pool is present
        # tense — "Another late one", "Late again. Sleep is also a feature" —
        # so firing it from history alone announced that the user was up late
        # at 10:36 in the MORNING, after a night of leaving the app running.
        # And because signal lines are weighted SIGNAL_WEIGHT times, it did not
        # merely appear, it dominated. The streak is a true observation; the
        # phrasing is only true at night. Same defect the day lines had, one
        # pool over: `dead_of_night` and `early_bird` below were already gated
        # this way — this one was the outlier.
        recent = times[-5:]
        if (len(recent) >= 3
                and sum(1 for t in recent if t.hour < 5) >= 3
                and time_bucket(now) == "night"):
            signals.add("late_night_streak")

        if all(t.month != now.month or t.year != now.year for t in times[-40:]):
            signals.add("month_first")

    if 5 <= hour < 7:
        signals.add("early_bird")
    if hour < SMALL_HOURS_END:
        # From midnight, not from 01:00 — 00:30 is the dead of night by any
        # reading, and it used to fall through to ordinary night lines.
        signals.add("dead_of_night")
    if greeting_weekday(now) >= 5:
        # Same rule as the day lines: 2am on Monday is still Sunday night, and
        # "Happy weekend!" is right up until the user actually sleeps.
        signals.add("weekend")
    if now.month == 1 and now.day <= 2:
        signals.add("new_year")
    return signals


# ── pools ────────────────────────────────────────────────────────────────────

def pool_for(now: datetime | None = None, signals=None) -> list:
    """Every (named, anon) phrasing valid right now.

    Signal lines are repeated SIGNAL_WEIGHT times so a plain uniform choice
    favours them heavily without needing a weighted-sampling code path.
    """
    now = now or datetime.now()
    signals = set(signals or ())
    # greeting_weekday, NOT now.weekday(): at 2am the night still belongs to
    # yesterday, and every day-anchored line here is read as "tonight".
    bucket, weekday = time_bucket(now), greeting_weekday(now)

    # A brand-new install gets ONLY welcome lines. The ordinary pools assume a
    # shared history — "Where were we?" is a strange thing to say to somebody
    # who has never opened the app.
    if "first_ever" in signals:
        return list(_SIGNAL_LINES["first_ever"])

    pool = list(_TIME_LINES[bucket])
    pool += list(_DAY_LINES.get(weekday, ()))
    # Day-opening phrasings only while the day could still be starting. Evening
    # and night get the any-hour day lines plus their own combos instead.
    if bucket in _OPENING_BUCKETS:
        pool += list(_DAY_OPENING_LINES.get(weekday, ()))
    pool += list(_DAY_TIME_LINES.get((weekday, bucket), ()))
    for sig in sorted(signals):
        pool += list(_SIGNAL_LINES.get(sig, ())) * SIGNAL_WEIGHT
    return pool


def all_phrasings():
    """Every pair in every pool — for the test that walks them."""
    for pairs in _TIME_LINES.values():
        yield from pairs
    for pairs in _DAY_OPENING_LINES.values():
        yield from pairs
    for pairs in _DAY_LINES.values():
        yield from pairs
    for pairs in _DAY_TIME_LINES.values():
        yield from pairs
    for pairs in _SIGNAL_LINES.values():
        yield from pairs
    yield from _ADMIN_LINES
    yield from _ROOT_LINES


def _render(pair, user_name: str) -> str:
    """Pick the named or name-less half of a pair and punctuate it."""
    named, anon = pair
    name = (user_name or "").strip()
    if not name or is_placeholder_name(name):
        return _punctuate(anon)
    return _punctuate(named.format(name=name))


def greeting(user_name: str = "", now: datetime | None = None,
             rng: random.Random | None = None, signals=None) -> str:
    """One greeting line, e.g. "Happy Friyay, Thirdy!" or "Back already?".

    A blank or placeholder name yields the name-less twin of the SAME phrasing,
    so the line never reads as though it is addressing somebody called USER.

    `rng` is injectable so a test can pin the choice; production calls leave it
    None and get module-level randomness.
    """
    pool = pool_for(now or datetime.now(), signals)
    return _render((rng or random).choice(pool), user_name)


def admin_note(user_name: str = "", root: bool = False,
               rng: random.Random | None = None) -> str:
    """The elevated-privileges sub-line shown under the greeting.

    It used to be a separate grey system note stacked below the banner, which
    made the greeting read as off-centre and doubled the visual noise of an
    empty session. It is a varied part of the opener now — but every phrasing
    still names the responsibility, because that is what the line is FOR.
    """
    pool = _ROOT_LINES if root else _ADMIN_LINES
    return _render((rng or random).choice(pool), user_name)
