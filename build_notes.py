#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
생각 노트 — 글 한 편마다 진짜 HTML 페이지를 만들어 주는 빌더.

  python3 build_notes.py

posts.js(글) + books.js(책)를 읽어서 아래를 만든다.

  p/<슬러그>.html   글 한 편의 단독 페이지 (검색엔진이 읽는 실제 본문)
  p/style.css       글 페이지 공용 스타일
  slugs.js          글 id → 슬러그 표 (notes.html·index.html이 링크를 걸 때 쓴다)
  sitemap.xml       전체 주소 목록
  feed.xml          RSS

왜 이렇게까지 하냐면 — notes.html#아이디 방식은 주소가 하나뿐이라
검색엔진에 '페이지 1개'로만 잡힌다. 특히 네이버 크롤러는 자바스크립트를
거의 실행하지 않아서, 글 본문을 아예 못 본다. 그래서 글마다 주소를 주고
본문을 HTML로 미리 박아 둔다.
"""

import json
import os
import re
import shutil
import unicodedata
from datetime import datetime, timezone, timedelta
from html import escape
from urllib.parse import quote

ROOT = os.path.dirname(os.path.abspath(__file__))
SITE = "https://hellmeone.com"
OUT_DIR = os.path.join(ROOT, "p")
KST = timezone(timedelta(hours=9))


def post_url(slug):
    """주소에 한글을 쓰므로, 기계가 읽는 곳(canonical·사이트맵·RSS)에는 인코딩해서 넣는다."""
    return "%s/p/%s.html" % (SITE, quote(slug))


# ---------------------------------------------------------------- 데이터 읽기
def load_posts():
    """posts.js는 '한 줄 = 글 한 편(JSON)' 규칙이라 줄 단위로 읽으면 된다."""
    src = open(os.path.join(ROOT, "posts.js"), encoding="utf-8").read()
    posts = []
    for line in src.split("\n"):
        s = line.strip()
        if not s.startswith('{"id"'):
            continue
        posts.append(json.loads(s.rstrip(",")))
    return posts


def load_books():
    """books.js는 손으로 쓴 JS 리터럴이라 필요한 칸만 정규식으로 뽑는다."""
    src = open(os.path.join(ROOT, "books.js"), encoding="utf-8").read()
    books = {}
    for m in re.finditer(r"\{\s*title:\s*\"((?:[^\"\\]|\\.)*)\"(.*?)quotes:\s*\[", src, re.S):
        title = json.loads('"%s"' % m.group(1))
        blob = m.group(2)

        def field(name):
            f = re.search(r'\b%s:\s*"((?:[^"\\]|\\.)*)"' % name, blob)
            return json.loads('"%s"' % f.group(1)) if f else ""

        year = re.search(r"\bpublished:\s*(\d{4})", blob)
        rating = re.search(r"\brating:\s*(\d)", blob)
        books[title] = {
            "title": title,
            "author": field("author"),
            "publisher": field("publisher"),
            "published": year.group(1) if year else "",
            "cover": field("cover"),
            # 아래 셋은 Reference 모달에서만 쓴다
            "summary": field("summary"),
            "review": field("review"),
            "rating": int(rating.group(1)) if rating else 0,
        }
    return books


# ---------------------------------------------------------------- 슬러그(주소)
def slugify(title):
    """제목 → 주소에 쓸 이름. 한글은 그대로 살린다(한국어 검색에 유리)."""
    s = unicodedata.normalize("NFC", title).strip().lower()
    s = re.sub(r"[^0-9a-z가-힣ㄱ-ㅎㅏ-ㅣ\s-]", " ", s)
    s = re.sub(r"[\s_-]+", "-", s).strip("-")
    if len(s) > 60:
        s = s[:60].rstrip("-")
    return s or "note"


def load_slug_map():
    """한 번 준 주소는 제목을 고쳐도 바뀌지 않게 slugs.js에 남겨 둔다."""
    path = os.path.join(ROOT, "slugs.js")
    if not os.path.exists(path):
        return {}
    m = re.search(r"const SLUGS = (\{.*?\});", open(path, encoding="utf-8").read(), re.S)
    return json.loads(m.group(1)) if m else {}


def assign_slugs(posts):
    known = load_slug_map()
    slugs, taken = {}, set()
    # 이미 발행돼서 주소가 알려진 글부터 자리를 잡는다 (주소 불변 = SEO 생명줄)
    for p in posts:
        s = known.get(p["id"])
        if s and s not in taken:
            slugs[p["id"]], _ = s, taken.add(s)
    for p in posts:
        if p["id"] in slugs:
            continue
        base = slugify(p["title"])
        s, n = base, 2
        while s in taken:
            s, n = "%s-%d" % (base, n), n + 1
        slugs[p["id"]] = s
        taken.add(s)
    return slugs


# ---------------------------------------------------------------- 마크다운 렌더
def inline(text):
    """**강조**만 쓴다 — notes.html의 화면 렌더와 결과를 맞춘다."""
    out = escape(text, quote=False)
    return re.sub(r"\*\*([^*\n]+?)\*\*", r"<strong>\1</strong>", out)


def render(md):
    md = re.sub(r"^(#{1,4})(?=[^#\s])", r"\1 ", md, flags=re.M)          # "##제목"도 소제목으로
    md = re.sub(r"\*\*[ \t]*([^*\n]+?)[ \t]*\*\*", lambda m: "**%s**" % m.group(1), md)

    def mynote(m):                                                        # "// 내 생각" → 여백 메모
        inner = "<br>".join(
            inline(l.lstrip("/").strip()) for l in m.group(0).split("\n") if l.strip()
        )
        return "\n\n@@MYNOTE:%s@@\n\n" % inner if inner else "\n\n"

    md = re.sub(r"(?:^//[^\n]*(?:\n|$))+", mynote, md, flags=re.M)

    html, buf, mode = [], [], None

    def flush():
        nonlocal buf, mode
        if not buf:
            mode = None
            return
        if mode == "p":
            html.append("<p>%s</p>" % "\n".join(inline(l) for l in buf))
        elif mode == "quote":
            html.append("<blockquote><p>%s</p></blockquote>"
                        % "\n".join(inline(re.sub(r"^>\s?", "", l)) for l in buf))
        elif mode in ("ul", "ol"):
            items = "".join("<li>%s</li>" % inline(re.sub(r"^(?:[-*+]|\d+\.)\s+", "", l)) for l in buf)
            html.append("<%s>%s</%s>" % (mode, items, mode))
        buf, mode = [], None

    for line in md.split("\n"):
        s = line.strip()
        if not s:
            flush()
            continue
        if s.startswith("@@MYNOTE:") and s.endswith("@@"):
            flush()
            html.append('<aside class="mynote">%s</aside>' % s[9:-2])
            continue
        h = re.match(r"^(#{1,4})\s+(.*)$", s)
        if h:
            flush()
            lv = min(4, max(2, len(h.group(1))))   # 글 제목이 h1이므로 본문 소제목은 h2부터
            html.append("<h%d>%s</h%d>" % (lv, inline(h.group(2)), lv))
            continue
        if re.match(r"^(-{3,}|\*{3,}|_{3,})$", s):
            flush()
            html.append("<hr>")
            continue
        kind = "quote" if s.startswith(">") else \
               "ul" if re.match(r"^[-*+]\s+", s) else \
               "ol" if re.match(r"^\d+\.\s+", s) else "p"
        if mode and mode != kind:
            flush()
        mode = kind
        buf.append(s)
    flush()
    return "\n".join(html)


def plain(md, limit=155):
    """meta description·미리보기용 — 마크다운 기호를 걷어낸 순수 문장."""
    t = re.sub(r"(?:^//[^\n]*\n?)+", " ", md, flags=re.M)
    t = re.sub(r"^#{1,4}\s+.*$", " ", t, flags=re.M)
    t = re.sub(r"[#>*`_\[\]]|^[-*+]\s+|^\d+\.\s+", " ", t, flags=re.M)
    t = re.sub(r"\s+", " ", t).strip()
    return (t[:limit].rstrip() + "…") if len(t) > limit else t


def read_min(body):
    return max(1, round(len(body) / 550))


# ---------------------------------------------------------------- 페이지 만들기
def cat_class(cat, order):
    try:
        return "c%d" % (order.index(str(cat or "노트")) % 6)
    except ValueError:
        return "c0"


def source_card(book, books):
    """글 끝의 Reference 카드 + 눌렀을 때 뜨는 책 소개 모달.

    글 페이지는 정적이라 books.js를 불러오지 않는다. 그래서 이 책의 정보만
    모달 안에 미리 심어 둔다 — 요청이 한 번도 늘지 않고 오프라인에서도 뜬다."""
    if not book:
        return ""
    b = books.get(book, {"title": book})
    sub = " · ".join(str(x) for x in (b.get("author"), b.get("publisher"), b.get("published")) if x)

    def thumb(cls, w, h):
        if b.get("cover"):
            return ('<img class="%s" src="%s" alt="%s 표지" loading="lazy" width="%d" height="%d">'
                    % (cls, escape(b.get("cover", "")), escape(book), w, h))
        return '<span class="%s ph">%s</span>' % (cls, escape(book[:1]))

    rating = b.get("rating")
    stars = ('<div class="bm-rate">%s</div>'
             % ("★" * int(rating) + "☆" * (5 - int(rating)))) if rating else ""

    def block(label, text):
        return ('<div class="bm-sec"><div class="bm-lab">%s</div><p>%s</p></div>'
                % (label, escape(str(text)))) if text else ""

    return """<button class="a-src" type="button" data-book-open>
  <span class="s-lab">Reference</span>
  <span class="s-row">
    %s
    <span class="s-txt">
      <span class="s-t">%s</span>
      %s
    </span>
  </span>
