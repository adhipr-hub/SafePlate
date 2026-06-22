"""Static marketing & legal pages for the public-facing SafePlate site.

The home page (the search app) lives in ``app_template.html`` and is served by
``api_server.app_html``. Everything else -- How it works, About, FAQ, Privacy,
Terms, Contact -- is rendered here from one shared shell so the navigation,
footer, and design language stay identical across the whole site without a
template engine.

Each page is plain static HTML built from Python strings, so serving is a cheap
dict lookup in :mod:`safeplate.api_server`. The design tokens mirror the ones in
``app_template.html`` (same fonts, palette, elevation) so the marketing pages and
the app feel like one product.
"""

from __future__ import annotations

# Reused everywhere -- the rounded "checkmark pin" brand mark.
_BRAND_MARK = (
    '<svg viewBox="0 0 24 24" fill="none">'
    '<path d="M12 21s-7-4.5-7-10a7 7 0 0 1 14 0c0 5.5-7 10-7 10Z" fill="rgba(255,255,255,0.18)"/>'
    '<path d="M9.5 11.5l1.8 1.8 3.4-3.6" stroke="#fff" stroke-width="2" '
    'stroke-linecap="round" stroke-linejoin="round"/>'
    "</svg>"
)

# Primary nav: (href, label). The CTA and legal links are handled separately.
_NAV_LINKS = (
    ("/how-it-works", "How it works"),
    ("/about", "About"),
    ("/faq", "FAQ"),
    ("/contact", "Contact"),
)

