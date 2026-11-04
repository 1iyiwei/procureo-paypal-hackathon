# Notes

## Features vs. a product

A **feature set** lists what the system can do: visual intent, sourcing, dynamic pricing, monitoring, support for both shopper types, checkout. Every item is reasonable. But a list doesn't say who would use it, for what, or why they'd pick it over what they use today. It also can't tell you what to cut.

A **product** is a promise to a specific person about a specific job. You can tell you have one when these five things are clear:

1. **One sentence with no "and."** "Delegate a purchase once, and stop watching it, without giving up control of your money." Every feature either serves that sentence or gets cut.
2. **A core object.** Here it's the *standing order*: what you need, plus your limits, held over time. Each feature is a step in that object's life:
   - the text and attachments create it;
   - sourcing and monitoring keep it open while it waits;
   - checkout carries it out.

   In a feature list, the features sit side by side with nothing tying them together.
3. **A moment of value.** The notification "bought it at $128, under your $150 cap, while you slept" is what the user is paying for. A product is designed backward from that moment, and the demo video should build up to it.
4. **A gap among the alternatives.** The draft's Inspiration section makes this case:
   - price alerts watch but can't buy;
   - agents that can check out buy immediately but can't wait;
   - nothing covers the whole job safely.

   A list of features gives judges no comparison to remember.
5. **A clear trust model.** For an agent that spends money, the limits, the decision to ask or buy automatically, and the option to cancel are the product, not a settings page. This is also the part that matches your HCI background.

Applying this test exposed one weakness, which I wrote down as an open decision rather than resolving: "both individuals and small businesses" is still two products. The pitch can cover both, but the demo has to follow one person through one standing order.

## [Do I need separate accounts for paypal payments and API](https://share.google/aimode/rDwFkRXz6A1PLLusX)

Sandbox: no; going live: use a separate Business account for the startup.

For the hackathon, you don’t need another real account. Your one real PayPal account is only your login to the developer dashboard. The sandbox business and personal accounts are fake test accounts that live inside that dashboard; they hold no real money and aren’t linked to your real account in any way. The hackathon only asks for sandbox use, so your setup is complete.

Separating them only matters if you go live. Live API credentials have to come from a PayPal Business account, which receives real payments. You could convert your personal account into one, but a separate business account, registered to your startup rather than to you, is the cleaner choice. It keeps company money and taxes apart from your own, and when you add co-founders or staff to that account later, they never touch your personal finances.

## [Random string generator Linux](https://share.google/aimode/kxASma9ESwijvCXYq)
```
cat /dev/urandom | LC_ALL=C tr -dc 'A-Za-z0-9' | head -c 32; echo
```