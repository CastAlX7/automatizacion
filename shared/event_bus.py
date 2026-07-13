from __future__ import annotations

# Event-name markers appended to CarSaleState.events (see shared/graph_state.py).
# Coordination between agents is now expressed via LangGraph edges/routing,
# not pub/sub — these constants are kept only as a readable event log.

CAR_ACQUIRED = "car.acquired"
INSPECTION_COMPLETED = "inspection.completed"
CAR_REJECTED = "car.rejected"
PUBLICATION_READY = "publication.ready"
PUBLISHED = "car.published"
LEAD_RECEIVED = "lead.received"
LEAD_QUALIFIED = "lead.qualified"
LEAD_DISCARDED = "lead.discarded"
NEGOTIATION_STARTED = "negotiation.started"
SALE_COMPLETED = "sale.completed"
NEGOTIATION_FAILED = "negotiation.failed"
