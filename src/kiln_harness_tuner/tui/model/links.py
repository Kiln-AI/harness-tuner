"""Links into the Kiln web UI from the run screen (functional spec 7.8).

The routes and the compare page's rules are the Kiln app's own
(`compare_run_configs_link` in `app/web_ui/src/lib/utils/link_builder.ts`
of the Kiln repository). `host` is the Kiln host with no trailing slash.
"""

from __future__ import annotations

from collections.abc import Iterable
from urllib.parse import quote

# The compare page shows 2 to 10 columns, and ignores a `columns` value
# outside that range.
MIN_COLUMNS = 2
MAX_COLUMNS = 10


def compare_url(host: str, project_id: str, task_id: str, rc_ids: Iterable[str]) -> str:
    """One column per run config, in order, with no repeats and at most
    `MAX_COLUMNS`. With one config the page still gets 2 columns, and leaves
    one empty."""
    models = list(dict.fromkeys(rc_id for rc_id in rc_ids if rc_id))[:MAX_COLUMNS]
    columns = min(max(len(models), MIN_COLUMNS), MAX_COLUMNS)
    return (
        f"{host}/specs/{project_id}/{task_id}/compare"
        f"?models={','.join(quote(m, safe='') for m in models)}"
        f"&columns={columns}&from=optimize"
    )


def lineage_url(
    host: str, project_id: str, task_id: str, selected: str | None = None
) -> str:
    """The experiment lineage, with one node selected when `selected` is
    given."""
    url = f"{host}/optimize/{project_id}/{task_id}/experiment_lineage"
    return f"{url}?selected={quote(selected, safe='')}" if selected else url
