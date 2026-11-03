# Demo video storyboard

Target length 2:50 (the limit is 3:00). One user, one standing order: headphones for a friend's birthday,
delivered by Dec 14, under $200. See [submission.md](submission.md) for the product decisions behind it.

**The story in one line:** the agent asks first; the user declines but hands over a tighter, automatic limit;
the agent buys on its own at night and shows its work.

Keeping it to one standing order means the viewer watches trust being granted step by step, instead of
watching two unrelated purchases.

## Shots

Times are approximate. "VO" is the voiceover. Bot messages are written as they should appear in Telegram.

### 1. The problem (0:00–0:15)

- **Screen:** title card, then a quick montage: a price-alert email, a product page with a price going up and down.
- **VO:** "Buying a gift at a good price means watching prices for weeks. Price alerts watch but can't buy.
  Agents that can buy do it right away. Nothing does both, safely."

### 2. Describe the need (0:15–0:40)

- **Screen:** Telegram, phone-sized. The user types:
  > Noise-cancelling over-ear headphones for Sam's birthday. Needs to arrive by Dec 14. Under $200.

  Then attaches a screenshot of a product page Sam shared, with the caption "something like this".
- **Bot replies with a standing order card:**
  > **Standing order: headphones for Sam**
  > Over-ear, noise-cancelling, similar to the attached model
  > Spend up to: **$200**
  > Arrive by: **Dec 14** (so buy by Dec 9, allowing for shipping)
  > Mode: **ask me before buying**
  >
  > [Start watching] [Edit]
- **VO:** "Just say what you need. A photo or link is optional, and it helps the agent be precise.
  It turns the request into a standing order with your limits, and it asks before buying."
- **Point to land:** text alone was enough. The photo only refined the request.

### 3. Sourcing and waiting (0:40–1:05)

- **Screen:** the user taps **Start watching**. The bot lists three matches from mock merchants with prices
  ($229, $219, $239). A **simulated time** label appears in the corner and the calendar advances.
  The bot sends short updates:
  > Nov 18: lowest is $219 at Merchant B. Still above your limit.
  > Nov 24: $209 at Merchant A.
- **VO:** "It checks merchants every few hours and only speaks up when something changes. We've sped up time for the demo."
- **Point to land:** the agent is patient and quiet, not noisy.

### 4. The ask (1:05–1:35)

- **Screen:** Black Friday. The bot:
  > **$189 at Merchant A** (lowest in 30 days), inside your $200 limit.
  > Arrives Dec 2.
  >
  > [Buy now with PayPal] [Not yet]
- The user taps **Not yet**. The bot asks what would change their mind. The user types:
  > Wait. If it drops under $170, just buy it.
- **Bot:**
  > Got it. I'll buy automatically under **$170**, until Dec 9.
  > To do that, save PayPal as your payment method once. You can remove it any time.
  >
  > [Save PayPal]
- **Screen:** the PayPal sandbox page for saving a payment method. The user approves. Back in Telegram:
  > ✅ PayPal saved. Automatic buying is on for this standing order only.
- **VO:** "The agent always asks first. Here the user doesn't approve the purchase. They set a tighter limit
  and give the agent permission to act on its own. That permission covers this one standing order and nothing else."
- **Point to land:** this is the key moment of the video: the user chose to give more control, on their own terms.

### 5. The agent acts on its own (1:35–2:05)

- **Screen:** the simulated clock runs to night. The phone goes dark, then lights up with a notification.
  > **Bought: headphones for Sam, $164** at Merchant C
  > You set: buy automatically under $170, by Dec 9.
  > Paid with your saved PayPal. Arrives Dec 4.
  >
  > [Receipt] [Why this one?] [Cancel order]
- The user taps **Why this one?**:
  > I compared 3 merchants. $164 was the lowest price I'd seen, under your $170 limit,
  > and it arrives 10 days before the birthday.
- **VO:** "At 2 a.m. the price drops, and the agent buys. It stayed within the limit the user set,
  and it explains its choice in plain words."

### 6. Still in control (2:05–2:25)

- **Screen:** the user sends `/orders`. The bot shows the standing order as completed, with a timeline:
  created, watching, asked at $189, user declined and allowed automatic buying under $170, bought at $164.
  Then a quick view of the PayPal sandbox transaction.
- **VO:** "Everything the agent did is on record. You can cancel an order, pause a standing order, or remove saved PayPal at any time."

### 7. How it works and close (2:25–2:50)

- **Screen:** one diagram: Telegram → agent (AI request understanding, sourcing, price watching) →
  PayPal (checkout in chat, saved payment method for automatic buying). Then the title card and tagline.
- **VO:** "Standing Order uses PayPal checkout when you approve a purchase, and a saved PayPal payment method when you've let the agent buy on its own.
  Tell it what you need and what you'll pay. It waits, and buys within your limits."

## What the video requires

| Piece | Must be real | Can be mocked | Status |
| --- | --- | --- | --- |
| Telegram bot conversation | ✔ | | Started |
| Text to standing order card, with an optional image | ✔ (LLM call) | | Planned |
| Merchants, products and prices | | ✔ (mock catalog) | Planned |
| Simulated clock and price changes | | ✔ (demo control, labeled on screen) | Planned |
| Price watching and deciding when to ask or buy | ✔ | | Planned |
| "Buy now with PayPal" checkout | ✔ (PayPal sandbox) | | **Works** |
| Saving PayPal for later (vault) | ✔ (PayPal sandbox) | | Planned; **test first** |
| Paying with saved PayPal, no user present | ✔ (PayPal sandbox) | | Planned |
| "Why this one?" explanation | ✔ (from logged decision data) | | Planned |
| `/orders` timeline | ✔ | | Planned |
| Cancel an order (refund) | | Button may be shown only | Optional |
| Shipping and arrival dates | | ✔ (from mock catalog) | Planned |

## Recording notes

- Record Telegram Desktop in a phone-sized window, or a real phone, at the same size throughout.
- The demo control for time and prices must not appear in the chat. Use a separate admin page or
  hidden commands, and show only the "simulated time" label in the video.
- Record shots 4 and 5 as two separate takes, so you can redo the PayPal approval without re-recording everything.
- Write the voiceover first, then time each shot to it. The script above is about 240 words, or roughly
  1:45 of speech at a calm pace. That leaves about a minute of pauses for viewers to read the chat.
