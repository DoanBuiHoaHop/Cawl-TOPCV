# scrape_topcv_company.py  (phiên bản chống 429)
import time
import re
import random
from typing import Dict, List, Optional
from urllib.parse import urljoin, urlparse

import requests
from bs4 import BeautifulSoup
import pandas as pd
from urllib3.util.retry import Retry
from requests.adapters import HTTPAdapter

BASE = "https://www.topcv.vn"
HEADERS = {
    # giữ UA thật; có thể xoay vòng nếu cần
    "User-Agent": ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                   "AppleWebKit/537.36 (KHTML, like Gecko) "
                   "Chrome/123.0.0.0 Safari/537.36"),
    "Accept-Language": "vi-VN,vi;q=0.9,en-US;q=0.8",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Referer": "https://www.topcv.vn/",
    "Connection": "keep-alive",
}

def build_session() -> requests.Session:
    s = requests.Session()
    s.headers.update(HEADERS)

    # Retry cho lỗi tạm thời và 429
    retry = Retry(
        total=6,
        connect=3,
        read=3,
        status=6,
        backoff_factor=1.2,               # backoff cơ bản
        status_forcelist=(429, 500, 502, 503, 504),
        allowed_methods=frozenset(["GET", "HEAD"]),
        raise_on_status=False,
        respect_retry_after_header=True,  # tôn trọng Retry-After
    )
    adapter = HTTPAdapter(max_retries=retry, pool_connections=20, pool_maxsize=50)
    s.mount("https://", adapter)
    s.mount("http://", adapter)

    # pre-warm cookie (nhiều site set cookie/anti-bot ở trang chủ)
    try:
        s.get(BASE, timeout=20)
        time.sleep(1.0)
    except requests.RequestException:
        pass
    return s

def text(el) -> Optional[str]:
    if not el:
        return None
    t = el.get_text(" ", strip=True)
    return re.sub(r"\s+", " ", t) if t else None

def smart_sleep(min_s=1.2, max_s=2.8):
    # nghỉ ngẫu nhiên để “giống người”
    time.sleep(random.uniform(min_s, max_s))

def get_soup(session: requests.Session, url: str) -> BeautifulSoup:
    # vòng lặp thủ công để xử lý 429 với jitter bổ sung
    for attempt in range(1, 6):
        r = session.get(url, timeout=30)
        if r.status_code == 429:
            retry_after = r.headers.get("Retry-After")
            if retry_after:
                try:
                    wait = int(retry_after)
                except ValueError:
                    wait = 6 * attempt
            else:
                wait = 6 * attempt
            # jitter
            wait = wait + random.uniform(0.5, 2.0)
            print(f"[WARN] 429 tại {url} → ngủ {wait:.1f}s (attempt {attempt})")
            time.sleep(wait)
            continue
        r.raise_for_status()
        return BeautifulSoup(r.text, "lxml")
    # lần cuối: raise
    r.raise_for_status()
    return BeautifulSoup("", "lxml")

# ------------ Search page ------------
def parse_search_page(session: requests.Session, url: str) -> List[Dict]:
    soup = get_soup(session, url)
    jobs = []
    for job in soup.select("div.job-item-search-result"):
        a_title = job.select_one("h3.title a[href]")
        if not a_title:
            continue
        title = text(a_title)
        job_url = urljoin(BASE, a_title.get("href"))

        comp_a = job.select_one("a.company[href]")
        company = text(job.select_one("a.company .company-name"))
        company_url = urljoin(BASE, comp_a.get("href")) if comp_a else None

        salary = text(job.select_one("label.title-salary"))
        address = text(job.select_one("label.address .city-text"))
        exp = text(job.select_one("label.exp span"))

        jobs.append({
            "title": title,
            "job_url": job_url,
            "company": company,
            "company_url": company_url,
            "salary_list": salary,
            "address_list": address,
            "exp_list": exp,
        })
    return jobs

# ------------ Job detail page ------------
def pick_info_value(soup: BeautifulSoup, title: str) -> Optional[str]:
    for sec in soup.select(".job-detail__info--section"):
        t = text(sec.select_one(".job-detail__info--section-content-title")) or ""
        if t.lower() == title.lower():
            v = sec.select_one(".job-detail__info--section-content-value")
            return text(v) if v else text(sec)
    return None