</button>

<div class="bm" data-book-modal hidden>
  <div class="bm-back" data-book-close></div>
  <div class="bm-sheet" role="dialog" aria-modal="true" aria-label="%s 소개">
    <button class="bm-x" type="button" data-book-close aria-label="닫기">×</button>
    <div class="bm-head">
      %s
      <div class="bm-meta">
        <div class="bm-t">%s</div>
        %s
        %s
      </div>
    </div>
    %s
    %s
    <a class="bm-go" href="../index.html#book/%s">서재에서 보기 →</a>
  </div>
</div>""" % (
        thumb("s-cover", 58, 84),
        escape(book),
        '<span class="s-s">%s</span>' % escape(sub) if sub else "",
        escape(book, quote=True),
        thumb("bm-cover", 120, 172),
        escape(book),
        '<div class="bm-s">%s</div>' % escape(sub) if sub else "",
        stars,
        block("줄거리", b.get("summary")),
        block("내 리뷰", b.get("review")),
        quote(book, safe=""),
    )


def page_html(post, slug, books, order, related, rel_by_book, slugs):
    url = post_url(slug)
    desc = plain(post["body"])
    title = post["title"]
    cat = post.get("category") or "노트"
    cover = books.get(post.get("book", ""), {}).get("cover", "")

    ld = {
        "@context": "https://schema.org",
        "@type": "BlogPosting",
        "headline": title,
        "description": desc,
        "datePublished": post["d"],
        "dateModified": post["d"],
        "articleSection": cat,
        "inLanguage": "ko-KR",
        "mainEntityOfPage": {"@type": "WebPage", "@id": url},
        "url": url,
        "author": {"@type": "Person", "name": "헬로"},
        "publisher": {"@type": "Organization", "name": "헬로의 서재", "url": SITE + "/"},
    }
    if cover:
        ld["image"] = cover
    if post.get("book"):
        ld["about"] = {"@type": "Book", "name": post["book"]}

    rel_html = ""
    if len(related) > 1:
        items = []
        for r in related:
            if r["id"] == post["id"]:      # 지금 읽는 글 — 링크가 아니라 현재 위치 표시
                items.append('<li class="cur"><span class="t">%s</span>'
                             '<span class="now">지금 읽는 글</span></li>' % escape(r["title"]))
            else:
                items.append('<li><a href="./%s.html">%s</a><span>%s</span></li>'
                             % (quote(slugs[r["id"]]), escape(r["title"]), r.get("d", "")))
        head = ('『%s』에서 쓴 노트' % escape(post["book"])) if rel_by_book \
               else ('「%s」 분류의 글' % escape(cat))
        rel_html = ('<section class="a-rel"><h2>%s</h2><ul>%s</ul></section>'
                    % (head, "".join(items)))

    return """<!DOCTYPE html>