# Shared CSS for every non-app page. Tokens kept in sync with app_template.html.
_BASE_CSS = """
:root{
  --page:#F5F3EF; --surface:#FFFFFF; --border:#E3DDD5; --border2:#C9C2B8;
  --tx:#1A1714; --tx2:#6A5F57; --tx3:#A09088;
  --g0:#166534; --g1:#16A34A; --g2:#22C55E; --gt:#DCFCE7;
  --e1:0 1px 3px rgba(0,0,0,.07),0 1px 2px rgba(0,0,0,.04);
  --e2:0 4px 16px -2px rgba(0,0,0,.09),0 1px 4px rgba(0,0,0,.05);
  --e3:0 12px 36px -6px rgba(0,0,0,.14),0 3px 10px rgba(0,0,0,.06);
  --e4:0 24px 64px -10px rgba(0,0,0,.18),0 6px 20px rgba(0,0,0,.07);
  --rXS:8px; --rS:12px; --rM:16px; --rL:20px; --rXL:24px; --pill:999px;
}
*,*::before,*::after{box-sizing:border-box;margin:0;padding:0;}
html{scroll-behavior:smooth;}
body{
  background:var(--page); color:var(--tx);
  font-family:"IBM Plex Sans",-apple-system,BlinkMacSystemFont,"Segoe UI",system-ui,sans-serif;
  -webkit-font-smoothing:antialiased; font-size:15px; letter-spacing:-.011em; line-height:1.5;
}
a{color:inherit;}
.wrap{max-width:1160px; margin:0 auto; padding:0 24px;}
.narrow{max-width:760px; margin:0 auto; padding:0 24px;}

/* ── Nav ── */
.topbar{position:sticky; top:0; z-index:30; background:rgba(255,255,255,.86);
  backdrop-filter:blur(20px) saturate(1.8); border-bottom:1px solid var(--border);}
.topbar-inner{display:flex; align-items:center; height:62px; gap:14px;}
.brand{display:flex; align-items:center; gap:9px; text-decoration:none;}
.brand-mark{width:30px; height:30px; border-radius:9px; flex:none;
  background:linear-gradient(145deg,#15803D,#16A34A); display:grid; place-items:center;
  box-shadow:0 3px 10px -2px rgba(21,128,61,.5);}
.brand-mark svg{width:16px; height:16px;}
.brand-name{font-weight:700; font-size:16.5px; letter-spacing:-.035em; color:var(--tx);}
.brand-name em{font-style:normal; color:var(--g1);}
.nav-links{display:flex; align-items:center; gap:4px; margin-left:18px;}
.nav-link{font-size:13.5px; font-weight:600; color:var(--tx2); text-decoration:none;
  padding:7px 12px; border-radius:10px; transition:all .15s;}
.nav-link:hover{color:var(--tx); background:rgba(0,0,0,.04);}
.nav-link.active{color:var(--g0); background:var(--gt);}
.nav-cta{margin-left:auto; display:inline-flex; align-items:center; gap:7px;
  padding:9px 16px; border-radius:var(--pill); text-decoration:none;
  background:linear-gradient(145deg,#16A34A,#15803D); color:#fff; font-size:13.5px;
  font-weight:700; box-shadow:0 4px 14px -4px rgba(21,128,61,.5); transition:transform .15s,box-shadow .15s;}
.nav-cta:hover{transform:translateY(-1px); box-shadow:0 8px 22px -4px rgba(21,128,61,.5);}
.nav-toggle{display:none; margin-left:auto; width:40px; height:40px; border-radius:10px;
  border:1.5px solid var(--border); background:var(--surface); color:var(--tx);
  cursor:pointer; align-items:center; justify-content:center;}

/* ── Page hero ── */
.phero{position:relative; overflow:hidden; padding:64px 0 30px; text-align:center;}
.phero::before{content:""; position:absolute; pointer-events:none; width:640px; height:520px;
  border-radius:50%; top:-240px; left:-180px;
  background:radial-gradient(circle,rgba(22,163,74,.11) 0%,transparent 68%);}
.phero::after{content:""; position:absolute; pointer-events:none; width:520px; height:420px;
  border-radius:50%; top:-140px; right:-120px;
  background:radial-gradient(circle,rgba(14,165,233,.08) 0%,transparent 65%);}
.eyebrow{display:inline-flex; align-items:center; gap:6px; position:relative; z-index:1;
  font-size:11.5px; font-weight:700; letter-spacing:.13em; text-transform:uppercase;
  color:var(--g0); padding:5px 13px; border-radius:var(--pill);
  border:1.5px solid rgba(22,101,52,.2); background:rgba(22,101,52,.07); margin-bottom:20px;}
.eyebrow-pip{width:5px; height:5px; border-radius:50%; background:var(--g1); flex:none;}
.phero h1{font-family:"Newsreader",Georgia,serif; font-weight:700; position:relative; z-index:1;
  font-size:clamp(34px,4.6vw,52px); line-height:1.04; letter-spacing:-.025em; margin-bottom:16px;}
.phero h1 .grad{background:linear-gradient(125deg,#15803D 5%,#0284C7 92%);
  -webkit-background-clip:text; background-clip:text; color:transparent;}
.phero-sub{font-size:17px; color:var(--tx2); line-height:1.6; max-width:560px;
  margin:0 auto; position:relative; z-index:1;}

/* ── Sections ── */
.section{padding:46px 0;}
.section-head{text-align:center; max-width:640px; margin:0 auto 34px;}
.section-kicker{font-size:11.5px; font-weight:700; letter-spacing:.13em; text-transform:uppercase;
  color:var(--g1); margin-bottom:10px;}
.section-title{font-family:"Newsreader",Georgia,serif; font-weight:700; font-size:clamp(26px,3.4vw,36px);
  letter-spacing:-.02em; line-height:1.1;}
.section-lede{font-size:16px; color:var(--tx2); margin-top:12px; line-height:1.6;}

/* ── Feature / step grids ── */
.grid3{display:grid; grid-template-columns:repeat(auto-fit,minmax(260px,1fr)); gap:16px;}
.grid2{display:grid; grid-template-columns:repeat(auto-fit,minmax(300px,1fr)); gap:16px;}
.card{background:var(--surface); border:1.5px solid var(--border); border-radius:var(--rL);
  padding:24px; box-shadow:var(--e1); transition:transform .2s,box-shadow .2s,border-color .2s;}
.card:hover{transform:translateY(-2px); box-shadow:var(--e3); border-color:var(--border2);}
.card-ico{width:44px; height:44px; border-radius:12px; display:grid; place-items:center;
  background:var(--gt); color:var(--g0); margin-bottom:15px;}
.card-ico svg{width:22px; height:22px;}
.card h3{font-size:17px; font-weight:700; letter-spacing:-.015em; margin-bottom:7px;}
.card p{font-size:14px; color:var(--tx2); line-height:1.55;}
.step-num{font-family:"Newsreader",Georgia,serif; font-weight:700; font-size:18px; color:#fff;
  width:36px; height:36px; border-radius:50%; display:grid; place-items:center;
  background:linear-gradient(145deg,#16A34A,#15803D); margin-bottom:15px;
  box-shadow:0 3px 10px -2px rgba(21,128,61,.45);}

/* ── Trust / provenance chips ── */
.pvchip{font-size:11px; font-weight:700; padding:3px 9px; border-radius:var(--pill);
  border:1.5px solid var(--border); display:inline-block;}
.pv-confirmed{color:var(--g0); border-color:rgba(22,101,52,.32); background:rgba(22,101,52,.08);}
.pv-signal{color:#2563EB; border-color:rgba(37,99,235,.26); background:rgba(37,99,235,.06);}
.pv-inferred{color:#92400E; border-color:rgba(146,64,14,.26); background:rgba(146,64,14,.06);}
.pv-estimate{color:var(--tx3); border-color:var(--border);}
.pv-callahead{color:#7c3aed; border-color:rgba(124,58,237,.26); background:rgba(124,58,237,.06);}
.trust-row{display:flex; gap:14px; align-items:flex-start; padding:16px 0; border-bottom:1px solid var(--border);}
.trust-row:last-child{border-bottom:none;}
.trust-row .tr-chip{flex:none; padding-top:2px;}
.trust-row .tr-text{font-size:14px; color:var(--tx2); line-height:1.55;}
.trust-row .tr-text strong{color:var(--tx); font-weight:600;}

/* ── Stat band ── */
.statband{background:linear-gradient(135deg,#103D26,#15803D); border-radius:var(--rXL);
  padding:34px 28px; color:#fff; box-shadow:var(--e3);}
.statgrid{display:grid; grid-template-columns:repeat(auto-fit,minmax(150px,1fr)); gap:24px; text-align:center;}
.stat-num{font-family:"Newsreader",Georgia,serif; font-weight:700; font-size:38px; letter-spacing:-.02em; line-height:1;}
.stat-lbl{font-size:13px; margin-top:8px; color:rgba(255,255,255,.82);}

/* ── Prose (legal pages) ── */
.prose{font-size:15px; color:var(--tx2); line-height:1.72;}
.prose h2{font-family:"Newsreader",Georgia,serif; font-size:23px; font-weight:700; color:var(--tx);
  letter-spacing:-.015em; margin:34px 0 12px;}
.prose h3{font-size:16px; font-weight:700; color:var(--tx); margin:22px 0 8px;}
.prose p{margin:0 0 14px;}
.prose ul{margin:0 0 14px; padding-left:20px;}
.prose li{margin:6px 0;}
.prose a{color:var(--g0); font-weight:600; text-decoration:underline; text-underline-offset:2px;}
.prose strong{color:var(--tx); font-weight:600;}
.updated{font-size:13px; color:var(--tx3); margin-bottom:8px;}

/* ── FAQ ── */
.faq-list{display:flex; flex-direction:column; gap:12px;}
.faq-item{background:var(--surface); border:1.5px solid var(--border); border-radius:var(--rM);
  overflow:hidden; transition:border-color .2s, box-shadow .2s;}
.faq-item[open]{border-color:var(--border2); box-shadow:var(--e2);}
.faq-item summary{list-style:none; cursor:pointer; padding:18px 20px; font-size:15.5px;
  font-weight:600; color:var(--tx); display:flex; align-items:center; justify-content:space-between; gap:14px;}
.faq-item summary::-webkit-details-marker{display:none;}
.faq-item summary .chev{flex:none; width:18px; height:18px; color:var(--tx3); transition:transform .2s;}
.faq-item[open] summary .chev{transform:rotate(180deg);}
.faq-body{padding:0 20px 18px; font-size:14.5px; color:var(--tx2); line-height:1.65;}
.faq-body a{color:var(--g0); font-weight:600; text-decoration:underline;}

/* ── Contact ── */
.contact-card{background:var(--surface); border:1.5px solid var(--border); border-radius:var(--rL);
  padding:24px; box-shadow:var(--e1); display:flex; gap:15px; align-items:flex-start;}
.contact-card .c-ico{width:44px; height:44px; border-radius:12px; flex:none; display:grid;
  place-items:center; background:var(--gt); color:var(--g0);}
.contact-card .c-ico svg{width:22px; height:22px;}
.contact-card h3{font-size:16px; font-weight:700; margin-bottom:4px;}
.contact-card p{font-size:14px; color:var(--tx2); line-height:1.55;}
.contact-card a{color:var(--g0); font-weight:600; text-decoration:none;}
.contact-card a:hover{text-decoration:underline;}

/* ── CTA band ── */
.cta{background:var(--surface); border:1.5px solid var(--border); border-radius:var(--rXL);
  padding:40px 28px; text-align:center; box-shadow:var(--e2); position:relative; overflow:hidden;}
.cta::before{content:""; position:absolute; pointer-events:none; width:480px; height:380px;
  border-radius:50%; top:-200px; left:50%; transform:translateX(-50%);
  background:radial-gradient(circle,rgba(22,163,74,.12) 0%,transparent 68%);}
.cta h2{font-family:"Newsreader",Georgia,serif; font-weight:700; font-size:clamp(24px,3vw,32px);
  letter-spacing:-.02em; margin-bottom:12px; position:relative; z-index:1;}
.cta p{font-size:16px; color:var(--tx2); max-width:480px; margin:0 auto 22px; position:relative; z-index:1;}
.btn-primary{display:inline-flex; align-items:center; gap:8px; padding:13px 24px; border-radius:var(--pill);
  text-decoration:none; background:linear-gradient(145deg,#16A34A,#15803D); color:#fff;
  font-size:15px; font-weight:700; box-shadow:0 6px 20px -4px rgba(21,128,61,.5);
  transition:transform .15s,box-shadow .15s; position:relative; z-index:1;}
.btn-primary:hover{transform:translateY(-1px); box-shadow:0 10px 28px -4px rgba(21,128,61,.5);}
.btn-primary svg{width:18px; height:18px; flex:none;}

/* ── Footer ── */
.footer{border-top:1px solid var(--border); background:var(--surface); margin-top:30px;}
.footer-inner{display:grid; grid-template-columns:1.6fr 1fr 1fr 1fr; gap:30px; padding:48px 0 36px;}
.footer-brand .brand{margin-bottom:13px;}
.footer-blurb{font-size:13.5px; color:var(--tx2); line-height:1.6; max-width:280px;}
.footer-col h4{font-size:12px; font-weight:700; text-transform:uppercase; letter-spacing:.1em;
  color:var(--tx3); margin-bottom:14px;}
.footer-col a{display:block; font-size:13.5px; color:var(--tx2); text-decoration:none;
  margin-bottom:10px; transition:color .15s; width:fit-content;}
.footer-col a:hover{color:var(--g0);}
.footer-bottom{border-top:1px solid var(--border); padding:20px 0; display:flex;
  align-items:center; justify-content:space-between; gap:14px; flex-wrap:wrap;}
.footer-copy{font-size:12.5px; color:var(--tx3);}
.footer-note{font-size:12px; color:var(--tx3); max-width:560px; line-height:1.55;}

@media (max-width:860px){
  .footer-inner{grid-template-columns:1fr 1fr;}
}
@media (max-width:680px){
  .nav-links{display:none;}
  .nav-cta{margin-left:auto;}
  .footer-inner{grid-template-columns:1fr 1fr; gap:24px;}
}
"""


