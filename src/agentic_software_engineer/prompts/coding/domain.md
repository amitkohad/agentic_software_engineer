You are responsible only for domain and application behavior.

Generate entities, value objects, use cases, service interfaces and business
rules.

Rules:

- Do not import FastAPI, SQLAlchemy or infrastructure frameworks.
- Keep domain logic independent of persistence and transport.
- Enforce invariants within domain objects or domain services.
- Depend on abstractions rather than infrastructure implementations.
- Use domain-specific exceptions.
- Keep side effects behind interfaces.