# search.py
from __future__ import annotations

import datetime as _dt
import logging
import re
from typing import Iterable, Tuple, List, Dict, Any

import pandas as pd
import requests
from urllib.parse import urlparse

from .config import (
    MAX_PDF_RESULTS,
    MAX_PAGE_RESULTS,
    SEARCH_LANGUAGE_PRIORITY,
    STAGE1_SCORING_ENABLED,
    COMPANY_NAME_COLUMN,
    COUNTRY_COLUMN,
)

logger = logging.getLogger(__name__)


# --- helpers -----------------------------------------------------------------

_CORE_TERMS = {
    "en": "Annual report OR Integrated report OR CSR report OR ESG report OR Impact report OR CDP report",
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
    "germany": "de", "austria": "de", "ger": "de", "aut": "de",
    "france": "fr", "belgium": "fr", "fra": "fr", "bel": "fr",
    "spain": "es", "mexico": "es", "colombia": "es", "argentina": "es", "peru": "es",
    "venezuela": "es", "chile": "es", "ecuador": "es", "bolivia": "es", "cuba": "es",
    "dominican republic": "es", "guatemala": "es", "honduras": "es", "el salvador": "es",
    "nicaragua": "es", "costa rica": "es", "panama": "es", "puerto rico": "es",
    "ury": "es", "pry": "es", "esp": "es", "mex": "es", "col": "es", "arg": "es",
    "per": "es", "ven": "es", "chl": "es", "ecu": "es", "bol": "es", "cub": "es",
    "dom": "es", "gtm": "es", "hnd": "es", "slv": "es", "nic": "es", "cri": "es",
    "pan": "es", "pri": "es",
    "russia": "ru", "rus": "ru",
    "china": "zh", "cn": "zh", "chn": "zh",
    "taiwan": "zh", "twn": "zh",
    "hong kong": "zh", "hongkong": "zh", "hkg": "zh",
    "macau": "zh", "macao": "zh", "mac": "zh",
    "japan": "ja", "jpn": "ja",
}


def _lang_for_country(country: str | None) -> str:
    if not country:
        return "en"
    key = str(country).strip().lower()
    return _COUNTRY_TO_LANG.get(key, "en")


# --- core API call -----------------------------------------------------------

def perform_search(
    company_name: str,
    api_key: str,
    google_domain: str,
    core_terms: str,
    company_country: str | None,
    include_country_in_search: bool,
    num_results: int,
    file_type_filter: str | None,
) -> Dict[str, Any]:
    """
    Call ScaleSERP and return JSON dict ({} on failure).
    """
    base_url = "https://api.scaleserp.com/search"
    query_parts = [company_name]
    if company_country and include_country_in_search:
        query_parts.append(company_country)
    query_parts.append(core_terms)

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
        r = requests.get(base_url, params=params, timeout=25)
        r.raise_for_status()
        try:
            return r.json()
        except ValueError:
            logger.warning("ScaleSERP non-JSON response for %s: %s", company_name, r.text[:300])
            return {}
    except requests.RequestException as e:
        logger.error("ScaleSERP request failed for %s (%s): %s", company_name, google_domain, e)
        return {}


# --- search entrypoints ------------------------------------------------------

def find_report_links_categorized(
    company_name: str,
    api_key: str,
    company_country: str | None,
    max_pdf_results: int = MAX_PDF_RESULTS,
    max_page_results: int = MAX_PAGE_RESULTS,
    include_country_in_search: bool = True,
) -> Tuple[List[str], List[str]]:
    """
    Primary search pass for PDF and non-PDF links using language preference.
    Returns (pdf_links, page_links).
    """
    native_lang = _lang_for_country(company_country)

    if SEARCH_LANGUAGE_PRIORITY == "english":
        primary_lang = "en"
    else:
        primary_lang = native_lang

    google_domain = _GOOGLE_DOMAIN[primary_lang]
    core_terms = _CORE_TERMS[primary_lang]

    # PDFs
    pdf_json = perform_search(
        company_name, api_key, google_domain, core_terms,
        company_country, include_country_in_search, max_pdf_results, "filetype:pdf"
    )
    if STAGE1_SCORING_ENABLED:
        pdf_links = score_results(pdf_json, company_name, company_country)
    else:
        pdf_links = [r.get("link") for r in pdf_json.get("organic_results", []) if r.get("link")]

    # Pages (non-PDF)
    page_json = perform_search(
        company_name, api_key, google_domain, core_terms,
        company_country, include_country_in_search, max_page_results, "-filetype:pdf"
    )
    if STAGE1_SCORING_ENABLED:
        page_links = score_results(page_json, company_name, company_country)
    else:
        page_links = [r.get("link") for r in page_json.get("organic_results", []) if r.get("link")]

    return pdf_links[:max_pdf_results], page_links[:max_page_results]