<html lang="ko">
<head>
<meta charset="utf-8" />
<meta name="viewport" content="width=device-width, initial-scale=1" />
<title>{title} — 헬로의 서재</title>
<meta name="description" content="{desc}" />
<meta name="author" content="헬로" />
<meta name="robots" content="index, follow, max-image-preview:large, max-snippet:-1" />
<link rel="canonical" href="{url}" />
<meta property="og:type" content="article" />
<meta property="og:site_name" content="헬로의 서재" />
<meta property="og:locale" content="ko_KR" />
<meta property="og:title" content="{title}" />
<meta property="og:description" content="{desc}" />
<meta property="og:url" content="{url}" />{og_image}
<meta property="article:published_time" content="{date}" />
<meta property="article:section" content="{cat}" />
<meta name="twitter:card" content="summary" />
<link rel="alternate" type="application/rss+xml" title="헬로의 서재 — 생각 노트" href="../feed.xml" />
<link rel="preconnect" href="https://fonts.googleapis.com" />
<link href="https://fonts.googleapis.com/css2?family=Noto+Serif+KR:wght@400;600&display=swap" rel="stylesheet" />
<link rel="stylesheet" href="./style.css" />
<script type="application/ld+json">{ld}</script>
</head>
<body>

<nav>
  <a class="brand" href="../index.html">헬로의 서재</a>
  <div class="links">
    <a href="../books.html">책</a>
    <a href="../quotes.html">문장</a>
    <a href="../notes.html" style="text-decoration:underline; text-underline-offset:6px; text-decoration-thickness:2px;">노트</a>
  </div>
