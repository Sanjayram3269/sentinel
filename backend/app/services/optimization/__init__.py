"""Mission optimization: hospital, route, and resource decision layer (Phase 6).

The package is deliberately split so that the two halves of the decision stay
separable and individually testable:

``constraints``
    Hard constraints. These only ever *reject* an option, never rank one.
``plan_scoring``
    The soft objective. A weighted sum of normalised costs over the options
    that survived the constraints.
``hospital_suitability``
    Deterministic hospital ranking with per-factor contributions.
``resource_allocation``
    Deterministic resource assignment with explicit rejections.
``mission_optimizer``
    The joint hospital x route search and plan persistence.
``whatif``
    Recompute a plan with one selected component removed.

Nothing in this package regenerates a route. Route candidates are produced by
Phase 5 routing and are only ever read here.
"""