def _nav_html(active: str) -> str:
    links = "".join(
        f'<a class="nav-link{" active" if href == active else ""}" href="{href}">{label}</a>'
        for href, label in _NAV_LINKS
    )
    return f"""<header class="topbar">
  <div class="wrap topbar-inner">
    <a class="brand" href="/">
      <span class="brand-mark">{_BRAND_MARK}</span>
      <span class="brand-name">Safe<em>Plate</em></span>
    </a>
    <nav class="nav-links">{links}</nav>
    <a class="nav-cta" href="/#search">Find safe places</a>
  </div>
</header>"""


def _footer_html() -> str:
    return f"""<footer class="footer">
  <div class="wrap footer-inner">
    <div class="footer-brand">
      <a class="brand" href="/">
        <span class="brand-mark">{_BRAND_MARK}</span>
        <span class="brand-name">Safe<em>Plate</em></span>
      </a>
      <p class="footer-blurb">Allergy-aware dining. We read real menus, cuisine patterns,
      and location to rank restaurants by how likely a dish involves nuts &mdash; with the
      evidence shown for every score.</p>
    </div>
    <div class="footer-col">
      <h4>Product</h4>
      <a href="/#search">Find places</a>
      <a href="/how-it-works">How it works</a>
      <a href="/faq">FAQ</a>
    </div>
    <div class="footer-col">
      <h4>Company</h4>
      <a href="/about">About</a>
      <a href="/contact">Contact</a>
    </div>
    <div class="footer-col">
      <h4>Legal</h4>
      <a href="/privacy">Privacy</a>
      <a href="/terms">Terms</a>
    </div>
  </div>
  <div class="wrap footer-bottom">
    <span class="footer-copy">&copy; 2026 SafePlate</span>
    <span class="footer-note">SafePlate estimates allergen risk from public information and
    is not a medical service. Always confirm directly with the restaurant before ordering
    with a serious allergy.</span>
  </div>
</footer>"""


