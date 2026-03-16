from __future__ import annotations

from typing import Any

from .data_pipeline import prepare_inputs
from .report_pipeline import render_reports
from .search_pipeline import run_search_from_pack


def run_engine(ctx: Any, cfg: Any) -> str:
    prepared_inputs = prepare_inputs(ctx, cfg)
    search_bundle = run_search_from_pack(
        ctx=ctx,
        pack=prepared_inputs["pack"],
        regime_by_date=prepared_inputs["regime_by_date"],
        cfg=cfg,
    )
    return render_reports(ctx, prepared_inputs=prepared_inputs, search_bundle=search_bundle)
