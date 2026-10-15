"""Seeds the RAG backend with the Deploy IQ website content in data/*.md.

Usage (once the backend is running and .env is configured):
    python scripts/seed_ingest.py --api-url http://localhost:8000 --token <jwt>
"""

import argparse
import asyncio
import pathlib

import httpx

DATA_DIR = pathlib.Path(__file__).parent.parent / "data"


async def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--api-url", default="http://localhost:8000")
    parser.add_argument("--token", required=True, help="Bearer token for /ingest")
    args = parser.parse_args()

    headers = {"Authorization": f"Bearer {args.token}"}

    md_files = await asyncio.to_thread(lambda: sorted(DATA_DIR.glob("*.md")))

    async with httpx.AsyncClient(timeout=60, headers=headers) as client:
        for md_file in md_files:
            content = await asyncio.to_thread(md_file.read_text)
            payload = {
                "source_name": md_file.stem,
                "source_type": "markdown",
                "content": content,
                "chunking_strategy": "auto",
                "metadata": {"page": md_file.stem},
            }
            resp = await client.post(f"{args.api_url}/ingest", json=payload)
            resp.raise_for_status()
            result = resp.json()
            print(
                f"{md_file.name}: {result['chunk_count']} chunks ({result['strategy_used']})"
            )


if __name__ == "__main__":
    asyncio.run(main())
