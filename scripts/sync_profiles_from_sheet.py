"""One-shot Google Sheet to profiles_ultra.txt sync."""

from __future__ import annotations

import json
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from flow.credentials.sheet_loader import SheetLoaderError, sync_profiles_from_sheet


def main() -> int:
    try:
        result = sync_profiles_from_sheet()
    except SheetLoaderError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1
    print(
        json.dumps(
            {
                "loaded": result.loaded,
                "profiles": result.profiles,
                "output_path": str(result.output_path),
            },
            ensure_ascii=False,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
