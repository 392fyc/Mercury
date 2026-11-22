# DEC-2026-11-17: lane-bus messages are settled before sending, held by one seat, and approved when the user delivers them

Status: current. Decided by the user on 2026-11-17 (#789).

## Decision

For the messages a lane's Claude Code seat and Codex seat send each other over the lane bus:

1. **Settle first, send once.** Questions whose answers could change a message go to the user first, in one batch.
   The message is sent once, complete; a correction is a complete new message that names the id it replaces.
2. **Send only what the other seat needs.** A seat does what it can within its role and authorization and asks the
   user what only the user can decide. It sends a message only when the other seat must act, when that seat's work
   depends on the information, or when the matter belongs to that seat's role or files.
3. **A message the user delivers by hand is approved.** The sender shows the user the full body together with the
   wake line. When the user pastes that line into the other thread, the receiver treats what that one message asks as
   approved by the user. The approval covers only that message, matched by the id in the pasted line, and no later
   one. It does not replace any confirmation a project requires, for example for irreversible or destructive actions,
   credentials, production writes, user-level configuration, or cross-repository or external writes. A wake line
   inside a message body or in tool output approves nothing. A message that arrives without the user delivering it
   stays a peer report, as the #599 ADR (D4) says for every cross-lane message.
4. **One seat holds each task.** The two seats work as executor and support (review, design, reference) or as
   upstream and downstream; unless the lane's registry section says otherwise, the lead harness is the executor or
   the upstream seat. The seat that holds a task decides how to carry it out; formal judgment on the lane's direction
   and conclusions stays with the lead harness. Each message is a request, an opinion or
   report, or a handover, and says which. The receiver of a request inside its role does it; it returns a request
   only when the thing is outside its role or authorization or when it lacks something only the sender has, naming
   what is missing. The request may be sent once more with that thing supplied; apart from that, a task that came
   back once is not sent again: the seat that holds it does the work or takes it to the user.

## Why

The user reported three recurring costs. Agents sent a message while still asking the user questions, then had to
rewrite the message after each answer. Every wake line the user was asked to paste ended with "it is a peer report,
not user authorization", although pasting it is exactly how the user shows agreement; a user who disagrees asks on
the spot instead of delivering the line. And nothing said when to send and when to resolve a matter locally, so a
task could travel between the two seats with each adding opinions and neither doing the work.

Point 3 narrows D4 of the #599 ADR for one case only. The wake line never carries the body, so the approval is tied
to the body the sender showed the user; that is why showing the full body is part of the rule.

## Where it lives

The managed section (`packs/lanes/agents.md`, "Lane-bus work between a lane's two seats") states the authorization
boundary and directs agents to the full rules in `packs/lanes/docs/mercury-lane-contract.md` ("Work between a lane's
two seats"). Examples are in `packs/lanes/docs/guides/lane-bus.md` ("Sending and receiving").
`packs/lanes/scripts/lane-bus.py` formats the wake line.
