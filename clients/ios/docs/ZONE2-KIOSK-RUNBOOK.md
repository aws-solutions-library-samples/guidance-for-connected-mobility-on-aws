# Zone 2 Kiosk — Operator Runbook

Audience: booth staff who will operate the Zone 2 "The Order" kiosk stations at
re:Invent 2026. You do not need to have read any design documents. Follow these
steps in order before the show floor opens.

---

## 1. Station setup

### Required hardware per station
- iPhone (any size supported by iOS 18) in a portrait mount
- Optional: portrait-mounted display connected via AirPlay or USB-C adapter
  (the phone mirrors to it automatically — no setup required)

### Before you start
1. Confirm the iPhone is charged to at least 80%.
2. Confirm Wi-Fi is connected and the Meridian staging environment is reachable
   (open a browser, visit the API health URL if provided).
3. Open the **MeridianMotorsCompanion** app. You should see the Meridian "Identify yourself"
   chooser screen.

---

## 2. Engaging Guided Access (lock the station for visitors)

Guided Access prevents visitors from leaving the app or changing phone settings.
**Enable it before the show floor opens.**

1. On the iPhone, go to **Settings → Accessibility → Guided Access** and confirm
   it is toggled ON.
2. Open **MeridianMotorsCompanion** so it is the foreground app.
3. Triple-click the **side button** (Face ID iPhones) or **home button**
   (Touch ID iPhones).
4. Tap **Start** in the Guided Access overlay.
5. The screen dimming indicator at the top disappears — the station is now locked.

> **If you see an amber badge in the app corner**: that means Guided Access is OFF
> while the app is in kiosk mode. Visitors can still use the app normally, but
> they may be able to exit it. Re-engage Guided Access using the steps above.

### To disengage Guided Access (between shows or for maintenance)
- Triple-click the side/home button and authenticate with the passcode you set
  when enabling Guided Access.

---

## 3. Mirror to the portrait wall panel

The wall display mirrors whatever is on the phone screen — no additional setup
is required from software. The OS handles mirroring automatically when the
display is connected.

1. Connect the display via **AirPlay** (select the display from Control Centre →
   Screen Mirroring) or via a **USB-C/HDMI cable**.
2. Confirm the wall shows the same screen as the phone.
3. If the wall shows a black screen or a home screen: disconnect and reconnect.
   If the issue persists, toggle AirPlay off and back on.

---

## 4. Running a visitor journey

A typical journey takes 2–3 minutes.

1. **Beat 0 — Identify**: The chooser screen shows **Scan badge** (currently
   disabled — badge scanning is not yet live) and **Enter name**. Most visitors
   tap **Enter name** or press **Continue without a name** to skip.
2. The journey proceeds: discover model → upgrade offer → configure color and
   interior → confirm order.
3. At the end, the visitor receives a confirmation screen. An NFC key card may
   be handed over by staff.
4. After the visitor leaves, the app **resets automatically** after 60 seconds
   of inactivity and returns to the chooser screen.

---

## 5. Manual reset (operator gesture)

If a journey gets stuck or you need to clear the screen immediately:

1. Press and **hold** anywhere in the **top-right corner** of the screen for
   **2 seconds**.
2. The app returns to the chooser screen. No confirmation prompt appears.

This gesture is invisible to visitors — it is intentionally subtle. Practice it
once before the show floor opens so you know where the hit area is.

---

## 6. What to do when a station appears wedged

A "wedged" station shows a stuck loading spinner, a blank screen, or refuses to
advance past a step.

1. **Try the manual reset first** (see §5). This clears all in-progress state.
2. If the app does not respond to the reset gesture, force-quit and reopen:
   - Swipe up from the bottom (or double-click Home), swipe away MeridianMotorsCompanion.
   - Re-open MeridianMotorsCompanion.
   - Re-engage Guided Access (§2) before admitting visitors.
3. If the app crashes repeatedly: confirm Wi-Fi is working and the API endpoint
   is reachable. A network outage causes the order step to fail; the app
   degrades gracefully (the catalog and previous steps still work offline).

---

## 7. End of show floor — shutdown

1. Disengage Guided Access (§2, disengagement steps).
2. Plug the phone in to charge overnight.
3. Leave the app open — no need to force-quit.

---

## 8. Common issues

| Symptom | Likely cause | Fix |
|---|---|---|
| Amber badge in corner | Guided Access is OFF | Triple-click side/home button, re-engage |
| Wall shows home screen | AirPlay not connected | Control Centre → Screen Mirroring → select display |
| Journey resets mid-visitor | Idle timer fired (visitor paused >60s) | Shorten briefing; use manual-reset gesture to recover |
| "Scan badge" doesn't work | Badge scan not yet implemented | Direct visitors to "Enter name" instead |
| Order fails at confirmation | API endpoint unreachable | Check Wi-Fi; app logs the HTTP status — report to engineering |
| App shows generic error banner | Backend unavailable | Journey degrades to demo-safe mode; catalog browsing still works |

---

## 9. Quick reference

| Action | How |
|---|---|
| Engage Guided Access | Triple-click side/home → Start |
| Disengage Guided Access | Triple-click side/home → passcode |
| Manual reset (operator) | 2-second long-press, top-right corner |
| Auto-reset (visitor idle) | Automatic after 60s of inactivity |
| Mirror to wall display | AirPlay or USB-C — automatic |
