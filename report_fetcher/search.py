# Whats new: Implementing search caching for some time to reduce api calls when testing.

# search.py
from __future__ import annotations

import datetime as _dt
import json
import logging
import math
import re
from pprint import pprint
from typing import Any, Dict, Iterable, List, Optional, Tuple
from urllib.parse import urlparse

import pandas as pd
import requests
from ddgs import DDGS
from tqdm import tqdm

from .config import (  # COMPANY_NAME_COLUMN,; COUNTRY_COLUMN,
    ASSOCIATION_NAME_COLUMN,
    CITY_NAME_COLUMN,
    COLUMNS,
    COUNTRY_NAME_COLUMN,
    MAX_PAGE_RESULTS,
    MAX_PDF_RESULTS,
    REGION_NAME_COLUMN,
    SCALESERP_API_KEY,
    SEARCH_CACHE_FILE,
    SEARCH_LANGUAGE_PRIORITY,
    STAGE1_SCORING_ENABLED,
    SUBREGION_NAME_COLUMN,
    USE_DDGS,
)

# for column in COLUMNS:
#     # from .config __import__(column)

logger = logging.getLogger(__name__)


# --- helpers -----------------------------------------------------------------

_CORE_TERMS = {
    # "en": "Annual report OR Integrated report OR CSR report OR ESG report OR Impact report OR CDP report",
    "en": "Annual report",
    "de": "Jahresbericht OR Integrierter Bericht OR CSR-Bericht OR ESG-Bericht OR Impact-Bericht OR CDP-Bericht",
    "fr": "rapport annuel OR rapport intégré OR rapport RSE OR rapport ESG OR rapport d'impact OR rapport CDP",
    "es": "informe anual OR informe integrado OR informe de RSE OR informe ESG OR informe de impacto OR informe CDP",
    "ru": "годовой отчет OR интегрированный отчет OR отчет о корпоративной социальной ответственности OR ESG отчет OR отчет о воздействии OR отчет CDP",
    "zh": "年度报告 OR 年報 OR 综合报告 OR 綜合報告 OR 整合报告 OR 整合報告 OR 企业社会责任报告 OR 企業社會責任報告 OR ESG报告 OR ESG報告 OR 影响报告 OR 影響報告 OR CDP报告 OR CDP報告",
    "ja": "有価証券報告書 OR 年次報告書 OR アニュアルレポート OR 統合報告書 OR CSR報告書 OR サステナビリティレポート OR ESGレポート OR インパクトレポート OR CDP 回答 OR CDP レポート",
}

_GOOGLE_DOMAIN = {
    "en": "google.com",
    "de": "google.de",
    "fr": "google.fr",
    "es": "google.es",
    "ru": "google.ru",
    "zh": "google.com.hk",
    "ja": "google.co.jp",
}

_COUNTRY_TO_LANG = {
    "germany": "de",
    "austria": "de",
    "ger": "de",
    "aut": "de",
    "france": "fr",
    "belgium": "fr",
    "fra": "fr",
    "bel": "fr",
    "spain": "es",
    "mexico": "es",
    "colombia": "es",
    "argentina": "es",
    "peru": "es",
    "venezuela": "es",
    "chile": "es",
    "ecuador": "es",
    "bolivia": "es",
    "cuba": "es",
    "dominican republic": "es",
    "guatemala": "es",
    "honduras": "es",
    "el salvador": "es",
    "nicaragua": "es",
    "costa rica": "es",
    "panama": "es",
    "puerto rico": "es",
    "ury": "es",
    "pry": "es",
    "esp": "es",
    "mex": "es",
    "col": "es",
    "arg": "es",
    "per": "es",
    "ven": "es",
    "chl": "es",
    "ecu": "es",
    "bol": "es",
    "cub": "es",
    "dom": "es",
    "gtm": "es",
    "hnd": "es",
    "slv": "es",
    "nic": "es",
    "cri": "es",
    "pan": "es",
    "pri": "es",
    "russia": "ru",
    "rus": "ru",
    "china": "zh",
    "cn": "zh",
    "chn": "zh",
    "taiwan": "zh",
    "twn": "zh",
    "hong kong": "zh",
    "hongkong": "zh",
    "hkg": "zh",
    "macau": "zh",
    "macao": "zh",
    "mac": "zh",
    "japan": "ja",
    "jpn": "ja",
}


def _lang_for_country(country: str | None) -> str:
    if not country:
        return "en"
    key = str(country).strip().lower()
    return _COUNTRY_TO_LANG.get(key, "en")


# --- core API call -----------------------------------------------------------