def _render_page(*, title: str, description: str, active: str, body: str) -> str:
    """Wrap page ``body`` in the shared HTML shell (head, nav, footer)."""
    return f"""<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8" />
  <meta name="viewport" content="width=device-width, initial-scale=1" />
  <title>{title} &middot; SafePlate</title>
  <meta name="description" content="{description}" />
  <link rel="preconnect" href="https://fonts.googleapis.com" />
  <link rel="preconnect" href="https://fonts.gstatic.com" crossorigin />
  <link href="https://fonts.googleapis.com/css2?family=IBM+Plex+Sans:wght@400;500;600;700&family=Newsreader:opsz,wght@6..72,600;6..72,700&display=swap" rel="stylesheet" />
  <style>{_BASE_CSS}</style>
</head>
<body>
{_nav_html(active)}
<main>
{body}
</main>
{_footer_html()}
<script>
  // Smooth-scroll the nav CTA only matters on the home page; here it just navigates.
  // Open the FAQ item that matches the URL hash (e.g. /faq#cross-contact).
  (function(){{
    var id = location.hash.slice(1);
    if (!id) return;
    var el = document.getElementById(id);
    if (el && el.tagName === "DETAILS") {{ el.open = true; el.scrollIntoView({{behavior:"smooth", block:"center"}}); }}
  }})();
</script>
</body>
</html>"""


# ─────────────────────────────────────────────────────────────────────────────
# Reusable small SVGs (stroked, 24x24)
# ─────────────────────────────────────────────────────────────────────────────
def _ico(paths: str) -> str:
    return f'<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round">{paths}</svg>'


_ICO_PIN = _ico('<path d="M12 21s-7-5.2-7-11a7 7 0 1 1 14 0c0 5.8-7 11-7 11Z"/><circle cx="12" cy="10" r="2.5"/>')
_ICO_DOC = _ico('<rect x="4" y="3" width="16" height="18" rx="2"/><path d="M8 8h8M8 12h6M8 16h4"/>')
_ICO_SPARK = _ico('<path d="M12 3v4M12 17v4M3 12h4M17 12h4M6 6l2.5 2.5M15.5 15.5L18 18M18 6l-2.5 2.5M8.5 15.5L6 18"/>')
_ICO_SHIELD = _ico('<path d="M12 3l7 3v5c0 4.5-3 7.6-7 9-4-1.4-7-4.5-7-9V6l7-3Z"/><path d="M9.5 12l1.8 1.8 3.4-3.6"/>')
_ICO_BOLT = _ico('<path d="M13 2L4 14h7l-1 8 9-12h-7l1-8Z"/>')
_ICO_EYE = _ico('<path d="M2 12s3.5-7 10-7 10 7 10 7-3.5 7-10 7-10-7-10-7Z"/><circle cx="12" cy="12" r="3"/>')
_ICO_LIST = _ico('<path d="M8 6h12M8 12h12M8 18h12M4 6h.01M4 12h.01M4 18h.01"/>')
_ICO_CHAT = _ico('<path d="M4 5h16v11H9l-5 4V5Z"/>')
_ICO_MAIL = _ico('<rect x="3" y="5" width="18" height="14" rx="2"/><path d="m3 7 9 6 9-6"/>')
_ICO_HEART = _ico('<path d="M12 20s-7-4.6-9.3-9C1 8 3 4.5 6.5 4.5c2 0 3.5 1.4 5.5 3.5 2-2.1 3.5-3.5 5.5-3.5C21 4.5 23 8 21.3 11 19 15.4 12 20 12 20Z"/>')
_CHEV = '<svg class="chev" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="m6 9 6 6 6-6"/></svg>'


