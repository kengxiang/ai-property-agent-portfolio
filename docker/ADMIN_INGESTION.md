# Local property administration

Use the isolated [portfolio demo](../docs/DEMO.md) with `ADMIN_INGESTION_ENABLED=1`. The account tool checks the portfolio Compose project before provisioning. Run it interactively:

```bash
docker compose --env-file .env.compose -p ai-property-portfolio exec backend python -m backend.create_sandbox_admin
```

Enter a local account name and a password of at least 14 characters. In Streamlit, open **Administration** and sign in. Add a synthetic property or upload a CSV matching the [sample schema](../data/synthetic_properties.csv). Review the preview and correct validation errors before confirming. The backend enforces authorization, uniqueness and transactional audit records. Logout revokes the session.

PDF ingestion uses the bundled fixture importer documented in the demo.
