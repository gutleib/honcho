"""
Reset all embeddings to pending so the reconciler regenerates them.

Use this when changing EMBEDDING_MODEL — vectors from different models occupy
different spaces, so mixed embeddings silently break semantic search.

Usage (from the host via docker compose):
    docker compose exec honcho-api python scripts/reindex_embeddings.py --status
    docker compose exec honcho-api python scripts/reindex_embeddings.py
    docker compose exec honcho-api python scripts/reindex_embeddings.py --yes
"""

import argparse
import asyncio
import os
import sys

project_root = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, project_root)

from sqlalchemy import func, select, update  # noqa: E402

from src import models  # noqa: E402
from src.dependencies import tracked_db  # noqa: E402


async def get_status() -> None:
    async with tracked_db("reindex_status") as db:
        for table, model, extra_filter in [
            ("documents", models.Document, models.Document.deleted_at.is_(None)),
            ("message_embeddings", models.MessageEmbedding, None),
        ]:
            filters = [extra_filter] if extra_filter is not None else []
            counts = {}
            for state in ("pending", "synced", "failed"):
                q = select(func.count()).select_from(model)
                if filters:
                    q = q.where(*filters)
                q = q.where(model.sync_state == state)
                counts[state] = (await db.scalar(q)) or 0
            total_q = select(func.count()).select_from(model)
            if filters:
                total_q = total_q.where(*filters)
            total = (await db.scalar(total_q)) or 0
            print(
                f"  {table:<22} pending={counts['pending']:>6}  "
                f"synced={counts['synced']:>6}  "
                f"failed={counts['failed']:>6}  "
                f"total={total:>6}"
            )


async def reset_embeddings() -> None:
    async with tracked_db("reindex_reset") as db:
        doc_result = await db.execute(
            update(models.Document)
            .where(models.Document.deleted_at.is_(None))
            .values(
                embedding=None,
                sync_state="pending",
                sync_attempts=0,
                last_sync_at=None,
            )
        )
        emb_result = await db.execute(
            update(models.MessageEmbedding).values(
                embedding=None,
                sync_state="pending",
                sync_attempts=0,
                last_sync_at=None,
            )
        )
        await db.commit()
        print(
            f"  Reset {doc_result.rowcount} documents, "
            f"{emb_result.rowcount} message_embeddings → pending"
        )


async def main() -> None:
    parser = argparse.ArgumentParser(
        description="Reset Honcho embeddings for reindexing with a new embedding model.",
    )
    parser.add_argument(
        "--status",
        action="store_true",
        help="Show pending/synced/failed counts without making changes.",
    )
    parser.add_argument(
        "--yes",
        action="store_true",
        help="Skip confirmation prompt (for scripting).",
    )
    args = parser.parse_args()

    print("=== Embedding status ===")
    await get_status()

    if args.status:
        return

    print()
    print("This will NULL all embeddings and mark them pending.")
    print("The reconciler will regenerate them using the current EMBEDDING_MODEL.")
    print("Semantic search degrades until reindexing completes (~5 min/cycle).")
    print()

    if not args.yes:
        answer = input("Continue? [y/N] ").strip().lower()
        if answer != "y":
            print("Aborted.")
            return

    print("=== Resetting ===")
    await reset_embeddings()

    print()
    print("=== Status after reset ===")
    await get_status()

    print()
    print("Done. Check progress with: python scripts/reindex_embeddings.py --status")


if __name__ == "__main__":
    asyncio.run(main())