def _phero(eyebrow: str, title_html: str, sub: str) -> str:
    return f"""<section class="phero">
  <div class="wrap">
    <div class="eyebrow"><span class="eyebrow-pip"></span>{eyebrow}</div>
    <h1>{title_html}</h1>
    <p class="phero-sub">{sub}</p>
  </div>
</section>"""


def _cta_band() -> str:
    return f"""<section class="section"><div class="wrap"><div class="cta">
  <h2>Find a safer table near you</h2>
  <p>Enter your neighbourhood and see nearby restaurants ranked by nut risk &mdash; with the
  evidence behind every score.</p>
  <a class="btn-primary" href="/#search">{_ICO_PIN}&nbsp;Find safe places</a>
</div></div></section>"""


# ─────────────────────────────────────────────────────────────────────────────
# Page content
# ─────────────────────────────────────────────────────────────────────────────
def _how_it_works_body() -> str:
    steps = [
        (_ICO_PIN, "1", "Tell us where &amp; what",
         "Search a city, neighbourhood, or use your location, and set your allergy &mdash; nuts "
         "today, with sesame, dairy and gluten on the way. You also set how serious your reaction "
         "is and how careful you need to be about cross-contact."),
        (_ICO_DOC, "2", "We read the real menu",
         "For each nearby restaurant we find and read its actual online menu, allergen charts, and "
         "allergy statements &mdash; not just the cuisine type. Structured allergen data is used "
         "directly; prose is interpreted dish by dish."),
        (_ICO_SPARK, "3", "You get a ranked, explained score",
         "Every place gets a nut-risk score and a plain-English reason. Tap any restaurant to see "
         "the riskiest dishes, what the restaurant says about allergies, and exactly where the "
         "score came from."),
    ]
    step_cards = "".join(
        f"""<div class="card">
          <div class="step-num">{n}</div>
          <h3>{title}</h3>
          <p>{body}</p>
        </div>"""
        for ico, n, title, body in steps
    )

    layers = [
        ("pv-confirmed", "Confirmed",
         "We found the allergen named in the restaurant&rsquo;s own published allergen chart or "
         "directly in the menu text. This is the strongest evidence."),
        ("pv-signal", "Restaurant info",
         "Based on the restaurant&rsquo;s own allergy statements &mdash; disclaimers, cross-contact "
         "warnings, or staff-protocol notes found on their site."),
        ("pv-inferred", "Inferred",
         "Inferred from dish names and descriptions, or from reading the full menu and finding no "
         "nut dishes. Informed, but not a confirmed allergen list."),
        ("pv-estimate", "Estimate",
         "Estimated from cuisine and area when no usable menu is available &mdash; the weakest "
         "signal, used only as a starting point."),
        ("pv-callahead", "Call ahead",
         "No online menu was found at all. We tell you plainly to confirm directly with the "
         "restaurant rather than trusting a guess."),
    ]
    trust_rows = "".join(
        f"""<div class="trust-row">
          <span class="tr-chip"><span class="pvchip {cls}">{label}</span></span>
          <span class="tr-text">{body}</span>
        </div>"""
        for cls, label, body in layers
    )

    return _phero(
        "How it works",
        'From a search to a score you can <span class="grad">actually trust</span>.',
        "SafePlate doesn&rsquo;t just guess from the cuisine. It reads real menus, weighs the "
        "evidence, and shows its work &mdash; so you decide with the full picture.",
    ) + f"""
<section class="section"><div class="wrap">
  <div class="section-head">
    <div class="section-kicker">Three steps</div>
    <h2 class="section-title">Search, read, decide</h2>
  </div>
  <div class="grid3">{step_cards}</div>
</div></section>

<section class="section" style="background:var(--surface); border-top:1px solid var(--border); border-bottom:1px solid var(--border);">
  <div class="narrow">
    <div class="section-head">
      <div class="section-kicker">Show your work</div>
      <h2 class="section-title">Every score carries its evidence</h2>
      <p class="section-lede">We never hand you a number with no context. Each restaurant is
      tagged with how we know what we know, so you can weigh it for yourself.</p>
    </div>
    {trust_rows}
  </div>
</section>

<section class="section"><div class="narrow">
  <div class="section-head">
    <div class="section-kicker">Safety first</div>
    <h2 class="section-title">Built to fail safe</h2>
    <p class="section-lede">When evidence is thin, we say so rather than guessing low. A missing
    menu is shown as &ldquo;call ahead,&rdquo; not as &ldquo;safe.&rdquo; The score is a starting
    point for a conversation with the restaurant &mdash; not a substitute for one.</p>
  </div>
</div></section>
{_cta_band()}
"""