def perform_search(
    city_name: str | None, # only used in logs
    api_key: str,
    google_domain: str,
    core_terms: str,
    city_country: str | None, # only used in logs.
    include_country_in_search: bool,
    num_results: int,
    file_type_filter: str | None,
    search_dict: dict,
) -> Dict[str, Any]:
    """
    Call ScaleSERP and return JSON dict ({} on failure).
    """
    use_ddgs = USE_DDGS
    # use_ddgs = not use_ddgs
    base_url = "https://api.scaleserp.com/search"
    # query_parts = [company_name]
    # if company_country and include_country_in_search:
    #     query_parts.append(company_country)
    # query_parts.append(core_terms)

    query_parts = []

    for key in search_dict.keys():
        val = search_dict.get(key)
        # print(type(val), val)
        if val and str(val).strip() != "":
            query_parts.append(str(val))
    # input()

    query = " ".join(query_parts)
    if file_type_filter:
        query += f" {file_type_filter}"

    params = {
        "api_key": api_key,
        "q": query,
        "google_domain": google_domain,
        "num": num_results,
    }
    try:
        with open(SEARCH_CACHE_FILE, "r+", encoding="utf-8") as search_cache_file_text:
            search_cache = json.load(search_cache_file_text)
    except:
        search_cache = dict()

    if use_ddgs:
        try:
            request_string = str([base_url, params, "ddgs"])
            stored_search_json = search_cache.get(request_string, None)
            if stored_search_json:
                logger.info("Using cached results")
                return stored_search_json
            # r = requests.get(base_url, params=params, timeout=25)
            # r.raise_for_status()
            try:
                results = DDGS().text(
                    query,
                    region="wt-wt",
                    safesearch="off",
                    timelimit="y",
                    max_results=num_results,
                )

                resp_json = dict()
                resp_json["organic_results"] = []
                for result in results:
                    resp_json["organic_results"].append(
                        {
                            "link": result.get("href"),
                            "title": result.get("title"),
                            "snippet": result.get("body"),
                        }
                    )

                search_cache[request_string] = resp_json
                with open(SEARCH_CACHE_FILE, "w+", encoding="utf-8") as search_cache_file_text:
                    json.dump(search_cache, search_cache_file_text)
                return result
            except ValueError:
                logger.warning("DDG non-JSON response for %s: %s", city_name, str(result))
                return {}
        except requests.RequestException as e:
            logger.error("DDG request failed for %s (%s): %s", city_name, google_domain, e)
            return {}
    else:
        try:
            request_string = str([base_url, params, "google"])
            stored_search_json = search_cache.get(request_string, None)
            if stored_search_json:
                logger.info("Using cached results")
                return stored_search_json
            r = requests.get(base_url, params=params, timeout=25)
            r.raise_for_status()
            try:
                resp_json = r.json()
                search_cache[request_string] = resp_json
                with open(SEARCH_CACHE_FILE, "w+", encoding="utf-8") as search_cache_file_text:
                    json.dump(search_cache, search_cache_file_text)
                return r.json()
            except ValueError:
                logger.warning("ScaleSERP non-JSON response for %s: %s", city_name, r.text[:300])
                return {}
        except requests.RequestException as e:
            logger.error("ScaleSERP request failed for %s (%s): %s", city_name, google_domain, e)
            return {}


# --- search entrypoints ------------------------------------------------------


def find_report_links_categorized(
    city_name: str,
    api_key: str,
    search_dict: dict,
    city_country: str | None,
    max_pdf_results: int = MAX_PDF_RESULTS,
    max_page_results: int = MAX_PAGE_RESULTS,
    include_country_in_search: bool = True,
) -> Tuple[List[str], List[str]]:
    """
    Primary search pass for PDF and non-PDF links using language preference.
    Returns (pdf_links, page_links).
    """
    # Find an equivalent?
    native_lang = _lang_for_country(city_country)

    if SEARCH_LANGUAGE_PRIORITY == "english":
        primary_lang = "en"
    else:
        primary_lang = native_lang

    google_domain = _GOOGLE_DOMAIN[primary_lang]
    core_terms = _CORE_TERMS[primary_lang]

    # PDFs
    pdf_json = perform_search(
        city_name,
        api_key,
        google_domain,
        core_terms,
        city_country,
        include_country_in_search,
        max_pdf_results,
        "filetype:pdf",
        search_dict,
    )
    if STAGE1_SCORING_ENABLED:
        pdf_links = score_results(pdf_json, city_name, city_country)
    else:
        pdf_links = [r.get("link") for r in pdf_json.get("organic_results", []) if r.get("link")]

    # Pages (non-PDF)
    page_json = perform_search(
        city_name,
        api_key,
        google_domain,
        core_terms,
        city_country,
        include_country_in_search,
        max_page_results,
        "-filetype:pdf",
        search_dict,
    )
    if STAGE1_SCORING_ENABLED:
        page_links = score_results(page_json, city_name, city_country)
    else:
        page_links = [r.get("link") for r in page_json.get("organic_results", []) if r.get("link")]

    return pdf_links[:max_pdf_results], page_links[:max_page_results]