def extract_deadline(soup: BeautifulSoup) -> Optional[str]:
    for el in soup.select(".job-detail__info--deadline, .job-detail__information-detail--actions-label"):
        t = text(el)
        if t and "Hạn nộp" in t:
            m = re.search(r"(\d{1,2}/\d{1,2}/\d{4})", t)
            return m.group(1) if m else t
    return None

def extract_tags(soup: BeautifulSoup):
    return [text(a) for a in soup.select(".job-tags a.item") if text(a)]

def extract_desc_blocks(soup: BeautifulSoup):
    data = {}
    for item in soup.select(".job-description .job-description__item"):
        h3 = text(item.select_one("h3")) or ""
        content = item.select_one(".job-description__item--content")
        if content:
            data[h3] = text(content)
    return data

def extract_working_addresses(soup: BeautifulSoup):
    out = []
    for item in soup.select(".job-description__item h3"):
        if "Địa điểm làm việc" in (text(item) or ""):
            wrap = item.find_parent(class_="job-description__item")
            if wrap:
                for d in wrap.select(".job-description__item--content div, .job-description__item--content li"):
                    val = text(d)
                    if val:
                        out.append(val)
    return out

def extract_working_times(soup: BeautifulSoup):
    out = []
    for item in soup.select(".job-description__item h3"):
        if "Thời gian làm việc" in (text(item) or ""):
            wrap = item.find_parent(class_="job-description__item")
            if wrap:
                for d in wrap.select(".job-description__item--content div, .job-description__item--content li"):
                    val = text(d)
                    if val:
                        out.append(val)
    return out

def extract_company_link_from_job(soup: BeautifulSoup) -> Optional[str]:
    cand = soup.select_one("a.company[href]") or soup.select_one("a[href*='/cong-ty/']")
    return urljoin(BASE, cand["href"]) if cand and cand.has_attr("href") else None

def scrape_job_detail(session: requests.Session, job_url: str) -> Dict:
    soup = get_soup(session, job_url)
    smart_sleep()  # nghỉ nhẹ giữa các trang

    title = text(soup.select_one(".job-detail__info--title, h1"))
    salary = pick_info_value(soup, "Mức lương")
    location = pick_info_value(soup, "Địa điểm")
    experience = pick_info_value(soup, "Kinh nghiệm")
    deadline = extract_deadline(soup)
    tags = extract_tags(soup)
    desc_blocks = extract_desc_blocks(soup)
    addrs = extract_working_addresses(soup)
    times = extract_working_times(soup)
    company_url_detail = extract_company_link_from_job(soup)

    return {
        "detail_title": title,
        "detail_salary": salary,
        "detail_location": location,
        "detail_experience": experience,
        "deadline": deadline,
        "tags": "; ".join(tags) if tags else None,
        "desc_mota": desc_blocks.get("Mô tả công việc"),
        "desc_yeucau": desc_blocks.get("Yêu cầu ứng viên"),
        "desc_quyenloi": desc_blocks.get("Quyền lợi"),
        "working_addresses": "; ".join(addrs) if addrs else None,
        "working_times": "; ".join(times) if times else None,
        "company_url_from_job": company_url_detail,
    }

# ------------ Company page ------------
def scrape_company(session: requests.Session, company_url: Optional[str]) -> Dict:
    if not company_url:
        return {
            "company_name_full": None,
            "company_website": None,
            "company_size": None,
            "company_industry": None,
            "company_address": None,
            "company_description": None,
        }
    soup = get_soup(session, company_url)
    smart_sleep()

    # name
    company_name = None
    for css in ["h1.company-name", "h1.title", "div.company-header h1", "div.company-info h1",
                "meta[property='og:title']", "meta[property='og:site_name']", "title"]:
        el = soup.select_one(css)
        if el:
            company_name = el.get("content") if el.name == "meta" else text(el)
            if company_name:
                company_name = re.sub(r"\s*\|\s*TopCV.*$", "", company_name, flags=re.I)
                break

    website = size = industry = address = None
    containers = [
        "div.company-overview", "div.company-detail", "div.company-profile",
        "section#company", "section.company-info", "div.box-intro-company",
        "div.company-info-container"
    ]
    container = None
    for css in containers:
        c = soup.select_one(css)
        if c:
            container = c
            break
    if container is None:
        container = soup

    rows = container.select("li, .row, .item, .info-item, .company-info-item, .dl, .d-flex")
    for row in rows:
        row_text = text(row) or ""
        label = None
        value = None
        strong = row.find(["strong", "b"])
        if strong:
            label = text(strong)
            value = row_text
            if label:
                value = re.sub(re.escape(label), "", value, flags=re.I).strip(" :-–—")
        else:
            m = re.match(r"^([^:：]+)[:：]\s*(.+)$", row_text)
            if m:
                label, value = m.group(1).strip(), m.group(2).strip()

        if not label or not value:
            continue

        ln = re.sub(r"\s+", " ", label.lower())
        if "website" in ln or "trang web" in ln:
            website = value
        elif "quy mô" in ln or "size" in ln or "nhân sự" in ln:
            size = value
        elif "lĩnh vực" in ln or "industry" in ln or "ngành" in ln:
            industry = value
        elif "địa chỉ" in ln or "address" in ln:
            address = value

    description = None
    for css in [
        "div.company-description", "div#company-description", "div.box-intro-company",
        "div.company-introduction", "div.description", "section.company-description",
        "div#readmore-company", "div#readmore-content"
    ]:
        el = soup.select_one(css)
        if el:
            description = text(el)
            if description:
                break

    return {
        "company_name_full": company_name,
        "company_website": website,
        "company_size": size,
        "company_industry": industry,
        "company_address": address,
        "company_description": description,
    }