def _about_body() -> str:
    values = [
        (_ICO_SHIELD, "Safety over convenience",
         "When we&rsquo;re unsure, we say so. We&rsquo;d rather show &ldquo;call ahead&rdquo; than "
         "a falsely reassuring score."),
        (_ICO_EYE, "Transparent by default",
         "Every score shows its evidence and where it came from. No black-box numbers."),
        (_ICO_HEART, "Built for real diners",
         "Designed around how people with allergies actually decide where to eat &mdash; quickly, "
         "and with the details that matter."),
    ]
    value_cards = "".join(
        f"""<div class="card">
          <div class="card-ico">{ico}</div>
          <h3>{title}</h3>
          <p>{body}</p>
        </div>"""
        for ico, title, body in values
    )

    return _phero(
        "About SafePlate",
        'Eating out should not be a <span class="grad">gamble</span>.',
        "For millions of people with food allergies, a meal out means a quiet calculation of risk. "
        "SafePlate exists to make that calculation honest, fast, and grounded in real evidence.",
    ) + f"""
<section class="section"><div class="narrow prose">
  <h2>Why we built it</h2>
  <p>If you have a nut allergy, you already know the routine: scan the menu, read between the
  lines, ask the server, and hope the kitchen got the message. Most apps either ignore allergies
  entirely or reduce them to a single &ldquo;allergy-friendly&rdquo; badge that tells you nothing
  about <em>this</em> dish at <em>this</em> restaurant.</p>
  <p>We thought the information to do better was already out there &mdash; in menus, allergen
  charts, and the things restaurants say about how they handle allergies. The hard part was
  reading all of it, weighing it honestly, and presenting it in a way you can trust in the few
  seconds you have before deciding where to eat. That&rsquo;s what SafePlate does.</p>

  <h2>How we&rsquo;re different</h2>
  <p>SafePlate ranks nearby restaurants by how likely their menu involves nuts, using real menu
  evidence rather than cuisine stereotypes alone. Crucially, it shows its work: every score is
  tagged with the strength of the evidence behind it, from a restaurant&rsquo;s published allergen
  chart down to a cuisine-based estimate. You always know whether a number is grounded or a guess.</p>
</div></section>

<section class="section" style="background:var(--surface); border-top:1px solid var(--border); border-bottom:1px solid var(--border);">
  <div class="wrap">
    <div class="section-head">
      <div class="section-kicker">What guides us</div>
      <h2 class="section-title">Our principles</h2>
    </div>
    <div class="grid3">{value_cards}</div>
  </div>
</section>

<section class="section"><div class="wrap"><div class="statband"><div class="statgrid">
  <div><div class="stat-num">1 in 13</div><div class="stat-lbl">children in the US have a food allergy</div></div>
  <div><div class="stat-num">200M+</div><div class="stat-lbl">people worldwide live with food allergies</div></div>
  <div><div class="stat-num">32M</div><div class="stat-lbl">Americans affected, across all ages</div></div>
  <div><div class="stat-num">Every score</div><div class="stat-lbl">comes with its evidence shown</div></div>
</div></div></div></section>

<section class="section"><div class="narrow prose">
  <h2>An honest limitation</h2>
  <p>SafePlate is an information tool, not a medical service or a guarantee. Menus change, kitchens
  vary, and cross-contact is hard to see from the outside. We&rsquo;re built to reduce uncertainty
  and to be transparent about what we don&rsquo;t know &mdash; but the final, definitive check is
  always a direct conversation with the restaurant. See our <a href="/faq">FAQ</a> for more.</p>
</div></section>
{_cta_band()}
"""


def _faq_body() -> str:
    faqs = [
        ("what-is", "What exactly does SafePlate tell me?",
         "For restaurants near you, SafePlate estimates how likely their menu involves nuts and "
         "ranks them from safest to riskiest. Open any restaurant to see a plain-English reason for "
         "the score, the dishes most likely to contain nuts, what the restaurant says about "
         "allergies, and how strong the underlying evidence is."),
        ("allergens", "Which allergens are supported?",
         "Today SafePlate focuses on tree nuts and peanuts, where we can be most accurate. Sesame, "
         "dairy, and gluten are on the roadmap. We&rsquo;d rather do one allergen really well than "
         "several poorly."),
        ("accuracy", "How accurate are the scores?",
         "It depends on the evidence available, which is why we always show it. A score built from a "
         "restaurant&rsquo;s published allergen chart is far stronger than one estimated from cuisine "
         "alone &mdash; and we label each so you can tell the difference. Treat every score as a "
         "well-informed starting point, not a guarantee."),
        ("cross-contact", "Does it account for cross-contact?",
         "Partly. SafePlate looks for cross-contact warnings, shared-fryer notes, and allergy "
         "protocols in what a restaurant publishes, and you can tell it how careful you need to be. "
         "But trace exposure in a kitchen can&rsquo;t be fully seen from the outside, so for serious "
         "allergies you should always confirm handling directly with staff."),
        ("call-ahead", "Why do some places say &ldquo;call ahead&rdquo;?",
         "When we can&rsquo;t find a usable online menu, we won&rsquo;t pretend to know. Rather than "
         "guess a low risk that might be wrong, we mark the place &ldquo;call ahead&rdquo; so you "
         "know to confirm directly. We treat missing information as a reason for caution, not "
         "reassurance."),
        ("medical", "Is this medical advice?",
         "No. SafePlate is an information tool to help you make decisions faster, not a medical "
         "service, allergist, or guarantee of safety. Always follow your doctor&rsquo;s guidance and "
         "confirm with the restaurant before ordering with a serious allergy."),
        ("data", "Where does the data come from?",
         "From public information: restaurant listings, their online menus, published allergen "
         "charts, and the allergy statements on their own websites. We read and interpret this "
         "material; we don&rsquo;t collect or sell your personal data. See our "
         "<a href=\"/privacy\">Privacy Policy</a>."),
        ("cost", "Is SafePlate free?",
         "The core experience &mdash; searching for places and seeing ranked, explained scores "
         "&mdash; is free to use."),
    ]
    items = "".join(
        f"""<details class="faq-item" id="{fid}">
          <summary>{q}{_CHEV}</summary>
          <div class="faq-body">{a}</div>
        </details>"""
        for fid, q, a in faqs
    )

    return _phero(
        "FAQ",
        'Questions, <span class="grad">answered honestly</span>.',
        "What SafePlate can tell you, what it can&rsquo;t, and how to use it safely.",
    ) + f"""
<section class="section"><div class="narrow">
  <div class="faq-list">{items}</div>
  <p style="text-align:center; margin-top:28px; font-size:14px; color:var(--tx2);">
    Still have a question? <a href="/contact" style="color:var(--g0); font-weight:600;">Get in touch</a>.
  </p>
</div></section>
{_cta_band()}
"""


