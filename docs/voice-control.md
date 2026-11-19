# Voice control: wake word and push to talk

rbvoice can listen in two ways, and both can be active together:

1. **Wake word.** rbvoice hears everything on the audio input and answers only an utterance that
   opens with a wake word ("computer, dimmi la quota").
2. **Trigger (push to talk).** A button, a curl command or any other client asks rbvoice to listen
   now. rbvoice then hears the first utterance spoken within the trigger window and answers it
   without a wake word ("dimmi la quota").

When the wake word is **off**, rbvoice ignores all audio until a trigger arrives. Use this when the
input carries a lot of radio traffic or passenger talk.

Both settings live on the RB-01 addons bridge (`/bridge/float`), so the RB-01 voice page, an addon
or a shell can read and change them.

## Bridge keys

| Key | Values | Meaning |
|---|---|---|
| `voice_wakeword` | `1` / `0` | `1`: answer after a wake word. `0`: listen only after a trigger. |
| `voice_trigger` | `1` / `0` | Write `1` to start listening. rbvoice writes `0` back when it stops listening, so the value also shows whether rbvoice is listening now. |

Bridge values are floats. rbvoice reads any value of 0.5 or more as on.

## How it works

- **Default.** The case file sets the default with `"wake_word_enabled": true` or `false`
  (`/boot/firmware/rb/rbvoice-cases.<lang>.json`). A case file without the key counts as `true`.
- **Startup.** When rbvoice has finished starting up (after the startup phrase), it posts
  `{"voice_wakeword": <default>, "voice_trigger": 0}` to `/bridge/float`. Then it connects to
  `/bridge/float/ws` and follows every change.
- **Session.** A change made on the bridge lasts until rbvoice restarts. On restart, rbvoice
  publishes the case file default again. To change the default for good, edit the case file.
- **Trigger window.** After a trigger, the pilot has 8 seconds to start speaking (`--trigger-window`
  in `rbvoice.sh`). Once speech has started, rbvoice keeps listening until the utterance ends.
  Whether an utterance was heard or the window ran out, rbvoice then sets `voice_trigger` back to `0`.
- **Display target.** A wake word still names a display ("secondario, visualizza mappa"). An
  utterance without a wake word names no display, so every RB-01 display acts on it.
- **Only a 0 → 1 change is a press.** The bridge sends its values only when one changes. Writing `1`
  while `voice_trigger` is already `1` (rbvoice is already listening) does nothing.
- **Bridge unreachable.** rbvoice retries the connection every 2 seconds and keeps its current
  setting meanwhile. If the case file default is `false` and the RB-01 cannot be reached, rbvoice
  hears nothing until the bridge comes back and a trigger arrives.

## On the RB-01

The Voice page (`web/RB-01/plates/voice.html`) has two buttons:

- **PUSH TO TALK** writes `voice_trigger = 1`. It turns green and reads **LISTENING** while rbvoice is
  listening.
- **WAKE WORD ON / OFF** switches `voice_wakeword` for the current session.

The knob reaches both buttons like the other buttons on the page.

## curl

Replace `192.168.10.1` with the address of your RB (the `SERVER` value in `rbvoice.sh`). On the RB
itself, use `localhost`.

Read the current values:

```bash
curl http://192.168.10.1/bridge/float
# {"voice_trigger":0,"voice_wakeword":1, ...other bridge keys...}
```

Switch the wake word off, so rbvoice listens only after a trigger:

```bash
curl -X POST http://192.168.10.1/bridge/float -d '{"voice_wakeword":0}'
```

Switch the wake word back on:

```bash
curl -X POST http://192.168.10.1/bridge/float -d '{"voice_wakeword":1}'
```

Trigger listening (push to talk), then speak within 8 seconds:

```bash
curl -X POST http://192.168.10.1/bridge/float -d '{"voice_trigger":1}'
```

Both in one request: wake word off, and listen now:

```bash
curl -X POST http://192.168.10.1/bridge/float -d '{"voice_wakeword":0,"voice_trigger":1}'
```

Watch the values change live (needs [`websocat`](https://github.com/vi/websocat)). The socket sends
the whole bridge map after every change, and nothing when you connect:

```bash
websocat ws://192.168.10.1/bridge/float/ws
```

The endpoint answers every POST with an empty body. Use the GET above to check that a value was
stored.

## Hardware button

A physical push-to-talk button only has to send the trigger POST. For example, a GPIO button on the
Pi, with `gpiomon` from `gpiod`:

```bash
# GPIO 17 on gpiochip0, button to ground: a falling edge is a press
while gpiomon --num-events=1 --falling-edge gpiochip0 17 >/dev/null; do
    curl -s -X POST http://localhost/bridge/float -d '{"voice_trigger":1}' >/dev/null
done
```

This is the gpiod 1.x syntax that Raspberry Pi OS Bookworm ships. With gpiod 2.x, use
`gpiomon --num-events=1 --edges=falling --chip gpiochip0 17`.

## Logs

`journalctl -u rbvoice -f` shows the bridge activity:

```
Bridge: published {'voice_wakeword': 1, 'voice_trigger': 0}
Bridge: connected to ws://192.168.10.1/bridge/float/ws
Bridge: wake word disabled
Bridge: trigger
Listening for 8 s, no wake word needed
Trigger window closed without an utterance
```
