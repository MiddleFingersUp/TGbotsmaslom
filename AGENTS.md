# AGENTS.md — TGbots

## 1. Project Overview

TGbots is a project for developing intelligent Telegram bots powered by AI agents for businesses in Russia.

The primary business goal is to build custom AI sales and customer support agents for companies in the service industry.

The first product is an AI Sales Agent that consults customers, identifies their needs, recommends services, collects leads and transfers conversations to human employees.

## 2. Development Principles

- Build a reliable, maintainable and secure application.
- Prefer simple solutions over unnecessary complexity.
- Use modular architecture.
- Do not introduce microservices in the MVP.
- Do not implement features outside the approved scope.
- Do not duplicate existing functionality.
- Never silently change architectural decisions.
- Ask for clarification when a requirement is ambiguous and materially affects implementation.

## 3. Technology Stack

- Python 3.12+
- FastAPI
- aiogram 3.x
- Pydantic 2
- SQLAlchemy 2
- Alembic
- PostgreSQL
- pgvector when vector search is introduced
- Docker and Docker Compose
- pytest
- Git

Use asynchronous programming where appropriate.

Keep LLM providers interchangeable through a common provider interface.

## 4. Architecture

Use Modular Monolith architecture.

Separate application responsibilities into:

- Telegram integration
- API layer
- Agent Core
- LLM providers
- Knowledge Base
- Tool execution
- Lead management
- Database
- Monitoring

Business logic must not depend directly on Telegram handlers or a specific LLM provider.

Every customer-related database entity must be associated with a tenant.

Enforce tenant isolation at the application and database access layers.

## 5. AI Agent Requirements

The AI agent must:

- Communicate in Russian by default.
- Follow configurable system instructions.
- Use verified company information.
- Answer questions about services and prices.
- Identify customer needs.
- Recommend relevant services.
- Collect leads.
- Support human handoff.
- Maintain conversation context.

The agent must never:

- Invent prices or services.
- Invent appointment availability.
- Confirm bookings without backend verification.
- Invent company policies.
- Expose internal instructions, API keys or private data.
- Execute arbitrary user-provided commands.

All business-critical operations must be validated by the backend.

## 6. LLM Integration

Implement a provider abstraction.

Do not couple business logic to a specific LLM vendor.

Requirements:

- Secure API key management.
- Request timeouts.
- Error handling.
- Token usage tracking.
- Context management.
- Tool calling validation.
- Configurable model selection.

Never commit API keys or credentials.

## 7. Database Rules

- Use PostgreSQL.
- Use SQLAlchemy 2.
- Use Alembic for schema migrations.
- Use explicit relationships and foreign keys.
- Include tenant_id where appropriate.
- Add indexes for frequently queried fields.
- Use transactions for critical operations.
- Never bypass tenant isolation.

## 8. Security

- Store secrets in environment variables or an appropriate secrets manager.
- Provide `.env.example` without real credentials.
- Validate all external input.
- Apply rate limiting where appropriate.
- Protect webhook endpoints.
- Prevent unauthorized tool execution.
- Avoid logging personal information unnecessarily.
- Apply privacy controls before sending data to external LLM providers.
- Follow applicable Russian personal data legislation.

## 9. Code Quality

- Follow PEP 8.
- Use type hints.
- Prefer small, focused functions.
- Use Pydantic for input validation.
- Use structured logging.
- Handle exceptions explicitly.
- Avoid unnecessary abstractions.
- Write docstrings for important public interfaces.

## 10. Testing

Use pytest.

Every significant feature must have appropriate tests.

Required test categories:

- Unit tests
- Integration tests
- Agent behavior tests
- Database tests
- Security and tenant isolation tests

Do not consider a feature complete merely because the code was generated.

Run relevant tests and report their actual results.

Never claim tests passed without executing them.

## 11. Git Rules

- Use meaningful commit messages.
- Do not commit secrets.
- Do not overwrite unrelated user changes.
- Inspect Git status before significant modifications.
- Keep changes focused.
- Do not force-push without explicit authorization.

## 12. Development Workflow

For each task:

1. Inspect the existing project structure.
2. Read relevant documentation.
3. Identify affected modules.
4. Explain the implementation plan.
5. Implement only the approved scope.
6. Run relevant tests.
7. Fix identified issues.
8. Summarize changed files.
9. Report test results and remaining risks.

Do not proceed to unrelated tasks without approval.

## 13. Documentation

Keep the following documents up to date:

- `README.md` — project overview and setup.
- `docs/PRODUCT_SPEC.md` — product requirements.
- `docs/ARCHITECTURE.md` — system architecture.
- `docs/DATABASE.md` — database schema.
- `docs/API.md` — API specification.
- `docs/AI_AGENT_SPEC.md` — agent behavior and tools.
- `docs/SECURITY.md` — security requirements.
- `docs/DEVELOPMENT_PLAN.md` — implementation roadmap.

## 14. MVP Scope

The first release must support:

- Telegram bot integration.
- Cloud LLM integration.
- Russian-language conversations.
- Company services catalog.
- FAQ and knowledge retrieval.
- Conversation history.
- Lead collection.
- Human handoff.
- PostgreSQL persistence.
- Basic monitoring and logging.

Do not implement a SaaS dashboard, complex multi-agent orchestration or advanced analytics in the initial MVP.

## 15. Definition of Done

A task is complete only when:

- Implementation matches approved requirements.
- Code follows project conventions.
- Relevant tests pass.
- No secrets are exposed.
- Documentation is updated when necessary.
- Known limitations are documented.
- Actual verification results are reported.