</nav>

<div class="wrap">
  <article class="article">
    <a class="a-back" href="../notes.html">← 생각 노트</a>
    <div><span class="a-cat cat {catcls}">{cat}</span></div>
    <h1 class="a-title">{title}</h1>
    <div class="a-meta"><time datetime="{date}">{date}</time> · 읽는 시간 약 {mins}분</div>
    <div class="a-body">
{body}
    </div>
    {source}
    {related}
    <div class="a-end">
      <a href="../notes.html">생각 노트 전체 보기 →</a>
    </div>
  </article>
</div>

<footer>
  <div class="f-brand">헬로의 서재</div>
  <p>읽는 사람, 헬로. 책과 문장을 수집합니다.</p>
  <p class="f-copy">© 2026 헬로. 직접 쓴 글과 리뷰, 그리고 문장을 고르고 엮은 방식에 대한 저작권은 헬로에게 있습니다.<br>
    인용한 문장과 책 표지의 권리는 각 저작권자에게 있으며, 출처를 밝혀 인용했습니다.</p>
  <p class="f-mail">문의·정정 요청은 <a href="mailto:hello_kitten@naver.com">hello_kitten@naver.com</a></p>
</footer>

<script>
/* 콘텐츠 보호: 복사·잘라내기·드래그·우클릭 차단 (입력칸 제외) */
(function(){{
  const exempt = e => e.target && (e.target.tagName === "INPUT" || e.target.tagName === "TEXTAREA");
  ["copy","cut","dragstart","contextmenu"].forEach(ev =>
    document.addEventListener(ev, e => {{ if(!exempt(e)) e.preventDefault(); }}));
}})();

/* Reference 카드 → 책 소개 모달 */
(function(){{
  const modal = document.querySelector("[data-book-modal]");
  if(!modal) return;
  const opener = document.querySelector("[data-book-open]");
  const show = on => {{
    modal.hidden = !on;
    document.body.style.overflow = on ? "hidden" : "";
    if(on) modal.querySelector(".bm-x").focus();
    else if(opener) opener.focus();
  }};
  if(opener) opener.onclick = () => show(true);
  modal.querySelectorAll("[data-book-close]").forEach(el => el.onclick = () => show(false));
  document.addEventListener("keydown", e => {{ if(e.key === "Escape" && !modal.hidden) show(false); }});
}})();
</script>
<!-- 네이버 애널리틱스 — 방문자 수·유입 검색어·인기 페이지.
     index.html·notes.html에도 같은 조각이 들어 있다(셋 다 같은 ID를 써야 한 사이트로 집계된다). -->