def find_report_links_fallback(
    company_name: str,
    api_key: str,
    search_dict: dict,
    company_country: str | None,
    max_pdf_results: int = MAX_PDF_RESULTS,
    max_page_results: int = MAX_PAGE_RESULTS,
    include_country_in_search: bool = True,
) -> Tuple[List[str], List[str]]:
    """
    Fallback search: flip the language preference (if primary was EN, use native; else EN).
    """
    # Need to find an equivalent way to judge language by country, without knowing the column.
    native_lang = _lang_for_country(company_country)

    if SEARCH_LANGUAGE_PRIORITY == "english":
        fallback_lang = native_lang
    else:
        fallback_lang = "en"

    google_domain = _GOOGLE_DOMAIN[fallback_lang]
    core_terms = _CORE_TERMS[fallback_lang]

    # PDFs
    pdf_json = perform_search(
        company_name,
        api_key,
        google_domain,
        core_terms,
        company_country,
        include_country_in_search,
        max_pdf_results,
        "filetype:pdf",
        search_dict,
    )
    pdf_links = (
        score_results(pdf_json, company_name, company_country)
        if STAGE1_SCORING_ENABLED
        else [r.get("link") for r in pdf_json.get("organic_results", []) if r.get("link")]
    )

    # Pages
    page_json = perform_search(
        company_name,
        api_key,
        google_domain,
        core_terms,
        company_country,
        include_country_in_search,
        max_page_results,
        "-filetype:pdf",
        search_dict,
    )
    page_links = (
        score_results(page_json, company_name, company_country)
        if STAGE1_SCORING_ENABLED
        else [r.get("link") for r in page_json.get("organic_results", []) if r.get("link")]
    )

    return pdf_links[:max_pdf_results], page_links[:max_page_results]


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
        out = df.copy() if df is not None else pd.DataFrame()
        return _ensure_link_columns(out)

    # Validate required columns
    # missing = [c for c in (COMPANY_NAME_COLUMN, COUNTRY_COLUMN) if c not in df.columns]
    missing = [c for c in COLUMNS if c not in df.columns]
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

    iterator = tqdm(
        out_df.itertuples(index=False),
        total=len(out_df),
        disable=not show_progress,
        desc="Stage 1: primary search",
    )

    for row in iterator:
        row_dict = row._asdict() if hasattr(row, "_asdict") else dict(zip(out_df.columns, row))
        row_dict = {k: v for k, v in row_dict.items() if not (v is None or (isinstance(v, float) and math.isnan(v)))}

        # print(row_dict)
        # print()
        # print(row_dict)
        # # input()
        # exit()
        city_name = (row_dict.get(CITY_NAME_COLUMN) or "").strip()
        province_name = (row_dict.get(REGION_NAME_COLUMN) or "").strip()
        country_name = (row_dict.get(COUNTRY_NAME_COLUMN) or "").strip()

        if not city_name:
            logger.warning("Skipping row with missing city name.")
            pdf_rows.append([None] * MAX_PDF_RESULTS)
            page_rows.append([None] * MAX_PAGE_RESULTS)
            continue

        try:
            pdf_links, page_links = find_report_links_categorized(
                city_name=city_name,
                api_key=api_key,
                city_country=country_name,
                max_pdf_results=MAX_PDF_RESULTS,
                max_page_results=MAX_PAGE_RESULTS,
                include_country_in_search=include_country_in_search,
                search_dict=row_dict,
            )

            # Defensive: ensure list type & cap
            pdf_links = list(pdf_links)[:MAX_PDF_RESULTS] if pdf_links else []
            page_links = list(page_links)[:MAX_PAGE_RESULTS] if page_links else []

        except Exception as e:
            logger.exception("Error fetching links for '%s' (%s): %s", city_name, country_name, e)
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


