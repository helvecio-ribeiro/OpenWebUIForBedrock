"""Deterministic stdio harness for the production Local Web Research MCP server."""

from __future__ import annotations

import server


class FixtureFetcher:
    limits = server.LIMITS
    user_agent = "LambdaWebUI-WebResearch-Test/1.0"

    def fetch_page(
        self,
        url: str,
        *,
        output_format: str = "markdown",
        max_characters: int | None = None,
        selector: str | None = None,
    ) -> dict:
        del output_format, selector
        is_root = url.rstrip("/") == "https://fixture.example"
        content = (
            "Deterministic fixture evidence. " * 80
            if is_root
            else "Linked fixture evidence."
        )
        if max_characters is not None:
            content = content[:max_characters]
        return {
            "url": url,
            "title": "Fixture root" if is_root else "Fixture child",
            "description": "Deterministic MCP stdio fixture",
            "published_at": None,
            "modified_at": None,
            "content": content,
            "links": (
                [{"url": "https://fixture.example/child", "text": "Child"}]
                if is_root
                else []
            ),
            "truncated": False,
            "source_truncated": False,
            "warnings": [],
        }


server.fetcher = FixtureFetcher()
server.ROBOTS_POLICY = "ignore"

if __name__ == "__main__":
    server.run_stdio(server.mcp)
