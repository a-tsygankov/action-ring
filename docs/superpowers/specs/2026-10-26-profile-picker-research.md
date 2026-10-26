# Entering the default profile when an app opens: research notes

Date: 2026-10-26. Question: when the dock launches an Apple TV app that
shows a "Who's watching" picker (YouTube, Max, Netflix, Disney+, Hulu,
Paramount+, Prime Video), can the launch name the profile so the picker
is skipped? Desk research only; the warm/cold launch survey on the TV is
postponed.

## Answer: no app offers a launch parameter for the profile

Nothing found for any of the seven apps, and two vendors say there is no
way at all:

| App | What is known |
|---|---|
| Netflix | No profile parameter documented. The old `nflx://` scheme is reported deprecated; the only profile-related URL is the web page `netflix.com/profiles/manage`, which *opens* the picker. Title links open the app but land on the home or title page (pyatv #2927). |
| Max | Help centre: on TV devices the app opens straight into the last profile **only if it was a Kids profile**; after an adult profile it shows "Who's Watching". No URL option. |
| Prime Video | Amazon forum staff (2020): a bypass of "Who's Watching" is **not available**; a later staff reply repeats it. Third-party workarounds are screen automation (a Chrome extension; FruitDeepLinks on Fire TV pressing DPAD_CENTER when it reads "who's watching" on screen, which fails on multi-profile accounts). |
| Disney+ | Web links open content; nothing about profiles. |
| Hulu | `hulu://watch/<id>` and `hulu://series/<id>` reported working on Apple TV; nothing about profiles, one report that direct links stopped working. |
| Paramount+ | Its tvOS app accepts neither `paramountplus://` nor `cbsaa://` any more and share links no longer launch it (community reports); nothing about profiles. |
| YouTube | The Apple TV app shows an account picker **on every launch** and offers no setting to turn it off (Android Authority). YouTube TV (a different app) has a setting that defaults to the last profile. On Android TV, YouTube remembers the last signed-in profile; whether the tvOS app does was not found. |

tvOS itself has a system-level "Choose Profile on Wake" setting
(Profiles and Accounts) for the *Apple TV user* picker on wake; it does
not touch in-app pickers.

## What that leaves

1. **Press OK after the launch.** The only move the dock has: a Siri
   Remote click selects the highlighted profile, and the apps highlight
   the last-used one. The risk is the blind press landing on the home
   screen instead (Netflix: it would start the billboard title; others:
   open the focused tile). The dock cannot see the screen, so the guard
   can only be timing plus "app in the foreground and nothing playing".
   Whether that is safe depends on the warm/cold survey (postponed): on a
   warm launch tvOS resumes the app where it was and no picker appears.
2. **Per-app tricks**: Max opens straight in if the last profile used was
   a Kids profile; YouTube TV (not YouTube) can default to the last
   profile; an app with a single profile shows no picker (Netflix, Max,
   Disney+ and Hulu only ask when there are several). The user's call per
   app.
3. **No deep link helps.** Content links open pages (Apple TV app, Disney+,
   Hulu, YouTube, Prime) but the picker, where an app shows one, comes
   before the content and the link is lost (FruitDeepLinks reports the
   same on Fire TV).

Sources: Home Assistant community "AppleTV Integration Deep Link URLs -
Which Are Working?" (and page 2); pyatv docs "Apps" and issue #2927;
Apple Developer Forums 19373 and 747274; Max help centre "profiles";
Amazon forum questions 0D54P00007kzEOhSAM and 0D54P00007pVCZUSA4;
Channels community "FruitDeepLinks" #868; Android Authority "youtube
apple tv launch screen"; Cord Cutters News on the YouTube TV update;
9to5Google on the Android TV account switcher; MacDailyNews on "Choose
Profile on Wake" (tvOS 26); GitHub turnepf/Show-Picker-Club PR 615.