def process_companies_for_fallback_reports(
    df: pd.DataFrame,
    scaleserp_api_key: str,
    # search_dict: dict,
    max_pdf_results: int = MAX_PDF_RESULTS,
    max_page_results: int = MAX_PAGE_RESULTS,
    include_country_in_search: bool = True,
) -> pd.DataFrame:
    """
    For each company in df, run the fallback search and attach columns
    pdf_link1..N, page_link1..N with the results.
    """

    if df is None or df.empty:
        out = df.copy()
        for i in range(1, max_pdf_results + 1):
            out[f"pdf_link{i}"] = None
        for i in range(1, max_page_results + 1):
            out[f"page_link{i}"] = None
        return out

    all_pdf_rows: List[List[str | None]] = []
    all_page_rows: List[List[str | None]] = []

    out_df = df.copy()

    with tqdm(total=out_df.shape[0], desc="Fallback: finding report links") as pbar:
        for _, row in out_df.iterrows():
            search_dict = (
                row._asdict() if hasattr(row, "_asdict") else dict(zip(out_df.columns, row))
            )
            search_dict = {key: search_dict[key] for key in COLUMNS}
            # pprint(search_dict)
            # input()

            city_name = (row.get(CITY_NAME_COLUMN) or "").strip()
            company_country = (row.get(COUNTRY_NAME_COLUMN) or "").strip()

            try:
                pdf_links, page_links = find_report_links_fallback(
                    company_name=city_name,
                    api_key=scaleserp_api_key,
                    company_country=company_country,
                    max_pdf_results=max_pdf_results,
                    max_page_results=max_page_results,
                    include_country_in_search=include_country_in_search,
                    search_dict=search_dict,
                )
                pdf_links = list(pdf_links)[:max_pdf_results] if pdf_links else []
                page_links = list(page_links)[:max_page_results] if page_links else []
            except Exception as e:
                logger.warning("[Fallback] Error for '%s': %s", city_name, e)
                pdf_links, page_links = [], []

            # pad to fixed length
            all_pdf_rows.append(pdf_links + [None] * (max_pdf_results - len(pdf_links)))
            all_page_rows.append(page_links + [None] * (max_page_results - len(page_links)))

            pbar.update(1)

    pdf_cols = [f"pdf_link{i}" for i in range(1, max_pdf_results + 1)]
    page_cols = [f"page_link{i}" for i in range(1, max_page_results + 1)]

    out_df[pdf_cols] = pd.DataFrame(all_pdf_rows, index=out_df.index)
    out_df[page_cols] = pd.DataFrame(all_page_rows, index=out_df.index)
    return out_df


# --- scoring -----------------------------------------------------------------


def score_results(
    results: Dict[str, Any], city_name: str, city_country: str | None
) -> List[str]:
    """
    Score ScaleSERP organic results and return link list sorted by score (desc).
    """
    links_scored: List[tuple[str, int]] = []
    current_year = _dt.datetime.now().year
    company_name_lower = city_name.lower()
    company_name_for_matching = re.sub(
        r"\b(?:inc|llc|ltd|gmbh|sa|nv|ag|pl)(\.?)\b", "", company_name_lower
    ).strip()

    if not isinstance(results, dict):
        return []

    for result in results.get("organic_results", []) or []:
        link = result.get("link")
        if not link:
            continue

        title = (result.get("title") or "").lower()
        snippet = (result.get("snippet") or "").lower()
        text_to_score = f"{(link or '').lower()} {title} {snippet}"
        total = 0
        domain = urlparse(link).netloc.replace("www.", "").lower()

        # strong company match
        if company_name_for_matching and company_name_for_matching in domain:
            total += 500
        elif company_name_lower and (company_name_lower in title or company_name_lower in snippet):
            total += 400

        # recency + keywords (only if strong match not already applied)
        if total < 400:
            cy = str(current_year)
            py = str(current_year - 1)
            if cy in text_to_score and any(
                t in text_to_score for t in ["sustainability", "esg", "corporate responsibility"]
            ):
                total += 100
            elif py in text_to_score and any(
                t in text_to_score for t in ["sustainability", "esg", "corporate responsibility"]
            ):
                total += 90
            elif cy in text_to_score and any(
                t in text_to_score for t in ["integrated", "annual report"]
            ):
                total += 80
            elif py in text_to_score and any(
                t in text_to_score for t in ["integrated", "annual report"]
            ):
                total += 70

            keywords = [
                "report",
                "publication",
                "impact",
                "climate",
                "bericht",
                "veröffentlichung",
                "auswirkung",
                "klima",
                "rapport",
                "publication",
                "impact",
                "climat",
                "informe",
                "publicación",
                "impacto",
                "clima",
                "отчет",
                "публикация",
                "влияние",
                "климат",
            ]
            total += sum(1 for kw in keywords if kw in text_to_score)

            # country signals
            if city_country:
                cc = str(city_country).lower()
                if cc in (link or "").lower():
                    total += 3
                if cc in title:
                    total += 2
                if cc in snippet:
                    total += 1

        links_scored.append((link, total))

    links_scored.sort(key=lambda x: x[1], reverse=True)
    return [link for link, _ in links_scored]
