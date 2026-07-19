// ============================================================
//  헬로의 서재 — 전용 검색 통로 (Cloudflare Worker)
//  사용법: dash.cloudflare.com → Workers & Pages → Create Worker
//         → 기본 코드를 전부 지우고 이 파일 내용 붙여넣기 → Deploy
//         → 발급된 주소(https://….workers.dev)를 관리 페이지에 저장
// ============================================================
export default {
  async fetch(request) {
    const target = new URL(request.url).searchParams.get("url");
    const ALLOWED = ["comic.naver.com", "search.kyobobook.co.kr"];
    let host = "";
    try { host = new URL(target).hostname; } catch (e) {}
    if (!target || !ALLOWED.includes(host)) {
      return new Response("forbidden", { status: 403 });
    }
    const upstream = await fetch(target, {
      headers: { "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7)" },
    });
    const res = new Response(upstream.body, upstream);
    res.headers.set("Access-Control-Allow-Origin", "*");
    return res;
  },
};