def _contact_body() -> str:
    cards = [
        (_ICO_MAIL, "General &amp; support",
         "Questions, feedback, or trouble using SafePlate.",
         "hello@safeplate.app", "mailto:hello@safeplate.app"),
        (_ICO_SHIELD, "Report an inaccurate score",
         "Spotted a restaurant we got wrong? Tell us which one and we&rsquo;ll take a look.",
         "accuracy@safeplate.app", "mailto:accuracy@safeplate.app"),
        (_ICO_CHAT, "Partnerships &amp; press",
         "Restaurants, allergy organisations, and media enquiries.",
         "partners@safeplate.app", "mailto:partners@safeplate.app"),
    ]
    contact_cards = "".join(
        f"""<div class="contact-card">
          <div class="c-ico">{ico}</div>
          <div>
            <h3>{title}</h3>
            <p>{body}</p>
            <p style="margin-top:8px;"><a href="{href}">{addr}</a></p>
          </div>
        </div>"""
        for ico, title, body, addr, href in cards
    )

    return _phero(
        "Contact",
        'We&rsquo;d love to <span class="grad">hear from you</span>.',
        "Feedback makes SafePlate safer. Whether something looks wrong or you just have an idea, "
        "reach out.",
    ) + f"""
<section class="section"><div class="narrow">
  <div class="grid2" style="grid-template-columns:1fr;">{contact_cards}</div>
  <div class="card" style="margin-top:16px; background:var(--gt); border-color:rgba(22,101,52,.2);">
    <h3 style="color:var(--g0);">Having an allergic reaction?</h3>
    <p style="color:var(--tx2);">SafePlate is not for emergencies. If you are having a serious
    allergic reaction, use your epinephrine if prescribed and call your local emergency number
    immediately.</p>
  </div>
</div></section>
"""


def _privacy_body() -> str:
    return _phero(
        "Privacy Policy",
        'Your privacy, <span class="grad">in plain words</span>.',
        "What we collect, what we don&rsquo;t, and why.",
    ) + """
<section class="section"><div class="narrow prose">
  <p class="updated">Last updated: 22 June 2026</p>

  <p>This policy explains how SafePlate (&ldquo;we&rdquo;) handles information when you use the
  SafePlate website and app. We&rsquo;ve tried to keep it short and readable.</p>

  <h2>The short version</h2>
  <ul>
    <li>We don&rsquo;t require an account to search.</li>
    <li>We don&rsquo;t sell your personal data.</li>
    <li>Location is used only to find restaurants near you, when you ask.</li>
    <li>Most of what we show comes from public restaurant information, not from you.</li>
  </ul>

  <h2>Information we process</h2>
  <h3>Location</h3>
  <p>When you type a location or tap &ldquo;use my location,&rdquo; we use it to find nearby
  restaurants and order results. If you use your device&rsquo;s location, your browser asks for
  permission first, and the coordinates are used to serve that search. We don&rsquo;t build a
  profile of your movements.</p>
  <h3>Your allergy preferences</h3>
  <p>The allergen, severity, and cross-contact settings you choose are used to rank and explain
  results for that session. They&rsquo;re part of your request, not a stored medical record.</p>
  <h3>Restaurant &amp; menu information</h3>
  <p>To produce scores, we retrieve and interpret publicly available information &mdash; restaurant
  listings, online menus, allergen charts, and the allergy statements restaurants publish on their
  own sites.</p>
  <h3>Technical &amp; usage data</h3>
  <p>Like most websites, our servers may process basic technical information (such as IP address
  and request logs) to operate the service, prevent abuse, and apply rate limits. We aim to keep
  this to the minimum needed to run SafePlate reliably and securely.</p>

  <h2>How we use information</h2>
  <ul>
    <li>To find restaurants near you and rank them by allergen risk.</li>
    <li>To explain each score and show the evidence behind it.</li>
    <li>To operate, secure, and improve the service.</li>
  </ul>

  <h2>Third-party services</h2>
  <p>SafePlate relies on third-party providers for things like mapping and place data, search, and
  language understanding of menu text. When a feature requires it, the relevant request data is
  shared with that provider to deliver the result. These providers process data under their own
  terms.</p>

  <h2>Data retention</h2>
  <p>We keep operational data only as long as needed to run and protect the service. Search inputs
  are used to serve your request and are not used to build an advertising profile of you.</p>

  <h2>Your choices</h2>
  <ul>
    <li>You can use SafePlate without granting device location &mdash; just type a place instead.</li>
    <li>You can control location permission at any time in your browser or device settings.</li>
  </ul>

  <h2>Children</h2>
  <p>SafePlate is intended for a general audience and is not directed at children under 13. We
  don&rsquo;t knowingly collect personal information from children.</p>

  <h2>Changes</h2>
  <p>We may update this policy as the service evolves. We&rsquo;ll revise the &ldquo;last
  updated&rdquo; date above when we do.</p>

  <h2>Contact</h2>
  <p>Questions about privacy? Email <a href="mailto:privacy@safeplate.app">privacy@safeplate.app</a>
  or visit our <a href="/contact">contact page</a>.</p>
</div></section>
"""


