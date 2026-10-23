## Docker

- Use multi-stage builds to keep final images small.
- Pin base images to a digest or version; never `latest` in production Dockerfiles.
- Run the application as a non-root user (`USER` directive).
- Combine related `RUN` commands to reduce layers; order layers from least to most frequently changing.
- Do not copy secrets or credentials into the image — use build-time secrets or runtime mounts.
- Include a `.dockerignore` that excludes `.git`, `node_modules`, build artifacts, and secrets.
- Use `HEALTHCHECK` for long-lived services.
