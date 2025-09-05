# pipeline_stage1.py
from __future__ import annotations

import logging
from typing import Optional, List

import pandas as pd
from tqdm import tqdm

from .search import find_report_links_categorized
from .config import (
    MAX_PDF_RESULTS,
    MAX_PAGE_RESULTS,
    COMPANY_NAME_COLUMN,
    COUNTRY_COLUMN,
    SCALESERP_API_KEY,   # default API key (can be overridden by arg)
)

logger = logging.getLogger(__name__)


def _ensure_link_columns(df: pd.DataFrame) -> pd.DataFrame:
    """Make sure pdf_link* and page_link* columns exist (filled with None)."""
    pdf_cols = [f"pdf_link{i}" for i in range(1, MAX_PDF_RESULTS + 1)]
    page_cols = [f"page_link{i}" for i in range(1, MAX_PAGE_RESULTS + 1)]
    for c in pdf_cols + page_cols:
        if c not in df.columns:
            df[c] = None
    return df


def process_companies_for_reports(
    df: pd.DataFrame,
    api_key: Optional[str] = None,
    *,
    include_country_in_search: bool = True,
    show_progress: bool = True,
) -> pd.DataFrame:
    """
    For each row, run the primary search (language decided in search.py) and add
    columns pdf_link1..N and page_link1..N.

    - Uses COMPANY_NAME_COLUMN and COUNTRY_COLUMN from config.
    - Does not mutate the input DataFrame.
    """
    if df is None or df.empty:
        logger.info("Empty DataFrame supplied to Stage 1; returning with link columns added.")
        out = (df.copy() if df is not None else pd.DataFrame())
        return _ensure_link_columns(out)

    # Validate required columns
    missing = [c for c in (COMPANY_NAME_COLUMN, COUNTRY_COLUMN) if c not in df.columns]
    if missing:
        raise ValueError(
            f"Input DataFrame is missing required columns: {missing}. "
            f"Configure COMPANY_NAME_COLUMN/COUNTRY_COLUMN in config.py or adjust your input."
        )

    api_key = api_key or SCALESERP_API_KEY
    if not api_key:
        raise ValueError(
            "No ScaleSERP API key provided. "
            "Pass `api_key=` to process_companies_for_reports or set SCALESERP_API_KEY in config.py / env."
        )

    out_df = df.copy()
    pdf_rows: List[list] = []
    page_rows: List[list] = []

    iterator = tqdm(out_df.itertuples(index=False), total=len(out_df),
                    disable=not show_progress, desc="Stage 1: primary search")

    for row in iterator:
        row_dict = row._asdict() if hasattr(row, "_asdict") else dict(zip(out_df.columns, row))
        company_name = (row_dict.get(COMPANY_NAME_COLUMN) or "").strip()
        company_country = (row_dict.get(COUNTRY_COLUMN) or "").strip()

        if not company_name:
            logger.warning("Skipping row with missing company name.")
            pdf_rows.append([None] * MAX_PDF_RESULTS)
            page_rows.append([None] * MAX_PAGE_RESULTS)
            continue

        try:
            pdf_links, page_links = find_report_links_categorized(
                company_name=company_name,
                api_key=api_key,
                company_country=company_country,
                max_pdf_results=MAX_PDF_RESULTS,
                max_page_results=MAX_PAGE_RESULTS,
                include_country_in_search=include_country_in_search,
            )

            # Defensive: ensure list type & cap
            pdf_links = list(pdf_links)[:MAX_PDF_RESULTS] if pdf_links else []
            page_links = list(page_links)[:MAX_PAGE_RESULTS] if page_links else []

        except Exception as e:
            logger.exception("Error fetching links for '%s' (%s): %s", company_name, company_country, e)
            pdf_links, page_links = [], []

        # Pad to fixed length
        pdf_rows.append(pdf_links + [None] * (MAX_PDF_RESULTS - len(pdf_links)))
        page_rows.append(page_links + [None] * (MAX_PAGE_RESULTS - len(page_links)))

    # assign columns
    pdf_cols = [f"pdf_link{i}" for i in range(1, MAX_PDF_RESULTS + 1)]
    page_cols = [f"page_link{i}" for i in range(1, MAX_PAGE_RESULTS + 1)]
    out_df[pdf_cols] = pd.DataFrame(pdf_rows, index=out_df.index)
    out_df[page_cols] = pd.DataFrame(page_rows, index=out_df.index)

    return out_df


def stage1_main(
    df_input: pd.DataFrame,
    scaleserp_api_key: Optional[str] = None,
    *,
    include_country_in_search: bool = True,
    show_progress: bool = True,
) -> pd.DataFrame:
    """
    Stage 1: run primary search and return a *clean* DataFrame of links.
    """
    logger.info("--- Stage 1: Finding Report Links (Primary Search) ---")

    df_with_links = process_companies_for_reports(
        df_input,
        api_key=scaleserp_api_key,
        include_country_in_search=include_country_in_search,
        show_progress=show_progress,
    )

    # ---- Clean up to avoid "blank every 2nd row" in CSV/Excel ----
    if not df_with_links.empty:
        # 1) Trim whitespace on all object (string) columns
        obj_cols = df_with_links.select_dtypes(include=["object"]).columns
        for c in obj_cols:
            df_with_links[c] = df_with_links[c].astype(str).str.strip()

        # 2) Turn pure-whitespace strings into real empties (NA)
        df_with_links.replace(to_replace=r"^\s*$", value=pd.NA, regex=True, inplace=True)

        # 3) Drop rows that are entirely empty
        df_with_links.dropna(how="all", inplace=True)

        # 4) Require Company & Country to be present
        if COMPANY_NAME_COLUMN in df_with_links and COUNTRY_COLUMN in df_with_links:
            df_with_links = df_with_links[
                df_with_links[COMPANY_NAME_COLUMN].notna() &
                df_with_links[COUNTRY_COLUMN].notna()
            ]

        # 5) Reset index so saving to CSV won’t introduce odd gaps
        df_with_links.reset_index(drop=True, inplace=True)

    logger.info("--- Stage 1: Finished; %d rows processed ---", len(df_with_links))
    return df_with_links