def _terms_body() -> str:
    return _phero(
        "Terms of Service",
        'The <span class="grad">terms</span>, kept simple.',
        "Please read these before relying on SafePlate &mdash; especially the part about allergies.",
    ) + """
<section class="section"><div class="narrow prose">
  <p class="updated">Last updated: 22 June 2026</p>

  <p>These Terms of Service (&ldquo;Terms&rdquo;) govern your use of SafePlate. By using the
  service, you agree to them. If you don&rsquo;t agree, please don&rsquo;t use SafePlate.</p>

  <h2>1. What SafePlate is &mdash; and isn&rsquo;t</h2>
  <p>SafePlate is an informational tool that estimates and ranks how likely restaurant menus
  involve certain allergens, based on public information. <strong>It is not medical advice, not a
  guarantee of safety, and not a substitute for confirming directly with a restaurant or following
  your healthcare provider&rsquo;s guidance.</strong></p>

  <h2>2. Allergy safety notice</h2>
  <p>Food allergies can be life-threatening. SafePlate&rsquo;s scores are estimates derived from
  information that may be incomplete, out of date, or incorrect. Menus, ingredients, suppliers, and
  kitchen practices change without notice, and cross-contact cannot be reliably detected from the
  outside. <strong>Always confirm with the restaurant before ordering, and carry any medication
  prescribed for your allergy.</strong> You are responsible for decisions you make about what you
  eat.</p>

  <h2>3. Acceptable use</h2>
  <ul>
    <li>Use SafePlate only for lawful, personal, non-commercial purposes.</li>
    <li>Don&rsquo;t scrape, overload, or attempt to disrupt the service or bypass rate limits.</li>
    <li>Don&rsquo;t misrepresent SafePlate&rsquo;s output as a guarantee or medical clearance.</li>
  </ul>

  <h2>4. Accuracy &amp; availability</h2>
  <p>We work to make SafePlate useful and honest about its evidence, but we don&rsquo;t warrant that
  results are accurate, complete, or available at any given time. The service is provided on an
  &ldquo;as is&rdquo; and &ldquo;as available&rdquo; basis.</p>

  <h2>5. Third-party information</h2>
  <p>SafePlate displays and interprets information from restaurants and third-party providers. We
  don&rsquo;t control that underlying information and aren&rsquo;t responsible for its accuracy.
  References to a restaurant don&rsquo;t imply endorsement in either direction.</p>

  <h2>6. Limitation of liability</h2>
  <p>To the fullest extent permitted by law, SafePlate and its operators will not be liable for any
  indirect, incidental, or consequential damages, or for any harm arising from reliance on the
  service, including any allergic reaction or health outcome. Your sole remedy for dissatisfaction
  with the service is to stop using it.</p>

  <h2>7. Changes to the service and these Terms</h2>
  <p>We may modify or discontinue features, and we may update these Terms from time to time.
  Continued use after changes means you accept the updated Terms. We&rsquo;ll update the &ldquo;last
  updated&rdquo; date above.</p>

  <h2>8. Contact</h2>
  <p>Questions about these Terms? Email <a href="mailto:legal@safeplate.app">legal@safeplate.app</a>
  or visit our <a href="/contact">contact page</a>.</p>
</div></section>
"""


# ─────────────────────────────────────────────────────────────────────────────
# Public registry: path -> rendered HTML (built once at import).
# ─────────────────────────────────────────────────────────────────────────────
_PAGE_SPECS = {
    "/how-it-works": (
        "How it works",
        "How SafePlate turns real menus, allergen charts, and location into a nut-risk score you can trust.",
        _how_it_works_body,
    ),
    "/about": (
        "About",
        "Why we built SafePlate: making allergy-aware dining honest, fast, and grounded in real evidence.",
        _about_body,
    ),
    "/faq": (
        "FAQ",
        "What SafePlate can tell you, which allergens it covers, how accurate it is, and how to use it safely.",
        _faq_body,
    ),
    "/contact": (
        "Contact",
        "Reach the SafePlate team for support, to report an inaccurate score, or for partnerships and press.",
        _contact_body,
    ),
    "/privacy": (
        "Privacy Policy",
        "How SafePlate handles location, allergy preferences, and data. We don't sell your personal data.",
        _privacy_body,
    ),
    "/terms": (
        "Terms of Service",
        "The terms for using SafePlate, including the important allergy safety notice.",
        _terms_body,
    ),
}

PAGES: dict[str, str] = {
    path: _render_page(title=title, description=desc, active=path, body=body_fn())
    for path, (title, desc, body_fn) in _PAGE_SPECS.items()
}


def get_page(path: str) -> str | None:
    """Return the rendered HTML for a static site path, or ``None`` if unknown."""
    return PAGES.get(path)
