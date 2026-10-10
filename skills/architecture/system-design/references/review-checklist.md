# Design Review Checklist

Use this when reviewing someone else's design or preparing your own. Ask the questions; a design
that cannot answer them is not ready.

## Problem and scope

- [ ] What problem is being solved, in two paragraphs or fewer?
- [ ] What is explicitly out of scope for this phase?
- [ ] Which existing systems does this touch?
- [ ] Who is the operator, and who is the consumer of the output?

## Requirements

- [ ] Functional requirements are enumerated and testable.
- [ ] Latency, availability, durability, and consistency targets are stated numerically.
- [ ] Data volume and growth are estimated.
- [ ] Retention and deletion requirements are stated.
- [ ] Regulatory or residency constraints are identified.

## Numbers

- [ ] Peak, not average, is used for capacity.
- [ ] Assumptions are written next to the numbers.
- [ ] Growth at 12 months is shown, not just today.
- [ ] Storage includes indexes, replicas, and retention.
- [ ] The most fragile assumption is named.

## Data model

- [ ] Entities and identities are clear.
- [ ] Access patterns are written as concrete queries.
- [ ] Indexes are justified by a query, and only those exist.
- [ ] Volume and cardinality per table are estimated.
- [ ] Which relationships are transactional and which are eventual is stated.

## Architecture

- [ ] Components are drawn with arrows, and every arrow is labeled with what flows.
- [ ] Each component has one line explaining why it exists.
- [ ] The two or three critical paths are described step by step.
- [ ] At least one path is walked through a failure.
- [ ] Synchronous versus asynchronous boundaries are marked.
- [ ] No component is doing two unrelated jobs.

## Scaling

- [ ] The scaling ladder is: index, query, cache, replicas, vertical, shard.
- [ ] Services are stateless, or the state and its failover are described.
- [ ] What breaks first at 10x is named.
- [ ] Sharding, if any, states the strategy and the rebalancing story.

## Reliability

- [ ] SLO and measurement window are stated.
- [ ] Error budget and its consequences are stated.
- [ ] Every dependency has a timeout and a degraded behavior.
- [ ] Retries are bounded and classified by retriability.
- [ ] Failure domains are named per component with counts.
- [ ] Backups exist, are isolated, and a restore has been tested.
- [ ] Load shedding or rate limiting exists at the edge.

## Security and data

- [ ] Authentication and authorization are described at the boundary, not assumed.
- [ ] Sensitive data is identified, encrypted, and its retention stated.
- [ ] Tenant isolation is enforced at the query path, not only at the UI.
- [ ] Audit trail exists for state changes.

## Delivery

- [ ] Migration path from the current state is described.
- [ ] Rollback plan exists.
- [ ] Rollout is staged, with a flag or a canary.
- [ ] Build versus buy, and build versus managed service, is justified.

## Tradeoffs

- [ ] At least three tradeoffs are stated explicitly.
- [ ] Each tradeoff names the trigger that would make you revisit it.
- [ ] Rejected options are named with the reason.
- [ ] Open questions have owners.

## Diagram checklist

- [ ] Direction of flow is unambiguous.
- [ ] Trust boundaries and network boundaries are marked.
- [ ] External systems are visually distinct from owned components.
- [ ] The failure domains are visible, not just implied.
- [ ] Anything that fans out is annotated with the multiplier.

## Questions that always expose something

1. What happens if this component is slow rather than down?
2. What happens if it returns stale data?
3. What is the largest single failure you could survive, and which one you cannot?
4. How do you know it is broken before a user tells you?
5. What is the most expensive mistake available in this design?
6. If we had to undo this in a year, what would be hard to undo?
7. Who operates this at 3 a.m., and what do they need that is not here?
8. What did you decide not to do, and why is that safe for now?