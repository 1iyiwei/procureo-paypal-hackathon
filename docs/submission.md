# Submission draft

Working draft for the Devpost form. Sections follow Devpost's standard project fields.
Anything marked *(planned)* is not built yet; update or remove it before submitting.

## Project name

**Standing Order**

A standing order is an instruction you give your bank once, which it then carries out on your behalf.
Here the instruction goes to an agent, and it is about buying something: what you need, what you'll pay, and by when.
The agent stands by until the conditions are met.

Alternatives considered: *Allowance* (bounded spending, but sounds like pocket money),
*Errand* (clear, but generic), *Mandate* (accurate, but cold).

## Elevator pitch
(Devpost limit: 200 characters.)

Tell an agent what you need and the most you'll pay. It finds the item, waits for the right price, and buys it with PayPal, never going past your limits.

## The product in one sentence

Delegate a purchase once, and stop watching it, without giving up control of your money.

## About the project

### Inspiration

As a frugal and yet busy person, I often want to find the best deal and yet have no time to watch the price or compare around.
Buying something well often means waiting: for a flight to dip, for a sold-out item to come back in stock,
for a better offer. Today's tools split that job in two. Price alerts watch but can't buy, so you still have to drop everything when the alert fires. Agents that can check out buy immediately, so they can't wait for a better price.
Nothing takes the whole job: understand what I want, wait for the right moment, and buy, safely.

The hard part isn't the AI, it's trust. Would you let an agent spend your money while you sleep?
Only if you set the limits and can see and stop everything it does.

### What it does

1. **You describe the need, in your own words.** Text is enough: "noise-cancelling over-ear headphones
   for my friend's birthday, delivered by Dec 14, under $200." You can add a photo, a video, a product link,
   or a voice note when words aren't enough. These are optional, and the agent uses them to refine the request.
2. **The agent turns it into a standing order.** This is a structured request plus your limits:
   the most to spend and the latest date to buy. You review it once.
3. **It sources and waits.** It searches merchants, tracks prices over time, and tells you what it's seeing.
4. **It asks, then buys.** When it finds the right offer, it asks you first, and you approve with a one-tap
   PayPal checkout in the chat. Once you trust it, you can turn on "buy automatically under $X". After that, the agent pays on its own with a PayPal payment method you saved once, but only within the limits you set. In both cases you get a receipt and a record of what it did and why.
5. **You stay in control.** Change the limits, pause, or cancel any standing order from the chat.

It's built for individual shoppers buying for themselves.

*(Status: only the one-tap PayPal checkout in step 4 works today; the rest is planned.)*

### How we built it

- **Interface:** a Telegram bot. Chat handles text and all the media types natively, and, crucially, it can
  reach you later. An agent that waits needs a way to say "the price dropped, buying now."
- **Payments:** PayPal Orders API v2 (sandbox) for checkout in the chat; PayPal vault (saved payment methods) for purchases the agent makes on its own *(planned)*.
- **AI:** a multimodal model turns the text and attachments into a structured request *(planned)*.
- **Backend:** Python, FastAPI, aiogram, and SQLite, deployed on Render.

### Challenges we ran into

*(fill in during the build)*

### Accomplishments that we're proud of

*(fill in during the build)*

### What we learned

*(fill in during the build. One candidate: who the merchant of record is in agentic commerce, and why a
saved payment method only works with the merchant that saved it.)*

### What's next

- Small businesses: the same standing order, with team approvals and recurring restocks of supplies.
- *(fill in the rest at the end)*

## Built with

python, fastapi, aiogram, telegram, paypal, sqlite, render

## Try it out

- Telegram bot: *(link)*
- Source code: *(GitHub link)*
- Demo video (under 3 minutes): *(YouTube link; plan in [storyboard.md](storyboard.md))*

---

## Product decisions (internal; not part of the submission)

- **Main user: individual shopper** (decided 2026-11-03). The video follows one person through one
  standing order. Small businesses are mentioned only under "What's next."
- **Autonomy: ask first by default; automatic buying is opt-in** (decided 2026-11-03). The user turns it on
  per standing order, with a cap. The demo shows both: first a purchase the user approves with one tap,
  then the user turns on automatic buying, and later the agent buys on its own while they are away.
- **Demo item: a gift needed by a date** (decided 2026-11-03). Working example: headphones for a friend's
  birthday, delivered by Dec 14, under $200. The deadline gives the agent a reason to wait and a point
  where it must stop waiting. The holiday sales between now and then make the price changes believable.
  Sold by mock merchants, because our PayPal checkout pays our own merchant account.