<script type="text/javascript" src="//wcs.pstatic.net/wcslog.js"></script>
<script type="text/javascript">
if(!wcs_add) var wcs_add = {{}};
wcs_add["wa"] = "995a28458386";
if(window.wcs) {{ wcs_do(); }}
</script>
</body>
</html>
""".format(
        title=escape(title, quote=True),
        desc=escape(desc, quote=True),
        url=url,
        og_image='\n<meta property="og:image" content="%s" />' % escape(cover, quote=True) if cover else "",
        date=post["d"],
        cat=escape(cat, quote=True),
        catcls=cat_class(cat, order),
        ld=json.dumps(ld, ensure_ascii=False),
        mins=read_min(post["body"]),
        body=render(post["body"]),
        source=source_card(post.get("book"), books),
        related=rel_html,
    )


# ---------------------------------------------------------------- 사이트맵·RSS
def write_sitemap(posts, slugs):
    # '지금'이 아니라 가장 최근 글의 날짜를 쓴다 — 돌릴 때마다 결과가 달라지면
    # 내용이 그대로여도 자동 빌드가 매번 빈 커밋을 남긴다.
    latest = max(p["d"] for p in posts) if posts else "2026-01-01"
    urls = [("%s/" % SITE, latest, "daily", "1.0"),
            ("%s/notes.html" % SITE, latest, "daily", "0.9"),
            ("%s/books.html" % SITE, latest, "daily", "0.9"),
            ("%s/quotes.html" % SITE, latest, "daily", "0.9")]
    urls += [(post_url(slugs[p["id"]]), p["d"], "monthly", "0.8") for p in posts]
    body = "\n".join(
        "  <url>\n    <loc>%s</loc>\n    <lastmod>%s</lastmod>\n"
        "    <changefreq>%s</changefreq>\n    <priority>%s</priority>\n  </url>" % u
        for u in urls)
    open(os.path.join(ROOT, "sitemap.xml"), "w", encoding="utf-8").write(
        '<?xml version="1.0" encoding="UTF-8"?>\n'
        '<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">\n%s\n</urlset>\n' % body)


def write_feed(posts, slugs):
    """RSS — 네이버·다음 검색과 구독기가 새 글을 빨리 알아채는 통로."""

    def pubdate(d):
        return datetime.strptime(d, "%Y-%m-%d").replace(tzinfo=KST).strftime(
            "%a, %d %b %Y 09:00:00 +0900")

    now = pubdate(max(p["d"] for p in posts)) if posts else pubdate("2026-01-01")

    items = "\n".join("""  <item>
    <title>{t}</title>
    <link>{u}</link>
    <guid isPermaLink="true">{u}</guid>
    <category>{c}</category>
    <pubDate>{pd}</pubDate>
    <description>{d}</description>
  </item>""".format(t=escape(p["title"]), u=post_url(slugs[p["id"]]),
                    c=escape(p.get("category") or "노트"), pd=pubdate(p["d"]),
                    d=escape(plain(p["body"], 300)))
                     for p in posts[:30])
    open(os.path.join(ROOT, "feed.xml"), "w", encoding="utf-8").write(
        """<?xml version="1.0" encoding="UTF-8"?>
<rss version="2.0" xmlns:atom="http://www.w3.org/2005/Atom">
<channel>
  <title>헬로의 서재 — 생각 노트</title>
  <link>{site}/notes.html</link>
  <atom:link href="{site}/feed.xml" rel="self" type="application/rss+xml" />
  <description>읽고 생각한 것을, 길게 풀어 씁니다.</description>
  <language>ko</language>
  <lastBuildDate>{now}</lastBuildDate>
{items}
</channel>
</rss>
""".format(site=SITE, now=now, items=items))


def write_slugs(posts, slugs):
    table = {p["id"]: slugs[p["id"]] for p in posts}
    open(os.path.join(ROOT, "slugs.js"), "w", encoding="utf-8").write(
        "// build_notes.py가 만드는 파일 — 손으로 고치지 마세요.\n"
        "// 글 id → 글 페이지 주소(p/<슬러그>.html). 한 번 준 주소는 바뀌지 않습니다.\n"
        "const SLUGS = %s;\n" % json.dumps(table, ensure_ascii=False, indent=2))


# ---------------------------------------------------------------- 실행
def main():
    posts = load_posts()
    books = load_books()
    slugs = assign_slugs(posts)
    order = sorted({str(p.get("category") or "노트") for p in posts})

    os.makedirs(OUT_DIR, exist_ok=True)
    shutil.copyfile(os.path.join(ROOT, "notes-post.css"), os.path.join(OUT_DIR, "style.css"))

    keep = {"style.css"}
    for p in posts:
        slug = slugs[p["id"]]
        keep.add(slug + ".html")
        # 같은 책에서 쓴 노트 다섯 편 — 한 권을 읽어나간 순서라 분류보다 촘촘하다.
        # 그 책 노트가 이 글 하나뿐이면 같은 분류로 대신한다.
        group = [q for q in posts if p.get("book") and q.get("book") == p.get("book")]
        by_book = len(group) > 1
        if not by_book:
            group = [q for q in posts if q.get("category") == p.get("category")]
        me = group.index(p)
        start = max(0, min(me - 2, len(group) - 5))
        html = page_html(p, slug, books, order, related=group[start:start + 5],
                         rel_by_book=by_book, slugs=slugs)
        open(os.path.join(OUT_DIR, slug + ".html"), "w", encoding="utf-8").write(html)

    removed = [f for f in os.listdir(OUT_DIR) if f not in keep]
    for f in removed:
        os.remove(os.path.join(OUT_DIR, f))

    write_slugs(posts, slugs)
    write_sitemap(posts, slugs)
    write_feed(posts, slugs)

    print("글 %d편 → p/*.html" % len(posts))
    if removed:
        print("정리한 옛 파일: %s" % ", ".join(removed))
    print("sitemap.xml · feed.xml · slugs.js 갱신 완료")


if __name__ == "__main__":
    main()