# ------------ Pipeline ------------
def crawl_to_dataframe(query_url_template: str, start_page: int = 1, end_page: int = 1,
                       delay_between_pages=(0.5 , 1)) -> pd.DataFrame:
    rows: List[Dict] = []
    seen_jobs = set()

    s = build_session()

    for page in range(start_page, end_page + 1):
        url = query_url_template.format(page=page)
        print(f"[INFO] Crawling search page {page}: {url}")
        jobs = parse_search_page(s, url)

        if not jobs:
            print(f"[INFO] Trang {page} không còn job — dừng sớm.")
            break

        for j in jobs:
            job_url = j["job_url"]
            job_id = urlparse(job_url).path
            if job_id in seen_jobs:
                continue
            seen_jobs.add(job_id)

            # chi tiết job
            try:
                detail = scrape_job_detail(s, job_url)
            except Exception as e:
                print(f"[WARN] Lỗi job detail {job_url}: {e}")
                detail = {k: None for k in [
                    "detail_title", "detail_salary", "detail_location",
                    "detail_experience", "deadline", "tags", "desc_mota",
                    "desc_yeucau", "desc_quyenloi", "working_addresses",
                    "working_times", "company_url_from_job"
                ]}

            company_url = detail.get("company_url_from_job") or j.get("company_url")

            # chi tiết công ty
            try:
                comp = scrape_company(s, company_url)
            except Exception as e:
                print(f"[WARN] Lỗi company {company_url}: {e}")
                comp = {k: None for k in [
                    "company_name_full", "company_website", "company_size",
                    "company_industry", "company_address", "company_description"
                ]}

            row = {**j, **detail, **comp}
            rows.append(row)

        # nghỉ giữa các trang (random)
        smart_sleep(*delay_between_pages)

    df = pd.DataFrame(rows)
    # sắp xếp cột
    cols = [
        "title", "detail_title",
        "job_url",
        "company", "company_name_full",
        "company_url", "company_url_from_job",
        "salary_list", "detail_salary",
        "address_list", "detail_location",
        "exp_list", "detail_experience",
        "deadline", "tags",
        "working_addresses", "working_times",
        "desc_mota", "desc_yeucau", "desc_quyenloi",
        "company_website", "company_size", "company_industry",
        "company_address", "company_description",
    ]
    cols = [c for c in cols if c in df.columns]
    return df.loc[:, cols] if cols else df

if __name__ == "__main__":
    qtpl = "https://www.topcv.vn/tim-viec-lam-data-analyst?type_keyword=1&page={page}&sba=1"
    df = crawl_to_dataframe(qtpl, start_page=1, end_page=1, delay_between_pages=(0.5, 1)) # thay end_page=5 nếu muốn nhiều trang hơn (5 trang)
    print(df.head())
    df.to_csv("../data-files/topcv_data_analyst_jobs.csv", index=False, encoding="utf-8-sig")
    print("Saved CSV: topcv_data_analyst_jobs.csv")

    df.to_excel("../data-files/topcv_data_analyst_jobs.xlsx")
    print("Saved Excel: topcv_data_analyst_jobs.xlsx")