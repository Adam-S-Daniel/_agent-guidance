## Security

- Validate anything crossing a trust boundary — user input, API responses,
  file contents.
- Never build SQL, shell commands or HTML by concatenating untrusted data:
  parameterized queries, shell arrays, context-aware escaping.
- Never commit secrets, credentials or `.env` files, or disable TLS
  verification, authentication or CSRF protection.
- **Automation authenticates as a scoped GitHub App, not a PAT.**