def find_report_links_fallback(
    company_name: str,
    api_key: str,
    company_country: str | None,
    max_pdf_results: int = MAX_PDF_RESULTS,
    max_page_results: int = MAX_PAGE_RESULTS,
    include_country_in_search: bool = True,
) -> Tuple[List[str], List[str]]:
    """
    Fallback search: flip the language preference (if primary was EN, use native; else EN).
    """
    native_lang = _lang_for_country(company_country)

    if SEARCH_LANGUAGE_PRIORITY == "english":
        fallback_lang = native_lang
    else:
        fallback_lang = "en"

    google_domain = _GOOGLE_DOMAIN[fallback_lang]
    core_terms = _CORE_TERMS[fallback_lang]

    # PDFs
    pdf_json = perform_search(
        company_name, api_key, google_domain, core_terms,
        company_country, include_country_in_search, max_pdf_results, "filetype:pdf"
    )
    pdf_links = (
        score_results(pdf_json, company_name, company_country)
        if STAGE1_SCORING_ENABLED else
        [r.get("link") for r in pdf_json.get("organic_results", []) if r.get("link")]
    )

    # Pages
    page_json = perform_search(
        company_name, api_key, google_domain, core_terms,
        company_country, include_country_in_search, max_page_results, "-filetype:pdf"
    )
    page_links = (
        score_results(page_json, company_name, company_country)
        if STAGE1_SCORING_ENABLED else
        [r.get("link") for r in page_json.get("organic_results", []) if r.get("link")]
    )

    return pdf_links[:max_pdf_results], page_links[:max_page_results]


def process_companies_for_fallback_reports(
    df: pd.DataFrame,
    scaleserp_api_key: str,
    max_pdf_results: int = MAX_PDF_RESULTS,
    max_page_results: int = MAX_PAGE_RESULTS,
    include_country_in_search: bool = True,
) -> pd.DataFrame:
    """
    For each company in df, run the fallback search and attach columns
    pdf_link1..N, page_link1..N with the results.
    """
    from tqdm import tqdm

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
            company_name = (row.get(COMPANY_NAME_COLUMN) or "").strip()
            company_country = (row.get(COUNTRY_COLUMN) or "").strip()

            try:
                pdf_links, page_links = find_report_links_fallback(
                    company_name=company_name,
                    api_key=scaleserp_api_key,
                    company_country=company_country,
                    max_pdf_results=max_pdf_results,
                    max_page_results=max_page_results,
                    include_country_in_search=include_country_in_search,
                )
                pdf_links = list(pdf_links)[:max_pdf_results] if pdf_links else []
                page_links = list(page_links)[:max_page_results] if page_links else []
            except Exception as e:
                logger.warning("[Fallback] Error for '%s': %s", company_name, e)
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

def score_results(results: Dict[str, Any], company_name: str, company_country: str | None) -> List[str]:
    """
    Score ScaleSERP organic results and return link list sorted by score (desc).
    """
    links_scored: List[tuple[str, int]] = []
    current_year = _dt.datetime.now().year
    company_name_lower = company_name.lower()
    company_name_for_matching = re.sub(r'\b(?:inc|llc|ltd|gmbh|sa|nv|ag|pl)(\.?)\b', '', company_name_lower).strip()

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
            if cy in text_to_score and any(t in text_to_score for t in ["sustainability", "esg", "corporate responsibility"]):
                total += 100
            elif py in text_to_score and any(t in text_to_score for t in ["sustainability", "esg", "corporate responsibility"]):
                total += 90
            elif cy in text_to_score and any(t in text_to_score for t in ["integrated", "annual report"]):
                total += 80
            elif py in text_to_score and any(t in text_to_score for t in ["integrated", "annual report"]):
                total += 70

            keywords = [
                "report", "publication", "impact", "climate",
                "bericht", "veröffentlichung", "auswirkung", "klima",
                "rapport", "publication", "impact", "climat",
                "informe", "publicación", "impacto", "clima",
                "отчет", "публикация", "влияние", "климат",
            ]
            total += sum(1 for kw in keywords if kw in text_to_score)

            # country signals
            if company_country:
                cc = str(company_country).lower()
                if cc in (link or "").lower():
                    total += 3
                if cc in title:
                    total += 2
                if cc in snippet:
                    total += 1

        links_scored.append((link, total))

    links_scored.sort(key=lambda x: x[1], reverse=True)
    return [link for link, _ in links_scored]
